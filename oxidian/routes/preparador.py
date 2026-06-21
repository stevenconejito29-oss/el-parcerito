from flask import Blueprint, render_template, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from functools import wraps
import logging
from extensions import db, get_or_404
from models import Order, OrderEvent, User
from services import (avanzar_estado_pedido, distribuir_repartidor,
                      redistribuir_pendientes_sin_asignar,
                      sincronizar_proveedores_pedido, lineas_preparacion_interna)

preparador_bp = Blueprint("preparador", __name__)
logger = logging.getLogger(__name__)

ROLES_PREPARADOR = {"admin", "super_admin", "cocina", "preparacion"}


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


def _puede_operar_pedido(pedido):
    # Pedidos 100% del bar no aparecen en la cola del preparador interno:
    # el bar los prepara y nuestro personal solo gestiona el reparto.
    from services import es_pedido_solo_bar
    if es_pedido_solo_bar(pedido):
        return False
    if pedido.items.count() and all(
        (item.display_canal_preparacion or "cocina").strip().lower() == "almacen"
        for item in pedido.items
    ):
        return False
    if _es_admin_operativo() or pedido.preparador_id == current_user.id:
        return True
    if pedido.preparador_id is not None:
        return False
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
    modo_operativo = (
        "inmediato" if current_user.rol == "cocina"
        else "programado" if current_user.rol == "preparacion"
        else "completo"
    )
    if _es_admin_operativo():
        pendientes = Order.query.filter_by(estado="pendiente").order_by(Order.creado_en).all()
        armando = Order.query.filter_by(estado="armando").order_by(Order.creado_en).all()
    else:
        pendientes = Order.query.filter(
            Order.estado == "pendiente",
            db.or_(
                Order.preparador_id == current_user.id,
                Order.preparador_id.is_(None),
            ),
        ).order_by(Order.creado_en).all() if disponible else []
        armando = Order.query.filter_by(
            estado="armando",
            preparador_id=current_user.id,
        ).order_by(Order.creado_en).all()

    companeros = User.query.filter(
        User.rol.in_(["cocina", "preparacion", "admin"]),
        User.activo == True,
        User.id != current_user.id
    ).all()

    pendientes = [p for p in pendientes if _puede_operar_pedido(p)]
    armando = [p for p in armando if _puede_operar_pedido(p)]
    almacen_listo = {
        pedido.id: _almacen_listo(pedido)
        for pedido in pendientes + armando
        if _es_pedido_mixto(pedido)
    }

    pendientes_encargo  = sorted([p for p in pendientes if _es_encargo(p)],
                                  key=lambda p: min(
                                      (i.display_fecha_entrega for i in p.items
                                       if i.display_fecha_entrega),
                                      default=None
                                  ) or p.creado_en.date())
    pendientes_inmediato = [p for p in pendientes if not _es_encargo(p)]

    return render_template("preparador/pedidos.html",
                           pendientes=pendientes_inmediato,
                           pendientes_encargo=pendientes_encargo,
                           armando=armando,
                           companeros=companeros,
                           disponible=disponible,
                           modo_operativo=modo_operativo,
                           almacen_listo=almacen_listo,
                           lineas_preparacion_interna=lineas_preparacion_interna)


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
        distribuir_repartidor(pedido)
        from services import enviar_whatsapp_estado
        enviar_whatsapp_estado(pedido)
        db.session.commit()
    except (ValueError, Exception) as e:
        db.session.rollback()
        flash(f"No se pudo marcar como listo: {e}", "danger")
        return redirect(url_for("preparador.pedidos"))
    try:
        from push_service import notify_order_state, notify_roles
        notify_order_state(pedido)
        notify_roles(["repartidor"], "📦 Pedido listo para recoger",
                     f"#{pedido.numero_pedido} está listo.", url="/repartidor/ruta")
    except Exception:
        logger.exception("No se pudo enviar push al marcar listo pedido %s", pedido.id)
    flash(f"Pedido {pedido.numero_pedido} listo. Repartidor asignado automáticamente.", "success")
    return redirect(url_for("preparador.pedidos"))
