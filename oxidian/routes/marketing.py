"""
Marketing blueprint — solo rutas únicas del rol marketing.
Promociones, cupones, afiliados y menú-config se gestionan desde /admin/
(ahora accesibles por el rol marketing gracias al decorador marketing_or_admin_required).
Las campañas masivas por WhatsApp se encolan en notification_outbox.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from functools import wraps
from datetime import date, timedelta
from sqlalchemy import func

from extensions import db, get_or_404
from models import (User, Order, Coupon, PointsLog, AffiliateCode,
                    AuditLog, CampanaMarketing, ZonaEntrega, Product, utcnow)
from services import encolar_whatsapp_generico

marketing_bp = Blueprint("marketing", __name__)

ROLES_MARKETING = {"admin", "super_admin"}  # marketing rol eliminado — acceso solo admin/superadmin


@marketing_bp.before_request
def _bloquear_puntos_si_feature_off():
    """Si FEATURE_PUNTOS=0, las rutas /marketing/puntos* devuelven 404 para
    no exponer panel de un módulo desactivado. Las otras rutas (campañas,
    cupones, etc.) siguen accesibles."""
    if "/puntos" not in (request.path or ""):
        return None
    from store_config import get_store_features
    if not get_store_features()["puntos"]:
        from flask import abort
        abort(404)
    return None


def marketing_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.rol not in ROLES_MARKETING:
            flash("Acceso restringido a administradores.", "danger")
            return redirect(url_for("public.index"))
        if current_user.rol == "admin":
            from models import AdminFeature
            if not AdminFeature.tiene_acceso(current_user.id, "marketing"):
                flash("No tienes acceso al módulo «marketing».", "warning")
                return redirect(url_for("admin.dashboard"))
        return f(*args, **kwargs)
    return decorated


# ─── DASHBOARD ───────────────────────────────────────────────────────────────

@marketing_bp.route("/dashboard")
@marketing_required
def dashboard():
    hoy = date.today()
    semana_ini = hoy - timedelta(days=7)
    semana_ant_ini = hoy - timedelta(days=14)
    semana_ant_fin = hoy - timedelta(days=8)

    nuevos_semana = User.query.filter(
        User.rol == "cliente",
        db.func.date(User.creado_en) >= semana_ini
    ).count()
    nuevos_semana_ant = User.query.filter(
        User.rol == "cliente",
        db.func.date(User.creado_en).between(semana_ant_ini, semana_ant_fin)
    ).count()

    cupones_activos = Coupon.query.filter_by(activo=True).count()
    afiliados_activos = AffiliateCode.query.filter_by(activo=True).count()

    puntos_circulacion = db.session.query(func.sum(User.puntos)).filter_by(
        rol="cliente", activo=True
    ).scalar() or 0

    top_cupones = Coupon.query.filter(Coupon.usos_actuales > 0)\
                              .order_by(Coupon.usos_actuales.desc()).limit(5).all()
    top_afiliados = AffiliateCode.query.filter(AffiliateCode.usos_actuales > 0)\
                                       .order_by(AffiliateCode.usos_actuales.desc()).limit(5).all()

    return render_template("marketing/dashboard.html",
                           nuevos_semana=nuevos_semana,
                           nuevos_semana_ant=nuevos_semana_ant,
                           cupones_activos=cupones_activos,
                           afiliados_activos=afiliados_activos,
                           puntos_circulacion=int(puntos_circulacion),
                           top_cupones=top_cupones,
                           top_afiliados=top_afiliados)


# ─── SISTEMA DE PUNTOS ───────────────────────────────────────────────────────

@marketing_bp.route("/puntos")
@marketing_required
def puntos():
    puntos_emitidos = db.session.query(func.sum(PointsLog.cantidad)).filter(
        PointsLog.tipo == "ganado"
    ).scalar() or 0
    puntos_canjeados = abs(db.session.query(func.sum(PointsLog.cantidad)).filter(
        PointsLog.tipo == "canjeado"
    ).scalar() or 0)
    puntos_circulacion = db.session.query(func.sum(User.puntos)).filter_by(
        rol="cliente", activo=True
    ).scalar() or 0

    top_clientes = User.query.filter_by(rol="cliente", activo=True)\
                             .order_by(User.puntos.desc()).limit(10).all()
    ultimos_movs = PointsLog.query.order_by(PointsLog.creado_en.desc()).limit(50).all()
    clientes = User.query.filter_by(rol="cliente", activo=True)\
                         .order_by(User.nombre).all()
    productos = Product.query.filter_by(activo=True)\
        .order_by(Product.canjeable_con_puntos.desc(), Product.nombre).all()
    productos_canjeables = [p for p in productos if p.canjeable_con_puntos]

    return render_template("marketing/puntos.html",
                           puntos_emitidos=int(puntos_emitidos),
                           puntos_canjeados=int(puntos_canjeados),
                           puntos_circulacion=int(puntos_circulacion),
                           top_clientes=top_clientes,
                           ultimos_movs=ultimos_movs,
                           clientes=clientes,
                           productos=productos,
                           productos_canjeables=productos_canjeables)


@marketing_bp.route("/puntos/productos/<int:producto_id>", methods=["POST"])
@marketing_required
def configurar_producto_puntos(producto_id):
    producto = get_or_404(Product, producto_id)
    activar = request.form.get("canjeable") == "1"
    try:
        puntos = int(request.form.get("puntos_para_canje") or 0)
    except (TypeError, ValueError):
        puntos = 0
    if activar and puntos <= 0:
        flash("Indica una cantidad de puntos mayor que cero.", "danger")
        return redirect(url_for("marketing.puntos"))
    if activar and producto.es_combo and producto.combo_items.filter_by(es_seleccionable=True).count():
        flash("Un combo con opciones seleccionables no puede ser canje directo.", "danger")
        return redirect(url_for("marketing.puntos"))
    producto.canjeable_con_puntos = activar
    producto.puntos_para_canje = puntos if activar else None
    AuditLog.registrar(current_user.id, "configurar_canje_producto", "product",
                       producto.id, detalle=f"activo={activar}, puntos={puntos}",
                       ip=request.remote_addr)
    try:
        db.session.commit()
        flash(f"Canje de '{producto.nombre}' actualizado.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"No se pudo guardar el canje: {exc}", "danger")
    return redirect(url_for("marketing.puntos"))


@marketing_bp.route("/puntos/ajustar", methods=["POST"])
@marketing_required
def ajustar_puntos():
    cliente_id = request.form.get("cliente_id", type=int)
    cantidad = request.form.get("cantidad", type=int)
    descripcion = request.form.get("descripcion", "Ajuste manual").strip()

    if not cliente_id or cantidad is None:
        flash("Datos inválidos.", "danger")
        return redirect(url_for("marketing.puntos"))

    if cantidad == 0:
        flash("La cantidad debe ser distinta de 0.", "warning")
        return redirect(url_for("marketing.puntos"))

    cliente = get_or_404(User, cliente_id)
    if cantidad > 0:
        cliente.sumar_puntos(cantidad, descripcion=descripcion)
    elif cantidad < 0:
        try:
            cliente.canjear_puntos(abs(cantidad))
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("marketing.puntos"))

    AuditLog.registrar(current_user.id, "ajuste_puntos", "user",
                       cliente_id, detalle=f"{cantidad} — {descripcion}",
                       ip=request.remote_addr)
    db.session.commit()
    flash(f"Puntos ajustados para {cliente.nombre}: {cantidad:+d}", "success")
    return redirect(url_for("marketing.puntos"))


# ─── CAMPAÑAS WhatsApp ───────────────────────────────────────────────────────

@marketing_bp.route("/campanas")
@marketing_required
def campanas():
    lista = CampanaMarketing.query.order_by(
        CampanaMarketing.creado_en.desc()
    ).all()
    zonas = ZonaEntrega.query.filter_by(activo=True).order_by(ZonaEntrega.nombre).all()
    return render_template("marketing/campanas.html", campanas=lista, zonas=zonas)


@marketing_bp.route("/campanas/crear", methods=["POST"])
@marketing_required
def crear_campana():
    titulo = request.form.get("titulo", "").strip()
    mensaje = request.form.get("mensaje", "").strip()
    filtro = request.form.get("filtro_audiencia", "todos")
    zona_id = request.form.get("zona_id", type=int) or None

    if not titulo or not mensaje:
        flash("Título y mensaje son obligatorios.", "danger")
        return redirect(url_for("marketing.campanas"))

    if len(mensaje) > 4096:
        flash("El mensaje no puede superar 4096 caracteres.", "danger")
        return redirect(url_for("marketing.campanas"))

    campana = CampanaMarketing(
        titulo=titulo,
        mensaje=mensaje,
        filtro_audiencia=filtro,
        zona_id=zona_id,
        creado_por=current_user.id,
    )
    db.session.add(campana)
    AuditLog.registrar(current_user.id, "crear_campana", "campana_marketing",
                       detalle=titulo, ip=request.remote_addr)
    try:
        db.session.commit()
        flash(f"Campaña '{titulo}' creada como borrador.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al crear campaña: {exc}", "danger")
    return redirect(url_for("marketing.campanas"))


@marketing_bp.route("/campanas/<int:campana_id>/enviar", methods=["POST"])
@marketing_required
def enviar_campana(campana_id):
    """Construye la audiencia y deja los WhatsApp en outbox persistente."""
    c = get_or_404(CampanaMarketing, campana_id)
    if c.estado == "enviado":
        flash("Esta campaña ya fue enviada.", "info")
        return redirect(url_for("marketing.campanas"))
    if c.estado == "enviando":
        flash("La campaña ya está en proceso de envío.", "warning")
        return redirect(url_for("marketing.campanas"))

    # Construir audiencia según el filtro
    query = User.query.filter_by(rol="cliente", activo=True).filter(User.telefono.isnot(None))

    if c.filtro_audiencia == "con_puntos":
        query = query.filter(User.puntos > 0)
    elif c.filtro_audiencia == "sin_compra_30":
        hace_30 = utcnow() - timedelta(days=30)
        sub = db.session.query(Order.cliente_id).filter(Order.creado_en >= hace_30).subquery()
        query = query.filter(~User.id.in_(sub))
    elif c.filtro_audiencia == "por_zona" and c.zona_id:
        pedidos_zona = db.session.query(Order.cliente_id).filter(
            Order.zona_id == c.zona_id
        ).subquery()
        query = query.filter(User.id.in_(pedidos_zona))

    destinatarios = [u for u in query.all() if u.telefono and u.telefono.strip()]
    if not destinatarios:
        flash("No hay destinatarios con teléfono para este filtro.", "warning")
        return redirect(url_for("marketing.campanas"))

    c.estado = "enviado"
    c.enviados = len(destinatarios)
    c.enviado_en = utcnow()
    AuditLog.registrar(current_user.id, "enviar_campana", "campana_marketing",
                       c.id, detalle=f"{len(destinatarios)} destinatarios", ip=request.remote_addr)
    for cliente in destinatarios:
        encolar_whatsapp_generico(
            cliente.telefono.strip(),
            c.mensaje,
            evento=f"marketing_campaign:{c.id}",
            user_id=cliente.id,
        )
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al encolar campaña: {exc}", "danger")
        return redirect(url_for("marketing.campanas"))

    flash(f"Campaña encolada para {len(destinatarios)} destinatarios. El worker enviará los mensajes.", "success")
    return redirect(url_for("marketing.campanas"))


@marketing_bp.route("/campanas/<int:campana_id>/eliminar", methods=["POST"])
@marketing_required
def eliminar_campana(campana_id):
    c = get_or_404(CampanaMarketing, campana_id)
    if c.estado == "enviado":
        flash("No se puede eliminar una campaña ya enviada.", "warning")
        return redirect(url_for("marketing.campanas"))
    db.session.delete(c)
    try:
        db.session.commit()
        flash("Campaña eliminada.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error: {exc}", "danger")
    return redirect(url_for("marketing.campanas"))
