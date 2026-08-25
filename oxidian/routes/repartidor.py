from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user
from functools import wraps
import logging
import math
import os
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import joinedload
from extensions import db, get_or_404
from models import (
    Order, OrderEvent, OrderItem, User, AuditLog, StaffPayment, RiderLocation,
    normalizar_metodo_pago, utcnow, FavorRequest, FavorOffer,
)
from services import (
    asignar_repartidor_pedido,
    avanzar_estado_pedido,
    generar_comision_entrega,
    award_points_on_delivery,
    enviar_whatsapp_codigo_entrega,
    enviar_whatsapp_estado,
    registrar_pago_pedido,
    registrar_ingreso_pedido,
    redistribuir_listos_sin_repartidor,
    capacidad_repartidor,
    solicitar_resena_pedido,
    pedidos_activos_que_bloquean_modulo,
)

repartidor_bp = Blueprint("repartidor", __name__)
logger = logging.getLogger(__name__)


@repartidor_bp.before_request
def exigir_delivery_habilitado():
    from store_config import get_store_features

    if (
        not get_store_features()["delivery"]
        and pedidos_activos_que_bloquean_modulo("delivery") == 0
    ):
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
            pedido.zona_nombre_aplicada or "Sin zona",
            pedido.creado_en,
        )

    pedidos_ordenados = sorted(pedidos, key=_zone_sort_key)
    grupos = []
    current_zone = None
    current_zone_order = None
    current_pedidos = []

    for pedido in pedidos_ordenados:
        zona_nombre = pedido.zona_nombre_aplicada or "Sin zona"
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


def _borrar_ubicacion_si_sin_ruta(rider_id):
    if not rider_id:
        return
    activa = Order.query.filter_by(
        repartidor_id=rider_id,
        estado="en_ruta",
        tipo_entrega_cliente="delivery",
    ).first()
    cruce_activo = FavorRequest.query.filter(
        FavorRequest.assigned_rider_id == rider_id,
        FavorRequest.status.in_(("matched", "at_pickup", "picked_up", "in_transit")),
    ).first()
    if activa is None and cruce_activo is None:
        RiderLocation.query.filter_by(rider_id=rider_id).delete()


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
    # Filtro por zona asignada al repartidor (Fase 5). Si el repartidor no tiene
    # zona asignada explícita, mantiene el comportamiento anterior (ve todo).
    zona_asignada_id = getattr(current_user, "zona_repartidor_id", None)
    aplicar_filtro_zona = (
        zona_asignada_id is not None and not _es_admin_operativo()
    )
    if _es_admin_operativo():
        listos_q = Order.query.options(_eager_zona).filter_by(
            estado="listo", tipo_entrega_cliente="delivery"
        ).filter(Order.slot_id.is_(None))
        en_ruta_q = Order.query.options(_eager_zona).filter_by(
            estado="en_ruta", tipo_entrega_cliente="delivery"
        )
        listos = listos_q.order_by(Order.creado_en).all()
        en_ruta = en_ruta_q.order_by(Order.creado_en).all()
    else:
        if disponible:
            listos_propios_q = Order.query.options(_eager_zona).filter_by(
                estado="listo", repartidor_id=current_user.id, tipo_entrega_cliente="delivery"
            ).filter(Order.slot_id.is_(None))
            sin_asignar_q = Order.query.options(_eager_zona).filter_by(
                estado="listo", repartidor_id=None, tipo_entrega_cliente="delivery"
            ).filter(Order.slot_id.is_(None))
            if aplicar_filtro_zona:
                sin_asignar_q = sin_asignar_q.filter(Order.zona_id == zona_asignada_id)
            listos_propios = listos_propios_q.order_by(Order.creado_en).all()
            sin_asignar = sin_asignar_q.order_by(Order.creado_en).all()
            listos = listos_propios + sin_asignar
        else:
            listos = []

        # Un pedido ya despachado sólo es visible para quien asumió la ruta.
        # Mostrar en_ruta sin asignar a todos los repartidores exponía datos
        # del cliente y permitía que dos personas intentaran gestionarlo.
        filtros_en_ruta = [
            Order.estado == "en_ruta",
            Order.repartidor_id == current_user.id,
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

    cruces_activos = FavorRequest.query.filter(
        FavorRequest.assigned_rider_id == current_user.id,
        FavorRequest.status.in_(("matched", "at_pickup", "picked_up", "in_transit")),
    ).count()
    cruces_abiertos = FavorRequest.query.filter_by(status="open").count() if current_user.acepta_cruces else 0
    return render_template("repartidor/ruta.html",
                           listos_grouped=listos_grouped,
                           listos_count=listos_count,
                           en_ruta=en_ruta,
                           codigo_enviado_ids=_codigo_enviado_ids(en_ruta),
                           companeros=companeros,
                           disponible=disponible,
                           cruces_activos=cruces_activos,
                           cruces_abiertos=cruces_abiertos)


def _favor_price(raw):
    try:
        value = Decimal(str(raw or "").replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return value if Decimal("1") <= value <= Decimal("999") else None


@repartidor_bp.get("/favores")
@repartidor_required
def favores():
    from cruce_policy import get_cruce_policy, recommended_price, expire_stale_open_favors
    try:
        expire_stale_open_favors()
    except Exception:
        logger.exception("No se pudieron expirar Cruces caducados (repartidor)")
    abiertos = FavorRequest.query.filter_by(status="open").order_by(FavorRequest.created_at.asc()).all() if current_user.acepta_cruces else []
    propios = FavorRequest.query.filter(
        FavorRequest.assigned_rider_id == current_user.id,
        FavorRequest.status.in_(("matched", "at_pickup", "picked_up", "in_transit")),
    ).order_by(FavorRequest.matched_at.asc()).all()
    policy = get_cruce_policy()
    guide_prices = {row.id: recommended_price(policy, float(row.distance_km or 0)) for row in (*abiertos, *propios)}
    return render_template("repartidor/favores.html", abiertos=abiertos, propios=propios, disponible=_esta_disponible(), policy=policy, guide_prices=guide_prices)


@repartidor_bp.post("/favores/preferencia")
@repartidor_required
def preferencia_cruces():
    current_user.acepta_cruces = request.form.get("acepta_cruces") == "1"
    db.session.commit()
    flash("Preferencia de El Cruce actualizada.", "success")
    return redirect(url_for("repartidor.favores"))


@repartidor_bp.post("/favores/<public_id>/aceptar")
@repartidor_required
def aceptar_favor(public_id):
    if not current_user.acepta_cruces:
        flash("Activa El Cruce antes de aceptar solicitudes.", "warning")
        return redirect(url_for("repartidor.favores"))
    if not _requiere_disponible_para_nuevo_trabajo():
        return redirect(url_for("repartidor.favores"))
    row = FavorRequest.query.filter_by(public_id=public_id).with_for_update().first_or_404()
    if row.status != "open":
        flash("Ese Cruce ya no está disponible.", "warning")
    else:
        offer = FavorOffer.query.filter_by(request_id=row.id, rider_id=current_user.id).first()
        if not offer:
            offer = FavorOffer(request_id=row.id, rider_id=current_user.id, amount=row.offered_amount)
            db.session.add(offer)
        # Aceptar el importe significa que el rider se compromete a hacerlo a
        # ese precio. El Cruce sigue abierto para que el cliente compare y
        # elija; no se adjudica al primero que pulse.
        offer.amount, offer.status = row.offered_amount, "pending"
        offer.customer_counter_amount = None
        offer.customer_countered_at = None
        from cruce_policy import registrar_evento_favor
        registrar_evento_favor(row, "rider", "offer_matches_price",
                               actor_id=current_user.id,
                               actor_label=current_user.nombre,
                               amount=offer.amount)
        db.session.commit()
        try:
            from push_service import notify_user
            if row.customer_id:
                notify_user(row.customer_id, "🛵 Un rider acepta tu precio", f"Puedes elegirlo por {offer.amount:.2f} € o esperar otras propuestas.", url="/favor", tag=f"cruce-offer-{row.id}-{current_user.id}")
        except Exception:
            logger.exception("No se pudo notificar propuesta del favor %s", row.id)
        flash("Disponibilidad enviada. El cliente decidirá entre las propuestas.", "success")
    return redirect(url_for("repartidor.favores"))


@repartidor_bp.post("/favores/<public_id>/contraoferta")
@repartidor_required
def contraofertar_favor(public_id):
    if not current_user.acepta_cruces:
        flash("Activa El Cruce antes de enviar propuestas.", "warning")
        return redirect(url_for("repartidor.favores"))
    if not _requiere_disponible_para_nuevo_trabajo():
        return redirect(url_for("repartidor.favores"))
    amount = _favor_price(request.form.get("amount"))
    try: eta = int(request.form.get("eta_minutes", 0))
    except (TypeError, ValueError): eta = 0
    row = FavorRequest.query.filter_by(public_id=public_id).with_for_update().first_or_404()
    from cruce_policy import get_cruce_policy, recommended_price
    minimum = recommended_price(get_cruce_policy(), float(row.distance_km or 0))
    if row.status != "open":
        flash("Este Cruce ya no admite propuestas.", "warning")
    elif not amount or amount < minimum or not 1 <= eta <= 480:
        flash("La contraoferta o el tiempo estimado no son válidos.", "warning")
    else:
        offer = FavorOffer.query.filter_by(request_id=row.id, rider_id=current_user.id).first()
        if not offer:
            offer = FavorOffer(request_id=row.id, rider_id=current_user.id)
            db.session.add(offer)
        offer.amount, offer.eta_minutes = amount, eta
        offer.note = request.form.get("note", "").strip()[:250]
        offer.customer_counter_amount = None
        offer.customer_countered_at = None
        offer.status = "pending"
        from cruce_policy import registrar_evento_favor
        registrar_evento_favor(row, "rider", "rider_countered",
                               actor_id=current_user.id,
                               actor_label=current_user.nombre,
                               amount=amount, note=f"ETA {eta} min")
        db.session.commit()
        try:
            from push_service import notify_user
            if row.customer_id:
                notify_user(row.customer_id, "💬 Tienes una propuesta para tu Cruce", f"Contraoferta: {amount:.2f} € · llegada aproximada en {eta} min.", url="/favor", tag=f"cruce-offer-{row.id}-{current_user.id}")
        except Exception:
            logger.exception("No se pudo notificar contraoferta del favor %s", row.id)
        flash("Contraoferta enviada al cliente.", "success")
    return redirect(url_for("repartidor.favores"))


@repartidor_bp.post("/favores/<public_id>/aceptar-contraoferta")
@repartidor_required
def aceptar_contraoferta_cliente(public_id):
    """Cierra de forma atómica la propuesta que el cliente hizo a este rider."""
    if not _requiere_disponible_para_nuevo_trabajo():
        return redirect(url_for("repartidor.favores"))
    row = FavorRequest.query.filter_by(public_id=public_id).with_for_update().first_or_404()
    offer = FavorOffer.query.filter_by(request_id=row.id, rider_id=current_user.id, status="pending").first()
    from cruce_policy import rider_can_be_assigned, registrar_evento_favor
    can_assign, unavailable_reason = rider_can_be_assigned(current_user.id)
    if row.status != "open" or not offer or offer.customer_counter_amount is None:
        flash("La propuesta ya no está disponible.", "warning")
    elif not can_assign:
        flash(unavailable_reason, "warning")
    else:
        agreed = offer.customer_counter_amount
        row.status, row.assigned_rider_id, row.agreed_amount, row.matched_at = "matched", current_user.id, agreed, utcnow()
        offer.amount, offer.status = agreed, "accepted"
        rejected_rider_ids = []
        for candidate in row.offers:
            if candidate.id != offer.id:
                if candidate.status == "pending":
                    rejected_rider_ids.append(candidate.rider_id)
                candidate.status = "rejected"
        registrar_evento_favor(row, "rider", "accepted_customer_counter",
                               actor_id=current_user.id,
                               actor_label=current_user.nombre,
                               amount=agreed)
        db.session.commit()
        try:
            from push_service import notify_user
            for rider_id in set(rejected_rider_ids):
                notify_user(rider_id, "Cruce asignado a otro rider",
                            "El cliente cerró el acuerdo con otra propuesta.",
                            url="/repartidor/favores",
                            tag=f"cruce-lost-{row.id}-{rider_id}")
        except Exception:
            logger.exception("No se pudo notificar riders descartados %s", row.id)
        try:
            from push_service import notify_user
            if row.customer_id:
                notify_user(row.customer_id, "✅ Precio acordado", f"El rider aceptó tu propuesta de {agreed:.2f} €.", url="/favor", tag=f"cruce-matched-{row.id}", require_interaction=True)
        except Exception:
            logger.exception("No se pudo notificar acuerdo bilateral %s", row.id)
        flash("Aceptaste la propuesta. Ya puedes iniciar la recogida.", "success")
    return redirect(url_for("repartidor.favores"))


@repartidor_bp.post("/favores/<public_id>/retirar-propuesta")
@repartidor_required
def retirar_propuesta_favor(public_id):
    """El rider conserva la decisión final mientras no exista acuerdo."""
    row = FavorRequest.query.filter_by(public_id=public_id).with_for_update().first_or_404()
    offer = FavorOffer.query.filter_by(request_id=row.id, rider_id=current_user.id, status="pending").first()
    if row.status != "open" or not offer:
        flash("La propuesta ya no se puede retirar.", "warning")
    else:
        offer.status = "withdrawn"
        offer.customer_counter_amount = None
        offer.customer_countered_at = None
        from cruce_policy import registrar_evento_favor
        registrar_evento_favor(row, "rider", "offer_withdrawn",
                               actor_id=current_user.id,
                               actor_label=current_user.nombre,
                               amount=offer.amount)
        db.session.commit()
        try:
            from push_service import notify_user
            if row.customer_id:
                notify_user(row.customer_id, "Propuesta retirada", "Ese rider ya no está disponible; puedes elegir otra propuesta.", url="/favor", tag=f"cruce-offer-withdrawn-{offer.id}")
        except Exception:
            logger.exception("No se pudo notificar retirada de propuesta %s", offer.id)
        flash("Retiraste tu propuesta. El Cruce sigue disponible para otros riders.", "success")
    return redirect(url_for("repartidor.favores"))


@repartidor_bp.post("/favores/<public_id>/cancelar")
@repartidor_required
def cancelar_favor_rider(public_id):
    """Rider abandona un Cruce ya asignado. Requiere motivo y libera al cliente."""
    from cruce_policy import CANCELLABLE_BY_RIDER, _clean_reason, registrar_evento_favor
    row = FavorRequest.query.filter_by(
        public_id=public_id, assigned_rider_id=current_user.id
    ).with_for_update().first_or_404()
    if row.status not in CANCELLABLE_BY_RIDER:
        flash("Este Cruce ya no se puede cancelar desde aquí.", "warning")
        return redirect(url_for("repartidor.favores"))
    reason = _clean_reason(request.form.get("reason"))
    if not reason:
        flash("Escribe brevemente por qué debes cancelar (mínimo 4 caracteres).", "warning")
        return redirect(url_for("repartidor.favores"))
    row.status = "cancelled"
    row.cancelled_at = utcnow()
    row.cancelled_by = "rider"
    row.cancellation_reason = reason
    for offer in row.offers:
        if offer.rider_id == current_user.id:
            offer.status = "rejected"
    customer_id = row.customer_id
    registrar_evento_favor(row, "rider", "cancelled_by_rider",
                           actor_id=current_user.id,
                           actor_label=current_user.nombre,
                           note=reason)
    db.session.commit()
    try:
        from push_service import notify_user
        if customer_id:
            notify_user(customer_id, "⚠️ El rider canceló tu Cruce",
                        (reason or "Publícalo de nuevo para conseguir otro rider."),
                        url="/favor", tag=f"cruce-rider-cancel-{row.id}",
                        require_interaction=True)
    except Exception:
        logger.exception("No se pudo notificar cancelación por rider %s", row.id)
    flash("Cancelaste el Cruce. El cliente ha sido informado.", "info")
    return redirect(url_for("repartidor.favores"))


@repartidor_bp.post("/favores/<public_id>/estado")
@repartidor_required
def avanzar_favor(public_id):
    row = FavorRequest.query.filter_by(public_id=public_id, assigned_rider_id=current_user.id).with_for_update().first_or_404()
    transitions = {"matched":"at_pickup", "at_pickup":"picked_up", "picked_up":"in_transit", "in_transit":"delivered"}
    next_status = transitions.get(row.status)
    if not next_status:
        flash("El Cruce no admite otro cambio de estado.", "warning")
    else:
        if next_status == "delivered":
            uploaded = request.files.get("proof_photo")
            if uploaded and uploaded.filename:
                try:
                    from image_service import save_image
                    row.proof_photo_path = save_image(uploaded, "cruces")
                except Exception:
                    logger.exception("No se pudo guardar foto de entrega del Cruce %s", row.id)
                    flash("La foto no se pudo procesar; guarda una nueva o marca sin foto.", "warning")
                    return redirect(url_for("repartidor.favores"))
        row.status = next_status
        if next_status == "delivered": row.completed_at = utcnow()
        from cruce_policy import registrar_evento_favor
        registrar_evento_favor(row, "rider", f"status_{next_status}",
                               actor_id=current_user.id,
                               actor_label=current_user.nombre,
                               note=("Con foto de entrega" if row.proof_photo_path and next_status == "delivered" else None))
        db.session.commit()
        try:
            from push_service import notify_user
            labels = {"at_pickup":"El rider llegó al punto de recogida", "picked_up":"Tu encargo ya fue recogido", "in_transit":"Tu encargo va en camino", "delivered":"Cruce entregado"}
            if row.customer_id:
                notify_user(row.customer_id, f"📦 {labels[next_status]}", "Consulta el seguimiento desde tu app.", url="/favor", tag=f"favor-status-{row.id}")
        except Exception:
            logger.exception("No se pudo notificar estado del favor %s", row.id)
        flash("Estado actualizado.", "success")
    return redirect(url_for("repartidor.favores"))


@repartidor_bp.route("/ubicacion", methods=["POST", "DELETE"])
@repartidor_required
def actualizar_ubicacion():
    """Guarda un único punto GPS mientras el repartidor tiene una ruta activa.

    No crea historial y rechaza posiciones de admins, coordenadas inválidas,
    precisión inútil y tracking sin pedidos propios en ruta.
    """
    if current_user.rol != "repartidor":
        return jsonify({"ok": False, "error": "Solo disponible para repartidores."}), 403

    if request.method == "DELETE":
        RiderLocation.query.filter_by(rider_id=current_user.id).delete()
        db.session.commit()
        return jsonify({"ok": True, "tracking": False})

    tiene_ruta = Order.query.filter_by(
        repartidor_id=current_user.id,
        estado="en_ruta",
        tipo_entrega_cliente="delivery",
    ).first() is not None or FavorRequest.query.filter(
        FavorRequest.assigned_rider_id == current_user.id,
        FavorRequest.status.in_(("matched", "at_pickup", "picked_up", "in_transit")),
    ).first() is not None
    if not tiene_ruta:
        RiderLocation.query.filter_by(rider_id=current_user.id).delete()
        db.session.commit()
        return jsonify({"ok": False, "error": "No tienes entregas activas.", "tracking": False}), 409

    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get("lat"))
        lng = float(data.get("lng"))
        accuracy = float(data["accuracy_m"]) if data.get("accuracy_m") is not None else None
        heading = float(data["heading"]) if data.get("heading") is not None else None
        speed = float(data["speed_mps"]) if data.get("speed_mps") is not None else None
    except (TypeError, ValueError, OverflowError):
        return jsonify({"ok": False, "error": "Ubicación inválida."}), 400
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify({"ok": False, "error": "Coordenadas fuera de rango."}), 400
    if accuracy is not None and not (0 <= accuracy <= 5000):
        return jsonify({"ok": False, "error": "Precisión inválida."}), 400
    if accuracy is not None and accuracy > 250:
        return jsonify({"ok": False, "error": "Señal GPS demasiado imprecisa."}), 422

    location = db.session.get(RiderLocation, current_user.id)
    if location is None:
        location = RiderLocation(rider_id=current_user.id, lat=lat, lng=lng)
        db.session.add(location)
    location.lat = lat
    location.lng = lng
    location.accuracy_m = accuracy
    location.heading = heading if heading is not None and 0 <= heading <= 360 else None
    location.speed_mps = speed if speed is not None and 0 <= speed <= 100 else None
    location.updated_at = utcnow()
    current_user.marcar_activo()
    db.session.commit()
    return jsonify({"ok": True, "tracking": True, "updated_at": location.updated_at.isoformat() + "Z"})


@repartidor_bp.route("/ruta/optimizar", methods=["POST"])
@repartidor_required
def optimizar_ruta():
    """Orden vial real mediante Google Routes, con fallback explícito en UI."""
    api_key = (os.environ.get("GOOGLE_ROUTES_API_KEY") or "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "Optimización vial no configurada."}), 503
    data = request.get_json(silent=True) or {}
    ids = _parse_pedido_ids(data.get("pedido_ids") or [])
    try:
        origin = {"lat": float(data["origin"]["lat"]), "lng": float(data["origin"]["lng"])}
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "error": "Origen GPS inválido."}), 400
    if not (-90 <= origin["lat"] <= 90 and -180 <= origin["lng"] <= 180):
        return jsonify({"ok": False, "error": "Origen GPS fuera de rango."}), 400
    if not 2 <= len(ids) <= 10:
        return jsonify({"ok": False, "error": "Selecciona entre 2 y 10 paradas."}), 400

    query = Order.query.filter(
        Order.id.in_(ids),
        Order.tipo_entrega_cliente == "delivery",
        Order.estado.in_(("listo", "en_ruta")),
    )
    if not _es_admin_operativo():
        query = query.filter(Order.repartidor_id == current_user.id)
    orders = {order.id: order for order in query.all()}
    if len(orders) != len(ids):
        return jsonify({"ok": False, "error": "La ruta contiene pedidos no disponibles."}), 403
    if any(order.direccion_lat is None or order.direccion_lng is None for order in orders.values()):
        return jsonify({"ok": False, "error": "Faltan coordenadas en una o más entregas."}), 422

    stops = [orders[order_id] for order_id in ids]
    # Compute Routes optimiza únicamente los puntos intermedios. Fijamos como
    # destino la parada más alejada del origen para evitar que una selección
    # arbitraria obligue a terminar en mitad del recorrido.
    def distance_sq(order):
        lat_scale = math.cos(math.radians(origin["lat"]))
        return (
            (float(order.direccion_lat) - origin["lat"]) ** 2
            + ((float(order.direccion_lng) - origin["lng"]) * lat_scale) ** 2
        )
    destination = max(stops, key=distance_sq)
    intermediates = [order for order in stops if order.id != destination.id]
    def waypoint(lat, lng):
        return {"location": {"latLng": {"latitude": float(lat), "longitude": float(lng)}}}
    payload = {
        "origin": waypoint(origin["lat"], origin["lng"]),
        "destination": waypoint(destination.direccion_lat, destination.direccion_lng),
        "intermediates": [waypoint(order.direccion_lat, order.direccion_lng) for order in intermediates],
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "optimizeWaypointOrder": True,
        "languageCode": "es-ES",
        "units": "METRIC",
    }
    try:
        import requests
        response = requests.post(
            "https://routes.googleapis.com/directions/v2:computeRoutes",
            json=payload,
            headers={
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "routes.optimizedIntermediateWaypointIndex,routes.duration,routes.distanceMeters",
            },
            timeout=6,
        )
        response.raise_for_status()
        route = (response.json().get("routes") or [{}])[0]
        indexes = route.get("optimizedIntermediateWaypointIndex") or list(range(len(intermediates)))
        ordered = [intermediates[index].id for index in indexes] + [destination.id]
        return jsonify({
            "ok": True,
            "pedido_ids": ordered,
            "distance_m": route.get("distanceMeters"),
            "duration": route.get("duration"),
            "source": "google_routes",
        })
    except Exception:
        logger.exception("No se pudo optimizar la ruta vial del repartidor %s", current_user.id)
        return jsonify({"ok": False, "error": "El optimizador vial no está disponible."}), 502


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
    if not pedido.repartidor_id:
        if capacidad_repartidor(current_user.id) <= 0:
            flash(
                "Tu ruta alcanzó el máximo de pedidos simultáneos. "
                "Completa una entrega antes de tomar otra.",
                "warning",
            )
            return redirect(url_for("repartidor.ruta"))
    asignar_repartidor_pedido(
        pedido,
        current_user.id,
        actor_id=current_user.id,
        canal="repartidor",
        aceptado=True,
    )
    db.session.commit()
    flash(f"Pedido {pedido.numero_pedido} asignado a ti.", "success")
    return redirect(url_for("repartidor.ruta"))


def _parse_pedido_ids(raw_values):
    """Convierte los `pedido_ids[]` del form en enteros únicos válidos."""
    ids = []
    for v in raw_values:
        try:
            n = int(v)
            if n > 0 and n not in ids:
                ids.append(n)
        except (TypeError, ValueError):
            continue
    return ids


@repartidor_bp.route("/ruta/tomar-multiples", methods=["POST"])
@repartidor_required
def tomar_multiples():
    """Asigna varios pedidos al repartidor como una única ruta.

    Reglas:
        * Solo pedidos en estado `listo` con `tipo_entrega_cliente="delivery"`.
        * Solo si están sin asignar o ya asignados al repartidor actual.
        * Admin operativo no puede usarlo (debe asignar desde admin).
    Cuenta éxitos/omitidos y devuelve mensaje agregado.
    """
    if _es_admin_operativo():
        flash("Asigna los pedidos desde la cola administrativa.", "warning")
        return redirect(url_for("repartidor.ruta"))
    ids = _parse_pedido_ids(request.form.getlist("pedido_ids"))
    if not ids:
        flash("No seleccionaste ningún pedido.", "warning")
        return redirect(url_for("repartidor.ruta"))
    if not _requiere_disponible_para_nuevo_trabajo():
        return redirect(url_for("repartidor.ruta"))
    asignados, omitidos = 0, 0
    capacidad = capacidad_repartidor(current_user.id)
    for pid in ids:
        pedido = Order.query.filter_by(id=pid).with_for_update().first()
        if pedido is None or pedido.estado != "listo" or not pedido.requiere_reparto:
            omitidos += 1
            continue
        if pedido.repartidor_id not in (None, current_user.id):
            omitidos += 1
            continue
        if pedido.repartidor_id is None and capacidad <= 0:
            omitidos += 1
            continue
        if pedido.repartidor_id is None:
            capacidad -= 1
        asignar_repartidor_pedido(
            pedido,
            current_user.id,
            actor_id=current_user.id,
            canal="repartidor_lote",
            aceptado=True,
        )
        asignados += 1
    db.session.commit()
    if asignados:
        flash(
            f"{asignados} pedido{'s' if asignados != 1 else ''} asignado{'s' if asignados != 1 else ''} a tu ruta"
            + (f" ({omitidos} omitido{'s' if omitidos != 1 else ''})." if omitidos else "."),
            "success",
        )
    else:
        flash("Ningún pedido pudo asignarse (ya no están disponibles).", "warning")
    return redirect(url_for("repartidor.ruta"))


@repartidor_bp.route("/ruta/salir-multiples", methods=["POST"])
@repartidor_required
def salir_multiples():
    """Marca `en_ruta` a varios pedidos asignados al repartidor.

    Cada pedido avanza individualmente (avanzar_estado_pedido + WhatsApp).
    Si alguno falla, se registra el error pero el resto continúa — la ruta
    del repartidor no debe romperse porque un solo pedido tenga un
    problema puntual.
    """
    ids = _parse_pedido_ids(request.form.getlist("pedido_ids"))
    if not ids:
        flash("No seleccionaste ningún pedido.", "warning")
        return redirect(url_for("repartidor.ruta"))
    if not _es_admin_operativo() and not _requiere_disponible_para_nuevo_trabajo():
        return redirect(url_for("repartidor.ruta"))
    despachados, fallidos = 0, []
    capacidad = None if _es_admin_operativo() else capacidad_repartidor(current_user.id)
    for pid in ids:
        pedido = Order.query.filter_by(id=pid).with_for_update().first()
        if pedido is None or pedido.estado != "listo" or not pedido.requiere_reparto:
            fallidos.append(str(pid))
            continue
        if not _es_admin_operativo() and pedido.repartidor_id not in (None, current_user.id):
            fallidos.append(pedido.numero_pedido)
            continue
        if not pedido.repartidor_id:
            if _es_admin_operativo():
                fallidos.append(pedido.numero_pedido)
                continue
            if capacidad <= 0:
                fallidos.append(pedido.numero_pedido)
                continue
            asignar_repartidor_pedido(
                pedido,
                current_user.id,
                actor_id=current_user.id,
                canal="repartidor_lote",
                aceptado=True,
            )
            capacidad -= 1
        elif not _es_admin_operativo():
            asignar_repartidor_pedido(
                pedido,
                current_user.id,
                actor_id=current_user.id,
                canal="repartidor_lote",
                aceptado=True,
            )
        try:
            avanzar_estado_pedido(pedido, actor_id=current_user.id, canal="repartidor")
            enviar_whatsapp_estado(pedido)
            db.session.commit()
            despachados += 1
            try:
                from push_service import notify_order_state
                notify_order_state(pedido)
            except Exception:
                logger.exception("push notify_order_state al despachar %s", pedido.id)
        except Exception as e:
            db.session.rollback()
            logger.warning("Fallo despachando %s en ruta múltiple: %s", pedido.id, e)
            fallidos.append(pedido.numero_pedido)
    if despachados:
        msg = f"{despachados} pedido{'s' if despachados != 1 else ''} en ruta."
        if fallidos:
            msg += f" No se pudo despachar: {', '.join(fallidos)}."
        flash(msg, "info")
    else:
        flash("Ningún pedido pudo despacharse.", "warning")
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
        if capacidad_repartidor(current_user.id) <= 0:
            flash(
                "Tu ruta alcanzó el máximo de pedidos simultáneos. "
                "Completa una entrega antes de iniciar otra.",
                "warning",
            )
            return redirect(url_for("repartidor.ruta"))
        asignar_repartidor_pedido(
            pedido,
            current_user.id,
            actor_id=current_user.id,
            canal="repartidor",
            aceptado=True,
        )
    elif not _es_admin_operativo():
        asignar_repartidor_pedido(
            pedido,
            current_user.id,
            actor_id=current_user.id,
            canal="repartidor",
            aceptado=True,
        )

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
        asignar_repartidor_pedido(
            pedido,
            current_user.id,
            actor_id=current_user.id,
            canal="repartidor",
            aceptado=True,
        )
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
        # Guard reforzado contra race: exigir que el repartidor haya usado
        # antes el botón "Tomar" o "Salir a entregar" (que asigna repartidor_id).
        # Cerrar entrega "en frío" sin flujo previo se rechaza — evita que un
        # repartidor entregue pedidos que no había asumido formalmente.
        flash(
            "Antes de cerrar la entrega debes tomar el pedido con «Salir a entregar». "
            "Si el pedido no aparece en tu ruta, contacta con operación.",
            "warning",
        )
        return redirect(url_for("repartidor.ruta"))

    codigo_ingresado = request.form.get("codigo_confirmacion", "").strip()

    metodo_pago = normalizar_metodo_pago(pedido.metodo_pago)
    if not metodo_pago:
        flash(
            "El pedido no tiene un método de pago válido. Operación debe corregirlo antes de entregar.",
            "danger",
        )
        return redirect(url_for("repartidor.ruta"))
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
    elif metodo_pago == "tarjeta":
        if not pedido.pago_confirmado and not bool(request.form.get("tarjeta_cobrada")):
            flash("Confirma el cobro aprobado en el terminal antes de entregar.", "warning")
            return redirect(url_for("repartidor.ruta"))
    elif not bool(request.form.get("cobro_recibido")):
        flash("Confirma que recibiste el pago en efectivo antes de marcar como entregado.", "warning")
        return redirect(url_for("repartidor.ruta"))

    if not pedido.codigo_confirmacion:
        try:
            pedido.generar_codigo_confirmacion()
            enviado = enviar_whatsapp_codigo_entrega(pedido, actor_id=current_user.id)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.exception("No se pudo regenerar código de entrega del pedido %s", pedido.id)
            flash(f"Este pedido no tenía código de entrega y no se pudo generar: {exc}", "danger")
            return redirect(url_for("repartidor.ruta"))
        if enviado:
            flash("El pedido no tenía código. Generamos uno nuevo y lo enviamos al cliente.", "warning")
        else:
            flash("El pedido no tenía código. Generamos uno nuevo; envíalo manualmente desde la ruta.", "warning")
        return redirect(url_for("repartidor.ruta"))

    ok, msg_codigo = pedido.confirmar_entrega_con_codigo(codigo_ingresado)
    if not ok:
        db.session.commit()  # guardar intentos_codigo
        # msg_codigo ya es mensaje completo ("Código incorrecto. N intentos restantes",
        # "El código ha expirado", "Demasiados intentos fallidos") — no prefijar.
        flash(msg_codigo, "danger")
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
        elif metodo_pago == "tarjeta":
            referencia = (request.form.get("tarjeta_referencia") or "").strip()
            detalle_pago = "tarjeta confirmada por repartidor"
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
        _borrar_ubicacion_si_sin_ruta(pedido.repartidor_id)
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


@repartidor_bp.route("/pedidos/<int:pedido_id>/no-entregado", methods=["POST"])
@repartidor_required
def marcar_no_entregado(pedido_id):
    """Escape para el repartidor cuando la entrega no es posible.

    Casos: cliente no está en la dirección, no responde al teléfono, código
    de confirmación bloqueado por 3 intentos fallidos, o rechaza el pedido.
    Restaura stock, cancela el pedido y registra el motivo. Es la única forma
    de sacar el pedido del limbo `en_ruta`/`listo` cuando no hay entrega física.
    """
    from services import cancelar_pedido_operativo, registrar_evento_pedido
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.repartidor_id != current_user.id and not _es_admin_operativo():
        flash("Este pedido no está asignado a ti.", "danger")
        return redirect(url_for("repartidor.ruta"))
    if pedido.estado in ("entregado", "cancelado"):
        flash(f"El pedido {pedido.numero_pedido} ya estaba en estado {pedido.estado}.", "warning")
        return redirect(url_for("repartidor.ruta"))
    if pedido.estado not in ("listo", "en_ruta"):
        flash("Solo puedes reportar no-entrega si el pedido está listo o en ruta.", "danger")
        return redirect(url_for("repartidor.ruta"))
    motivo = (request.form.get("motivo") or "").strip()[:300] or "Cliente no disponible en la dirección"
    registrar_evento_pedido(
        pedido,
        "pedido_no_entregado",
        actor_id=current_user.id,
        estado_anterior=pedido.estado,
        estado_nuevo="cancelado",
        canal="repartidor",
        detalle=motivo,
        metadata={"repartidor_id": current_user.id, "motivo": motivo},
    )
    try:
        cancelar_pedido_operativo(
            pedido,
            actor_id=current_user.id,
            canal="repartidor_no_entregado",
            detalle=f"No entregado: {motivo}",
        )
        _borrar_ubicacion_si_sin_ruta(pedido.repartidor_id)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("no_entregado falló para pedido %s", pedido.id)
        flash(f"Error al reportar no-entrega: {e}", "danger")
        return redirect(url_for("repartidor.ruta"))
    AuditLog.registrar(
        current_user.id,
        "pedido_no_entregado",
        "order",
        entity_id=pedido.id,
        detalle=motivo,
        ip=request.remote_addr,
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    flash(
        f"Pedido {pedido.numero_pedido} marcado como no entregado. "
        "Stock restaurado. Devuelve el pedido al local.",
        "warning",
    )
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


# ═══════════════════════════════════════════════════════════════════
# Módulo delivery por franjas horarias — endpoints repartidor.
# Toggle: delivery_franjas_activo. Devuelve 404 si está OFF.
# Self-assign: el repartidor "toma" una franja libre y aparece asignado
# hasta el max_repartidores de la franja. UI dedicada llegará después.
# ═══════════════════════════════════════════════════════════════════

def _franjas_modulo_activo() -> bool:
    from store_config import get_store_value
    return str(get_store_value("delivery_franjas_activo", "0")).strip() in ("1", "true", "True")


@repartidor_bp.route("/franjas/panel", methods=["GET"])
@repartidor_required
def franjas_panel():
    """Vista HTML del panel repartidor de franjas.

    Datos cargados por JS desde franjas_listar (JSON). Si el módulo está
    apagado, la fetch devuelve 404 y la UI muestra el aviso correspondiente.
    """
    return render_template("repartidor/franjas.html")


@repartidor_bp.route("/franjas", methods=["GET"])
@repartidor_required
def franjas_listar():
    if not _franjas_modulo_activo():
        abort(404)
    from delivery_slots_service import (listar_franjas_admin, _repartidores_activos,
                                        estado_operativo, pedidos_por_salida)
    from models import SlotRepartidor
    from datetime import date as _date, timedelta as _td

    from business_time import business_today
    hoy = business_today()
    slots = listar_franjas_admin(hoy, hoy + _td(days=6))
    # Marca "mías" y "libres" para cada franja.
    ids = [s.id for s in slots]
    mias_ids = set()
    if ids:
        mias = (
            SlotRepartidor.query
            .filter(
                SlotRepartidor.slot_id.in_(ids),
                SlotRepartidor.repartidor_id == current_user.id,
                SlotRepartidor.liberado_en.is_(None),
            )
            .all()
        )
        mias_ids = {m.slot_id for m in mias}
    salida = []
    # Conteos de pedidos por slot en una sola query (evita N+1 en pantalla).
    conteos_totales: dict[int, int] = {}
    conteos_listos: dict[int, int] = {}
    if ids:
        rows_total = (
            db.session.query(Order.slot_id, db.func.count(Order.id))
            .filter(Order.slot_id.in_(ids), Order.estado != "cancelado")
            .group_by(Order.slot_id).all()
        )
        conteos_totales = {slot_id: n for slot_id, n in rows_total}
        rows_listos = (
            db.session.query(Order.slot_id, db.func.count(Order.id))
            .filter(Order.slot_id.in_(ids), Order.estado == "listo",
                    Order.tipo_entrega_cliente == "delivery")
            .group_by(Order.slot_id).all()
        )
        conteos_listos = {slot_id: n for slot_id, n in rows_listos}

    for s in slots:
        if not s.activo:
            continue
        activos = _repartidores_activos(s.id)
        salida.append({
            "id": s.id,
            "fecha": s.fecha.isoformat(),
            "hora_inicio": s.hora_inicio.strftime("%H:%M"),
            "hora_fin": s.hora_fin.strftime("%H:%M"),
            "capacidad_max": s.capacidad_max,
            "max_repartidores": s.max_repartidores,
            "repartidores_activos": activos,
            "tomada_por_mi": s.id in mias_ids,
            "llena_de_repartidores": activos >= s.max_repartidores and s.id not in mias_ids,
            "pedidos_total": conteos_totales.get(s.id, 0),
            "pedidos_listos": conteos_listos.get(s.id, 0),
            "operativa": estado_operativo(s),
            "pedidos_por_salida": pedidos_por_salida(),
        })
    return jsonify({"franjas": salida})


@repartidor_bp.route("/franjas/<int:slot_id>/tomar", methods=["POST"])
@repartidor_required
def franjas_tomar(slot_id):
    if not _franjas_modulo_activo():
        abort(404)
    from delivery_slots_service import tomar_franja_repartidor, ResultadoRepartidor

    res = tomar_franja_repartidor(slot_id, current_user.id)
    if res.tipo == ResultadoRepartidor.NO_EXISTE:
        return jsonify({"error": "no_existe"}), 404
    if res.tipo in (ResultadoRepartidor.INACTIVA, ResultadoRepartidor.CERRADA,
                     ResultadoRepartidor.LLENA_DE_REPARTIDORES):
        db.session.rollback()
        return jsonify({"error": res.tipo.value}), 409
    db.session.commit()
    return jsonify({"resultado": res.tipo.value})


@repartidor_bp.route("/franjas/<int:slot_id>/liberar", methods=["POST"])
@repartidor_required
def franjas_liberar(slot_id):
    if not _franjas_modulo_activo():
        abort(404)
    from delivery_slots_service import liberar_franja_repartidor

    try:
        liberado = liberar_franja_repartidor(slot_id, current_user.id)
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 409
    db.session.commit()
    return jsonify({"liberado": liberado})


@repartidor_bp.route("/franjas/<int:slot_id>/pedidos", methods=["GET"])
@repartidor_required
def franjas_pedidos(slot_id):
    """Lista los pedidos asignados a una franja concreta con su estado.

    Base para la operativa de "batch route": el repartidor ve los N
    pedidos que le tocan en esta franja y puede lanzarlos todos a
    en_ruta con una sola acción.
    """
    if not _franjas_modulo_activo():
        abort(404)
    from models import DeliverySlot, SlotRepartidor

    slot = get_or_404(DeliverySlot, slot_id)
    asignada = SlotRepartidor.query.filter_by(
        slot_id=slot.id, repartidor_id=current_user.id, liberado_en=None
    ).first()
    if not asignada and not _es_admin_operativo():
        abort(403)
    pedidos = (
        Order.query
        .filter(
            Order.slot_id == slot.id,
            Order.estado != "cancelado",
        )
        .order_by(Order.creado_en)
        .all()
    )
    return jsonify({
        "slot": {
            "id": slot.id,
            "fecha": slot.fecha.isoformat(),
            "hora_inicio": slot.hora_inicio.strftime("%H:%M"),
            "hora_fin": slot.hora_fin.strftime("%H:%M"),
        },
        "pedidos": [
            {
                "id": p.id,
                "numero_pedido": p.numero_pedido,
                "estado": p.estado,
                "cliente": (p.cliente.nombre if p.cliente else ""),
                "direccion": p.direccion_entrega or "",
                "zona": p.zona_nombre_aplicada or "",
                "total": float(p.total or 0),
                "repartidor_id": p.repartidor_id,
            }
            for p in pedidos
        ],
    })


@repartidor_bp.route("/franjas/<int:slot_id>/iniciar-reparto", methods=["POST"])
@repartidor_required
def franjas_iniciar_reparto(slot_id):
    """Saca una tanda atómica y limitada durante la ventana de la franja."""
    if not _franjas_modulo_activo():
        abort(404)
    from models import DeliverySlot, SlotRepartidor
    from delivery_slots_service import estado_operativo, pedidos_por_salida

    slot = get_or_404(DeliverySlot, slot_id)
    asignada = SlotRepartidor.query.filter_by(
        slot_id=slot.id, repartidor_id=current_user.id, liberado_en=None
    ).first()
    if not asignada:
        abort(403)
    if not _requiere_disponible_para_nuevo_trabajo():
        return redirect(url_for("repartidor.franjas_panel"))
    if estado_operativo(slot)["estado"] != "activa":
        flash("Solo puedes iniciar una tanda durante el horario de la franja.", "warning")
        return redirect(url_for("repartidor.franjas_panel"))
    activos = Order.query.filter_by(
        repartidor_id=current_user.id, estado="en_ruta",
        tipo_entrega_cliente="delivery",
    ).count()
    disponibles = max(0, pedidos_por_salida() - activos)
    if not disponibles:
        flash("Entrega tu tanda actual antes de volver por más pedidos.", "warning")
        return redirect(url_for("repartidor.ruta"))
    pedidos = (
        Order.query
        .filter(
            Order.slot_id == slot.id,
            Order.estado == "listo",
            Order.tipo_entrega_cliente == "delivery",
        )
        .filter(db.or_(Order.repartidor_id.is_(None), Order.repartidor_id == current_user.id))
        .order_by(Order.creado_en)
        .with_for_update(skip_locked=True)
        .limit(disponibles)
        .all()
    )
    if not pedidos:
        flash("No hay pedidos listos para despachar en esta franja.", "info")
        return redirect(url_for("repartidor.franjas_panel"))

    despachados = 0
    for pedido in pedidos:
        if not pedido.repartidor_id:
            asignar_repartidor_pedido(
                pedido, current_user.id,
                actor_id=current_user.id, canal="franja", aceptado=True,
            )
        avanzar_estado_pedido(pedido, actor_id=current_user.id, canal="franja")
        despachados += 1

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("No se pudo despachar la franja completa. Reintenta.", "danger")
        return redirect(url_for("repartidor.franjas_panel"))

    # Notificaciones best-effort tras commit — no bloquean el flujo.
    for pedido in pedidos:
        if pedido.estado == "en_ruta":
            try:
                enviar_whatsapp_estado(pedido)
                from push_service import notify_order_state
                notify_order_state(pedido)
            except Exception:
                logger.exception("Fallo notificación batch para pedido %s", pedido.id)

    mensaje = f"Ruta iniciada: {despachados} pedidos en_ruta."
    flash(mensaje, "success")
    return redirect(url_for("repartidor.ruta"))


@repartidor_bp.route("/pedido/<int:pedido_id>/en-la-puerta", methods=["POST"])
@repartidor_required
def pedido_en_la_puerta(pedido_id):
    """Notifica al cliente por WhatsApp que el repartidor está en la puerta.

    Único mensaje WhatsApp del flujo de franjas (política anti-baneo Meta).
    Idempotente: el mismo pedido no genera dos notificaciones.
    Disponible siempre que exista el pedido y el repartidor sea el asignado
    o admin; funciona tanto en flujo inmediato como en franjas.
    """
    pedido = get_or_404(Order, pedido_id)
    if pedido.repartidor_id not in (None, current_user.id) and not _es_admin_operativo():
        abort(403)
    from delivery_slots_service import notificar_en_la_puerta

    notificar_en_la_puerta(pedido, actor_id=current_user.id)
    db.session.commit()
    return jsonify({"notificado": True})
