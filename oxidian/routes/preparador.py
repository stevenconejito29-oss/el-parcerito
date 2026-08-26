from flask import (Blueprint, render_template, redirect, url_for, flash,
                   jsonify, request, session, g)
from flask_login import login_required, current_user
from functools import wraps
import logging
import hashlib
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
                      agrupar_items_por_producto,
                      pedido_programado_disponible_para_preparar,
                      minutos_anticipacion_pedido_programado)


# ─────────────────────────────────────────────────────────────────────
# Umbrales de la vista de cocina (Fase 6).
# Fuentes en cascada: SiteConfig → env → default.
# Cambiar en /superadmin/config sin redeploy.
# ─────────────────────────────────────────────────────────────────────
_DEFAULT_QUEUE_REFRESH_S = 6

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


def _queue_refresh_s():
    """Cadencia de sincronización sin reservar un thread de Gunicorn."""
    return _cfg_int(
        "PREP_QUEUE_REFRESH_SECONDS",
        _DEFAULT_QUEUE_REFRESH_S,
        3,
        60,
    )


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


def preparador_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.rol not in ROLES_PREPARADOR:
            flash("Acceso restringido.", "danger")
            return redirect(url_for("public.index"))
        return f(*args, **kwargs)
    return decorated


def _thermal_config_key():
    return f"THERMAL_PRINTER_U_{current_user.id}"


@preparador_bp.route("/impresora", methods=["GET", "PUT", "DELETE"])
@preparador_required
def impresora_preferencia():
    """Persiste el vínculo lógico de la impresora por operador.

    La autorización Bluetooth continúa en el navegador por seguridad. El
    servidor solo conserva identificador opaco, nombre y transporte para que
    ``getDevices()`` seleccione el periférico correcto tras una recarga.
    """
    key = _thermal_config_key()
    if request.method == "GET":
        raw = SiteConfig.get(key, "") or ""
        try:
            value = _json.loads(raw) if raw else None
        except (TypeError, ValueError):
            value = None
        return jsonify({"ok": True, "printer": value})
    if request.method == "DELETE":
        entry = SiteConfig.query.filter_by(clave=key).first()
        if entry:
            db.session.delete(entry)
            db.session.commit()
        g.__dict__.get("_siteconfig_cache", {}).pop(key, None)
        return jsonify({"ok": True})

    payload = request.get_json(silent=True) or {}
    transport = str(payload.get("transport") or "").strip().lower()
    device_id = str(payload.get("device_id") or "").strip()[:180]
    name = str(payload.get("name") or "Impresora térmica").strip()[:80]
    if transport not in {"bt", "usb"} or (transport == "bt" and not device_id):
        return jsonify({"ok": False, "error": "impresora_invalida"}), 400
    value = {"transport": transport, "device_id": device_id, "name": name}
    SiteConfig.set(key, _json.dumps(value, ensure_ascii=False), current_user.id,
                   "Impresora térmica preferida del puesto")
    db.session.commit()
    return jsonify({"ok": True, "printer": value})

ESTADOS_ENCARGO_ACTIVOS = ("pendiente", "armando", "listo")


@preparador_bp.before_request
def exigir_modulo_del_rol():
    from store_config import get_store_features

    if (
        current_user.is_authenticated
        and current_user.rol == "preparacion"
        and not get_store_features()["pedidos_programados"]
    ):
        from services import pedidos_activos_que_bloquean_modulo
        if pedidos_activos_que_bloquean_modulo("programados") == 0:
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
    # Un pedido pendiente de validar puede consultarse desde administración,
    # pero todavía no es trabajo de cocina. Ocultarlo de la cola evita que se
    # asigne manualmente o contamine los totales antes de confirmar el teléfono.
    if pedido.confirmacion_estado == "pending":
        return False
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


@preparador_bp.route("/vista-compacta", methods=["POST"])
@preparador_required
def toggle_vista_compacta():
    """Alterna la densidad de la cola sin modificar pedidos ni preferencias globales."""
    session["prep_vista_compacta"] = not bool(
        session.get("prep_vista_compacta", True)
    )
    session.modified = True
    vista = (request.form.get("vista") or "").strip().lower()
    redirect_args = {"vista": vista} if vista in {"resumen", "pedidos"} else {}
    return redirect(url_for("preparador.pedidos", **redirect_args))


@preparador_bp.route("/kds")
@preparador_required
def kds():
    """Vista ligera para tablet de cocina.

    Sin imágenes, sin menús, solo la cola operable del usuario.
    Auto-refresh y alerta sonora en el propio HTML.
    """
    return render_template(
        "preparador/kds.html",
        refresh_seconds=_queue_refresh_s(),
    )


def _kds_cola_del_usuario():
    """Pedidos pendientes+armando visibles para el usuario, filtrados por rol."""
    # ``Order.items`` es una relación dynamic y no admite joinedload; el slot
    # sí se precarga porque KDS lo usa para agrupar cada salida.
    _eager = (joinedload(Order.slot),)
    if _es_admin_operativo():
        base = Order.query.options(*_eager).filter(
            Order.estado.in_(("pendiente", "armando"))
        )
    else:
        base = Order.query.options(*_eager).filter(
            Order.estado.in_(("pendiente", "armando")),
            db.or_(
                Order.preparador_id == current_user.id,
                Order.preparador_id.is_(None),
            ),
        )
    pedidos = base.order_by(Order.creado_en.asc()).all()
    return [p for p in pedidos if _puede_operar_pedido(p)]


@preparador_bp.route("/kds/data")
@preparador_required
def kds_data():
    """JSON minimal para la vista KDS. Refresca cada N segundos."""
    from flask import make_response
    pedidos = _kds_cola_del_usuario()
    payload = []
    for p in pedidos:
        items = []
        for it in p.items:
            items.append({
                "cantidad": int(it.cantidad or 0),
                "nombre": it.display_nombre or "",
                "variante": (it.selected_presentation_label or "").strip(),
                "sabores": list(it.selected_flavor_names or []),
            })
        payload.append({
            "id": p.id,
            "numero": p.numero_pedido,
            "estado": p.estado,
            "creado": p.creado_en.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "creado_hm": p.creado_en.strftime("%H:%M"),
            "notas": (p.notas or "").strip(),
            "tipo": (p.tipo_entrega_cliente or "recogida"),
            "mio": bool(p.preparador_id == current_user.id),
            "sin_asignar": p.preparador_id is None,
            "slot": ({
                "id": p.slot.id,
                "fecha": p.slot.fecha.isoformat(),
                "hora_inicio": p.slot.hora_inicio.strftime("%H:%M"),
                "hora_fin": p.slot.hora_fin.strftime("%H:%M"),
            } if p.slot else None),
            "items": items,
        })
    resp = jsonify({
        "ok": True,
        "signature": _cola_signature(),
        "refresh_seconds": _queue_refresh_s(),
        "pedidos": payload,
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp


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
    _eager = (
        joinedload(Order.zona), joinedload(Order.slot), joinedload(Order.cliente),
    )
    if _es_admin_operativo():
        pendientes = Order.query.options(*_eager).filter_by(estado="pendiente").order_by(Order.creado_en).all()
        armando = Order.query.options(*_eager).filter_by(estado="armando").order_by(Order.creado_en).all()
    else:
        pendientes = Order.query.options(*_eager).filter(
            Order.estado == "pendiente",
            db.or_(
                Order.preparador_id == current_user.id,
                Order.preparador_id.is_(None),
            ),
        ).order_by(Order.creado_en).all()
        armando = Order.query.options(*_eager).filter_by(
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
    # Cocina recibe primero lo inmediato y luego las franjas cronológicamente;
    # dentro de cada franja conserva FIFO. El chip del ticket comunica la salida.
    pendientes_inmediato.sort(key=lambda p: (
        0 if p.slot_id is None else 1,
        p.slot.fecha if p.slot else p.creado_en.date(),
        p.slot.hora_inicio if p.slot else p.creado_en.time(),
        p.creado_en,
    ))

    # Agrupar los encargos por fecha de entrega para que preparación vea la
    # planificación del día: cuántos pedidos para hoy, mañana, próximos
    # días. La compatibilidad del carrito/API garantiza una única fecha por
    # pedido y el modelo la obtiene siempre desde el snapshot histórico.
    from collections import OrderedDict
    encargos_por_fecha: "OrderedDict[object, list]" = OrderedDict()
    for p in pendientes_encargo:
        fecha = _fecha_encargo(p) or p.creado_en.date()
        encargos_por_fecha.setdefault(fecha, []).append(p)
    from business_time import business_today
    hoy_date = business_today()

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

    # Fuente común cocina/rider/admin. La franja permanece visible cuando se
    # empaca el último pedido para que cocina vea el cierre y rider pueda
    # recogerla; antes desaparecía precisamente al completarse.
    from models import DeliverySlot
    from delivery_slots_service import (asegurar_horizonte_recurrente,
                                        resumen_preparacion_franjas)
    if asegurar_horizonte_recurrente(hoy_date, hoy_date + timedelta(days=6)):
        db.session.commit()
    slots_operativos = (
        DeliverySlot.query
        .filter(
            DeliverySlot.fecha >= hoy_date,
            DeliverySlot.fecha <= hoy_date + timedelta(days=6),
            DeliverySlot.activo.is_(True),
        )
        .order_by(DeliverySlot.fecha, DeliverySlot.hora_inicio)
        .all()
    )
    resumen_slots = resumen_preparacion_franjas(slot.id for slot in slots_operativos)
    franjas_cocina = [
        {"slot": slot, **resumen_slots[slot.id]}
        for slot in slots_operativos
        if resumen_slots[slot.id]["total"] > 0
        and resumen_slots[slot.id]["entregados"] < resumen_slots[slot.id]["total"]
    ]

    # El rol de encargos abre en el resumen de producción. La vista de pedidos
    # individuales queda a un toque, pero no se mezclan ambos niveles en la
    # misma pantalla. Admin conserva su cola operativa habitual.
    vista_encargos = (request.args.get("vista") or "").strip().lower()
    if vista_encargos not in {"resumen", "pedidos"}:
        # Preparación suele abrir en el resumen de encargos. Si el balanceador
        # le asignó excepcionalmente un pedido inmediato por falta de cocina,
        # abrir ese tablero directamente evita esconder trabajo ya asignado.
        tiene_inmediato_asignado = any(
            pedido.preparador_id == current_user.id
            for pedido in pendientes_inmediato
        ) or any(
            pedido.preparador_id == current_user.id and not _es_encargo(pedido)
            for pedido in armando
        )
        vista_encargos = (
            "pedidos"
            if current_user.rol != "preparacion" or tiene_inmediato_asignado
            else "resumen"
        )
    prep_vista_compacta = bool(session.get("prep_vista_compacta", True))

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
                           prep_vista_compacta=prep_vista_compacta,
                           almacen_listo=almacen_listo,
                           lineas_preparacion_interna=lineas_preparacion_interna,
                           agrupar_items_por_producto=agrupar_items_por_producto,
                           # Fase 6
                           prep_ahora=prep_ahora,
                           prep_programados=prep_programados_planos,
                           prep_buffer_min=buffer_min,
                           franjas_cocina=franjas_cocina,
                           puede_preparar_encargo=_encargo_disponible_para_preparar,
                           queue_status_url=url_for("preparador.eventos"),
                           queue_refresh_s=_queue_refresh_s(),
                           tickets_recientes=tickets_recientes)


# ─────────────────────────────────────────────────────────────────────
# Sincronización de cola sin conexiones persistentes.
#
# Cada SSE ocupaba un thread durante minutos. Con 2 workers × 2 threads,
# cuatro tablets podían agotar la concurrencia de toda la aplicación.
# ─────────────────────────────────────────────────────────────────────
def _cola_signature():
    """Firma portable del estado y responsable de cada pedido activo."""
    rows = (
        db.session.query(
            Order.id,
            Order.estado,
            Order.preparador_id,
            Order.confirmacion_estado,
        )
        .filter(Order.estado.in_(ESTADOS_ENCARGO_ACTIVOS))
        .order_by(Order.id)
        .all()
    )
    payload = "|".join(
        f"{order_id}:{estado}:{preparador_id or 0}:{confirmacion or '-'}"
        for order_id, estado, preparador_id, confirmacion in rows
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


@preparador_bp.route("/eventos")
@preparador_required
def eventos():
    """Devuelve una versión corta; el cliente consulta sólo cuando está visible."""
    resp = jsonify({
        "ok": True,
        "signature": _cola_signature(),
        "refresh_seconds": _queue_refresh_s(),
    })
    resp.headers["Cache-Control"] = "no-store"
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
    # Sin auto-print aquí: la impresión sale sólo al marcar Listo /
    # enviar a repartidor (`preparador.marcar_listo`), nunca al iniciar
    # el armado. Evita tickets prematuros que se descartan si el pedido
    # cambia o se cancela mientras se prepara.
    flash(f"Armando {pedido.numero_pedido}.", "info")
    return redirect(url_for("preparador.pedidos"))


@preparador_bp.route("/pedidos/<int:pedido_id>/listo", methods=["POST"])
@preparador_required
def marcar_listo(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    # Refresh explícito post-lock. Sin él, SQLAlchemy podía servir el objeto
    # desde la identity-map con `estado` cacheado de otro request y aprobar
    # una transición fantasma cuando otro preparador ya había marcado listo
    # entre nuestras dos primeras queries. Refresh trae la row REAL bajo el
    # lock que acabamos de tomar.
    db.session.refresh(pedido)
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
        # Auto-print vive en `empezar_armar` (transición pendiente→armando)
        # para que el preparador tenga el ticket DURANTE el armado. Aquí
        # (armando→listo) ya no imprimimos para evitar duplicados. Si el
        # ticket se perdió, el POS tiene botón "Reimprimir".
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
        from push_service import notify_delivery_ready, notify_order_state
        notify_order_state(pedido)
        notify_delivery_ready(pedido)
    except Exception:
        logger.exception("No se pudo enviar push al marcar listo pedido %s", pedido.id)
    if not pedido.requiere_reparto:
        flash(f"Pedido {pedido.numero_pedido} listo para recogida en local.", "success")
    elif repartidor:
        flash(f"Pedido {pedido.numero_pedido} listo. Repartidor asignado automáticamente.", "success")
    else:
        flash(f"Pedido {pedido.numero_pedido} listo, pendiente de repartidor disponible.", "warning")
    # Fallback manual: abre el diálogo nativo de impresión del navegador
    # al volver a la lista. Complementa el auto-print server-side (CUPS)
    # cuando la impresora está en un dispositivo distinto (BT en tablet,
    # OTG, etc.) o cuando CUPS falló.
    return redirect(url_for("preparador.pedidos", print_after=pedido.id))


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
           AND (o.confirmacion_estado IS NULL OR o.confirmacion_estado <> 'pending')
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
        Order.estado.in_(ESTADOS_ENCARGO_ACTIVOS),
        db.or_(
            Order.confirmacion_estado.is_(None),
            Order.confirmacion_estado != "pending",
        ),
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
            "variaciones": {},
        })
        cantidad = int(item.cantidad or 0)
        entry["unidades_totales"] += cantidad
        entry["unidades_por_estado"][item.pedido.estado] += cantidad
        entry["pedidos"].add(item.pedido.numero_pedido)
        partes = []
        if item.selected_presentation_label:
            partes.append(item.selected_presentation_label)
        if item.selected_flavor_names:
            partes.append(", ".join(item.selected_flavor_names))
        etiqueta_variacion = " · ".join(partes) if partes else "Sin variante"
        entry["variaciones"][etiqueta_variacion] = (
            entry["variaciones"].get(etiqueta_variacion, 0) + cantidad
        )

    for entry in por_producto.values():
        entry["pedidos"] = sorted(entry["pedidos"])
        entry["pedidos_total"] = len(entry["pedidos"])
        entry["variaciones"] = [
            {"nombre": nombre, "unidades": unidades}
            for nombre, unidades in sorted(entry["variaciones"].items())
        ]

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
