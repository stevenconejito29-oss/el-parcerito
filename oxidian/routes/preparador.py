from flask import (Blueprint, render_template, redirect, url_for, flash,
                   jsonify, Response, stream_with_context, current_app,
                   request)
from flask_login import login_required, current_user
from functools import wraps
import logging
import time
import json as _json
import os as _os
from datetime import timedelta
from sqlalchemy.orm import joinedload
from sqlalchemy import func as _sa_func
from extensions import db, get_or_404
from models import (Order, OrderEvent, OrderItem, User, SiteConfig, Product,
                    ProductBatch, ESTADOS_EN_REPARTO, ESTADOS_TERMINALES,
                    utcnow as _utcnow)
from services import (avanzar_estado_pedido, distribuir_repartidor,
                      redistribuir_pendientes_sin_asignar,
                      sincronizar_proveedores_pedido, lineas_preparacion_interna,
                      pedido_programado_disponible_para_preparar,
                      minutos_anticipacion_pedido_programado)


# ─────────────────────────────────────────────────────────────────────
# Umbrales de la vista de cocina (Fase 6).
# Fuentes en cascada: SiteConfig → env → default.
# Cambiar en /superadmin/config sin redeploy.
# ─────────────────────────────────────────────────────────────────────
_DEFAULT_SSE_HEARTBEAT_S = 15          # keep-alive del stream (segundos)
_DEFAULT_SSE_POLL_S = 3                # cada cuánto miramos cambios reales
_DEFAULT_SSE_MAX_LIFETIME_S = 300      # cerramos y el cliente reconecta

def _cfg_int(clave, default, minimo=1, maximo=None):
    """Lee int desde SiteConfig → env → default con clamps defensivos."""
    val = None
    try:
        val = SiteConfig.get(clave, None)
    except Exception:
        val = None
    if val in (None, ""):
        val = _os.environ.get(clave)
    try:
        n = int(str(val).strip()) if val not in (None, "") else default
    except (TypeError, ValueError):
        n = default
    if n < minimo:
        n = minimo
    if maximo is not None and n > maximo:
        n = maximo
    return n


def _sse_heartbeat_s():
    return _cfg_int("SSE_HEARTBEAT_SECONDS", _DEFAULT_SSE_HEARTBEAT_S, 3, 120)


def _sse_poll_s():
    return _cfg_int("SSE_POLL_SECONDS", _DEFAULT_SSE_POLL_S, 1, 30)


def _sse_max_lifetime_s():
    return _cfg_int("SSE_MAX_LIFETIME_SECONDS", _DEFAULT_SSE_MAX_LIFETIME_S, 30, 3600)


def _tickets_recientes_del_operador():
    """Pedidos recién cerrados que el operador puede volver a imprimir."""
    if current_user.rol not in {"cocina", "preparacion"}:
        return []
    horas = _cfg_int("TICKET_REPRINT_LOOKBACK_HOURS", 72, 1, 168)
    limite = _cfg_int("TICKET_REPRINT_RECENT_LIMIT", 12, 1, 50)
    ultima_actividad = (
        db.session.query(
            OrderEvent.pedido_id.label("pedido_id"),
            _sa_func.max(OrderEvent.creado_en).label("ultima_actividad"),
        )
        .group_by(OrderEvent.pedido_id)
        .subquery()
    )
    return (
        Order.query
        .join(ultima_actividad, ultima_actividad.c.pedido_id == Order.id)
        .filter(
            Order.preparador_id == current_user.id,
            Order.estado.in_(ESTADOS_EN_REPARTO + ESTADOS_TERMINALES),
            ultima_actividad.c.ultima_actividad >= _utcnow() - timedelta(hours=horas),
        )
        .order_by(ultima_actividad.c.ultima_actividad.desc(), Order.id.desc())
        .limit(limite)
        .all()
    )

preparador_bp = Blueprint("preparador", __name__)
logger = logging.getLogger(__name__)

ROLES_PREPARADOR = {"admin", "super_admin", "cocina", "preparacion"}
ESTADOS_ENCARGO_ACTIVOS = ("pendiente", "armando", "listo")


@preparador_bp.before_request
def exigir_modulo_del_rol():
    from store_config import get_store_features

    if (
        current_user.is_authenticated
        and current_user.rol == "preparacion"
        and not get_store_features()["pedidos_programados"]
    ):
        flash("Los pedidos por fecha están desactivados para esta tienda.", "info")
        return redirect(url_for("public.index"))


def _es_admin_operativo():
    return current_user.rol in ("admin", "super_admin")


def _esta_disponible():
    if _es_admin_operativo():
        return True
    usuario = db.session.get(User, current_user.id, populate_existing=True)
    return bool(usuario and usuario.disponible_para_pedidos)


def _requiere_disponible_para_nuevo_trabajo():
    if not _esta_disponible():
        flash("Ponte online para tomar o iniciar pedidos nuevos.", "warning")
        return False
    return True


def _es_encargo(pedido):
    return any(
        item.display_tipo_entrega in ("programado", "encargo")
        for item in pedido.items
    )


def _fecha_encargo(pedido):
    return pedido.fecha_entrega_programada


def _encargo_disponible_para_preparar(pedido):
    return pedido_programado_disponible_para_preparar(pedido)


def _puede_operar_pedido(pedido):
    # Pedidos 100% del bar externo no aparecen en la cola del preparador interno:
    # el bar los prepara y nuestro personal solo gestiona el reparto.
    from services import es_pedido_solo_bar
    if es_pedido_solo_bar(pedido):
        return False
    # NOTA: el atributo Product.canal_preparacion ('cocina' | 'almacen') era una
    # separación interna heredada. NO existe un rol "almacén" — cualquier
    # preparador puede preparar pedidos 100% de productos empaquetados. Esa
    # regla se dejaba pedidos huérfanos y se retiró 2026-07-02.
    if _es_admin_operativo() or pedido.preparador_id == current_user.id:
        return True
    if pedido.preparador_id is not None:
        return False
    # Reparto por rol operativo (misma persona no ve las 2 colas):
    # · cocina        → solo pedidos inmediatos (comida al momento)
    # · preparacion   → solo encargos programados (con fecha)
    # · admin/super_admin → ve TODO
    if current_user.rol == "cocina":
        return not _es_encargo(pedido)
    if current_user.rol == "preparacion":
        return _es_encargo(pedido)
    return False


def _canales_pedido(pedido):
    return {
        (item.display_canal_preparacion or "cocina").strip().lower()
        for item in pedido.items
    }


def _es_pedido_mixto(pedido):
    canales = _canales_pedido(pedido)
    return "cocina" in canales and "almacen" in canales


def _almacen_listo(pedido):
    evento = OrderEvent.query.filter(
        OrderEvent.pedido_id == pedido.id,
        OrderEvent.tipo.in_(["almacen_preparado", "almacen_reabierto"]),
    ).order_by(OrderEvent.id.desc()).first()
    return bool(evento and evento.tipo == "almacen_preparado")


def _notificar_proveedores_pendientes(pedido):
    """Notifica a TODOS los users operadores de cada Proveedor pendiente.

    Antes el `proveedor_id` era un user; ahora es una entidad restaurante con
    potencialmente varios users operadores enlazados por `User.proveedor_id`."""
    from models import User
    proveedor_ids = {
        estado.proveedor_id
        for estado in pedido.estados_proveedor
        if not estado.preparado
    }
    if not proveedor_ids:
        return
    operadores = User.query.filter(
        User.proveedor_id.in_(proveedor_ids),
        User.activo.is_(True),
    ).all()
    if not operadores:
        return
    try:
        from push_service import notify_user
        for operador in operadores:
            notify_user(
                operador.id,
                "Pedido para preparar",
                f"#{pedido.numero_pedido} necesita tu preparación.",
                url="/proveedor/pedidos",
            )
    except Exception:
        logger.exception("No se pudo avisar a proveedores del pedido %s", pedido.id)


def preparador_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.rol not in ROLES_PREPARADOR:
            flash("Acceso restringido.", "danger")
            return redirect(url_for("public.index"))
        return f(*args, **kwargs)
    return decorated


@preparador_bp.route("/toggle-disponible", methods=["POST"])
@preparador_required
def toggle_disponible():
    current_user.toggle_disponible()
    db.session.commit()
    # Al ponerse online, repartir equitativamente los pedidos que esperaban sin preparador
    pedidos_asignados = 0
    if current_user.en_linea:
        pedidos_asignados = redistribuir_pendientes_sin_asignar()
        if pedidos_asignados:
            db.session.commit()
    return jsonify({"ok": True, "en_linea": current_user.en_linea, "pedidos_asignados": pedidos_asignados})


@preparador_bp.route("/pedidos")
@preparador_required
def pedidos():
    disponible = _esta_disponible()
    tickets_recientes = _tickets_recientes_del_operador()
    modo_operativo = (
        "inmediato" if current_user.rol == "cocina"
        else "programado" if current_user.rol == "preparacion"
        else "completo"
    )
    _eager = joinedload(Order.zona)
    if _es_admin_operativo():
        pendientes = Order.query.options(_eager).filter_by(estado="pendiente").order_by(Order.creado_en).all()
        armando = Order.query.options(_eager).filter_by(estado="armando").order_by(Order.creado_en).all()
    else:
        pendientes = Order.query.options(_eager).filter(
            Order.estado == "pendiente",
            db.or_(
                Order.preparador_id == current_user.id,
                Order.preparador_id.is_(None),
            ),
        ).order_by(Order.creado_en).all()
        armando = Order.query.options(_eager).filter_by(
            estado="armando",
            preparador_id=current_user.id,
        ).order_by(Order.creado_en).all()

    companeros = User.query.filter(
        User.rol.in_(["cocina", "preparacion", "admin"]),
        User.activo == True,
        User.id != current_user.id
    ).all()

    # La disponibilidad controla si el empleado puede tomar trabajo, no si puede
    # ver la cola. Ocultarla estando offline impedía planificar y hacía parecer
    # que no existían pedidos. Las rutas POST siguen aplicando el bloqueo real.
    pendientes = [p for p in pendientes if _puede_operar_pedido(p)]
    armando = [p for p in armando if _puede_operar_pedido(p)]
    # Almacén retirado: negocio opera como punto único (cocina + despacho).
    # Se envía dict vacío para no romper referencias del template legacy.
    almacen_listo = {}

    pendientes_encargo  = sorted([p for p in pendientes if _es_encargo(p)],
                                  key=lambda p: min(
                                      (i.display_fecha_entrega for i in p.items
                                       if i.display_fecha_entrega),
                                      default=None
                                  ) or p.creado_en.date())
    pendientes_inmediato = [p for p in pendientes if not _es_encargo(p)]

    # Agrupar los encargos por fecha de entrega para que preparación vea la
    # planificación del día: cuántos pedidos para hoy, mañana, próximos
    # días. La compatibilidad del carrito/API garantiza una única fecha por
    # pedido y el modelo la obtiene siempre desde el snapshot histórico.
    from collections import OrderedDict
    encargos_por_fecha: "OrderedDict[object, list]" = OrderedDict()
    for p in pendientes_encargo:
        fecha = _fecha_encargo(p) or p.creado_en.date()
        encargos_por_fecha.setdefault(fecha, []).append(p)
    hoy_date = _utcnow().date()

    # ── Fase 6: partición "Preparar ahora" vs "Programados" ──────────
    # "Ahora" = inmediatos + encargos con fecha ≤ hoy + buffer(min).
    # "Programados" = encargos con fecha > hoy + buffer.
    buffer_min = minutos_anticipacion_pedido_programado()

    prep_ahora = list(pendientes_inmediato)
    prep_programados_planos: list = []
    for p in pendientes_encargo:
        if pedido_programado_disponible_para_preparar(p):
            prep_ahora.append(p)
        else:
            prep_programados_planos.append(p)

    # El rol de encargos abre en el resumen de producción. La vista de pedidos
    # individuales queda a un toque, pero no se mezclan ambos niveles en la
    # misma pantalla. Admin conserva su cola operativa habitual.
    vista_encargos = (request.args.get("vista") or "").strip().lower()
    if vista_encargos not in {"resumen", "pedidos"}:
        vista_encargos = "resumen" if current_user.rol == "preparacion" else "pedidos"

    # Totales agregados por fecha para el resumen de producción. Incluimos
    # también las fechas que ya están en preparación: antes desaparecían del
    # resumen en cuanto el operador iniciaba el pedido.
    items_encargo_activos = _items_encargo_activos()
    fechas_resumen = {
        item.display_fecha_entrega
        for item in items_encargo_activos
        if item.display_fecha_entrega is not None
        and (item.display_tipo_entrega or "").lower() in {"programado", "encargo"}
    }
    # Incluye TODOS los encargos programados (con y sin lote): los
    # batches muestran tandas, los productos sueltos muestran unidades.
    totales_lote_por_fecha = {}
    for _fecha in sorted(fechas_resumen):
        _agregado = _encargos_agregados_por_fecha(
            _fecha,
            items_activos=items_encargo_activos,
        )
        if _agregado:
            totales_lote_por_fecha[_fecha] = _agregado

    return render_template("preparador/pedidos.html",
                           pendientes=pendientes_inmediato,
                           pendientes_encargo=pendientes_encargo,
                           encargos_por_fecha=encargos_por_fecha,
                           totales_lote_por_fecha=totales_lote_por_fecha,
                           hoy_date=hoy_date,
                           armando=armando,
                           companeros=companeros,
                           disponible=disponible,
                           modo_operativo=modo_operativo,
                           vista_encargos=vista_encargos,
                           almacen_listo=almacen_listo,
                           lineas_preparacion_interna=lineas_preparacion_interna,
                           # Fase 6
                           prep_ahora=prep_ahora,
                           prep_programados=prep_programados_planos,
                           prep_buffer_min=buffer_min,
                           puede_preparar_encargo=_encargo_disponible_para_preparar,
                           sse_url=url_for("preparador.eventos"),
                           sse_heartbeat_s=_sse_heartbeat_s(),
                           tickets_recientes=tickets_recientes)


# ─────────────────────────────────────────────────────────────────────
# SSE — cambios en la cola del preparador
# El cliente escucha /preparador/eventos y recibe un `ping` heartbeat y
# `refresh` cuando cambia el conjunto de pedidos pendientes/armando.
# ─────────────────────────────────────────────────────────────────────
def _cola_signature():
    """Firma barata del estado observable de la cola.

    Combina COUNT + MAX(id) + MAX(creado_en) + suma de hashes de estado
    para detectar cambios sin cargar toda la lista.
    """
    row = db.session.execute(db.text("""
        SELECT COALESCE(COUNT(*),0),
               COALESCE(MAX(id),0),
               COALESCE(MAX(EXTRACT(EPOCH FROM creado_en))::bigint, 0),
               COALESCE(SUM(('x'||substr(md5(estado),1,8))::bit(32)::bigint), 0)
          FROM orders
         WHERE estado IN ('pendiente','armando')
    """)).first()
    if not row:
        return "0"
    return "|".join(str(v) for v in row)


@preparador_bp.route("/eventos")
@preparador_required
def eventos():
    """Server-Sent Events: notifica cambios en la cola del preparador.

    Contrato con el cliente:
      - `event: ping`  → keep-alive, ignorar
      - `event: refresh` → recargar la vista (el HTML manda)
    El cliente reconecta automáticamente (EventSource) al desconectar.
    """
    heartbeat = _sse_heartbeat_s()
    poll = _sse_poll_s()
    lifetime = _sse_max_lifetime_s()
    app = current_app._get_current_object()

    @stream_with_context
    def gen():
        # Firma inicial: dentro del app_context (stream_with_context lo garantiza).
        try:
            last_sig = _cola_signature()
        except Exception:
            logger.exception("SSE: no se pudo calcular firma inicial")
            last_sig = ""
        # Aviso inicial para que el cliente sepa que está enganchado.
        yield f"retry: 5000\nevent: hello\ndata: {_json.dumps({'heartbeat': heartbeat})}\n\n"
        started = time.monotonic()
        last_beat = started
        while True:
            now = time.monotonic()
            if now - started > lifetime:
                # Cerramos: el navegador reconectará solo.
                yield "event: bye\ndata: {}\n\n"
                return
            try:
                sig = _cola_signature()
            except Exception:
                logger.exception("SSE: error calculando firma; seguimos vivos")
                sig = last_sig
            if sig != last_sig:
                last_sig = sig
                yield f"event: refresh\ndata: {_json.dumps({'sig': sig})}\n\n"
                last_beat = now
            elif now - last_beat >= heartbeat:
                yield f"event: ping\ndata: {int(now - started)}\n\n"
                last_beat = now
            time.sleep(poll)

    resp = Response(gen(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache, no-transform"
    resp.headers["X-Accel-Buffering"] = "no"  # nginx: no bufferizar
    resp.headers["Connection"] = "keep-alive"
    return resp


@preparador_bp.route("/pedidos/<int:pedido_id>/tomar", methods=["POST"])
@preparador_required
def tomar_pedido(pedido_id):
    """El preparador toma manualmente un pedido sin asignar."""
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.estado != "pendiente":
        flash("Este pedido ya no está pendiente.", "warning")
        return redirect(url_for("preparador.pedidos"))
    if not _puede_operar_pedido(pedido):
        flash("Este pedido corresponde a otro equipo de preparación.", "danger")
        return redirect(url_for("preparador.pedidos"))
    if not pedido.preparador_id and not _requiere_disponible_para_nuevo_trabajo():
        return redirect(url_for("preparador.pedidos"))
    if pedido.preparador_id and pedido.preparador_id != current_user.id and not _es_admin_operativo():
        flash("Este pedido ya está asignado a otro preparador.", "warning")
        return redirect(url_for("preparador.pedidos"))
    pedido.preparador_id = current_user.id
    try:
        db.session.commit()
        flash(f"Pedido {pedido.numero_pedido} asignado a ti.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al asignar pedido: {exc}", "danger")
    return redirect(url_for("preparador.pedidos"))


@preparador_bp.route("/pedidos/<int:pedido_id>/empezar", methods=["POST"])
@preparador_required
def empezar_armar(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.estado != "pendiente":
        flash("Este pedido no está en estado pendiente.", "warning")
        return redirect(url_for("preparador.pedidos"))
    if not _puede_operar_pedido(pedido):
        flash("Este pedido corresponde a otro equipo de preparación.", "danger")
        return redirect(url_for("preparador.pedidos"))
    if _es_encargo(pedido) and not _encargo_disponible_para_preparar(pedido):
        flash(f"Este encargo está reservado para el {_fecha_encargo(pedido).strftime('%d/%m/%Y')}.", "warning")
        return redirect(url_for("preparador.pedidos"))
    if not pedido.preparador_id and not _requiere_disponible_para_nuevo_trabajo():
        return redirect(url_for("preparador.pedidos"))
    if not _es_admin_operativo() and pedido.preparador_id and pedido.preparador_id != current_user.id:
        flash("Este pedido ya está asignado a otro preparador.", "danger")
        return redirect(url_for("preparador.pedidos"))
    try:
        sincronizar_proveedores_pedido(pedido)
        avanzar_estado_pedido(pedido, actor_id=current_user.id, canal="preparador")
        if not pedido.preparador_id:
            pedido.preparador_id = current_user.id
        from services import enviar_whatsapp_estado
        enviar_whatsapp_estado(pedido)
        db.session.commit()
    except (ValueError, Exception) as e:
        db.session.rollback()
        flash(f"No se pudo iniciar el armado: {e}", "danger")
        return redirect(url_for("preparador.pedidos"))
    _notificar_proveedores_pendientes(pedido)
    try:
        from push_service import notify_order_state
        notify_order_state(pedido)
    except Exception:
        logger.exception("No se pudo enviar push al iniciar pedido %s", pedido.id)
    flash(f"Armando {pedido.numero_pedido}.", "info")
    return redirect(url_for("preparador.pedidos"))


@preparador_bp.route("/pedidos/<int:pedido_id>/listo", methods=["POST"])
@preparador_required
def marcar_listo(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.estado != "armando":
        flash("El pedido debe estar en 'armando'.", "warning")
        return redirect(url_for("preparador.pedidos"))
    if not _es_admin_operativo() and pedido.preparador_id != current_user.id:
        flash("Este pedido no está asignado a ti.", "danger")
        return redirect(url_for("preparador.pedidos"))
    try:
        avanzar_estado_pedido(
            pedido,
            actor_id=current_user.id,
            canal="preparador",
            validar_operativa=True,
        )
        repartidor = distribuir_repartidor(pedido)
        from services import enviar_whatsapp_estado
        enviar_whatsapp_estado(pedido)
        db.session.commit()
    except ValueError as e:
        # Errores de negocio con mensaje intencional (proveedor pendiente,
        # responsable no asignado, etc.) → se muestra al usuario tal cual.
        db.session.rollback()
        flash(f"No se pudo marcar como listo: {e}", "warning")
        return redirect(url_for("preparador.pedidos"))
    except Exception as e:
        # Excepción no anticipada → log completo + mensaje neutro al usuario
        # para no filtrar detalles técnicos ni stacktrace en la UI.
        db.session.rollback()
        logger.exception("Error inesperado al marcar listo pedido %s", pedido.id)
        flash(
            "No se pudo marcar como listo por un problema técnico. "
            "Inténtalo de nuevo en unos segundos o avisa a operación.",
            "danger",
        )
        return redirect(url_for("preparador.pedidos"))
    try:
        from push_service import notify_order_state, notify_roles
        notify_order_state(pedido)
        if pedido.requiere_reparto:
            notify_roles(["repartidor"], "📦 Pedido listo para recoger",
                         f"#{pedido.numero_pedido} está listo.", url="/repartidor/ruta")
    except Exception:
        logger.exception("No se pudo enviar push al marcar listo pedido %s", pedido.id)
    if not pedido.requiere_reparto:
        flash(f"Pedido {pedido.numero_pedido} listo para recogida en local.", "success")
    elif repartidor:
        flash(f"Pedido {pedido.numero_pedido} listo. Repartidor asignado automáticamente.", "success")
    else:
        flash(f"Pedido {pedido.numero_pedido} listo, pendiente de repartidor disponible.", "warning")
    return redirect(url_for("preparador.pedidos"))


# ─────────────────────────────────────────────────────────────────────
# Encargos por lote — vista agregada "TOTAL DEL DÍA"
# El preparador ve el total real de tandas a producir por
# (producto, fecha) sumando pedidos vivos (pendiente/armando/listo).
# Fuente única: `OrderItem.metadata_json.batch_id` congelado en checkout.
# ─────────────────────────────────────────────────────────────────────
def _lotes_agregados(fecha=None):
    """Devuelve lista de dicts con totales por batch en estados vivos.

    Estructura:
        [{batch_id, producto_id, producto_nombre, fecha_entrega,
          tandas_por_lote, tandas_totales, unidades_totales,
          estado_batch, listo_en, pedidos: [numero_pedido...]}]

    Solo cuenta ítems cuyo pedido esté en {pendiente, armando, listo}
    para no arrastrar cancelados/entregados.
    """
    q = ProductBatch.query
    if fecha is not None:
        q = q.filter(ProductBatch.fecha_entrega == fecha)
    batches = q.order_by(ProductBatch.fecha_entrega).all()
    if not batches:
        return []

    # Precarga de productos (evita N+1 en el bucle principal).
    prod_ids = [b.producto_id for b in batches]
    productos = {p.id: p for p in Product.query.filter(Product.id.in_(prod_ids)).all()}

    # UNA sola query: trae todos los OrderItems de pedidos vivos y agrupa
    # por batch_id en Python. Antes ejecutaba el SELECT por batch —
    # O(batches × order_items). Ahora es O(order_items).
    from sqlalchemy import bindparam
    _stmt = db.text("""
        SELECT oi.metadata_json, o.numero_pedido, o.estado
          FROM order_items oi
          JOIN orders o ON o.id = oi.pedido_id
         WHERE o.estado IN :estados
    """).bindparams(bindparam("estados", expanding=True))
    rows = db.session.execute(
        _stmt, {"estados": list(ESTADOS_ENCARGO_ACTIVOS)}
    ).fetchall()

    por_batch = {}
    for meta_json, num, estado in rows:
        if not meta_json:
            continue
        try:
            meta = _json.loads(meta_json)
        except Exception:
            continue
        bid = meta.get("batch_id")
        if not bid:
            continue
        t = int(meta.get("tandas_reservadas") or 0)
        if t <= 0:
            continue
        slot = por_batch.setdefault(bid, {
            "tandas": 0,
            "pedidos": set(),
            "tandas_por_estado": {key: 0 for key in ESTADOS_ENCARGO_ACTIVOS},
        })
        slot["tandas"] += t
        slot["pedidos"].add(num)
        slot["tandas_por_estado"][estado] += t

    resultado = []
    for b in batches:
        prod = productos.get(b.producto_id)
        if prod is None:
            continue
        slot = por_batch.get(b.id, {
            "tandas": 0,
            "pedidos": set(),
            "tandas_por_estado": {key: 0 for key in ESTADOS_ENCARGO_ACTIVOS},
        })
        tandas = slot["tandas"]
        pedidos = sorted(slot["pedidos"])
        if tandas == 0 and b.estado != "listo":
            continue
        resultado.append({
            "batch_id": b.id,
            "producto_id": prod.id,
            "producto_nombre": prod.nombre,
            "fecha_entrega": b.fecha_entrega.isoformat(),
            "tandas_por_lote": b.cantidad_por_tanda,
            "tandas_totales": tandas,
            "unidades_totales": tandas * b.cantidad_por_tanda,
            "unidades_por_estado": {
                estado: cantidad * b.cantidad_por_tanda
                for estado, cantidad in slot["tandas_por_estado"].items()
            },
            "pedidos_total": len(pedidos),
            "estado_batch": b.estado,
            "listo_en": b.listo_en.isoformat() if b.listo_en else None,
            "pedidos": pedidos,
        })
    return resultado


def _items_encargo_activos():
    """Carga una vez las líneas activas con su snapshot y pedido."""
    return OrderItem.query.options(
        joinedload(OrderItem.producto),
        joinedload(OrderItem.pedido),
    ).join(Order).filter(
        Order.estado.in_(ESTADOS_ENCARGO_ACTIVOS)
    ).all()


def _encargos_agregados_por_fecha(fecha, items_activos=None):
    """Agregado unificado de TODOS los encargos programados de una fecha.

    Fusiona dos fuentes:
        * `ProductBatch` (encargos por lote) — vía `_lotes_agregados`.
        * `OrderItem` sin batch (encargos programados normales) —
          agrupa por producto sumando cantidades de pedidos vivos.

    Cada entrada del resultado tiene la misma forma:
        {
          "producto_id", "producto_nombre",
          "es_lote": bool,                # True → tandas; False → unidades sueltas
          "batch_id": int|None,           # solo si es_lote
          "tandas_por_lote": int|None,    # solo si es_lote
          "tandas_totales": int|None,     # solo si es_lote
          "unidades_totales": int,        # total a producir (unidades reales)
          "estado_batch": str|None,       # solo si es_lote
          "pedidos": [numero_pedido, ...],
        }

    Así el preparador ve una única tabla "TOTAL DEL DÍA" con TODOS los
    productos programados de la fecha, tengan o no `cantidad_por_lote`.
    """
    resultado = list(_lotes_agregados(fecha=fecha))
    ids_ya_agregados = {r["batch_id"] for r in resultado if r.get("batch_id")}
    productos_batch_ids = {
        b.producto_id for b in ProductBatch.query.filter_by(fecha_entrega=fecha).all()
    } if fecha else set()

    # Recolectar encargos sin batch desde el snapshot del OrderItem. La fecha,
    # el nombre y el tipo del producto se congelan al confirmar la compra; usar
    # aquí el Product vivo movía pedidos históricos si el catálogo se editaba.
    rows = items_activos if items_activos is not None else _items_encargo_activos()

    por_producto = {}
    for item in rows:
        if item.display_fecha_entrega != fecha:
            continue
        if (item.display_tipo_entrega or "").lower() not in {"programado", "encargo"}:
            continue
        # Si el ítem tiene batch, ya está sumado en `_lotes_agregados`.
        batch_id = None
        meta = item.get_metadata()
        batch_id = meta.get("batch_id")
        if batch_id and batch_id in ids_ya_agregados:
            continue
        # Si el producto tiene batch en esta fecha (aunque este ítem no lo
        # apunte por algún checkout antiguo), evita doble contabilidad.
        if item.producto_id in productos_batch_ids and not batch_id:
            continue
        entry = por_producto.setdefault(item.producto_id, {
            "producto_id": item.producto_id,
            "producto_nombre": item.display_nombre,
            "es_lote": False,
            "batch_id": None,
            "tandas_por_lote": None,
            "tandas_totales": None,
            "unidades_totales": 0,
            "unidades_por_estado": {key: 0 for key in ESTADOS_ENCARGO_ACTIVOS},
            "estado_batch": None,
            "pedidos": set(),
        })
        cantidad = int(item.cantidad or 0)
        entry["unidades_totales"] += cantidad
        entry["unidades_por_estado"][item.pedido.estado] += cantidad
        entry["pedidos"].add(item.pedido.numero_pedido)

    for entry in por_producto.values():
        entry["pedidos"] = sorted(entry["pedidos"])
        entry["pedidos_total"] = len(entry["pedidos"])

    # Etiquetar los batches como "es_lote" para el template.
    for r in resultado:
        r["es_lote"] = True
    resultado.extend(por_producto.values())
    resultado.sort(key=lambda r: (r["producto_nombre"] or "").lower())
    return resultado


@preparador_bp.route("/encargos/agregado")
@preparador_required
def encargos_agregado():
    """JSON: agregado de tandas por batch, opcionalmente filtrado por fecha.

    Query params:
        fecha=YYYY-MM-DD (opcional) — filtra un único día.
    """
    from datetime import date as _date
    fecha_raw = (request.args.get("fecha") or "").strip()
    fecha = None
    if fecha_raw:
        try:
            fecha = _date.fromisoformat(fecha_raw)
        except ValueError:
            return jsonify(ok=False, error="fecha inválida"), 400
    return jsonify(ok=True, lotes=_lotes_agregados(fecha))


@preparador_bp.route("/encargos/<int:batch_id>/listo", methods=["POST"])
@preparador_required
def marcar_lote_listo(batch_id):
    """Marca un batch entero como listo y notifica clientes por push.

    NO envía WhatsApp masivo (política anti-baneo). Los clientes que
    tengan push web habilitado reciben una notificación individual.
    """
    if current_user.rol not in {"preparacion", "admin", "super_admin"}:
        flash("Solo el equipo de encargos puede cerrar un lote programado.", "warning")
        return redirect(url_for("preparador.pedidos"))
    if not _requiere_disponible_para_nuevo_trabajo():
        return redirect(url_for("preparador.pedidos", vista="resumen"))

    batch = ProductBatch.query.filter_by(id=batch_id).with_for_update().first_or_404()
    if batch.estado == "listo":
        flash("El lote ya está marcado como listo.", "info")
        return redirect(url_for("preparador.pedidos"))
    batch.estado = "listo"
    batch.listo_en = _utcnow()
    db.session.commit()

    # Recolecta pedidos vivos del batch para notificar (push web, no WA).
    try:
        from push_service import notify_order_state
        rows = db.session.execute(db.text("""
            SELECT DISTINCT o.id
              FROM order_items oi
              JOIN orders o ON o.id = oi.pedido_id
             WHERE o.estado IN ('pendiente','armando','listo')
        """)).fetchall()
        for (oid,) in rows:
            pedido = Order.query.get(oid)
            if pedido is None:
                continue
            afectado = False
            for it in pedido.items:
                try:
                    meta = _json.loads(it.metadata_json or "{}")
                except Exception:
                    meta = {}
                if meta.get("batch_id") == batch.id:
                    afectado = True
                    break
            if afectado:
                try:
                    notify_order_state(pedido)
                except Exception:
                    logger.exception("push notify_order_state fallo pedido=%s", pedido.id)
    except Exception:
        logger.exception("Fallo notificando lote listo batch=%s", batch.id)

    flash(f"Lote del {batch.fecha_entrega.strftime('%d/%m')} marcado como listo.", "success")
    return redirect(url_for("preparador.pedidos"))
