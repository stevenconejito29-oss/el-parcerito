import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from functools import wraps
from extensions import db, get_or_404
from models import Product, Stock, AdminFeature, Order, OrderEvent, OrderItem, User
from datetime import date, datetime
from services import (
    avanzar_estado_pedido,
    distribuir_repartidor,
    redistribuir_pendientes_sin_asignar,
)

staff_bp = Blueprint("staff", __name__)
logger = logging.getLogger(__name__)

# Tras la consolidación de roles, "staff" ya no existe como rol propio.
# El panel /staff/* lo opera el rol unificado "preparacion" (cubre cocina + almacén).
ROLES_STAFF = {"admin", "super_admin", "preparacion"}


def staff_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.rol not in ROLES_STAFF:
            flash("Acceso restringido.", "danger")
            return redirect(url_for("public.index"))
        inventario_endpoints = {"staff.inventario", "staff.registrar_entrada"}
        if (
            current_user.rol == "admin"
            and request.endpoint in inventario_endpoints
            and not AdminFeature.tiene_acceso(current_user.id, "stock")
        ):
            flash("No tienes acceso al módulo «stock».", "warning")
            return redirect(url_for("admin.dashboard"))
        return f(*args, **kwargs)
    return decorated


def _es_admin_operativo():
    return current_user.rol in ("admin", "super_admin")


def _esta_disponible():
    if _es_admin_operativo():
        return True
    usuario = db.session.get(User, current_user.id, populate_existing=True)
    return bool(usuario and usuario.disponible_para_pedidos)


def _es_pedido_almacen(pedido):
    """True si TODOS los ítems del pedido son de canal almacen."""
    items = list(pedido.items)
    if not items:
        return False
    return all(
        (i.display_canal_preparacion or "cocina").strip().lower() == "almacen"
        for i in items
    )


def _es_pedido_mixto(pedido):
    canales = {
        (i.display_canal_preparacion or "cocina").strip().lower()
        for i in pedido.items
    }
    return "almacen" in canales and "cocina" in canales


def _almacen_listo(pedido):
    evento = OrderEvent.query.filter(
        OrderEvent.pedido_id == pedido.id,
        OrderEvent.tipo.in_(["almacen_preparado", "almacen_reabierto"]),
    ).order_by(OrderEvent.id.desc()).first()
    return bool(evento and evento.tipo == "almacen_preparado")


def _notificar_preparador(pedido, titulo, mensaje):
    if not pedido.preparador_id:
        return
    try:
        from push_service import notify_user
        notify_user(pedido.preparador_id, titulo, mensaje, url="/preparador/pedidos")
    except Exception:
        logger.exception("No se pudo notificar al preparador del pedido %s", pedido.id)


# ─── DASHBOARD ────────────────────────────────────────────────────────────────

@staff_bp.route("/")
@staff_required
def dashboard():
    pedidos_pendientes = sum(
        1 for pedido in Order.query.filter(Order.estado.in_(["pendiente", "armando"])).all()
        if _es_pedido_almacen(pedido)
    )
    lotes = Stock.query.count()
    productos = Product.query.filter_by(activo=True).count()
    return render_template(
        "staff/dashboard.html",
        total_lotes=lotes,
        total_productos=productos,
        pedidos_pendientes=pedidos_pendientes,
    )


# ─── PEDIDOS DE ALMACÉN ───────────────────────────────────────────────────────

@staff_bp.route("/pedidos")
@staff_required
def pedidos():
    disponible = _esta_disponible()

    base = Order.query.filter(
        Order.estado.in_(["pendiente", "armando"]),
    )

    if _es_admin_operativo():
        pendientes = base.filter_by(estado="pendiente").order_by(Order.creado_en).all()
        empacando  = base.filter_by(estado="armando").order_by(Order.creado_en).all()
    else:
        if disponible:
            pendientes = base.filter(
                Order.estado == "pendiente",
                db.or_(
                    Order.preparador_id == current_user.id,
                    Order.preparador_id.is_(None),
                ),
            ).order_by(Order.creado_en).all()
        else:
            pendientes = []
        empacando = base.filter(
            Order.estado == "armando",
            Order.preparador_id == current_user.id,
        ).order_by(Order.creado_en).all()

    # Filtrar solo los que realmente son pedidos de almacén
    pendientes = [p for p in pendientes if _es_pedido_almacen(p)]
    empacando  = [p for p in empacando  if _es_pedido_almacen(p)]
    mixtos = [
        pedido
        for pedido in base.order_by(Order.creado_en).all()
        if _es_pedido_mixto(pedido)
    ]
    mixtos_estado = {pedido.id: _almacen_listo(pedido) for pedido in mixtos}

    companeros = User.query.filter(
        User.rol.in_(["preparacion", "admin"]),
        User.activo == True,
        User.id != current_user.id,
    ).all()

    return render_template(
        "staff/pedidos.html",
        pendientes=pendientes,
        empacando=empacando,
        companeros=companeros,
        disponible=disponible,
        mixtos=mixtos,
        mixtos_estado=mixtos_estado,
    )


@staff_bp.route("/pedidos/<int:pedido_id>/tomar", methods=["POST"])
@staff_required
def tomar_pedido(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if not _es_pedido_almacen(pedido):
        flash("Este pedido corresponde al equipo de cocina o preparación.", "danger")
        return redirect(url_for("staff.pedidos"))
    if pedido.estado != "pendiente":
        flash("Este pedido ya no está pendiente.", "warning")
        return redirect(url_for("staff.pedidos"))
    if not pedido.preparador_id and not _esta_disponible():
        flash("Ponte online para tomar pedidos nuevos.", "warning")
        return redirect(url_for("staff.pedidos"))
    if pedido.preparador_id and pedido.preparador_id != current_user.id and not _es_admin_operativo():
        flash("Este pedido ya está asignado a otro operador.", "warning")
        return redirect(url_for("staff.pedidos"))
    pedido.preparador_id = current_user.id
    try:
        db.session.commit()
        flash(f"Pedido {pedido.numero_pedido} asignado a ti.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error: {exc}", "danger")
    return redirect(url_for("staff.pedidos"))


@staff_bp.route("/pedidos/<int:pedido_id>/empacar", methods=["POST"])
@staff_required
def empacar_pedido(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if not _es_pedido_almacen(pedido):
        flash("Este pedido corresponde al equipo de cocina o preparación.", "danger")
        return redirect(url_for("staff.pedidos"))
    if pedido.estado != "pendiente":
        flash("El pedido no está en estado pendiente.", "warning")
        return redirect(url_for("staff.pedidos"))
    if not pedido.preparador_id and not _esta_disponible():
        flash("Ponte online para iniciar pedidos nuevos.", "warning")
        return redirect(url_for("staff.pedidos"))
    if not _es_admin_operativo() and pedido.preparador_id and pedido.preparador_id != current_user.id:
        flash("Este pedido está asignado a otro operador.", "danger")
        return redirect(url_for("staff.pedidos"))
    try:
        avanzar_estado_pedido(
            pedido,
            actor_id=current_user.id,
            canal="staff",
            validar_operativa=True,
        )
        if not pedido.preparador_id:
            pedido.preparador_id = current_user.id
        from services import enviar_whatsapp_estado
        enviar_whatsapp_estado(pedido)
        db.session.commit()
    except (ValueError, Exception) as e:
        db.session.rollback()
        flash(f"No se pudo iniciar el empaque: {e}", "danger")
        return redirect(url_for("staff.pedidos"))
    flash(f"Empacando {pedido.numero_pedido}.", "info")
    return redirect(url_for("staff.pedidos"))


@staff_bp.route("/pedidos/<int:pedido_id>/almacen-listo", methods=["POST"])
@staff_required
def marcar_almacen_listo(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.estado not in ("pendiente", "armando") or not _es_pedido_mixto(pedido):
        flash("Este pedido no tiene una parte mixta de almacén pendiente.", "warning")
        return redirect(url_for("staff.pedidos"))
    if not _es_admin_operativo() and not _esta_disponible():
        flash("Ponte online para confirmar un empaque nuevo.", "warning")
        return redirect(url_for("staff.pedidos"))
    if _almacen_listo(pedido):
        flash("La parte de almacén ya estaba confirmada.", "info")
        return redirect(url_for("staff.pedidos"))

    from services import registrar_evento_pedido
    registrar_evento_pedido(
        pedido,
        "almacen_preparado",
        actor_id=current_user.id,
        estado_anterior=pedido.estado,
        estado_nuevo=pedido.estado,
        canal="staff",
        detalle="Parte de almacén empacada en pedido mixto",
    )
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(f"No se pudo confirmar el empaque: {exc}", "danger")
        return redirect(url_for("staff.pedidos"))
    flash(f"Almacén de {pedido.numero_pedido} listo.", "success")
    _notificar_preparador(
        pedido,
        "Almacén listo",
        f"#{pedido.numero_pedido}: la parte de almacén ya está empacada.",
    )
    return redirect(url_for("staff.pedidos"))


@staff_bp.route("/pedidos/<int:pedido_id>/almacen-reabrir", methods=["POST"])
@staff_required
def reabrir_almacen(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.estado not in ("pendiente", "armando") or not _es_pedido_mixto(pedido):
        flash("Este pedido ya no admite correcciones de almacén.", "warning")
        return redirect(url_for("staff.pedidos"))
    if not _almacen_listo(pedido):
        flash("La parte de almacén ya estaba pendiente.", "info")
        return redirect(url_for("staff.pedidos"))

    from services import registrar_evento_pedido
    registrar_evento_pedido(
        pedido,
        "almacen_reabierto",
        actor_id=current_user.id,
        estado_anterior=pedido.estado,
        estado_nuevo=pedido.estado,
        canal="staff",
        detalle="Corrección de empaque en pedido mixto",
    )
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(f"No se pudo reabrir el empaque: {exc}", "danger")
        return redirect(url_for("staff.pedidos"))
    flash(f"Almacén de {pedido.numero_pedido} reabierto.", "warning")
    _notificar_preparador(
        pedido,
        "Almacén reabierto",
        f"#{pedido.numero_pedido}: la parte de almacén vuelve a estar pendiente.",
    )
    return redirect(url_for("staff.pedidos"))


@staff_bp.route("/pedidos/<int:pedido_id>/listo", methods=["POST"])
@staff_required
def marcar_listo(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if not _es_pedido_almacen(pedido):
        flash("Este pedido corresponde al equipo de cocina o preparación.", "danger")
        return redirect(url_for("staff.pedidos"))
    if pedido.estado != "armando":
        flash("El pedido debe estar en empaque.", "warning")
        return redirect(url_for("staff.pedidos"))
    if not _es_admin_operativo() and pedido.preparador_id != current_user.id:
        flash("Este pedido no está asignado a ti.", "danger")
        return redirect(url_for("staff.pedidos"))
    try:
        avanzar_estado_pedido(pedido, actor_id=current_user.id, canal="staff")
        distribuir_repartidor(pedido)
        from services import enviar_whatsapp_estado
        enviar_whatsapp_estado(pedido)
        db.session.commit()
    except (ValueError, Exception) as e:
        db.session.rollback()
        flash(f"No se pudo marcar como listo: {e}", "danger")
        return redirect(url_for("staff.pedidos"))
    try:
        from push_service import notify_order_state, notify_roles
        notify_order_state(pedido)
        notify_roles(["repartidor"], "📦 Pedido listo para recoger",
                     f"#{pedido.numero_pedido} empacado y listo.", url="/repartidor/ruta")
    except Exception:
        logger.exception("No se pudo enviar push al marcar listo pedido %s", pedido.id)
    flash(f"Pedido {pedido.numero_pedido} listo para despacho.", "success")
    return redirect(url_for("staff.pedidos"))


# ─── TOGGLE DISPONIBLE ────────────────────────────────────────────────────────

@staff_bp.route("/toggle-disponible", methods=["POST"])
@staff_required
def toggle_disponible():
    current_user.toggle_disponible()
    db.session.commit()
    pedidos_asignados = 0
    if current_user.en_linea:
        pedidos_asignados = redistribuir_pendientes_sin_asignar()
        if pedidos_asignados:
            db.session.commit()
    return jsonify({
        "ok": True,
        "en_linea": current_user.en_linea,
        "pedidos_asignados": pedidos_asignados,
    })


# ─── INVENTARIO ───────────────────────────────────────────────────────────────

@staff_bp.route("/inventario")
@staff_required
def inventario():
    productos = Product.query.filter_by(activo=True).order_by(Product.nombre).all()
    lotes = Stock.query.order_by(Stock.fecha_caducidad.asc()).all()
    return render_template("staff/inventario.html", productos=productos, lotes=lotes)


@staff_bp.route("/stock/entrada", methods=["POST"])
@staff_required
def registrar_entrada():
    producto_id = request.form.get("producto_id", type=int)
    cantidad = request.form.get("cantidad", type=int)
    lote = request.form.get("lote", "").strip()
    fecha_caducidad = request.form.get("fecha_caducidad")
    ubicacion = request.form.get("ubicacion", "").strip()

    if not producto_id or not cantidad or cantidad <= 0:
        flash("Datos inválidos.", "danger")
        return redirect(url_for("staff.inventario"))

    if not Product.query.filter_by(id=producto_id, activo=True).first():
        flash("Producto no encontrado o inactivo.", "danger")
        return redirect(url_for("staff.inventario"))

    caducidad_date = None
    if fecha_caducidad:
        try:
            caducidad_date = datetime.strptime(fecha_caducidad, "%Y-%m-%d").date()
        except ValueError:
            flash("Fecha de caducidad inválida. Usa formato YYYY-MM-DD.", "danger")
            return redirect(url_for("staff.inventario"))

    entrada = Stock(
        producto_id=producto_id,
        cantidad=cantidad,
        lote=lote or None,
        fecha_caducidad=caducidad_date,
        ubicacion=ubicacion or None,
    )
    db.session.add(entrada)
    try:
        db.session.commit()
        flash("Entrada de stock registrada.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al registrar entrada: {exc}", "danger")
    return redirect(url_for("staff.inventario"))
