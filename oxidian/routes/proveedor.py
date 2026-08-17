import json
import logging
from decimal import Decimal, InvalidOperation
from flask import Blueprint, abort, current_app, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from functools import wraps
from extensions import db, get_or_404
from models import (AuditLog, Categoria, ComboGroup, ComboItem,
                    ESTADOS_EN_PREPARACION, Order, OrderEvent,
                    OrderItem, OrderProviderStatus, Product, Proveedor, ProveedorProducto,
                    SiteConfig, User, utcnow)
from image_service import delete_image, save_image
from combo_validators import ComboLimits
from services import (lineas_proveedor_pedido, registrar_evento_pedido,
                       es_pedido_solo_bar, distribuir_repartidor,
                       marcar_pedido_preparado,
                       cancelar_pedido_operativo,
                       calcular_liquidaciones_proveedores,
                       lineas_socio_pedido,
                       notificar_bot_sync)

proveedor_bp = Blueprint("proveedor", __name__)
logger = logging.getLogger(__name__)

ROLES_PROVEEDOR = {"admin", "super_admin", "socio_producto", "proveedor"}


def proveedor_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.rol not in ROLES_PROVEEDOR:
            flash("Acceso restringido.", "danger")
            return redirect(url_for("public.index"))
        if current_user.rol in {"socio_producto", "proveedor"} and not current_user.proveedor_id:
            flash("Tu cuenta no está enlazada a ningún socio de productos.", "warning")
            return redirect(url_for("public.index"))
        return f(*args, **kwargs)
    return decorated


def _proveedor_id_efectivo():
    """Devuelve el proveedor (entidad) al que pertenece el usuario actual.

    Para admin/super_admin sin socio enlazado retorna None (vista global). Para
    un socio retorna exclusivamente su entidad, evitando cruces de pedidos."""
    if current_user.rol in ("admin", "super_admin") and not current_user.proveedor_id:
        return None
    return current_user.proveedor_id


def _decimal_no_negativo(valor, campo, *, opcional=False):
    raw = "" if valor is None else str(valor).strip()
    if not raw:
        if opcional:
            return None
        raise ValueError(f"{campo} es obligatorio.")
    # Locale-tolerant: iOS Safari con teclado español devuelve "9,99" para
    # <input type="number">. Sin normalización el navegador tampoco muestra
    # error visible y el POST parece "no hacer nada". Si hay una única coma
    # y ningún punto, tratamos la coma como decimal.
    normalizado = raw.replace(",", ".") if ("," in raw and "." not in raw) else raw
    try:
        numero = Decimal(normalizado)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{campo} debe ser un número válido.")
    if not numero.is_finite() or numero < 0:
        raise ValueError(f"{campo} debe ser mayor o igual que 0.")
    return numero


def _entero_no_negativo(valor, campo):
    raw = "" if valor is None else str(valor).strip()
    if not raw:
        raise ValueError(f"{campo} es obligatorio.")
    try:
        numero = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{campo} debe ser un número entero válido.")
    if str(numero) != raw and raw not in {f"+{numero}", f"-{abs(numero)}"}:
        raise ValueError(f"{campo} debe ser un número entero válido.")
    if numero < 0:
        raise ValueError(f"{campo} debe ser mayor o igual que 0.")
    return numero


def _socio_actual():
    """Resuelve exclusivamente el socio del operador autenticado.

    Las vistas globales de admin pueden reutilizar el módulo operativo, pero
    nunca deben poder dar de alta un producto sin propietario por esta ruta.
    """
    if current_user.rol not in {"socio_producto", "proveedor"}:
        return None
    proveedor = db.session.get(Proveedor, current_user.proveedor_id)
    if (
        not proveedor
        or not proveedor.activo
        or proveedor.modelo_acuerdo != "socio_porcentaje"
    ):
        return None
    return proveedor


def socio_capital_required(f):
    """Decorator que garantiza que el operador es un socio-capital activo.

    Cualquier endpoint que dependa del contrato ``socio_porcentaje`` (dar de alta
    productos, combos, editar inventario propio) debe declararlo. Al fallar
    devuelve 403 y registra el intento en AuditLog para dejar huella del bloqueo.
    Inyecta el objeto ``Proveedor`` resuelto como primer argumento posicional
    del handler (``proveedor`` kwarg no colisiona con parámetros de ruta).
    """

    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        proveedor = _socio_actual()
        if proveedor is None:
            try:
                AuditLog.registrar(
                    current_user.id if current_user.is_authenticated else None,
                    "socio_capital_denegado",
                    "proveedor",
                    getattr(current_user, "proveedor_id", None),
                    detalle=f"rol={getattr(current_user, 'rol', None)} ruta={request.path}",
                    ip=request.remote_addr,
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
                logger.exception("No se pudo auditar denegación socio_capital")
            abort(403)
        kwargs.setdefault("_proveedor", proveedor)
        return f(*args, **kwargs)

    return decorated


def _assert_owned_by(proveedor, producto):
    """Rechaza cualquier cross-socio: si el producto/combo no pertenece al
    proveedor autenticado, abortamos con 404 (no revelamos existencia)."""
    if producto is None or producto.proveedor_despachador_id != proveedor.id:
        abort(404)


def _parse_time_form(val):
    """Parsea 'HH:MM' del formulario a `datetime.time` o None si está vacío."""
    from datetime import datetime as _dt
    if not val or not val.strip():
        return None
    try:
        return _dt.strptime(val.strip(), "%H:%M").time()
    except ValueError:
        return None


def _campos_producto_socio(form, *, incluir_stock=True):
    """Valida la ficha comercial que un socio está autorizado a proponer."""
    nombre = (form.get("nombre") or "").strip()
    descripcion = (form.get("descripcion") or "").strip()
    origen_pais = (form.get("origen_pais") or "").strip()
    if len(nombre) < 3 or len(nombre) > 150:
        raise ValueError("El nombre debe tener entre 3 y 150 caracteres.")
    if len(descripcion) > 2000:
        raise ValueError("La descripción no puede superar 2.000 caracteres.")
    if len(origen_pais) > 50:
        raise ValueError("El país de origen no puede superar 50 caracteres.")
    precio = _decimal_no_negativo(form.get("precio"), "El precio de venta")
    if precio <= 0:
        raise ValueError("El precio de venta debe ser mayor que 0.")
    stock = (
        _entero_no_negativo(form.get("stock"), "El stock")
        if incluir_stock else 0
    )
    categoria_id = form.get("categoria_id", type=int)
    categoria = (
        Categoria.query.filter_by(id=categoria_id, activo=True).first()
        if categoria_id else None
    )
    if not categoria:
        raise ValueError("Selecciona una categoría activa.")
    modalidad = (form.get("modalidad_entrega") or "ambas").strip().lower()
    if modalidad not in {"ambas", "delivery", "recogida"}:
        raise ValueError("La modalidad de entrega no es válida.")

    hora_inicio = _parse_time_form(form.get("hora_inicio_visibilidad"))
    hora_fin = _parse_time_form(form.get("hora_fin_visibilidad"))
    if bool(hora_inicio) != bool(hora_fin):
        raise ValueError("Indica la franja de visibilidad completa o deja ambas vacías.")

    dias = []
    for raw in form.getlist("dias_semana"):
        try:
            dia = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= dia <= 6 and dia not in dias:
            dias.append(dia)
    dias_json = json.dumps(dias) if dias else None

    vertical = (SiteConfig.get("TIPO_TIENDA", "comida") or "comida").strip().lower()
    if vertical not in {"comida", "producto"}:
        vertical = "comida"
    return {
        "nombre": nombre,
        "descripcion": descripcion or None,
        "origen_pais": origen_pais or None,
        "precio": precio,
        "categoria_id": categoria.id,
        "modalidad_entrega": modalidad,
        "vertical": vertical,
        "stock": stock,
        "hora_inicio_visibilidad": hora_inicio,
        "hora_fin_visibilidad": hora_fin,
        "dias_semana_json": dias_json,
        "stock_mostrar_en_web": True,
    }


def _notificar_preparador(pedido, titulo, mensaje):
    if not pedido.preparador_id:
        return
    try:
        from push_service import notify_user
        notify_user(
            pedido.preparador_id,
            titulo,
            mensaje,
            url="/preparador/pedidos",
        )
    except Exception:
        logger.exception("No se pudo notificar al preparador del pedido %s", pedido.id)


@proveedor_bp.route("/")
@socio_capital_required
def dashboard(_proveedor=None):
    """Landing del socio con contadores de propuestas, ventas del mes y saldo.

    Solo aplica al rol ``socio_producto``. Bares clásicos (``proveedor``)
    caen directamente en ``/pedidos`` desde el login (ver ``auth.py``).
    """
    from business_time import business_today

    proveedor = _proveedor
    propuestas = (
        db.session.query(
            Product.partner_submission_status,
            Product.es_combo,
            db.func.count(Product.id),
        )
        .filter(Product.proveedor_despachador_id == proveedor.id)
        .group_by(Product.partner_submission_status, Product.es_combo)
        .all()
    )
    conteo = {"pending": 0, "approved": 0, "rejected": 0, "combos_pending": 0}
    for estado, es_combo, n in propuestas:
        clave = estado or "approved"
        if clave in conteo:
            conteo[clave] += int(n)
        if es_combo and clave == "pending":
            conteo["combos_pending"] += int(n)

    hoy = business_today()
    inicio_mes = hoy.replace(day=1)
    cierre = calcular_liquidaciones_proveedores(
        inicio_mes, hoy, proveedor_id=proveedor.id,
    )
    bucket = cierre["por_proveedor"].get(proveedor.id) or {}
    ventas_mes = bucket.get("ventas_netas") or Decimal("0")
    saldo_pendiente = bucket.get("pendiente_pago") or Decimal("0")
    saldo_ya_pagado = bucket.get("pagado") or Decimal("0")

    unidades_activas = 0
    if proveedor.es_socio_capital:
        candidatos = (
            Order.query
            .filter(
                Order.estado.in_(["pendiente", "armando", "listo", "en_ruta"]),
                db.or_(
                    Order.confirmacion_estado.is_(None),
                    Order.confirmacion_estado != "pending",
                ),
            )
            .all()
        )
        for pedido in candidatos:
            for linea in lineas_socio_pedido(pedido, proveedor.id):
                unidades_activas += int(linea.get("cantidad") or 0)

    return render_template(
        "proveedor/dashboard.html",
        proveedor=proveedor,
        conteo=conteo,
        ventas_mes=ventas_mes,
        saldo_pendiente=saldo_pendiente,
        saldo_pagado=saldo_ya_pagado,
        unidades_activas=unidades_activas,
        inicio_mes=inicio_mes,
        hoy=hoy,
    )


@proveedor_bp.route("/pedidos")
@proveedor_required
def pedidos():
    prov_id = _proveedor_id_efectivo()
    socio = db.session.get(Proveedor, prov_id) if prov_id else None

    # El socio de productos no prepara pedidos. Su vista es de seguimiento de
    # mercancía, sin dirección, teléfono ni otros datos personales del cliente.
    if socio and socio.es_socio_capital:
        candidatos = (
            Order.query
            .filter(
                Order.estado.in_(["pendiente", "armando", "listo", "en_ruta"]),
                db.or_(
                    Order.confirmacion_estado.is_(None),
                    Order.confirmacion_estado != "pending",
                ),
            )
            .order_by(Order.creado_en.asc())
            .all()
        )
        items_por_pedido = {}
        if candidatos:
            for item in OrderItem.query.filter(
                OrderItem.pedido_id.in_([pedido.id for pedido in candidatos])
            ).all():
                items_por_pedido.setdefault(item.pedido_id, []).append(item)
        ventas_activas = []
        for pedido in candidatos:
            lineas = lineas_socio_pedido(
                pedido,
                socio.id,
                items=items_por_pedido.get(pedido.id, []),
            )
            if lineas:
                ventas_activas.append({"pedido": pedido, "lineas": lineas})
        return render_template(
            "proveedor/ventas_activas.html",
            socio=socio,
            ventas_activas=ventas_activas,
            unidades_activas=sum(
                linea["cantidad"]
                for venta in ventas_activas
                for linea in venta["lineas"]
            ),
        )

    if prov_id:
        base = Order.query.join(OrderProviderStatus).filter(
            Order.estado.in_(ESTADOS_EN_PREPARACION),
            db.or_(
                Order.confirmacion_estado.is_(None),
                Order.confirmacion_estado != "pending",
            ),
            OrderProviderStatus.proveedor_id == prov_id,
        )
    else:
        base = Order.query.join(OrderProviderStatus).filter(
            Order.estado.in_(ESTADOS_EN_PREPARACION),
            db.or_(
                Order.confirmacion_estado.is_(None),
                Order.confirmacion_estado != "pending",
            ),
        ).distinct()

    pendientes = base.filter(Order.estado == "pendiente").order_by(Order.creado_en).all()
    armando    = base.filter(Order.estado == "armando").order_by(Order.creado_en).all()

    return render_template(
        "proveedor/pedidos.html",
        pendientes=pendientes,
        armando=armando,
        prov_id=prov_id,
        lineas_proveedor_pedido=lineas_proveedor_pedido,
        es_pedido_solo_bar=es_pedido_solo_bar,
        proveedores=(
            Proveedor.query.filter_by(activo=True).order_by(Proveedor.nombre).all()
            if not prov_id else []
        ),
    )


@proveedor_bp.route("/pedidos/<int:pedido_id>/preparado", methods=["POST"])
@proveedor_required
def marcar_preparado(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.confirmacion_estado == "pending":
        flash(
            "El cliente aún no confirmó su primer pedido por WhatsApp. No se modificó la preparación.",
            "warning",
        )
        return redirect(url_for("proveedor.pedidos"))
    if pedido.estado not in ESTADOS_EN_PREPARACION:
        flash("Este pedido ya no está activo.", "warning")
        return redirect(url_for("proveedor.pedidos"))
    prov_id = _proveedor_id_efectivo()
    if not prov_id and current_user.rol in ("admin", "super_admin"):
        prov_id = request.form.get("proveedor_id", type=int)

    estados_q = OrderProviderStatus.query.filter_by(pedido_id=pedido.id)
    estados_totales = estados_q.count()
    if not prov_id and estados_totales > 1:
        flash("Selecciona el proveedor concreto que confirmó su preparación.", "warning")
        return redirect(url_for("proveedor.pedidos"))
    if prov_id:
        estados_q = estados_q.filter_by(proveedor_id=prov_id)
    estados = estados_q.with_for_update().all()
    if not estados:
        if _proveedor_id_efectivo():
            flash("Este pedido no contiene tus productos.", "danger")
        else:
            flash("Este pedido no tiene proveedores pendientes registrados.", "warning")
        return redirect(url_for("proveedor.pedidos"))

    if all(estado.preparado for estado in estados):
        flash(f"Pedido {pedido.numero_pedido} ya estaba marcado como preparado.", "info")
        return redirect(url_for("proveedor.pedidos"))

    ahora = utcnow()
    for estado in estados:
        estado.preparado = True
        estado.preparado_en = ahora
        estado.actualizado_por = current_user.id
    db.session.flush()
    estado_anterior = pedido.estado
    registrar_evento_pedido(
        pedido,
        "proveedor_preparado",
        actor_id=current_user.id,
        estado_anterior=estado_anterior,
        estado_nuevo=pedido.estado,
        canal="proveedor",
        metadata={"proveedor_ids": [estado.proveedor_id for estado in estados]},
    )

    # Auto-advance: si el pedido es 100% del bar y TODOS sus OrderProviderStatus
    # están preparados, no hay preparador interno que pueda marcarlo listo —
    # avanzamos el estado nosotros y asignamos repartidor.
    db.session.expire(pedido, ["estados_proveedor"])
    todos_listos = bool(pedido.estados_proveedor) and not pedido.proveedores_pendientes
    hizo_auto_advance = False
    repartidor = None
    if todos_listos and es_pedido_solo_bar(pedido) and pedido.estado in ESTADOS_EN_PREPARACION:
        hizo_auto_advance = True
        marcar_pedido_preparado(
            pedido,
            actor_id=current_user.id,
            canal="proveedor",
            detalle="Auto-advance: pedido 100% del bar con todos los proveedores listos.",
        )
        try:
            repartidor = distribuir_repartidor(pedido)
        except Exception:
            repartidor = None
            logger.exception("No se pudo asignar repartidor automático al pedido %s", pedido.id)
        # Si el pedido requiere reparto y no se asignó nadie, avisamos a admin
        # con un evento explícito para que aparezca destacado en la cola sin
        # repartidor. Sin este marcador, el pedido queda "listo" pero solo se
        # detecta mediante la query de pedidos_delivery_sin_repartidor_query.
        if pedido.requiere_reparto and not repartidor:
            registrar_evento_pedido(
                pedido,
                "reparto_sin_asignar",
                actor_id=current_user.id,
                estado_anterior=pedido.estado,
                estado_nuevo=pedido.estado,
                canal="proveedor",
                detalle="Auto-advance a listo pero no hay repartidor disponible.",
            )
    try:
        db.session.commit()
        try:
            from push_service import notify_roles
            roles_destino = ["cocina", "preparacion"]
            if pedido.requiere_reparto:
                roles_destino.append("repartidor")
            notify_roles(
                roles_destino,
                "Proveedor listo",
                f"El proveedor confirmó su parte del pedido #{pedido.numero_pedido}.",
                url="/preparador/pedidos",
            )
        except Exception:
            logger.exception("No se pudo avisar a preparación del pedido %s", pedido.id)
        if hizo_auto_advance and pedido.requiere_reparto and not repartidor:
            flash(
                f"Pedido {pedido.numero_pedido} está listo pero no hay repartidor libre. "
                "Admin debe asignarlo manualmente.",
                "warning",
            )
            try:
                from push_service import notify_roles
                notify_roles(
                    ["admin", "super_admin"],
                    "⚠️ Pedido listo sin repartidor",
                    f"#{pedido.numero_pedido} necesita asignación manual.",
                    url="/admin/pedidos",
                    tag=f"pedido-sin-rider-{pedido.id}",
                    require_interaction=True,
                )
            except Exception:
                logger.exception("No se pudo notificar sin-rider al admin (pedido %s)", pedido.id)
        else:
            flash(f"Pedido {pedido.numero_pedido} marcado como preparado.", "success")
    except Exception as exc:
        db.session.rollback()
        logger.exception("Error marcando pedido %s como preparado", pedido_id)
        flash(f"Error al guardar: {exc}", "danger")
        return redirect(url_for("proveedor.pedidos"))
    _notificar_preparador(
        pedido,
        "Proveedor listo",
        f"#{pedido.numero_pedido}: la preparación externa está lista.",
    )
    return redirect(url_for("proveedor.pedidos"))


@proveedor_bp.route("/finanzas")
@proveedor_required
def finanzas():
    """Resumen contable del socio basado en el mismo cierre que administración."""
    from datetime import datetime
    from business_time import business_today

    prov_id = _proveedor_id_efectivo()
    es_admin_general = not prov_id and current_user.rol in ("admin", "super_admin")
    bar = db.session.get(Proveedor, prov_id) if prov_id else None
    if not bar and not es_admin_general:
        flash("Tu cuenta no está enlazada a ningún bar.", "warning")
        return redirect(url_for("public.index"))
    # Si un admin general llega aquí sin bar, el panel /proveedor/finanzas
    # no es la herramienta correcta: tiene una vista global mejor en
    # /admin/liquidacion-proveedores. Redirigimos con mensaje claro.
    if es_admin_general and not request.args.get("forzar"):
        flash(
            "Esta vista es la del bar. Para el resumen global por bar usa "
            "Liquidación de proveedores.",
            "info",
        )
        return redirect(url_for("admin.liquidacion_proveedores"))

    # Selector de fechas (default último mes)
    fecha_inicio_str = (request.args.get("fecha_inicio") or "").strip()
    fecha_fin_str = (request.args.get("fecha_fin") or "").strip()
    try:
        hoy_negocio = business_today()
        fecha_inicio = (datetime.strptime(fecha_inicio_str, "%Y-%m-%d").date()
                        if fecha_inicio_str else hoy_negocio.replace(day=1))
        fecha_fin = (datetime.strptime(fecha_fin_str, "%Y-%m-%d").date()
                     if fecha_fin_str else hoy_negocio)
    except ValueError:
        flash("Fechas inválidas, mostrando mes actual.", "warning")
        hoy_negocio = business_today()
        fecha_inicio = hoy_negocio.replace(day=1)
        fecha_fin = hoy_negocio
    if fecha_inicio > fecha_fin:
        fecha_inicio, fecha_fin = fecha_fin, fecha_inicio

    cierre = calcular_liquidaciones_proveedores(
        fecha_inicio,
        fecha_fin,
        proveedor_id=bar.id if bar else None,
    )
    bucket = cierre["por_proveedor"].get(bar.id) if bar else None
    if not bucket:
        bucket = {
            "lineas": [],
            "sin_costo": [],
            "total": Decimal("0"),
            "total_entregado": Decimal("0"),
            "total_extraviado": Decimal("0"),
            "ventas_netas": Decimal("0"),
            "ventas_extraviadas": Decimal("0"),
            "registrado": Decimal("0"),
            "pagado": Decimal("0"),
            "programado": Decimal("0"),
            "pendiente_registrar": Decimal("0"),
            "pendiente_pago": Decimal("0"),
        }
    modelo = bar.modelo_acuerdo if bar else "stock_proveedor"
    comision_pct = Decimal(str(bar.comision_pct or 0)) if bar else Decimal("0")
    if modelo == "socio_porcentaje":
        formula = f"Socio {Decimal('100') - comision_pct}% · tienda {comision_pct}%"
    elif modelo == "stock_propio_bar":
        formula = f"{comision_pct}% del PVP neto vendido"
    else:
        formula = "Coste congelado por producto o componente"

    return render_template(
        "proveedor/finanzas.html",
        bar=bar,
        es_admin_general=es_admin_general,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        bucket=bucket,
        pvp_vendido=bucket["ventas_netas"],
        pvp_extraviado=bucket["ventas_extraviadas"],
        a_pagar_bar=bucket["total"],
        ingreso_marketplace=(
            bucket["ventas_netas"] - bucket["total_entregado"]
        ),
        formula=formula,
        modelo=modelo,
        comision_pct=comision_pct,
    )


@proveedor_bp.route("/pedidos/<int:pedido_id>/extraviado", methods=["POST"])
@proveedor_required
def marcar_extraviado(pedido_id):
    """El bar declara que un pedido se extravió/perdió. Se cancela el pedido
    (restaura stock al bar) y queda registrado el evento para diferenciarlo
    de una cancelación normal en finanzas."""
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    prov_id = _proveedor_id_efectivo()
    if prov_id:
        ops = OrderProviderStatus.query.filter_by(
            pedido_id=pedido.id, proveedor_id=prov_id
        ).first()
        if not ops:
            flash("Este pedido no es de tu bar.", "danger")
            return redirect(url_for("proveedor.pedidos"))
        if not es_pedido_solo_bar(pedido) or len(pedido.estados_proveedor) != 1:
            flash(
                "En pedidos mixtos reporta la incidencia; solo administración puede cancelar el pedido completo.",
                "danger",
            )
            return redirect(url_for("proveedor.pedidos"))
        if pedido.estado not in ESTADOS_EN_PREPARACION:
            flash("Solo puedes reportar extravío antes de que el pedido esté listo o en ruta.", "danger")
            return redirect(url_for("proveedor.pedidos"))
    if pedido.estado in ("entregado", "cancelado"):
        flash(f"El pedido {pedido.numero_pedido} ya estaba en estado {pedido.estado}.", "warning")
        return redirect(url_for("proveedor.pedidos"))

    motivo = (request.form.get("motivo") or "").strip()[:300]
    # Primero registramos el evento de extravío (queda visible en finanzas)
    registrar_evento_pedido(
        pedido,
        "pedido_extraviado",
        actor_id=current_user.id,
        estado_anterior=pedido.estado,
        estado_nuevo="cancelado",
        canal="proveedor",
        detalle=motivo or "Extravío reportado por el bar",
        metadata={"proveedor_id": prov_id, "motivo": motivo},
    )
    try:
        cancelar_pedido_operativo(
            pedido,
            actor_id=current_user.id,
            canal="proveedor_extravio",
            detalle=f"Extravío reportado por bar: {motivo or 'sin motivo detallado'}",
        )
        db.session.commit()
        flash(f"Pedido {pedido.numero_pedido} marcado como extraviado.", "warning")
    except Exception as exc:
        db.session.rollback()
        logger.exception("Error marcando extravío del pedido %s", pedido_id)
        flash(f"No se pudo registrar el extravío: {exc}", "danger")
    return redirect(url_for("proveedor.pedidos"))


@proveedor_bp.route("/incidencias", methods=["GET", "POST"])
@proveedor_required
def incidencias():
    """Bandeja de incidencias: novedades reportadas por clientes sobre los
    pedidos que despacha el bar del operador (o del admin si no tiene bar)."""
    prov_id = _proveedor_id_efectivo()
    es_admin = current_user.rol in ("admin", "super_admin") and not prov_id

    if request.method == "POST":
        accion = request.form.get("accion", "marcar_atendida")
        if accion == "marcar_atendida":
            evento_id = request.form.get("evento_id", type=int)
            evento = db.session.get(OrderEvent, evento_id) if evento_id else None
            if not evento or evento.tipo != "cliente_reporto_novedad":
                flash("Incidencia no encontrada.", "warning")
                return redirect(url_for("proveedor.incidencias"))
            # Verificar que la incidencia pertenece a un pedido de este bar
            if prov_id:
                ops = OrderProviderStatus.query.filter_by(
                    pedido_id=evento.pedido_id, proveedor_id=prov_id
                ).first()
                if not ops:
                    flash("Esa incidencia no es de un pedido tuyo.", "danger")
                    return redirect(url_for("proveedor.incidencias"))
            from services import registrar_evento_pedido
            registrar_evento_pedido(
                evento.pedido,
                "incidencia_atendida",
                actor_id=current_user.id,
                estado_anterior=evento.pedido.estado,
                estado_nuevo=evento.pedido.estado,
                canal="proveedor",
                detalle=f"Incidencia #{evento.id} marcada como atendida",
                metadata={"incidencia_id": evento.id},
            )
            db.session.commit()
            flash("Incidencia marcada como atendida.", "success")
            return redirect(url_for("proveedor.incidencias"))

    # Listado: novedades cliente cuyas órdenes pertenecen al bar.
    q = (
        OrderEvent.query
        .filter(OrderEvent.tipo == "cliente_reporto_novedad")
        .join(Order, OrderEvent.pedido_id == Order.id)
        .order_by(OrderEvent.creado_en.desc())
    )
    if prov_id:
        # Solo incidencias de pedidos donde el bar participa
        q = q.join(
            OrderProviderStatus,
            (OrderProviderStatus.pedido_id == Order.id) &
            (OrderProviderStatus.proveedor_id == prov_id),
        )
    incidencias_list = q.limit(100).all()

    # Marcar las ya atendidas para resaltarlas suaves: si para esa incidencia
    # existe un evento posterior 'incidencia_atendida' con su id en metadata.
    atendidas_ids = set()
    if incidencias_list:
        ids = [e.id for e in incidencias_list]
        eventos_atendidos = OrderEvent.query.filter(
            OrderEvent.tipo == "incidencia_atendida"
        ).all()
        for ev in eventos_atendidos:
            meta = ev.get_metadata() if hasattr(ev, "get_metadata") else {}
            iid = meta.get("incidencia_id")
            if iid in ids:
                atendidas_ids.add(iid)

    return render_template(
        "proveedor/incidencias.html",
        incidencias=incidencias_list,
        atendidas_ids=atendidas_ids,
        es_admin=es_admin,
    )


@proveedor_bp.route("/inventario", methods=["GET", "POST"])
@proveedor_required
def inventario():
    """Inventario separado: el socio solo opera los SKU de los que es dueño."""
    prov_id = _proveedor_id_efectivo()
    if not prov_id:
        flash("Tu cuenta no está enlazada a ningún proveedor.", "warning")
        return redirect(url_for("public.index"))

    proveedor = db.session.get(Proveedor, prov_id)
    if not proveedor:
        flash("Proveedor no encontrado.", "danger")
        return redirect(url_for("public.index"))

    if current_user.rol == "socio_producto":
        if (not proveedor or not proveedor.activo
                or proveedor.modelo_acuerdo != "socio_porcentaje"):
            try:
                AuditLog.registrar(
                    current_user.id, "socio_capital_denegado", "proveedor",
                    prov_id, detalle=f"ruta={request.path}",
                    ip=request.remote_addr,
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
            abort(403)

    if request.method == "POST":
        # El socio financia la mercancía, pero el stock físico lo custodia y
        # opera la tienda. Impedir la mutación también en backend evita que un
        # formulario antiguo o una petición manual desincronicen existencias.
        if current_user.rol == "socio_producto":
            abort(403)
        accion = request.form.get("accion", "actualizar_sku")
        if accion == "actualizar_sku":
            fila_id = request.form.get("fila_id", type=int)
            fila = (
                ProveedorProducto.query
                .filter_by(id=fila_id, proveedor_id=prov_id)
                .with_for_update()
                .first()
                if fila_id else None
            )
            if not fila or fila.proveedor_id != prov_id:
                flash("SKU no encontrado en tu inventario.", "danger")
                return redirect(url_for("proveedor.inventario"))
            try:
                stock = _entero_no_negativo(request.form.get("stock"), "El stock")
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
                return redirect(url_for("proveedor.inventario"))
            if (
                fila.producto.es_combo
                or fila.producto.proveedor_despachador_id != prov_id
            ):
                db.session.rollback()
                flash("Ese producto no pertenece al inventario de este socio.", "danger")
                return redirect(url_for("proveedor.inventario"))
            if current_user.rol == "socio_producto":
                _assert_owned_by(proveedor, fila.producto)
            fila.stock = stock
            # En reparto porcentual no existe coste para la tienda: el socio
            # recibe su participación y conserva la propiedad del inventario.
            fila.precio_costo = None
            try:
                db.session.commit()
                flash(f"«{fila.producto.nombre}» actualizado.", "success")
            except Exception as exc:
                db.session.rollback()
                flash(f"Error al guardar: {exc}", "danger")
            return redirect(url_for("proveedor.inventario"))

    skus = (
        ProveedorProducto.query
        .filter_by(proveedor_id=prov_id)
        .join(Product, ProveedorProducto.producto_id == Product.id)
        .filter(
            Product.es_combo.is_(False),
            Product.proveedor_despachador_id == prov_id,
        )
        .order_by(Product.nombre)
        .all()
    )
    combos = Product.query.filter_by(
        proveedor_despachador_id=prov_id,
        es_combo=True,
    ).order_by(Product.nombre).all()
    return render_template(
        "proveedor/inventario.html",
        proveedor=proveedor,
        skus=skus,
        combos=combos,
    )


@proveedor_bp.route("/productos/nuevo", methods=["GET", "POST"])
@proveedor_bp.route("/productos/<int:producto_id>/editar", methods=["GET", "POST"])
@socio_capital_required
def registrar_producto(producto_id=None, _proveedor=None):
    """Registra o corrige una propuesta, siempre ligada al socio autenticado."""
    proveedor = _proveedor

    producto = None
    if producto_id is not None:
        producto = db.session.get(Product, producto_id)
        _assert_owned_by(proveedor, producto)
        if producto.es_combo:
            abort(404)
        if producto.partner_submission_status not in {"pending", "rejected"}:
            flash(
                "Un producto ya aprobado se actualiza desde inventario. "
                "Los cambios comerciales deben revisarse con administración.",
                "warning",
            )
            return redirect(url_for("proveedor.inventario"))

    if request.method == "POST":
        # Instrumentación de diagnóstico: registra qué campos llegaron. Sin
        # esto no podíamos distinguir "el navegador bloqueó el submit" de
        # "el submit llegó pero un valor era inválido".
        current_app.logger.info(
            "socio.registrar_producto POST proveedor=%s producto_id=%s "
            "form_keys=%s files_keys=%s content_length=%s",
            proveedor.id, producto_id,
            sorted(request.form.keys()),
            sorted(request.files.keys()),
            request.content_length,
        )
        nueva_imagen = None
        imagen_anterior = producto.imagen_url if producto else None
        try:
            campos = _campos_producto_socio(request.form)
            archivo = request.files.get("imagen_archivo")
            if archivo and getattr(archivo, "filename", None):
                if not archivo.filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    raise ValueError("La imagen debe ser JPG, PNG o WebP.")
                nueva_imagen = save_image(archivo, "productos")

            if producto is None:
                producto = Product(
                    nombre=campos["nombre"],
                    descripcion=campos["descripcion"],
                    precio=campos["precio"],
                    precio_costo=None,
                    categoria_id=campos["categoria_id"],
                    imagen_url=nueva_imagen,
                    origen_pais=campos["origen_pais"],
                    es_combo=False,
                    tipo_producto="simple",
                    activo=False,
                    vertical=campos["vertical"],
                    canal_preparacion="almacen",
                    proveedor_despachador_id=proveedor.id,
                    tipo_entrega="inmediato",
                    modalidad_entrega=campos["modalidad_entrega"],
                    hora_inicio_visibilidad=campos["hora_inicio_visibilidad"],
                    hora_fin_visibilidad=campos["hora_fin_visibilidad"],
                    dias_semana_json=campos["dias_semana_json"],
                    stock_mostrar_en_web=campos["stock_mostrar_en_web"],
                    partner_submitted_by=current_user.id,
                    partner_submitted_at=utcnow(),
                    partner_submission_status="pending",
                )
                db.session.add(producto)
                db.session.flush()
                db.session.add(ProveedorProducto(
                    proveedor_id=proveedor.id,
                    producto_id=producto.id,
                    stock=campos["stock"],
                    precio_costo=None,
                    activo=True,
                ))
                accion_auditoria = "socio_producto_registrado"
            else:
                producto.nombre = campos["nombre"]
                producto.descripcion = campos["descripcion"]
                producto.precio = campos["precio"]
                producto.precio_costo = None
                producto.categoria_id = campos["categoria_id"]
                producto.origen_pais = campos["origen_pais"]
                producto.modalidad_entrega = campos["modalidad_entrega"]
                producto.hora_inicio_visibilidad = campos["hora_inicio_visibilidad"]
                producto.hora_fin_visibilidad = campos["hora_fin_visibilidad"]
                producto.dias_semana_json = campos["dias_semana_json"]
                producto.stock_mostrar_en_web = campos["stock_mostrar_en_web"]
                producto.activo = False
                producto.partner_submission_status = "pending"
                producto.partner_submitted_by = current_user.id
                producto.partner_submitted_at = utcnow()
                producto.partner_reviewed_by = None
                producto.partner_reviewed_at = None
                producto.partner_review_note = None
                if nueva_imagen:
                    producto.imagen_url = nueva_imagen
                fila = ProveedorProducto.query.filter_by(
                    proveedor_id=proveedor.id,
                    producto_id=producto.id,
                ).with_for_update().first()
                if not fila:
                    fila = ProveedorProducto(
                        proveedor_id=proveedor.id,
                        producto_id=producto.id,
                        precio_costo=None,
                        activo=True,
                    )
                    db.session.add(fila)
                fila.stock = campos["stock"]
                fila.precio_costo = None
                fila.activo = True
                accion_auditoria = "socio_producto_reenviado"

            AuditLog.registrar(
                current_user.id,
                accion_auditoria,
                "producto",
                producto.id,
                detalle=f"Socio #{proveedor.id}: {producto.nombre}",
                ip=request.remote_addr,
            )
            db.session.commit()
            if nueva_imagen and imagen_anterior and imagen_anterior != nueva_imagen:
                delete_image(imagen_anterior)
        except ValueError as exc:
            db.session.rollback()
            if nueva_imagen:
                delete_image(nueva_imagen)
            flash(str(exc), "danger")
        except Exception:
            db.session.rollback()
            if nueva_imagen:
                delete_image(nueva_imagen)
            logger.exception("Error registrando producto del socio %s", proveedor.id)
            flash("No se pudo registrar el producto. Inténtalo de nuevo.", "danger")
        else:
            try:
                from push_service import notify_roles
                notify_roles(
                    ["super_admin"],
                    "Producto de socio pendiente",
                    f"{proveedor.nombre} envió «{producto.nombre}» para revisión.",
                    url="/admin/productos?revision=pending",
                    tag=f"partner-product-{producto.id}",
                    # Excluye al propio socio (si por casualidad tiene rol
                    # super_admin además de socio_producto) para no auto-
                    # notificarse su propia submisión.
                    exclude_user_ids=[current_user.id],
                )
            except Exception:
                logger.exception("No se pudo notificar la propuesta %s", producto.id)
            flash(
                "Producto enviado a revisión. Podrás gestionar su stock cuando sea aprobado.",
                "success",
            )
            return redirect(url_for("proveedor.inventario"))

    categorias = Categoria.query.filter_by(activo=True).order_by(Categoria.nombre).all()
    fila = (
        ProveedorProducto.query.filter_by(
            proveedor_id=proveedor.id,
            producto_id=producto.id,
        ).first()
        if producto else None
    )
    return render_template(
        "proveedor/producto_form.html",
        proveedor=proveedor,
        producto=producto,
        categorias=categorias,
        stock_actual=fila.stock if fila else 0,
    )


@proveedor_bp.route("/productos/<int:producto_id>/publicacion", methods=["POST"])
@socio_capital_required
def toggle_publicacion_producto(producto_id, _proveedor=None):
    """Permite al socio pausar o reanudar la publicación de un producto ya
    aprobado. La aprobación de super_admin sigue siendo requisito previo:
    aquí solo se alterna ``Product.activo`` sin tocar el estado de revisión."""
    proveedor = _proveedor
    producto = db.session.get(Product, producto_id)
    _assert_owned_by(proveedor, producto)
    if producto.partner_submission_status != "approved":
        flash(
            "Solo los productos aprobados pueden pausarse o reanudarse.",
            "warning",
        )
        return redirect(url_for("proveedor.inventario"))

    nuevo_activo = not bool(producto.activo)
    if not nuevo_activo:
        # Pausar un componente de un combo activo rompería el combo publicado.
        combos_afectados = (
            ComboItem.query
            .filter_by(producto_id=producto.id, activo=True)
            .join(Product, ComboItem.combo_id == Product.id)
            .filter(Product.activo.is_(True))
            .count()
        )
        if combos_afectados > 0:
            flash(
                f"Este producto forma parte de {combos_afectados} combo(s) "
                "activo(s). Pausa o modifica esos combos antes de retirarlo.",
                "warning",
            )
            return redirect(url_for("proveedor.inventario"))

    producto.activo = nuevo_activo
    try:
        AuditLog.registrar(
            current_user.id,
            "socio_producto_publicacion",
            "producto",
            producto.id,
            detalle=(
                f"Socio #{proveedor.id}: {'activado' if nuevo_activo else 'pausado'}"
            ),
            ip=request.remote_addr,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception(
            "No se pudo alternar publicación del producto %s", producto.id
        )
        flash("No se pudo actualizar la publicación. Reintenta.", "danger")
        return redirect(url_for("proveedor.inventario"))

    notificar_bot_sync()
    flash(
        f"«{producto.nombre}» "
        + ("está publicado en la tienda." if nuevo_activo else "quedó pausado."),
        "success",
    )
    return redirect(url_for("proveedor.inventario"))


def _combo_groups_payload_from_form_socio(form):
    """Idéntico contrato al helper del admin; duplicado local para evitar
    importaciones cruzadas entre blueprints. Lee los mismos names que
    ``admin._combo_groups_payload_from_form``."""
    uids = form.getlist("combo_group_uid") or []
    names = form.getlist("combo_group_name") or []
    types = form.getlist("combo_group_type") or []
    max_sels = form.getlist("combo_group_max_sel") or []
    orders = form.getlist("combo_group_order") or []
    payload = {}
    for i, uid in enumerate(uids):
        uid = (uid or "").strip()
        if not uid:
            continue
        tipo_raw = (types[i] if i < len(types) else "fijo").strip().lower()
        tipo = "seleccion" if tipo_raw in ("sel", "seleccion", "choice") else "fijo"
        try:
            max_sel = int(max_sels[i]) if i < len(max_sels) and max_sels[i] else 1
        except (TypeError, ValueError):
            max_sel = 1
        try:
            orden = int(orders[i]) if i < len(orders) and orders[i] else i
        except (TypeError, ValueError):
            orden = i
        nombre_raw = (names[i] if i < len(names) else "").strip()[:80]
        if not nombre_raw:
            nombre_raw = "Base incluida" if tipo == "fijo" else "Eleccion"
        payload[uid] = {
            "tipo": tipo,
            "nombre": nombre_raw,
            "max_selecciones": max_sel,
            "orden": orden,
        }
    return payload


@proveedor_bp.route("/combos/nuevo", methods=["GET", "POST"])
@proveedor_bp.route("/combos/<int:combo_id>/editar", methods=["GET", "POST"])
@socio_capital_required
def registrar_combo(combo_id=None, _proveedor=None):
    """Permite al socio proponer combos formados solo por su propio inventario."""
    proveedor = _proveedor

    combo = None
    if combo_id is not None:
        combo = db.session.get(Product, combo_id)
        _assert_owned_by(proveedor, combo)
        if not combo.es_combo:
            abort(404)
        if combo.partner_submission_status not in {"pending", "rejected"}:
            flash("Los cambios de un combo aprobado deben revisarse con administración.", "warning")
            return redirect(url_for("proveedor.inventario"))

    productos_propios = Product.query.filter(
        Product.proveedor_despachador_id == proveedor.id,
        Product.es_combo.is_(False),
        Product.activo.is_(True),
    ).order_by(Product.nombre).all()

    productos_permitidos = {p.id: p for p in productos_propios}

    if request.method == "POST":
        from combo_form_parser import parse_componentes, ComboParseError
        from combo_builder import build_combo

        nueva_imagen = None
        imagen_anterior = combo.imagen_url if combo else None
        try:
            campos = _campos_producto_socio(request.form, incluir_stock=False)
            archivo = request.files.get("imagen_archivo")
            if archivo and getattr(archivo, "filename", None):
                if not archivo.filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    raise ValueError("La imagen debe ser JPG, PNG o WebP.")
                nueva_imagen = save_image(archivo, "productos")

            if combo is None:
                combo = Product(
                    nombre=campos["nombre"],
                    descripcion=campos["descripcion"],
                    precio=campos["precio"],
                    precio_costo=None,
                    categoria_id=campos["categoria_id"],
                    imagen_url=nueva_imagen,
                    origen_pais=campos["origen_pais"],
                    es_combo=True,
                    tipo_producto="combo",
                    activo=False,
                    vertical=campos["vertical"],
                    canal_preparacion="almacen",
                    proveedor_despachador_id=proveedor.id,
                    tipo_entrega="inmediato",
                    modalidad_entrega=campos["modalidad_entrega"],
                    stock_mostrar_en_web=True,
                    combo_precio_modo="fijo",
                    combo_precio_base=campos["precio"],
                )
                db.session.add(combo)
                db.session.flush()
                audit_action = "socio_combo_registrado"
            else:
                combo.nombre = campos["nombre"]
                combo.descripcion = campos["descripcion"]
                combo.precio = campos["precio"]
                combo.combo_precio_base = campos["precio"]
                combo.categoria_id = campos["categoria_id"]
                combo.origen_pais = campos["origen_pais"]
                combo.modalidad_entrega = campos["modalidad_entrega"]
                if nueva_imagen:
                    combo.imagen_url = nueva_imagen
                audit_action = "socio_combo_reenviado"

            # ── Parser + builder compartidos con admin ──
            group_defs_socio = _combo_groups_payload_from_form_socio(request.form)
            try:
                componentes_parsed = parse_componentes(
                    request.form,
                    productos_permitidos=productos_permitidos,
                    group_defs=group_defs_socio,
                    parent_vertical=combo.vertical,
                    combo_id=combo.id,
                    enforce_owner_id=proveedor.id,
                )
            except ComboParseError as exc:
                raise ValueError(str(exc))

            build_combo(
                combo,
                componentes_parsed,
                group_defs=group_defs_socio,
                autor_role="socio_producto",
                actor_user_id=current_user.id,
            )
            AuditLog.registrar(
                current_user.id,
                audit_action,
                "producto",
                combo.id,
                detalle=f"Socio #{proveedor.id}: {combo.nombre}",
                ip=request.remote_addr,
            )
            db.session.commit()
            if nueva_imagen and imagen_anterior and nueva_imagen != imagen_anterior:
                delete_image(imagen_anterior)
        except ValueError as exc:
            db.session.rollback()
            if nueva_imagen:
                delete_image(nueva_imagen)
            flash(str(exc), "danger")
        except Exception:
            db.session.rollback()
            if nueva_imagen:
                delete_image(nueva_imagen)
            logger.exception("Error registrando combo del socio %s", proveedor.id)
            flash("No se pudo registrar el combo. Revisa la composición.", "danger")
        else:
            try:
                from push_service import notify_roles
                notify_roles(
                    ["super_admin"],
                    "Combo de socio pendiente",
                    f"{proveedor.nombre} envió «{combo.nombre}» para revisión.",
                    url="/admin/productos?revision=pending",
                    tag=f"partner-product-{combo.id}",
                    exclude_user_ids=[current_user.id],
                )
            except Exception:
                logger.exception("No se pudo notificar el combo %s", combo.id)
            flash("Combo enviado a revisión.", "success")
            return redirect(url_for("proveedor.inventario"))

    selected = {}
    if combo:
        for item in ComboItem.query.filter_by(combo_id=combo.id).all():
            selected[item.producto_id] = {
                "cantidad": item.cantidad,
                "modo": "eleccion" if item.es_seleccionable else "fijo",
                "grupo": item.grupo_seleccion or "",
            }
    categorias = Categoria.query.filter_by(activo=True).order_by(Categoria.nombre).all()

    # ── Precarga anti-N+1: tamaños y sabores por producto, mismo payload
    #    que consume el builder del admin (nuevo_combo.html) ──
    from product_presentations_service import product_presentation_catalog_payload
    from models import ProductExtraGroup, ProductExtraOption
    from sqlalchemy import func

    presentaciones_por_producto = {
        str(p.id): product_presentation_catalog_payload(p) for p in productos_propios
    }
    flavor_options_by_product = {}
    flavor_counts_by_product = {}
    if productos_propios:
        _ids = [p.id for p in productos_propios]
        rows = (
            db.session.query(
                ProductExtraGroup.producto_id,
                ProductExtraOption.id,
                ProductExtraOption.nombre,
                ProductExtraOption.orden,
            )
            .join(ProductExtraOption, ProductExtraOption.grupo_id == ProductExtraGroup.id)
            .filter(
                ProductExtraGroup.tipo == "sabor",
                ProductExtraGroup.activo.is_(True),
                ProductExtraOption.activo.is_(True),
                ProductExtraGroup.producto_id.in_(_ids),
            )
            .order_by(
                ProductExtraGroup.producto_id,
                ProductExtraOption.orden,
                ProductExtraOption.id,
            )
            .all()
        )
        for pid, oid, nom, _ in rows:
            flavor_options_by_product.setdefault(int(pid), []).append(
                {"id": int(oid), "nombre": nom or ""}
            )
        flavor_counts_by_product = {
            pid: len(opts) for pid, opts in flavor_options_by_product.items()
        }

    return render_template(
        "proveedor/combo_form.html",
        proveedor=proveedor,
        combo=combo,
        categorias=categorias,
        productos_propios=productos_propios,
        selected=selected,
        max_qty=ComboLimits.max_qty_per_component(),
        presentaciones_por_producto=presentaciones_por_producto,
        flavor_options_by_product=flavor_options_by_product,
        flavor_counts_by_product=flavor_counts_by_product,
    )


@proveedor_bp.route("/pedidos/<int:pedido_id>/reabrir", methods=["POST"])
@proveedor_required
def reabrir_preparacion(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.estado not in ESTADOS_EN_PREPARACION:
        flash("Solo se puede corregir un pedido que siga en preparación.", "warning")
        return redirect(url_for("proveedor.pedidos"))

    prov_id = _proveedor_id_efectivo()
    if not prov_id:
        prov_id = request.form.get("proveedor_id", type=int)

    estados_q = OrderProviderStatus.query.filter_by(pedido_id=pedido.id)
    if prov_id:
        estados_q = estados_q.filter_by(proveedor_id=prov_id)
    estados = estados_q.with_for_update().all()
    if not estados:
        flash("No se encontró la preparación de proveedor que quieres corregir.", "warning")
        return redirect(url_for("proveedor.pedidos"))
    if len(estados) > 1:
        flash("Selecciona el proveedor concreto que debe reabrir su preparación.", "warning")
        return redirect(url_for("proveedor.pedidos"))
    if not estados[0].preparado:
        flash("Esta preparación ya estaba pendiente.", "info")
        return redirect(url_for("proveedor.pedidos"))

    estado = estados[0]
    estado.preparado = False
    estado.preparado_en = None
    estado.actualizado_por = current_user.id
    registrar_evento_pedido(
        pedido,
        "proveedor_reabierto",
        actor_id=current_user.id,
        estado_anterior=pedido.estado,
        estado_nuevo=pedido.estado,
        canal="proveedor",
        metadata={"proveedor_ids": [estado.proveedor_id]},
    )
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception("Error reabriendo proveedor del pedido %s", pedido_id)
        flash(f"Error al guardar: {exc}", "danger")
        return redirect(url_for("proveedor.pedidos"))

    flash(f"Preparación de {pedido.numero_pedido} reabierta.", "warning")
    _notificar_preparador(
        pedido,
        "Proveedor reabrió preparación",
        f"#{pedido.numero_pedido}: la preparación externa vuelve a estar pendiente.",
    )
    return redirect(url_for("proveedor.pedidos"))
