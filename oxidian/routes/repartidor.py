from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from functools import wraps
import logging

from sqlalchemy.orm import joinedload
from extensions import db, get_or_404
from models import Order, OrderEvent, OrderItem, User, AuditLog, StaffPayment, normalizar_metodo_pago
from services import (
    avanzar_estado_pedido,
    generar_comision_entrega,
    award_points_on_delivery,
    enviar_whatsapp_codigo_entrega,
    enviar_whatsapp_estado,
    registrar_pago_pedido,
    registrar_ingreso_pedido,
    redistribuir_listos_sin_repartidor,
    solicitar_resena_pedido,
)

repartidor_bp = Blueprint("repartidor", __name__)
logger = logging.getLogger(__name__)


@repartidor_bp.before_request
def exigir_delivery_habilitado():
    from store_config import get_store_features

    if not get_store_features()["delivery"]:
        flash("El módulo de delivery está desactivado para esta tienda.", "info")
        if current_user.is_authenticated and current_user.rol in ("admin", "super_admin"):
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("public.index"))

ROLES_REPARTIDOR = {"admin", "super_admin", "repartidor"}


def _es_admin_operativo():
    return current_user.rol in ("admin", "super_admin")


def _esta_disponible():
    if _es_admin_operativo():
        return True
    usuario = db.session.get(User, current_user.id, populate_existing=True)
    return bool(usuario and usuario.disponible_para_pedidos)


def _requiere_disponible_para_nuevo_trabajo():
    if not _esta_disponible():
        flash("Ponte online para tomar o despachar pedidos nuevos.", "warning")
        return False
    return True


def repartidor_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.rol not in ROLES_REPARTIDOR:
            flash("Acceso restringido.", "danger")
            return redirect(url_for("public.index"))
        return f(*args, **kwargs)
    return decorated


def _group_orders_by_zone(pedidos):
    """Agrupa pedidos listos por zona para que el repartidor vea conjuntos cercanos."""
    def _zone_sort_key(pedido):
        z = pedido.zona
        return (
            z.orden if z and z.orden is not None else 9999,
            z.nombre if z else "Sin zona",
            pedido.creado_en,
        )

    pedidos_ordenados = sorted(pedidos, key=_zone_sort_key)
    grupos = []
    current_zone = None
    current_zone_order = None
    current_pedidos = []

    for pedido in pedidos_ordenados:
        zona_nombre = pedido.zona.nombre if pedido.zona else "Sin zona"
        zona_orden = pedido.zona.orden if pedido.zona and pedido.zona.orden is not None else 9999
        if current_zone is None or current_zone != zona_nombre:
            if current_pedidos:
                grupos.append({
                    "zona_nombre": current_zone,
                    "zona_orden": current_zone_order,
                    "pedidos": current_pedidos,
                    "count": len(current_pedidos),
                })
            current_zone = zona_nombre
            current_zone_order = zona_orden
            current_pedidos = [pedido]
        else:
            current_pedidos.append(pedido)

    if current_pedidos:
        grupos.append({
            "zona_nombre": current_zone,
            "zona_orden": current_zone_order,
            "pedidos": current_pedidos,
            "count": len(current_pedidos),
        })

    return grupos


def _codigo_enviado_ids(pedidos):
    pedido_ids = [p.id for p in pedidos if p and p.id]
    if not pedido_ids:
        return set()
    rows = db.session.query(OrderEvent.pedido_id).filter(
        OrderEvent.pedido_id.in_(pedido_ids),
        OrderEvent.tipo == "codigo_entrega_enviado",
    ).distinct().all()
    return {row[0] for row in rows}


@repartidor_bp.route("/toggle-disponible", methods=["POST"])
@repartidor_required
def toggle_disponible():
    current_user.toggle_disponible()
    db.session.commit()
    pedidos_asignados = 0
    if current_user.en_linea:
        pedidos_asignados = redistribuir_listos_sin_repartidor()
        if pedidos_asignados:
            db.session.commit()
    return jsonify({
        "ok": True,
        "en_linea": current_user.en_linea,
        "pedidos_asignados": pedidos_asignados,
    })


@repartidor_bp.route("/ruta")
@repartidor_required
def ruta():
    disponible = _esta_disponible()
    _eager_zona = joinedload(Order.zona)
    if _es_admin_operativo():
        listos = Order.query.options(_eager_zona).filter_by(
            estado="listo", tipo_entrega_cliente="delivery"
        ).order_by(Order.creado_en).all()
        en_ruta = Order.query.options(_eager_zona).filter_by(
            estado="en_ruta", tipo_entrega_cliente="delivery"
        ).order_by(Order.creado_en).all()
    else:
        if disponible:
            listos_propios = Order.query.options(_eager_zona).filter_by(
                estado="listo", repartidor_id=current_user.id, tipo_entrega_cliente="delivery"
            ).order_by(Order.creado_en).all()
            sin_asignar = Order.query.options(_eager_zona).filter_by(
                estado="listo", repartidor_id=None, tipo_entrega_cliente="delivery"
            ).order_by(Order.creado_en).all()
            listos = listos_propios + sin_asignar
        else:
            listos = []

        filtros_en_ruta = [
            Order.estado == "en_ruta",
            Order.repartidor_id == current_user.id,
            Order.tipo_entrega_cliente == "delivery",
        ]
        if disponible:
            filtros_en_ruta = [
                Order.estado == "en_ruta",
                db.or_(
                    Order.repartidor_id == current_user.id,
                    Order.repartidor_id.is_(None),
                ),
                Order.tipo_entrega_cliente == "delivery",
            ]
        en_ruta = Order.query.options(_eager_zona).filter(
            *filtros_en_ruta
        ).order_by(Order.creado_en).all()

    listos_grouped = _group_orders_by_zone(listos)
    listos_count = len(listos)

    companeros = User.query.filter(
        User.rol.in_(["repartidor", "admin"]),
        User.activo == True,
        User.id != current_user.id
    ).all()

    return render_template("repartidor/ruta.html",
                           listos_grouped=listos_grouped,
                           listos_count=listos_count,
                           en_ruta=en_ruta,
                           codigo_enviado_ids=_codigo_enviado_ids(en_ruta),
                           companeros=companeros,
                           disponible=disponible)


@repartidor_bp.route("/pedidos/<int:pedido_id>/tomar", methods=["POST"])
@repartidor_required
def tomar_pedido(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if _es_admin_operativo():
        flash("Asigna el pedido a un repartidor desde la cola administrativa.", "warning")
        return redirect(url_for("repartidor.ruta"))
    if pedido.estado != "listo":
        flash("El pedido no está disponible.", "warning")
        return redirect(url_for("repartidor.ruta"))
    if not pedido.requiere_reparto:
        flash("Este pedido es para recoger; no requiere repartidor.", "warning")
        return redirect(url_for("repartidor.ruta"))
    if not pedido.repartidor_id and not _requiere_disponible_para_nuevo_trabajo():
        return redirect(url_for("repartidor.ruta"))
    if pedido.repartidor_id and pedido.repartidor_id != current_user.id and not _es_admin_operativo():
        flash("Este pedido ya está asignado a otro repartidor.", "warning")
        return redirect(url_for("repartidor.ruta"))
    pedido.repartidor_id = current_user.id
    db.session.commit()
    flash(f"Pedido {pedido.numero_pedido} asignado a ti.", "success")
    return redirect(url_for("repartidor.ruta"))


@repartidor_bp.route("/pedidos/<int:pedido_id>/salir", methods=["POST"])
@repartidor_required
def salir_entregar(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.estado != "listo":
        flash("El pedido no está listo para despachar.", "warning")
        return redirect(url_for("repartidor.ruta"))
    if not pedido.requiere_reparto:
        flash("Este pedido es para recoger; no se despacha por delivery.", "warning")
        return redirect(url_for("repartidor.ruta"))
    if not _es_admin_operativo() and pedido.repartidor_id not in (None, current_user.id):
        flash("Este pedido no está asignado a ti.", "danger")
        return redirect(url_for("repartidor.ruta"))

    if not pedido.repartidor_id and not _requiere_disponible_para_nuevo_trabajo():
        return redirect(url_for("repartidor.ruta"))

    if not pedido.repartidor_id:
        if _es_admin_operativo():
            flash("Asigna un repartidor antes de despachar el pedido.", "warning")
            return redirect(url_for("repartidor.ruta"))
        pedido.repartidor_id = current_user.id

    try:
        avanzar_estado_pedido(pedido, actor_id=current_user.id, canal="repartidor")
        enviar_whatsapp_estado(pedido)
        db.session.commit()
    except (ValueError, Exception) as e:
        db.session.rollback()
        flash(f"No se pudo despachar el pedido: {e}", "danger")
        return redirect(url_for("repartidor.ruta"))

    try:
        from push_service import notify_order_state
        notify_order_state(pedido)
    except Exception:
        logger.exception("No se pudo enviar push al despachar pedido %s", pedido.id)
    flash(
        f"Pedido {pedido.numero_pedido} en ruta. "
        "El cliente recibió el aviso de salida. Envía el código cuando llegues.",
        "info",
    )
    return redirect(url_for("repartidor.ruta"))


@repartidor_bp.route("/pedidos/<int:pedido_id>/enviar-codigo", methods=["POST"])
@repartidor_required
def enviar_codigo_entrega(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.estado != "en_ruta":
        flash("El código solo se envía cuando el pedido está en ruta.", "warning")
        return redirect(url_for("repartidor.ruta"))
    if not pedido.requiere_reparto:
        flash("Este pedido es para recoger; no usa código de reparto.", "warning")
        return redirect(url_for("repartidor.ruta"))
    if not _es_admin_operativo() and pedido.repartidor_id not in (None, current_user.id):
        flash("Este pedido no está asignado a ti.", "danger")
        return redirect(url_for("repartidor.ruta"))
    if not pedido.repartidor_id:
        if _es_admin_operativo():
            flash("Asigna un repartidor antes de enviar el código.", "warning")
            return redirect(url_for("repartidor.ruta"))
        if not _requiere_disponible_para_nuevo_trabajo():
            return redirect(url_for("repartidor.ruta"))
        pedido.repartidor_id = current_user.id
    if not pedido.cliente or not pedido.cliente.telefono:
        flash("Este cliente no tiene teléfono para enviar el código.", "warning")
        return redirect(url_for("repartidor.ruta"))

    try:
        if not pedido.codigo_confirmacion:
            pedido.generar_codigo_confirmacion()
        if not enviar_whatsapp_codigo_entrega(pedido, actor_id=current_user.id):
            flash("No se pudo encolar el WhatsApp del código.", "danger")
            return redirect(url_for("repartidor.ruta"))
        db.session.commit()
        flash(f"Código de entrega enviado para {pedido.numero_pedido}.", "success")
    except Exception as exc:
        db.session.rollback()
        logger.exception("No se pudo enviar código de entrega del pedido %s", pedido.id)
        flash(f"No se pudo enviar el código: {exc}", "danger")
    return redirect(url_for("repartidor.ruta"))


@repartidor_bp.route("/pedidos/<int:pedido_id>/entregar", methods=["POST"])
@repartidor_required
def confirmar_entrega(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.estado != "en_ruta":
        flash("El pedido no está en ruta.", "warning")
        return redirect(url_for("repartidor.ruta"))
    if not pedido.requiere_reparto:
        flash("Este pedido es para recoger; debe cerrarse desde operación.", "warning")
        return redirect(url_for("repartidor.ruta"))
    if not _es_admin_operativo() and pedido.repartidor_id not in (None, current_user.id):
        flash("Este pedido no está asignado a ti.", "danger")
        return redirect(url_for("repartidor.ruta"))
    if not pedido.repartidor_id:
        if _es_admin_operativo():
            flash("Asigna un repartidor antes de cerrar la entrega.", "warning")
            return redirect(url_for("repartidor.ruta"))
        if not _requiere_disponible_para_nuevo_trabajo():
            return redirect(url_for("repartidor.ruta"))
        pedido.repartidor_id = current_user.id

    codigo_ingresado = request.form.get("codigo_confirmacion", "").strip()

    metodo_pago = normalizar_metodo_pago(pedido.metodo_pago)
    if metodo_pago == "bizum":
        bizum_recibido = bool(request.form.get("bizum_recibido"))
        if not pedido.pago_confirmado and not bizum_recibido:
            flash("Confirma que el Bizum fue recibido antes de marcar como entregado.", "warning")
            return redirect(url_for("repartidor.ruta"))
        # Antifraude: si el bizum aún no estaba pre-confirmado, exigimos
        # referencia (últimos 4 dígitos, importe o concepto) para que quede
        # rastro auditable en OrderEvent.
        if not pedido.pago_confirmado:
            ref = (request.form.get("bizum_referencia") or "").strip()
            if len(ref) < 3:
                flash(
                    "Para confirmar Bizum como repartidor tienes que añadir una referencia (mínimo 3 caracteres): "
                    "últimos 4 dígitos del teléfono o concepto.",
                    "warning",
                )
                return redirect(url_for("repartidor.ruta"))
    elif not bool(request.form.get("cobro_recibido")):
        flash("Confirma que recibiste el pago en efectivo antes de marcar como entregado.", "warning")
        return redirect(url_for("repartidor.ruta"))

    if pedido.codigo_confirmacion:
        ok, msg_codigo = pedido.confirmar_entrega_con_codigo(codigo_ingresado)
        if not ok:
            db.session.commit()  # guardar intentos_codigo
            flash(f"Código incorrecto: {msg_codigo}", "danger")
            return redirect(url_for("repartidor.ruta"))

    try:
        avanzar_estado_pedido(pedido, actor_id=current_user.id, canal="repartidor")
    except ValueError as e:
        flash(f"No se pudo confirmar la entrega: {e}", "danger")
        return redirect(url_for("repartidor.ruta"))

    if not pedido.pago_confirmado:
        detalle_pago = metodo_pago
        if metodo_pago == "bizum":
            referencia = (request.form.get("bizum_referencia") or "").strip()
            detalle_pago = "bizum confirmado por repartidor"
            if referencia:
                detalle_pago = f"{detalle_pago} ({referencia[:80]})"
        registrar_pago_pedido(
            pedido,
            actor_id=current_user.id,
            canal="repartidor",
            detalle=detalle_pago,
        )
    registrar_ingreso_pedido(pedido, registrado_por=current_user.id)

    generar_comision_entrega(pedido)
    award_points_on_delivery(pedido)

    AuditLog.registrar(
        current_user.id, "pedido_entregado", "order", pedido.id,
        detalle=f"{pedido.numero_pedido} total={pedido.total} repartidor={pedido.repartidor_id} puntos={pedido.puntos_ganados}",
        ip=request.remote_addr,
    )
    try:
        enviar_whatsapp_estado(pedido)
        solicitar_resena_pedido(pedido)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"Error al confirmar la entrega: {e}", "danger")
        return redirect(url_for("repartidor.ruta"))

    try:
        from push_service import notify_order_state
        notify_order_state(pedido)
    except Exception:
        logger.exception("No se pudo enviar push al entregar pedido %s", pedido.id)

    flash(f"Pedido {pedido.numero_pedido} entregado y confirmado.", "success")
    return redirect(url_for("repartidor.ruta"))


@repartidor_bp.route("/mis-comisiones")
@repartidor_required
def mis_comisiones():
    comisiones = StaffPayment.query.filter_by(user_id=current_user.id, tipo="comision")\
                                   .order_by(StaffPayment.creado_en.desc()).all()
    pendiente = sum(float(c.monto or 0) for c in comisiones if not c.pagado)
    cobrado = sum(float(c.monto or 0) for c in comisiones if c.pagado)
    return render_template("repartidor/comisiones.html",
                           comisiones=comisiones,
                           pendiente=pendiente, cobrado=cobrado)
