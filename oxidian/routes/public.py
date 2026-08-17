import json
import os
import uuid
import random
import re
import inspect
import secrets
import unicodedata
import hashlib


def _strip_accents(s: str) -> str:
    """Normaliza a NFD y elimina marcas de acento — para búsqueda ACCENT-insensitive.

    Ej: 'Café' → 'Cafe', 'Jamón' → 'Jamon'. Postgres no tiene unaccent() sin la
    extensión, así que hacemos el fold en Python. Coste bajo (<100 productos)."""
    if not s:
        return ""
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()
from urllib.parse import quote
from datetime import datetime, date
from decimal import Decimal

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session, make_response
from flask_login import current_user
from flask import current_app
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from extensions import db, get_or_404, limiter, csrf
from models import (Product, Categoria, Order, OrderItem, Review, Coupon,
                     ComboItem, ProductExtraGroup, ProductExtraOption,
                     ProductPresentation, SiteConfig,
                     ZonaEntrega, MenuConfig, User, Proveedor, normalizar_metodo_pago,
                     AffiliateCode, IdempotencyKey, metadata_componente_combo,
                     metadata_item_pedido, utcnow as _utcnow,
                     internal_customer_email, FavorRequest, FavorOffer)
from idempotency import (request_idempotency_key, request_body_hash,
                          IDEMPOTENCY_TTL)
from services import (buscar_cliente_por_telefono, distribuir_pedido,
                       calcular_puntos_ganados,
                       cancelar_pedido_operativo,
                       enviar_whatsapp_estado, validar_radio_entrega,
                       asignar_zona_por_direccion,
                       asignar_zona_por_coordenadas,
                       registrar_uso_afiliado, get_puntos_config, get_pedido_minimo,
                       registrar_pedido_creado, sincronizar_proveedores_pedido,
                       encolar_notificaciones_proveedores_pedido,
                       aplicar_snapshot_zona_pedido,
                       tienda_abierta_en_horario)
from pricing_service import calcular_precio
from loyalty_service import (
    aplicar_canje_en_pedido,
    bloquear_cliente_puntos,
    enviar_saldo_puntos,
    solicitar_codigo,
)
from phone_utils import normalizar_telefono_cliente, telefono_local_ambiguo, telefono_valido
from store_config import (
    get_loyalty_terms,
    get_public_store_url,
    get_store_value,
    get_store_features,
    get_service_commission,
    is_service_mode,
)
from catalog_projection import build_catalog_projection
from product_options_service import validate_product_option_selection
from cart_lines_service import (
    line_signature,
    producto_id_from_line_key,
    iter_producto_ids,
    migrate_legacy_session,
)
from product_presentations_service import (
    presentation_metadata,
    product_presentation_catalog_payload,
    validate_product_presentation_selection,
)

public_bp = Blueprint("public", __name__)


@public_bp.get("/ayuda")
def chat():
    """Vista de conversación pública; en PWA funciona como pestaña propia."""
    return render_template("public/chat.html")


def _favor_visitor_hash():
    token = session.get("favor_visitor_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["favor_visitor_token"] = token
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _favor_amount(raw):
    try:
        amount = Decimal(str(raw or "").replace(",", ".")).quantize(Decimal("0.01"))
    except Exception:
        return None
    return amount if Decimal("1.00") <= amount <= Decimal("999.00") else None


def _favor_signature(rows):
    value = "|".join(
        f"{row.id}:{row.status}:{row.updated_at.isoformat()}:{','.join(f'{offer.id}-{offer.status}-{offer.customer_counter_amount or 0}-{offer.updated_at.isoformat()}' for offer in row.offers)}"
        for row in rows
    )
    return hashlib.sha256(value.encode()).hexdigest()


@public_bp.route("/favor", methods=["GET", "POST"])
@limiter.limit("12 per minute")
def favor():
    """El Cruce: encargos PWA con ambos extremos dentro de cobertura."""
    if not get_store_features().get("favores"):
        flash("El Cruce no está disponible en este momento.", "info")
        return redirect(url_for("public.index"))
    from cruce_policy import get_cruce_policy, distance_km, recommended_price
    policy = get_cruce_policy()
    visitor_hash = _favor_visitor_hash()
    if request.method == "POST":
        amount = _favor_amount(request.form.get("offered_amount"))
        values = {
            "customer_name": request.form.get("customer_name", "").strip()[:100],
            "customer_phone": (normalizar_telefono_cliente(request.form.get("customer_phone", "")) or "")[:30],
            "pickup_address": request.form.get("pickup_address", "").strip()[:350],
            "dropoff_address": request.form.get("dropoff_address", "").strip()[:350],
            "item_description": request.form.get("item_description", "").strip()[:500],
        }
        try:
            weight_kg = Decimal(str(request.form.get("weight_kg", "1")).replace(",", "."))
            declared_value = Decimal(str(request.form.get("declared_value", "0") or "0").replace(",", "."))
        except Exception:
            weight_kg, declared_value = Decimal("0"), Decimal("-1")
        package_type = request.form.get("package_type", "package")
        allowed_types = {"restaurant", "package", "keys", "shopping", "other"}
        active_count = FavorRequest.query.filter_by(visitor_token_hash=visitor_hash).filter(
            FavorRequest.status.in_(("open", "matched", "at_pickup", "picked_up", "in_transit"))
        ).count()
        if active_count >= policy["max_active"]:
            flash(f"Ya tienes {policy['max_active']} Cruces activos. Finaliza uno antes de publicar otro.", "warning")
        elif not amount or any(not value for value in values.values()) or not telefono_valido(values["customer_phone"]):
            flash("Completa origen, destino, encargo, contacto y una oferta válida.", "warning")
        elif package_type not in allowed_types or weight_kg <= 0 or weight_kg > policy["max_weight"]:
            flash(f"El encargo debe caber en una mochila y pesar máximo {policy['max_weight']:g} kg.", "warning")
        elif declared_value < 0 or declared_value > policy["max_value"]:
            flash(f"El valor declarado no puede superar {policy['max_value']:g} €.", "warning")
        elif request.form.get("accept_rules") != "1":
            flash("Confirma que el encargo cumple las reglas de transporte seguro.", "warning")
        elif values["pickup_address"].casefold() == values["dropoff_address"].casefold():
            flash("El punto de recogida y el destino deben ser diferentes.", "warning")
        else:
            pickup_geo = validar_radio_entrega(values["pickup_address"])
            dropoff_geo = validar_radio_entrega(values["dropoff_address"])
            if not pickup_geo.get("ok"):
                flash(f"Punto A: {pickup_geo.get('mensaje', 'dirección fuera de cobertura')}", "warning")
                return redirect(url_for("public.favor"))
            if not dropoff_geo.get("ok"):
                flash(f"Punto B: {dropoff_geo.get('mensaje', 'dirección fuera de cobertura')}", "warning")
                return redirect(url_for("public.favor"))
            kilometers = distance_km(pickup_geo["lat"], pickup_geo["lon"], dropoff_geo["lat"], dropoff_geo["lon"])
            guide_price = recommended_price(policy, kilometers)
            if amount < guide_price:
                flash(f"Para este recorrido la oferta mínima orientativa es {guide_price:.2f} €.", "warning")
                return redirect(url_for("public.favor"))
            customer_id = session.get("push_cliente_id")
            row = FavorRequest(
                visitor_token_hash=visitor_hash, offered_amount=amount,
                customer_id=int(customer_id) if customer_id else None, **values,
                pickup_lat=pickup_geo["lat"], pickup_lng=pickup_geo["lon"],
                dropoff_lat=dropoff_geo["lat"], dropoff_lng=dropoff_geo["lon"],
                pickup_zone_id=pickup_geo["zona_id"], dropoff_zone_id=dropoff_geo["zona_id"],
                package_type=package_type,
                pickup_reference=request.form.get("pickup_reference", "").strip()[:160] or None,
                weight_kg=weight_kg, declared_value=declared_value,
                distance_km=Decimal(str(kilometers)).quantize(Decimal("0.01")),
            )
            db.session.add(row)
            db.session.flush()
            from cruce_policy import registrar_evento_favor
            registrar_evento_favor(
                row, "customer", "created",
                actor_id=int(customer_id) if customer_id else None,
                actor_label=values["customer_name"], amount=amount,
                note=f"{values['pickup_address']} → {values['dropoff_address']}",
            )
            db.session.commit()
            try:
                from push_service import notify_user
                riders = User.query.filter_by(rol="repartidor", activo=True, acepta_cruces=True).all()
                for rider in riders:
                    notify_user(rider.id, "🤝 Nuevo Cruce disponible", f"{values['item_description'][:70]} · oferta {amount:.2f} €", url="/repartidor/favores", tag=f"cruce-open-{row.id}")
            except Exception:
                current_app.logger.exception("No se pudo encolar push del favor %s", row.id)
            flash("Cruce publicado. Te mostraremos aquí las propuestas de riders.", "success")
            return redirect(url_for("public.favor"))
    try:
        from cruce_policy import expire_stale_open_favors
        expired = expire_stale_open_favors()
    except Exception:
        current_app.logger.exception("No se pudieron expirar Cruces caducados")
        expired = []
    if expired:
        try:
            from push_service import notify_user
            for stale in expired:
                if stale.customer_id:
                    notify_user(stale.customer_id, "⌛ Tu Cruce expiró sin ofertas",
                                "Puedes publicarlo de nuevo ajustando el precio o el detalle.",
                                url="/favor", tag=f"cruce-expired-{stale.id}")
        except Exception:
            current_app.logger.exception("No se pudo notificar expiración de Cruces")
    rows = FavorRequest.query.filter_by(visitor_token_hash=visitor_hash).filter(
        FavorRequest.status.notin_(("delivered", "cancelled", "expired"))
    ).order_by(FavorRequest.created_at.desc()).all()
    guide_prices = {row.id: recommended_price(policy, float(row.distance_km or 0)) for row in rows}
    return render_template("public/favor.html", favors=rows, favor_signature=_favor_signature(rows), policy=policy, guide_prices=guide_prices)


@public_bp.post("/favor/<public_id>/cancelar")
@limiter.limit("10 per minute")
def cancel_favor(public_id):
    from cruce_policy import CANCELLABLE_BY_CUSTOMER, _clean_reason, registrar_evento_favor
    row = FavorRequest.query.filter_by(public_id=public_id, visitor_token_hash=_favor_visitor_hash()).with_for_update().first_or_404()
    if row.status not in CANCELLABLE_BY_CUSTOMER:
        flash("Este Cruce ya no puede cancelarse.", "warning")
        return redirect(url_for("public.favor"))
    was_assigned = row.status != "open"
    reason = _clean_reason(request.form.get("reason"))
    if was_assigned and not reason:
        flash("Explica brevemente por qué cancelas el Cruce en curso (mínimo 4 caracteres).", "warning")
        return redirect(url_for("public.favor"))
    previous_rider_id = row.assigned_rider_id
    row.status = "cancelled"
    row.cancelled_at = _utcnow()
    row.cancelled_by = "customer"
    row.cancellation_reason = reason or ("Cancelado antes de asignar" if not was_assigned else None)
    row.assigned_rider_id = None
    rider_ids = []
    for offer in row.offers:
        if offer.status == "pending":
            offer.status = "rejected"
            rider_ids.append(offer.rider_id)
        elif offer.status == "accepted":
            offer.status = "rejected"
    registrar_evento_favor(
        row, "customer", "cancelled_post_match" if was_assigned else "cancelled",
        actor_id=row.customer_id, actor_label=row.customer_name,
        note=row.cancellation_reason,
    )
    db.session.commit()
    try:
        from push_service import notify_user
        if was_assigned and previous_rider_id:
            notify_user(previous_rider_id, "❌ Cruce cancelado por el cliente",
                        (reason or "El cliente canceló el Cruce en curso."),
                        url="/repartidor/favores", tag=f"cruce-cancelled-{row.id}",
                        require_interaction=True)
        for rider_id in set(rider_ids):
            if rider_id == previous_rider_id:
                continue
            notify_user(rider_id, "Cruce cancelado",
                        "El cliente retiró la solicitud.",
                        url="/repartidor/favores", tag=f"cruce-cancelled-{row.id}")
    except Exception:
        current_app.logger.exception("No se pudo notificar cancelación del favor %s", row.id)
    flash("Cruce cancelado.", "success")
    return redirect(url_for("public.favor"))


@public_bp.post("/favor/<public_id>/ofertas/<int:offer_id>/aceptar")
@limiter.limit("10 per minute")
def accept_favor_offer(public_id, offer_id):
    row = FavorRequest.query.filter_by(public_id=public_id, visitor_token_hash=_favor_visitor_hash()).with_for_update().first_or_404()
    offer = FavorOffer.query.filter_by(id=offer_id, request_id=row.id, status="pending").first_or_404()
    from cruce_policy import rider_can_be_assigned, registrar_evento_favor
    can_assign, unavailable_reason = rider_can_be_assigned(offer.rider_id)
    if row.status != "open":
        flash("Otro rider ya fue asignado a este favor.", "warning")
    elif not can_assign:
        offer.status = "withdrawn"
        registrar_evento_favor(row, "system", "offer_withdrawn_unavailable",
                               actor_id=offer.rider_id, amount=offer.amount,
                               note=unavailable_reason)
        db.session.commit()
        flash(f"{unavailable_reason} Elige otra propuesta.", "warning")
    else:
        row.status, row.assigned_rider_id, row.agreed_amount, row.matched_at = "matched", offer.rider_id, offer.amount, _utcnow()
        rejected_rider_ids = []
        for candidate in row.offers:
            if candidate.id == offer.id:
                candidate.status = "accepted"
            else:
                if candidate.status == "pending":
                    rejected_rider_ids.append(candidate.rider_id)
                candidate.status = "rejected"
        registrar_evento_favor(row, "customer", "offer_accepted",
                               actor_id=row.customer_id, actor_label=row.customer_name,
                               amount=offer.amount,
                               note=f"Rider #{offer.rider_id}")
        db.session.commit()
        try:
            from push_service import notify_user
            notify_user(
                offer.rider_id, "✅ Tu propuesta fue aceptada",
                f"Cruce por {offer.amount:.2f} €. Ya puedes iniciar la recogida.",
                url="/repartidor/favores", tag=f"favor-matched-{row.id}", require_interaction=True,
            )
            for rider_id in set(rejected_rider_ids):
                notify_user(rider_id, "Cruce asignado a otro rider",
                            "El cliente eligió otra propuesta. Sigue disponible para nuevos Cruces.",
                            url="/repartidor/favores",
                            tag=f"cruce-lost-{row.id}-{rider_id}")
        except Exception:
            current_app.logger.exception("No se pudo notificar al rider del favor %s", row.id)
        flash("Propuesta aceptada. El rider ya puede iniciar el favor.", "success")
    return redirect(url_for("public.favor"))


@public_bp.post("/favor/<public_id>/ofertas/<int:offer_id>/contraoferta")
@limiter.limit("12 per minute")
def counter_favor_offer(public_id, offer_id):
    """El cliente negocia con un rider sin cerrar las demás propuestas."""
    from cruce_policy import get_cruce_policy, recommended_price
    row = FavorRequest.query.filter_by(
        public_id=public_id, visitor_token_hash=_favor_visitor_hash()
    ).with_for_update().first_or_404()
    offer = FavorOffer.query.filter_by(id=offer_id, request_id=row.id, status="pending").first_or_404()
    amount = _favor_amount(request.form.get("amount"))
    minimum = recommended_price(get_cruce_policy(), float(row.distance_km or 0))
    if row.status != "open":
        flash("Este Cruce ya fue acordado con un rider.", "warning")
    elif not amount or amount < minimum:
        flash(f"La propuesta debe ser de al menos {minimum:.2f} € para este recorrido.", "warning")
    else:
        offer.customer_counter_amount = amount
        offer.customer_countered_at = _utcnow()
        from cruce_policy import registrar_evento_favor
        registrar_evento_favor(row, "customer", "customer_countered",
                               actor_id=row.customer_id, actor_label=row.customer_name,
                               amount=amount, note=f"Rider #{offer.rider_id}")
        db.session.commit()
        try:
            from push_service import notify_user
            notify_user(offer.rider_id, "💬 El cliente te propone otro precio", f"Nueva propuesta: {amount:.2f} €.", url="/repartidor/favores", tag=f"cruce-customer-counter-{offer.id}", require_interaction=True)
        except Exception:
            current_app.logger.exception("No se pudo notificar contraoferta del cliente %s", offer.id)
        flash("Propuesta enviada. Puedes seguir comparando otros riders.", "success")
    return redirect(url_for("public.favor"))


@public_bp.get("/api/favor/cards")
@limiter.limit("30 per minute")
def favor_cards_fragment():
    """Fragmento HTML de las cards activas del visitante para HTMX polling."""
    if not get_store_features().get("favores"):
        return _json_no_store({"ok": False}, 404)
    from cruce_policy import get_cruce_policy, recommended_price, expire_stale_open_favors
    try:
        expire_stale_open_favors()
    except Exception:
        current_app.logger.exception("No se pudieron expirar Cruces caducados (fragment)")
    policy = get_cruce_policy()
    rows = FavorRequest.query.filter_by(visitor_token_hash=_favor_visitor_hash()).filter(
        FavorRequest.status.notin_(("delivered", "cancelled", "expired"))
    ).order_by(FavorRequest.created_at.desc()).all()
    guide_prices = {row.id: recommended_price(policy, float(row.distance_km or 0)) for row in rows}
    response = make_response(render_template(
        "public/_favor_cards.html", favors=rows, policy=policy, guide_prices=guide_prices,
    ))
    response.headers["Cache-Control"] = "no-store"
    return response


@public_bp.get("/api/favor/state")
@limiter.limit("30 per minute")
def favor_state():
    if not get_store_features().get("favores"):
        return _json_no_store({"ok": False}, 404)
    try:
        from cruce_policy import expire_stale_open_favors
        expire_stale_open_favors()
    except Exception:
        current_app.logger.exception("No se pudieron expirar Cruces caducados (state)")
    rows = FavorRequest.query.filter_by(visitor_token_hash=_favor_visitor_hash()).filter(
        FavorRequest.status.notin_(("delivered", "cancelled", "expired"))
    ).order_by(FavorRequest.updated_at.desc()).all()
    return _json_no_store({"ok": True, "signature": _favor_signature(rows), "count": len(rows)})

# TTL para el token que autoriza ver /pedido/<id>/confirmado desde la sesión
# del navegador. Suficiente para que el cliente pinche el link del WhatsApp,
# pero evita que la info del pedido quede accesible indefinidamente si el
# navegador queda abierto (kioscos, portátiles compartidos, etc.).
GUEST_ORDER_TOKEN_TTL_S = 24 * 3600


def _normalize_phone(raw):
    """Normaliza el teléfono que identifica de forma única a cada cliente."""
    return normalizar_telefono_cliente(raw)


def _whatsapp_phone_digits(raw):
    """Devuelve un telefono valido para wa.me, prefijando pais si falta."""
    phone = (raw or "").strip()
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return ""
    if phone.startswith("+"):
        return digits[:20]
    if digits.startswith("00"):
        return digits[2:22]

    country = SiteConfig.get(
        "WHATSAPP_COUNTRY_CODE",
        current_app.config.get("WHATSAPP_COUNTRY_CODE", "34"),
    )
    country_digits = re.sub(r"\D", "", country or "")
    if country_digits and len(digits) <= 10 and not digits.startswith(country_digits):
        digits = f"{country_digits}{digits}"
    return digits[:20]


def _cart_max_qty():
    from models import SiteConfig
    try:
        return max(1, int(SiteConfig.get("CART_MAX_QTY", current_app.config.get("CART_MAX_QTY", 99))))
    except (ValueError, TypeError):
        return 99


def _json_no_store(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


# La búsqueda de cliente por teléfono vive en services.buscar_cliente_por_telefono.
# Se importa arriba y se usa directamente — sin wrapper local.


def _normalizar_origen(raw):
    origen = str(raw or "").strip().lower()
    if origen == "propio":
        return origen
    if origen.startswith("proveedor:"):
        try:
            provider_id = int(origen.split(":", 1)[1])
        except (TypeError, ValueError):
            return None
        if provider_id > 0:
            return f"proveedor:{provider_id}"
    return None


def _proveedor_id_origen(origen):
    origen = _normalizar_origen(origen)
    if not origen or origen == "propio":
        return None
    return int(origen.split(":", 1)[1])


def _origen_logistico(origen):
    """Separa el dueño del stock del establecimiento que prepara el pedido."""
    origen = _normalizar_origen(origen) or "propio"
    proveedor_id = _proveedor_id_origen(origen)
    if not proveedor_id:
        return "propio"
    proveedor = db.session.get(Proveedor, proveedor_id)
    return "propio" if proveedor and proveedor.es_socio_capital else origen


def _origen_inventario_producto(producto):
    return (
        f"proveedor:{producto.proveedor_despachador_id}"
        if producto and producto.proveedor_despachador_id else "propio"
    )


def _establecimiento_para_origen(origen):
    origen = _normalizar_origen(origen)
    proveedor_id = _proveedor_id_origen(origen)
    if proveedor_id:
        proveedor = db.session.get(Proveedor, proveedor_id)
        if not proveedor:
            return None
        return {
            "origen": origen,
            "nombre": proveedor.nombre,
            "abierto": bool(proveedor.disponible_para_venta),
            "url": url_for("public.menu_bar", proveedor_id=proveedor.id),
        }
    if origen == "propio":
        return {
            "origen": origen,
            "nombre": SiteConfig.get("NOMBRE_NEGOCIO", "") or "Mi tienda",
            "abierto": True,
            "url": url_for("public.index"),
        }
    return None


def _producto_pertenece_al_vertical(producto):
    """Filtra productos por nicho activo (comida vs retail).

    Comida y retail son tiendas SEPARADAS. Un producto solo aparece si su
    `Product.vertical` coincide EXACTAMENTE con `SiteConfig.TIPO_TIENDA`.

    - `vertical="comida"` → visible SOLO si TIPO_TIENDA == "comida".
    - `vertical="producto"` → visible SOLO si TIPO_TIENDA == "producto".
    - `vertical="ambos"` (legacy) → invisible. Un producto sin nicho no cruza
      al otro; la migración de deploy convierte "ambos" al TIPO_TIENDA inicial.
    """
    if not producto:
        return False
    v = (getattr(producto, "vertical", None) or "").strip().lower()
    from models import SiteConfig
    tt = (SiteConfig.get("TIPO_TIENDA", "comida") or "comida").lower()
    return v == tt


def _producto_disponible_en_origen(producto, origen, cantidad=1):
    if producto and _delivery_family(producto) == "programado" and not _feature_enabled("pedidos_programados"):
        return False
    if producto and _programmed_date_expired(producto):
        return False
    if producto and not _fulfillment_options([producto]):
        return False
    if not _producto_pertenece_al_vertical(producto):
        return False
    # Coerción socio-capital: `_carrito_origen()` y otros callers pasan
    # "propio" como origen logístico (porque la tienda despacha), pero
    # `Product.pertenece_a_origen("propio")` chequea estrictamente
    # `proveedor_despachador_id`. Para socio-capital, la fila real vive
    # en ProveedorProducto → resolvemos al origen de INVENTARIO real del
    # producto para los chequeos de pertenencia y stock. Sin esto, cada
    # producto de socio devolvía "no está disponible" en checkout y en
    # todo call site que pase el origen logístico del carrito.
    origen_real = origen
    if (
        producto
        and origen == "propio"
        and getattr(producto, "proveedor_despachador_id", None)
    ):
        prov = producto.proveedor_despachador
        if prov and getattr(prov, "es_socio_capital", False):
            origen_real = f"proveedor:{producto.proveedor_despachador_id}"
    return bool(
        producto
        and producto.activo
        and producto.visible_ahora
        and producto.pertenece_a_origen(origen_real)
        and producto.disponible_para_venta_en_origen(origen_real, cantidad)
    )


def _stock_en_origen(producto, origen):
    return producto.stock_en_origen(origen)


def _producto_canjeable_en_origen(producto, origen, cantidad=1):
    if not _feature_enabled("puntos"):
        return False
    if (
        not producto
        or not producto.activo
        or not producto.canjeable_con_puntos
        or not producto.puntos_para_canje
        or not producto.visible_ahora
    ):
        return False
    if producto.es_combo and any(item.es_seleccionable for item in producto.combo_items):
        return False
    if producto.extra_groups.filter(
        ProductExtraGroup.activo.is_(True),
        ProductExtraGroup.min_selecciones > 0,
    ).first():
        return False
    return _producto_disponible_en_origen(producto, origen, cantidad)


def _feature_enabled(name):
    return bool(get_store_features().get(name))


def _product_fulfillment_modes(producto):
    mode = (getattr(producto, "modalidad_entrega", None) or "ambas").strip().lower()
    if mode == "delivery":
        return {"delivery"}
    if mode == "recogida":
        return {"recogida"}
    return {"delivery", "recogida"}


_FULFILLMENT_LABELS = {
    "delivery": {
        "emoji": "🛵",
        "label": "Envío a domicilio",
        "short": "Domicilio",
        "exclusive": "solo con envío a domicilio",
    },
    "recogida": {
        "emoji": "🏪",
        "label": "Recoger en local",
        "short": "Recoger",
        "exclusive": "solo para recoger en local",
    },
}


def _fulfillment_mode_label(mode, short=False):
    data = _FULFILLMENT_LABELS.get(mode)
    if not data:
        return str(mode or "")
    text = data["short"] if short else data["label"]
    return f"{data['emoji']} {text}"


def _product_fulfillment_badge(producto):
    modes = _product_fulfillment_modes(producto)
    if modes == {"delivery"}:
        data = _FULFILLMENT_LABELS["delivery"]
        return {"emoji": data["emoji"], "label": data["label"], "title": "Disponible solo con envío a domicilio"}
    if modes == {"recogida"}:
        data = _FULFILLMENT_LABELS["recogida"]
        return {"emoji": data["emoji"], "label": data["label"], "title": "Disponible solo para recoger"}
    return {"emoji": "🔁", "label": "Llevar o recoger", "title": "Disponible para llevar y recoger"}


def _fulfillment_blockers_for_mode(productos, mode):
    """Productos que no permiten la modalidad logística solicitada."""
    return [
        producto for producto in (productos or [])
        if producto and mode not in _product_fulfillment_modes(producto)
    ]


def _fulfillment_unavailable_reasons(productos, check_zone_availability=False):
    productos = [p for p in (productos or []) if p]
    reasons = {}
    features = get_store_features()
    for mode in ("delivery", "recogida"):
        blockers = _fulfillment_blockers_for_mode(productos, mode)
        if mode == "delivery" and not features.get("delivery", False):
            reasons[mode] = {
                "label": _fulfillment_mode_label(mode),
                "reason": "El módulo de delivery está desactivado.",
                "products": [],
            }
        elif (
            mode == "delivery"
            and check_zone_availability
            and not ZonaEntrega.query.filter_by(activo=True).first()
        ):
            reasons[mode] = {
                "label": _fulfillment_mode_label(mode),
                "reason": "El reparto está temporalmente sin zonas activas.",
                "products": [],
            }
        elif mode == "recogida" and not features.get("recogida", False):
            reasons[mode] = {
                "label": _fulfillment_mode_label(mode),
                "reason": "El módulo de recogida está desactivado.",
                "products": [],
            }
        elif blockers:
            reasons[mode] = {
                "label": _fulfillment_mode_label(mode),
                "reason": "Estos productos no permiten esa modalidad.",
                "products": blockers,
            }
    return reasons


def _fulfillment_options(productos=None):
    features = get_store_features()
    allowed = set()
    if features.get("delivery", False):
        allowed.add("delivery")
    if features.get("recogida", False):
        allowed.add("recogida")
    for producto in (productos or []):
        allowed &= _product_fulfillment_modes(producto)
    return [mode for mode in ("delivery", "recogida") if mode in allowed]


def _fulfillment_from_request(default=None, options=None):
    options = list(options if options is not None else _fulfillment_options())
    if not options:
        return None
    explicit = request.form.get("tipo_entrega_cliente")
    requested = (explicit or default or options[0]).strip().lower()
    if requested not in options:
        return None if explicit else options[0]
    return requested


def _establecimiento_abierto_checkout(origen, proveedor=None):
    origen = _origen_logistico(origen)
    proveedor_id = _proveedor_id_origen(origen)
    if proveedor_id:
        proveedor = proveedor or db.session.get(Proveedor, proveedor_id)
        return bool(proveedor and proveedor.disponible_para_venta), (
            "El establecimiento de este pedido está cerrado o ya no está activo."
        )
    cfg = {r.clave: r.valor for r in SiteConfig.query.filter(
        SiteConfig.clave.in_(["HORARIO_APERTURA", "HORARIO_CIERRE",
                              "TIENDA_FORZAR_CERRADA", "TIENDA_FORZAR_ABIERTA",
                              "TIENDA_MENSAJE_CIERRE"])
    ).all()}
    apertura = cfg.get("HORARIO_APERTURA", "09:00")
    cierre = cfg.get("HORARIO_CIERRE", "22:30")
    forzada = str(cfg.get("TIENDA_FORZAR_CERRADA", "0")).lower() in ("1", "true", "yes", "on")
    forzada_ab = str(cfg.get("TIENDA_FORZAR_ABIERTA", "0")).lower() in ("1", "true", "yes", "on")
    ahora = datetime.now().strftime("%H:%M")
    if tienda_abierta_en_horario(apertura, cierre, ahora, forzada, forzada_ab):
        return True, ""
    mensaje = (cfg.get("TIENDA_MENSAJE_CIERRE") or "").strip()
    from schedule_service import configured_schedule_context
    schedule = configured_schedule_context()
    fallback = f"La tienda está cerrada ahora. {schedule['today']}."
    if schedule["next_opening"]:
        fallback += f" Próxima apertura: {schedule['next_opening']}."
    return False, mensaje or fallback


def _metadata_item_con_origen(producto, metadata, origen):
    """Congela el origen aunque la firma nueva de models aún no esté integrada."""
    params = inspect.signature(metadata_item_pedido).parameters
    if "origen_operativo" in params:
        return metadata_item_pedido(
            producto,
            metadata,
            origen_operativo=origen,
        )
    data = metadata_item_pedido(producto, metadata)
    snapshot = data.setdefault("producto", {})
    snapshot["origen_operativo_key"] = origen
    snapshot["origen_operativo"] = "propio" if origen == "propio" else "proveedor"
    snapshot["proveedor_despachador_id"] = _proveedor_id_origen(origen)
    return data


def _descontar_stock_en_origen(producto, origen, cantidad, seleccion_item_ids=None):
    method = producto.descontar_stock_en_origen
    params = inspect.signature(method).parameters
    if producto.es_combo and seleccion_item_ids is not None:
        if "seleccion_item_ids" in params:
            return method(
                origen,
                cantidad,
                seleccion_item_ids=seleccion_item_ids,
            )
        if len(params) >= 3:
            return method(origen, cantidad, seleccion_item_ids)
    return method(origen, cantidad)


def _productos_canjeables_disponibles(origen, productos_carrito=None):
    """Catálogo canónico de recompensas válidas para este pedido.

    ``solo_canje`` implica precio cero por diseño, por lo que el precio nunca
    puede utilizarse para decidir si una recompensa existe. Además de las
    reglas propias del producto, filtramos aquí la compatibilidad operativa con
    el carrito para que GET, verificación OTP y selección presenten exactamente
    las mismas opciones.
    """
    origen = _normalizar_origen(origen)
    if not origen:
        return []

    candidatos = (
        Product.query.filter_by(activo=True, canjeable_con_puntos=True)
        .filter(Product.puntos_para_canje.isnot(None), Product.puntos_para_canje > 0)
        .order_by(Product.puntos_para_canje.asc(), Product.nombre.asc())
        .all()
    )
    productos_carrito = [p for p in (productos_carrito or []) if p]
    disponibles = []
    for producto in candidatos:
        if not _producto_canjeable_en_origen(producto, origen):
            continue
        if productos_carrito and not _cart_compatibility(
            productos_carrito + [producto]
        )["ok"]:
            continue
        disponibles.append(producto)
    return disponibles


def _canjeables_payload(cliente, origen=None, productos_carrito=None):
    puntos = max(0, int(cliente.puntos or 0)) if cliente else 0
    candidatos = _productos_canjeables_disponibles(origen, productos_carrito)
    canjeables = [p for p in candidatos if (p.puntos_para_canje or 0) <= puntos] if puntos > 0 else []
    proximo = next((p for p in candidatos if (p.puntos_para_canje or 0) > puntos), None)

    def _prod(p):
        return {
            "id": p.id,
            "nombre": p.nombre,
            "puntos": int(p.puntos_para_canje or 0),
            "precio": float(p.precio_final or 0),
            "imagen_url": p.imagen_url or "",
            "es_combo": bool(p.es_combo),
            "origen": p.origen_pais or "",
            "categoria": p.categoria.nombre if p.categoria else "",
        }

    return {
        "puntos": puntos,
        "canjeables": [_prod(p) for p in canjeables],
        "proximo_canje": _prod(proximo) if proximo else None,
    }


# ─── CATÁLOGO ────────────────────────────────

@public_bp.route("/")
def index():
    return _render_catalogo("propio")


@public_bp.route("/informacion-legal")
def informacion_legal():
    """Información legal alimentada por configuración, sin identidades ficticias."""
    fiscal_name = (SiteConfig.get("NOMBRE_FISCAL", "") or "").strip()
    business_name = (SiteConfig.get("NOMBRE_NEGOCIO", "") or "").strip()
    contact_email = (SiteConfig.get("EMAIL_CONTACTO", "") or "").strip()
    privacy_email = (SiteConfig.get("EMAIL_PRIVACIDAD", "") or "").strip() or contact_email
    legal = {
        "titular": fiscal_name or business_name,
        "nombre_comercial": business_name,
        "nif": (SiteConfig.get("NIF_NEGOCIO", "") or "").strip(),
        "direccion": ((SiteConfig.get("DIRECCION_FISCAL", "") or "").strip()
                      or (SiteConfig.get("DIRECCION_NEGOCIO", "") or "").strip()),
        "email": contact_email,
        "email_privacidad": privacy_email,
        "telefono": (SiteConfig.get("TELEFONO_NEGOCIO", "") or "").strip(),
        "registro": (SiteConfig.get("REGISTRO_MERCANTIL", "") or "").strip(),
        "version": (SiteConfig.get("LEGAL_VERSION", "1.0") or "1.0").strip(),
        "retencion": SiteConfig.get("LEGAL_RETENCION_PEDIDOS", ""),
        "devoluciones": SiteConfig.get("LEGAL_CONDICIONES_DEVOLUCION", ""),
    }
    legal["faltantes"] = [
        label for value, label in (
            (legal["titular"], "titular o razón social"),
            (legal["nif"], "NIF/CIF"),
            (legal["direccion"], "domicilio fiscal"),
            (legal["email_privacidad"], "correo de privacidad"),
        ) if not value
    ]
    return render_template("public/informacion_legal.html", legal=legal)


@public_bp.route("/bar/<int:proveedor_id>")
def menu_bar(proveedor_id):
    flash("Esta tienda funciona como un único establecimiento.", "info")
    return redirect(url_for("public.index"))


def _render_catalogo(origen, proveedor=None):
    categorias = Categoria.query.filter_by(activo=True).all()
    categoria_id = request.args.get("categoria", type=int)
    busqueda = request.args.get("q", "").strip()

    base_query = Product.query.filter_by(activo=True)
    # Nota: NO filtramos por nombre en SQL. `ilike` en Postgres es
    # case-insensitive pero NO accent-insensitive, así que 'cafe' no
    # encontraría 'café'. Filtramos en Python con _strip_accents después
    # de traer los productos activos (catálogo típico <200 items).
    todos = base_query.all()
    if busqueda:
        _q_norm = _strip_accents(busqueda)
        if _q_norm:
            todos = [p for p in todos if _q_norm in _strip_accents(p.nombre or "")]
    # La tienda pública reúne producto propio y de socios. Cada tarjeta conserva
    # su origen real para que inventario, pedido y liquidación nunca se mezclen.
    product_origins = {
        product.id: (
            f"proveedor:{product.proveedor_despachador_id}"
            if product.proveedor_despachador_id else "propio"
        )
        for product in todos
    }
    # Origen LOGÍSTICO por producto: si el proveedor es socio-capital, el
    # despacho es "propio" aunque el inventario cuelgue de proveedor:X.
    # `_carrito_origen()` devuelve el origen logístico (por diseño), así que
    # la comparación en la tarjeta de producto debe usar el logístico también
    # — con inventario directo, un producto de socio-capital nunca coincidía
    # con carrito_origen y NUNCA se marcaba como "En canasta".
    product_logistic_origins = {
        pid: _origen_logistico(origen) for pid, origen in product_origins.items()
    }
    projection = {}
    origins = sorted(set(product_origins.values()))
    for product_origin in origins:
        group = [p for p in todos if product_origins[p.id] == product_origin]
        projection.update(build_catalog_projection(group, product_origin))
    store_features = get_store_features()
    active_vertical = (SiteConfig.get("TIPO_TIENDA", "comida") or "comida").lower()

    def catalog_eligible(product):
        if not projection[product.id].available:
            return False
        if (product.vertical or "").strip().lower() != active_vertical:
            return False
        if _delivery_family(product) == "programado" and not store_features.get("pedidos_programados", False):
            return False
        if _programmed_date_expired(product):
            return False
        modes = _product_fulfillment_modes(product)
        return bool(
            ("delivery" in modes and store_features.get("delivery", False))
            or ("recogida" in modes and store_features.get("recogida", False))
        )

    productos_catalogo = [product for product in todos if catalog_eligible(product)]
    categoria_counts = {}
    for producto in productos_catalogo:
        if producto.categoria_id:
            categoria_counts[producto.categoria_id] = categoria_counts.get(producto.categoria_id, 0) + 1
    categorias_con_productos = set(categoria_counts)
    categorias = [c for c in categorias if c.id in categorias_con_productos]

    # Se entrega el catálogo completo para cambiar de categoría sin recargar.
    # El filtro inicial y los cambios posteriores se aplican en el navegador.
    productos_vis = productos_catalogo
    productos = sorted(
        productos_vis,
        key=lambda p: (
            0 if p.es_combo else 1,
            p.categoria.orden if p.categoria else 99,
            p.nombre,
        ),
    )
    carrito = session.get("carrito", {})

    menu_items = MenuConfig.query.filter(
        MenuConfig.pagina.in_(["home", "menu"]),
        MenuConfig.activo == True,
    ).order_by(MenuConfig.pagina.asc(), MenuConfig.orden.asc(), MenuConfig.id.asc()).all()
    menu_items = [
        item for item in menu_items
        if item.tipo != "producto_destacado"
        or (
            item.producto
            and item.producto.activo
            and item.producto.visible_ahora
            and item.producto.pertenece_a_origen(product_origins.get(item.producto.id, "propio"))
            and projection.get(item.producto.id)
            and catalog_eligible(item.producto)
        )
    ]
    todas_resenas = Review.query.filter_by(aprobada=True).all()
    resenas_recientes = random.sample(todas_resenas, min(8, len(todas_resenas)))
    zona_principal = ZonaEntrega.query.filter_by(activo=True)\
        .order_by(ZonaEntrega.orden, ZonaEntrega.nombre).first()

    # Subtotal del carrito para el botón flotante
    _, carrito_subtotal = _build_items_from_carrito(carrito)
    bares = []
    establecimiento = {
        "origen": origen,
        "nombre": proveedor.nombre if proveedor else (SiteConfig.get("NOMBRE_NEGOCIO", "") or "Mi tienda"),
        "abierto": proveedor.disponible_para_venta if proveedor else True,
        "url": url_for("public.index"),
    }

    # Recomendaciones automáticas fallback: si el admin no configuró destacados
    # en MenuConfig y el flag no está desactivado, calculamos top 3 por rating
    # (con desempate por precio_final) para que el bloque no quede vacío.
    auto_destacados_enabled = str(SiteConfig.get("AUTO_DESTACADOS_ENABLED", "1")).strip() == "1"
    productos_auto_destacados = []
    if auto_destacados_enabled:
        _tiene_destacados_manuales = any(
            it.tipo == "producto_destacado" and it.pagina == "home"
            for it in menu_items
        )
        if not _tiene_destacados_manuales:
            _candidatos = [
                p for p in productos
                if p.activo
                and not getattr(p, "solo_canje", False)
                and catalog_eligible(p)
            ]
            # Primer intento: top por rating con reviews aprobadas.
            con_rating = [p for p in _candidatos if projection[p.id].rating > 0]
            if con_rating:
                productos_auto_destacados = sorted(
                    con_rating,
                    key=lambda p: (
                        -projection[p.id].rating,
                        -float(p.precio_final or 0),
                    ),
                )[:3]
            else:
                # Fallback secundario: aún sin reviews, mostramos "premium"
                # (precio más alto) para que el bloque nunca quede vacío.
                # Priorizamos combos si existen, luego productos individuales.
                productos_auto_destacados = sorted(
                    _candidatos,
                    key=lambda p: (
                        0 if getattr(p, "es_combo", False) else 1,
                        -float(p.precio_final or 0),
                    ),
                )[:3]

    return render_template("public/index.html",
                           productos=productos, categorias=categorias,
                           categoria_counts=categoria_counts,
                           categoria_activa=categoria_id,
                           busqueda=busqueda,
                           menu_items=menu_items,
                           productos_auto_destacados=productos_auto_destacados,
                           auto_destacados_con_rating=any(
                               projection[product.id].rating > 0
                               for product in productos_auto_destacados
                           ),
                           resenas_recientes=resenas_recientes,
                           zona_principal=zona_principal,
                           carrito=carrito,
                           carrito_origen=_carrito_origen(carrito),
                           carrito_subtotal=round(carrito_subtotal, 2),
                           cart_max_qty=_cart_max_qty(),
                           origen_actual=origen,
                           establecimiento=establecimiento,
                           bares=bares,
                           proveedor_actual=proveedor,
                           product_cards=projection,
                           product_origins=product_origins,
                           product_logistic_origins=product_logistic_origins,
                           fulfillment_badge=_product_fulfillment_badge)


@public_bp.route("/whatsapp")
def whatsapp():
    """Enlace publico unico del dominio hacia el chatbot de WhatsApp."""
    telefono = SiteConfig.get("TELEFONO_NEGOCIO", "") or os.environ.get("OWNER_NUMBER", "")
    digits = _whatsapp_phone_digits(telefono)
    if not digits:
        flash("WhatsApp no esta configurado todavia.", "warning")
        return redirect(url_for("public.index"))

    nombre = SiteConfig.get("NOMBRE_NEGOCIO", "") or "Mi tienda"
    public_url = get_public_store_url(request.url_root)
    default_text = f"Hola, quiero pedir en {nombre}. Vi la tienda aqui: {public_url}"
    text = (request.args.get("text") or default_text).strip()[:500]
    return redirect(f"https://wa.me/{digits}?text={quote(text)}")


@public_bp.route("/producto/<int:producto_id>")
def producto_detalle(producto_id):
    from models import ComboItem
    producto = get_or_404(Product, producto_id)
    origen = _normalizar_origen(request.args.get("origen")) or "propio"
    if is_service_mode() and origen != "propio":
        flash("Este producto no está disponible en el catálogo.", "warning")
        return redirect(url_for("public.index"))
    proveedor_id = _proveedor_id_origen(origen)
    proveedor = db.session.get(Proveedor, proveedor_id) if proveedor_id else None
    if (
        not _producto_disponible_en_origen(producto, origen)
        or (proveedor_id and (not proveedor or not proveedor.activo))
    ):
        flash("Este producto no está disponible ahora.", "warning")
        if proveedor and proveedor.activo:
            return redirect(url_for("public.menu_bar", proveedor_id=proveedor.id))
        return redirect(url_for("public.index"))
    reviews = Review.query.filter_by(producto_id=producto_id, aprobada=True).all()
    combo_items = ComboItem.query.filter_by(combo_id=producto_id)\
        .order_by(ComboItem.orden.asc(), ComboItem.id.asc()).all() if producto.es_combo else []
    combo_fixed_base = sum(
        float(item.componente.precio_final) * max(1, int(item.cantidad or 1))
        for item in combo_items
        if not item.es_seleccionable and item.componente
    )
    extra_groups = ProductExtraGroup.query.filter_by(producto_id=producto.id, activo=True)\
        .order_by(ProductExtraGroup.orden, ProductExtraGroup.id).all()
    establecimiento_abierto, mensaje_cierre = _establecimiento_abierto_checkout(
        origen, proveedor
    )
    return render_template("public/producto.html",
                           producto=producto, reviews=reviews, combo_items=combo_items,
                           extra_groups=extra_groups,
                           presentation_catalog=product_presentation_catalog_payload(producto),
                           combo_fixed_base=round(combo_fixed_base, 2),
                           cart_max_qty=_cart_max_qty(),
                           origen_actual=origen,
                           establecimiento_abierto=establecimiento_abierto,
                           mensaje_cierre=mensaje_cierre,
                           volver_url=url_for("public.menu_bar", proveedor_id=proveedor.id)
                           if proveedor else url_for("public.index"),
                           stock_en_origen=_stock_en_origen,
                           fulfillment_badge=_product_fulfillment_badge)


# ─── CARRITO (sesión Flask) ──────────────────

def _get_carrito():
    # Migración transparente de sesiones legacy (claves = str(producto_id))
    # al modelo de líneas (claves = line_key con firma de selección). Idempotente.
    if session.get("carrito"):
        try:
            migrate_legacy_session(session)
            session.modified = True
        except Exception:
            current_app.logger.exception("cart_lines migration failed; sesión legacy conservada")
    return session.get("carrito", {})

def _save_carrito(carrito):
    session["carrito"] = carrito
    if not carrito:
        # Limpieza COMPLETA de todo el estado de sesión ligado al carrito
        # para evitar datos huérfanos que se filtran al siguiente pedido.
        # Historial de un bug: `presentaciones_carrito` y `notas_combo`
        # quedaban con datos del carrito anterior tras vaciar.
        for _k in (
            "carrito_origen",
            "cart_puntos",
            "cart_producto_canje_id",
            "cart_cupon",       # aplicado desde /carrito/cupon
            "cart_afiliado",    # aplicado desde /carrito/afiliado
            "combo_selecciones",
            "extras_selecciones",
            "presentaciones_carrito",
            "variantes_carrito",
            "notas_combo",
        ):
            session.pop(_k, None)
    session.modified = True


def _carrito_origen(carrito=None):
    carrito = _get_carrito() if carrito is None else carrito
    origen = _normalizar_origen(session.get("carrito_origen"))
    if origen:
        logistico = _origen_logistico(origen)
        if logistico != origen:
            session["carrito_origen"] = logistico
            session.modified = True
        return logistico
    if not carrito:
        return None

    # Compatibilidad para sesiones creadas antes de que el origen fuera explícito.
    origenes = _cart_origins(carrito)
    origenes_logisticos = {_origen_logistico(item) for item in origenes}
    if len(origenes_logisticos) == 1:
        origen = next(iter(origenes_logisticos))
        session["carrito_origen"] = origen
        session.modified = True
        return origen
    if len(origenes) == 1:
        origen = next(iter(origenes))
        session["carrito_origen"] = origen
        session.modified = True
        return origen
    return None


def _set_carrito_origen(origen):
    origen = _normalizar_origen(origen)
    anterior = _normalizar_origen(session.get("carrito_origen"))
    if anterior != origen:
        # Cambio de tienda/origen invalida descuentos aplicados
        # (algunos cupones son específicos por origen, y el carrito
        # anterior podría haber tenido un producto de canje incompatible).
        session.pop("cart_puntos", None)
        session.pop("cart_producto_canje_id", None)
        session.pop("cart_cupon", None)
        session.pop("cart_afiliado", None)
    if origen:
        session["carrito_origen"] = origen
    else:
        session.pop("carrito_origen", None)
    session.modified = True


def _delivery_family(producto):
    tipo = (getattr(producto, "tipo_entrega", None) or "inmediato").strip().lower()
    return "programado" if tipo in ("programado", "encargo") else "inmediato"


def _programmed_date_expired(producto):
    if not producto or _delivery_family(producto) != "programado":
        return False
    fecha = getattr(producto, "fecha_llegada", None)
    from business_time import business_today
    return bool(fecha and fecha < business_today())


def _order_group(producto):
    """Grupo configurable que determina qué productos comparten pedido."""
    key = getattr(producto, "grupo_pedido_key", None)
    if key:
        return key
    value = " ".join(str(getattr(producto, "grupo_pedido", None) or "").split()).casefold()
    return value or "__general__"


def _order_group_label(producto):
    label = getattr(producto, "grupo_pedido_label", None)
    if label:
        return label
    return " ".join(str(getattr(producto, "grupo_pedido", None) or "").split()) or "Pedido general"


def _cart_products_from_carrito(carrito, exclude_key=None):
    # `exclude_key` puede ser una line_key concreta (nueva) o un producto_id
    # como string (compat con llamadas legacy). Excluimos la línea exacta si
    # coincide, o todas las líneas de un producto si es un producto_id.
    exclude_str = str(exclude_key) if exclude_key is not None else None
    exclude_pid = None
    if exclude_str is not None:
        try:
            exclude_pid = int(exclude_str)
        except (TypeError, ValueError):
            exclude_pid = producto_id_from_line_key(exclude_str)
    ids = []
    for key in (carrito or {}).keys():
        if exclude_str is not None and str(key) == exclude_str:
            continue
        pid = producto_id_from_line_key(key)
        if pid is None:
            continue
        # Excluir por producto_id sólo si la exclusión venía como int puro
        # (mantiene la semántica del llamado legacy `exclude_key=producto_id`).
        if exclude_pid is not None and str(exclude_key) == str(exclude_pid) and pid == exclude_pid:
            continue
        if pid not in ids:
            ids.append(pid)
    if not ids:
        return []
    productos = Product.query.filter(Product.id.in_(ids), Product.activo == True).all()
    order = {pid: i for i, pid in enumerate(ids)}
    return sorted(productos, key=lambda p: order.get(p.id, 9999))


def _product_names(productos, limit=4):
    names = [f"«{getattr(p, 'nombre', 'Producto')}»" for p in (productos or []) if p]
    if len(names) > limit:
        return ", ".join(names[:limit]) + f" y {len(names) - limit} más"
    return ", ".join(names)


_CART_ISSUE_TITLES = {
    "vertical": "Producto de otro tipo de catálogo",
    "programados_disabled": "Pedidos programados desactivados",
    "delivery_family": "Fecha fija e inmediato no van juntos",
    "order_group": "Estos productos requieren pedidos separados",
    "fulfillment_modules_disabled": "Modalidad no disponible",
    "fulfillment_conflict": "No se pueden combinar esas modalidades",
    "minimum_order": "Pedido mínimo pendiente",
    "programados_expired": "Fecha programada vencida",
    "programados_missing_date": "Falta definir la fecha programada",
    "programados_mixed_dates": "Las fechas programadas no coinciden",
}


def _cart_issue_payload(issue, action_url=None, action_label=None):
    """Versión JSON segura y accionable de un issue de compatibilidad."""
    issue = issue or {}
    code = issue.get("code") or "cart_issue"
    products = []
    for product in issue.get("products") or []:
        if not product:
            continue
        products.append({
            "id": getattr(product, "id", None),
            "nombre": getattr(product, "nombre", "Producto"),
            "modalidad": sorted(_product_fulfillment_modes(product)),
            "modalidad_label": _product_fulfillment_badge(product)["label"],
            "tipo_entrega": _delivery_family(product),
            "fecha_entrega": (
                product.fecha_llegada.isoformat()
                if getattr(product, "fecha_llegada", None) else None
            ),
            "grupo": _order_group_label(product),
            "vertical": getattr(product, "vertical", "ambos") or "ambos",
        })
    return {
        "code": code,
        "title": _CART_ISSUE_TITLES.get(code, "Revisa tu carrito"),
        "message": issue.get("message") or "",
        "severity": issue.get("severity") or "warning",
        "products": products[:8],
        "action_url": action_url,
        "action_label": action_label,
    }


def _cart_compatibility(
    productos,
    subtotal=None,
    pedido_minimo=0,
    check_zone_availability=False,
):
    """Diagnóstico único para carrito y checkout.

    Agrupa las reglas que definen si un conjunto de productos puede convertirse
    en un solo pedido: nicho/vertical, módulos activos, fecha, grupo operativo y
    modalidad logística. Mantenerlo centralizado evita que el carrito deje pasar
    algo que luego falla en checkout.
    """
    productos = [p for p in (productos or []) if p]
    features = get_store_features()
    issues = []

    def add(code, message, products=None, severity="warning"):
        issues.append({
            "code": code,
            "message": message,
            "products": [p for p in (products or []) if p],
            "severity": severity,
        })

    vertical_blockers = [p for p in productos if not _producto_pertenece_al_vertical(p)]
    if vertical_blockers:
        add(
            "vertical",
            "Algunos productos ya no pertenecen al tipo de tienda activo. "
            f"Retira {_product_names(vertical_blockers)} y vuelve a añadir productos del catálogo actual.",
            vertical_blockers,
        )

    # Regla: no mezclar productos de nicho comida con nicho retail en el
    # mismo carrito. Aunque ambos verticales estén activos (o el producto
    # sea vertical='ambos'), un pedido no puede combinar Hamburguesa (cocina)
    # con Camiseta (paquetería). Flujos operativos distintos, empaquetado
    # distinto, tiempo de entrega distinto.
    verticales_reales = {
        (getattr(p, "vertical", None) or "ambos").strip().lower()
        for p in productos
    }
    verticales_reales.discard("ambos")
    if len(verticales_reales) > 1:  # {comida, producto}
        add(
            "vertical_mix",
            "No puedes mezclar productos de comida con productos de retail "
            "(ropa/accesorios) en el mismo pedido. Sepáralos en dos pedidos.",
            productos,
            "danger",
        )

    programados = [p for p in productos if _delivery_family(p) == "programado"]
    programados_sin_fecha = [p for p in programados if not getattr(p, "fecha_llegada", None)]
    programados_vencidos = [p for p in programados if _programmed_date_expired(p)]
    fechas_programadas = {
        p.fecha_llegada for p in programados if getattr(p, "fecha_llegada", None)
    }
    if programados_sin_fecha:
        add(
            "programados_missing_date",
            "Algunos productos programados todavía no tienen fecha de entrega. "
            f"Retira {_product_names(programados_sin_fecha)} hasta que el negocio defina una fecha.",
            programados_sin_fecha,
            "danger",
        )
    if programados_vencidos:
        add(
            "programados_expired",
            "La fecha programada de algunos productos ya pasó. "
            f"Retira {_product_names(programados_vencidos)} y vuelve a elegir productos disponibles.",
            programados_vencidos,
            "danger",
        )
    if len(fechas_programadas) > 1:
        fechas_txt = ", ".join(fecha.strftime("%d/%m/%Y") for fecha in sorted(fechas_programadas))
        add(
            "programados_mixed_dates",
            "Los productos programados corresponden a fechas distintas "
            f"({fechas_txt}). Crea un pedido separado para cada fecha de entrega.",
            programados,
            "danger",
        )
    if programados and not features.get("pedidos_programados", False):
        add(
            "programados_disabled",
            "Los pedidos con fecha programada están desactivados. "
            f"Retira {_product_names(programados)} para continuar.",
            programados,
        )

    familias = {_delivery_family(p) for p in productos}
    if len(familias) > 1:
        add(
            "delivery_family",
            "El carrito mezcla productos inmediatos y productos con fecha fija. "
            "Sepáralos en dos pedidos para evitar errores de preparación y despacho.",
            productos,
        )

    grupos = {_order_group(p): _order_group_label(p) for p in productos}
    if len(grupos) > 1:
        add(
            "order_group",
            "Estos grupos requieren pedidos separados: " + ", ".join(grupos.values()) + ".",
            productos,
        )

    fulfillment_options = _fulfillment_options(productos)
    fulfillment_unavailable = _fulfillment_unavailable_reasons(
        productos,
        check_zone_availability=check_zone_availability,
    )
    delivery_sin_zonas = "delivery" in fulfillment_options and "delivery" in fulfillment_unavailable
    if delivery_sin_zonas:
        fulfillment_options.remove("delivery")
    if delivery_sin_zonas and not fulfillment_options:
        add(
            "delivery_no_active_zones",
            "El reparto está temporalmente sin zonas activas. "
            "Elige recogida en el local si está disponible.",
            [],
            "danger",
        )
    if productos and not fulfillment_options:
        if delivery_sin_zonas:
            pass
        elif not features.get("delivery", False) and not features.get("recogida", False):
            add(
                "fulfillment_modules_disabled",
                "La tienda no tiene delivery ni recogida activos. Contacta con el negocio.",
                productos,
                "danger",
            )
        else:
            details = []
            for p in productos:
                modes = _product_fulfillment_modes(p)
                if modes == {"delivery"}:
                    details.append(f"«{p.nombre}» solo con envío a domicilio")
                elif modes == {"recogida"}:
                    details.append(f"«{p.nombre}» solo para recoger")
            suffix = f" Detectado: {'; '.join(details[:4])}." if details else ""
            add(
                "fulfillment_conflict",
                "Los productos del carrito no comparten modalidad de entrega. "
                "No mezcles productos solo con envío a domicilio con productos solo para recoger."
                + suffix,
                productos,
            )

    if subtotal is not None and pedido_minimo and pedido_minimo > 0 and subtotal < pedido_minimo:
        falta = pedido_minimo - subtotal
        add(
            "minimum_order",
            f"El pedido mínimo es €{pedido_minimo:.2f}. Añade €{falta:.2f} más para poder finalizar.",
            [],
        )

    return {
        "ok": not issues,
        "issues": issues,
        "message": issues[0]["message"] if issues else "",
        "fulfillment_options": fulfillment_options,
        "fulfillment_unavailable": fulfillment_unavailable,
        "features": features,
        "scheduled_date": next(iter(fechas_programadas), None)
        if len(fechas_programadas) == 1 else None,
    }


def _cart_origins(carrito, exclude_key=None):
    if not carrito:
        return set()
    exclude_str = str(exclude_key) if exclude_key is not None else None
    ids = []
    for key in carrito.keys():
        if exclude_str is not None and str(key) == exclude_str:
            continue
        pid = producto_id_from_line_key(key)
        if pid is None:
            continue
        if pid not in ids:
            ids.append(pid)
    if not ids:
        return set()
    productos = Product.query.filter(Product.id.in_(ids), Product.activo == True).all()
    return {p.origen_operativo_key for p in productos}


@public_bp.route("/carrito/agregar/<int:producto_id>", methods=["GET"])
def agregar_carrito_get(producto_id):
    """GET directo (URL pegada, click en enlace externo) → no muestra 405.
    Redirige al detalle del producto donde el usuario puede añadir vía form."""
    return redirect(url_for("public.producto_detalle", producto_id=producto_id))


@public_bp.route("/carrito/agregar/<int:producto_id>", methods=["POST"])
def agregar_carrito(producto_id):
    _ajax = request.headers.get("X-Ajax") == "1"
    cart_name = str(get_store_value("UI_CART_NAME", "canasta") or "canasta").strip().lower()
    cart_action = str(get_store_value("UI_CART_VIEW_ACTION", "Ver canasta") or "Ver canasta").strip()

    def _err(msg, category="warning", issue=None, action_url=None, action_label=None):
        if _ajax:
            payload = {"ok": False, "msg": msg, "category": category}
            if issue:
                payload["issue"] = _cart_issue_payload(issue, action_url, action_label)
            return jsonify(payload), 200
        flash(msg, category)
        return redirect(request.referrer or url_for("public.index"))

    producto = get_or_404(Product, producto_id)
    # Bloqueo: productos EXCLUSIVOS de canje con puntos no se pueden comprar.
    # Redirige al cliente al club para canjear con puntos.
    if getattr(producto, "solo_canje", False):
        loyalty_terms = get_loyalty_terms()
        return _err(
            "«{}» sólo se obtiene canjeando {}. Ve a {}.".format(
                producto.nombre, loyalty_terms["plural"], loyalty_terms["name"]
            ),
            "info",
        )
    origen_solicitado = _normalizar_origen(request.form.get("origen"))
    if not origen_solicitado:
        origen_solicitado = "propio"
    proveedor_id = _proveedor_id_origen(origen_solicitado)
    proveedor = db.session.get(Proveedor, proveedor_id) if proveedor_id else None
    single_compat = _cart_compatibility([producto])
    if not single_compat["ok"]:
        issue = single_compat["issues"][0]
        return _err(
            issue["message"],
            issue.get("severity", "warning"),
            issue=issue,
            action_url=url_for("public.index"),
            action_label="Ver catálogo actual",
        )
    if not _producto_disponible_en_origen(producto, origen_solicitado):
        return _err("Este producto no está disponible ahora.")
    if proveedor_id and (not proveedor or not proveedor.disponible_para_venta):
        return _err("El establecimiento que prepara este producto está cerrado ahora.")
    origen_logistico = _origen_logistico(origen_solicitado)
    skip_delivery_validation = bool(
        current_app.testing
        and current_app.config.get("SKIP_DELIVERY_VALIDATION", False)
    )
    if origen_logistico == "propio" and not skip_delivery_validation:
        # Bloqueo temprano cuando la tienda propia está cerrada por horario:
        # antes solo se detectaba en checkout, dejando llenar el carrito en vano.
        abierto_local, msg_cierre = _establecimiento_abierto_checkout(origen_logistico, None)
        if not abierto_local:
            return _err(msg_cierre or "La tienda está cerrada ahora, no podemos añadir productos, parce.")
    cart_max_qty = _cart_max_qty()
    try:
        cantidad = max(1, min(cart_max_qty, int(request.form.get("cantidad", 1))))
    except (ValueError, TypeError):
        cantidad = 1
    carrito = _get_carrito()
    origen_carrito = _carrito_origen(carrito)
    if origen_carrito and origen_logistico != origen_carrito:
        return _err(
            f"Tu {cart_name} ya contiene productos de otro responsable. "
            "Para proteger el stock, el despacho y la liquidación de cada socio, "
            f"finaliza primero esa compra o vacía la {cart_name} antes de cambiar."
        )

    # --- Compat: chequeo de mezcla de productos (por producto_id, no por línea) ---
    productos_candidato = _cart_products_from_carrito(carrito) + [producto]
    compat = _cart_compatibility(productos_candidato)
    hay_otros_productos = any(
        producto_id_from_line_key(k) not in (None, producto_id) and int(q or 0) > 0
        for k, q in carrito.items()
    )
    if not compat["ok"]:
        issue = compat["issues"][0]
        return _err(
            compat["message"],
            issue.get("severity", "warning"),
            issue=issue,
            action_url=url_for("public.ver_carrito") if carrito else url_for("public.index"),
            action_label=cart_action if carrito else "Ver catálogo",
        )
    if hay_otros_productos and not compat["fulfillment_options"]:
        return _err(
            f"Tu {cart_name} tiene productos incompatibles entre sí. "
            f"Vacíalo o retira los productos que bloquean a «{producto.nombre}»."
        )

    # --- Parseo de TODA la selección antes de calcular la firma ---
    variant_id = 0
    variante = None
    if getattr(producto, "tiene_variantes", False):
        from models import ProductVariant  # noqa: F401
        variant_raw = (request.form.get("variant_id") or "").strip()
        variantes_activas = producto.variantes_activas
        try:
            variant_id = int(variant_raw) if variant_raw else 0
        except (TypeError, ValueError):
            variant_id = 0
        variantes_map = {v.id: v for v in variantes_activas}
        if not variant_id or variant_id not in variantes_map:
            return _err(f"Elige una variante para «{producto.nombre}».")
        variante = variantes_map[variant_id]
        if not variante.disponible():
            return _err(f"«{variante.label_publico}» está agotado.")

    presentation_size_raw = (request.form.get("presentation_size") or "").strip()
    presentation, presentation_error = validate_product_presentation_selection(
        producto, presentation_size_raw
    )
    if presentation_error:
        return _err(presentation_error)
    presentation_canonico = presentation.tamaño if presentation else ""

    combo_seleccion = {}
    if producto.es_combo:
        seleccion, error = _parse_combo_selection(
            producto,
            request.form,
            cantidad,
            origen_solicitado,
        )
        if error:
            if _ajax:
                return jsonify({"ok": False, "msg": error}), 200
            flash(error, "danger")
            return redirect(request.referrer or url_for(
                "public.producto_detalle",
                producto_id=producto_id,
                origen=origen_solicitado,
            ))
        combo_seleccion = seleccion

    extras, extras_error = _parse_product_extras(producto, request.form, presentation)
    if extras_error:
        return _err(extras_error, "danger")

    # La nota vive en la cookie de sesión hasta cerrar el pedido. Limitarla en
    # el punto de entrada (y no sólo al renderizar el carrito) evita inflar la
    # sesión con payloads manipulados y mantiene el mismo contrato de 240
    # caracteres que consumen cocina, ticket y checkout.
    notas_personalizacion = request.form.get(
        "notas_personalizacion", ""
    ).strip()[:240]

    # --- Firma de línea: mismo producto con distinta selección = línea aparte ---
    key = line_signature(
        producto_id,
        presentation_size=presentation_canonico,
        sabores=extras,          # sabores + extras viven en un solo dict; el hash absorbe todo
        extras=None,
        variant_id=variant_id or None,
        combo_seleccion=combo_seleccion,
        notas=notas_personalizacion,
    )

    nueva_cantidad_total = int(carrito.get(key, 0) or 0) + cantidad
    if nueva_cantidad_total > cart_max_qty:
        return _err(f"No puedes añadir más de {cart_max_qty} unidades por producto.")

    # Validación de stock con la cantidad total sumando TODAS las líneas del
    # mismo producto (no sólo esta línea) — el stock es del producto, no de la línea.
    qty_producto_total = nueva_cantidad_total + sum(
        int(q or 0)
        for k, q in carrito.items()
        if k != key and producto_id_from_line_key(k) == producto_id
    )
    if not producto.disponible_para_venta_en_origen(origen_solicitado, qty_producto_total):
        return _err("No hay stock suficiente para esa cantidad.")
    if variante is not None and variante.stock is not None and nueva_cantidad_total > variante.stock:
        return _err(
            f"No hay stock suficiente de «{variante.label_publico}» "
            f"(quedan {variante.stock})."
        )
    if producto.es_combo:
        try:
            producto.validar_stock_combo_seleccion(
                nueva_cantidad_total,
                _combo_selection_ids_from_saved(combo_seleccion),
                origen=origen_solicitado,
            )
        except ValueError as exc:
            if _ajax:
                return jsonify({"ok": False, "msg": str(exc)}), 200
            flash(str(exc), "danger")
            return redirect(request.referrer or url_for(
                "public.producto_detalle",
                producto_id=producto_id,
                origen=origen_solicitado,
            ))

    # --- Persistencia: todos los dicts paralelos indexados por line_key ---
    if variant_id:
        variantes_carrito = session.get("variantes_carrito", {})
        variantes_carrito[key] = variant_id
        session["variantes_carrito"] = variantes_carrito
    if presentation_canonico:
        presentaciones_carrito = session.get("presentaciones_carrito", {})
        presentaciones_carrito[key] = presentation_canonico
        session["presentaciones_carrito"] = presentaciones_carrito
    if producto.es_combo:
        selecciones_combo = session.get("combo_selecciones", {})
        selecciones_combo[key] = combo_seleccion
        session["combo_selecciones"] = selecciones_combo
    extras_guardados = session.get("extras_selecciones", {})
    if extras:
        extras_guardados[key] = extras
    else:
        extras_guardados.pop(key, None)
    session["extras_selecciones"] = extras_guardados
    if notas_personalizacion:
        notas_combo = session.get("notas_combo", {})
        notas_combo[key] = notas_personalizacion
        session["notas_combo"] = notas_combo
    carrito[key] = nueva_cantidad_total
    _set_carrito_origen(origen_logistico)
    _save_carrito(carrito)
    session.modified = True
    if _ajax:
        return jsonify({
            "ok": True,
            "nombre": producto.nombre,
            # Ahora sí es número de líneas — dos sabores del mismo producto = 2 líneas.
            "cart_count": len(carrito),
            "line_key": key,
        }), 200
    flash(f"'{producto.nombre}' añadido a tu {cart_name}.", "success")
    return redirect(request.referrer or url_for("public.index"))


@public_bp.route("/carrito/actualizar", methods=["POST"])
def actualizar_carrito():
    carrito = _get_carrito()
    origen = _carrito_origen(carrito)
    selecciones_combo = session.get("combo_selecciones", {})
    notas_combo = session.get("notas_combo", {})
    cart_max_qty = _cart_max_qty()

    def _cleanup_key(k):
        """Elimina TODAS las selecciones paralelas de un producto retirado
        del carrito. Antes: extras_selecciones y presentaciones_carrito
        quedaban huérfanas si el producto desaparecía por unavailability,
        y ensuciaban la sesión hasta un vaciado completo."""
        selecciones_combo.pop(k, None)
        notas_combo.pop(k, None)
        for _s in ("extras_selecciones", "presentaciones_carrito",
                   "variantes_carrito"):
            _map = session.get(_s) or {}
            if k in _map:
                _map.pop(k, None)
                session[_s] = _map

    for key in list(carrito.keys()):
        try:
            nueva_cantidad = max(0, min(cart_max_qty, int(request.form.get(f"cantidad_{key}", 0))))
        except (ValueError, TypeError):
            nueva_cantidad = 0
        if nueva_cantidad <= 0:
            del carrito[key]
            _cleanup_key(key)
        else:
            pid = producto_id_from_line_key(key)
            producto = db.session.get(Product, pid) if pid is not None else None
            origen_item = _origen_inventario_producto(producto)
            if not _producto_disponible_en_origen(producto, origen_item):
                del carrito[key]
                _cleanup_key(key)
                continue
            try:
                if producto.es_combo:
                    producto.validar_stock_combo_seleccion(
                        nueva_cantidad,
                        _combo_selection_ids_from_saved(selecciones_combo.get(key, {})),
                        origen=origen_item,
                    )
                elif not producto.disponible_para_venta_en_origen(origen_item, nueva_cantidad):
                    raise ValueError(f"No hay stock suficiente para {producto.nombre}.")
            except ValueError as exc:
                flash(str(exc), "warning")
                continue
            carrito[key] = nueva_cantidad
    _save_carrito(carrito)
    session["combo_selecciones"] = selecciones_combo
    session["notas_combo"] = notas_combo
    session.modified = True
    return redirect(url_for("public.ver_carrito"))


def _eliminar_lineas(keys):
    """Elimina una o varias líneas y todas sus selecciones paralelas."""
    carrito = _get_carrito()
    combos = session.get("combo_selecciones", {}) or {}
    notas = session.get("notas_combo", {}) or {}
    extras = session.get("extras_selecciones", {}) or {}
    presentaciones = session.get("presentaciones_carrito", {}) or {}
    variantes = session.get("variantes_carrito", {}) or {}
    for k in keys:
        carrito.pop(k, None)
        combos.pop(k, None)
        notas.pop(k, None)
        extras.pop(k, None)
        presentaciones.pop(k, None)
        variantes.pop(k, None)
    session["combo_selecciones"] = combos
    session["notas_combo"] = notas
    session["extras_selecciones"] = extras
    session["presentaciones_carrito"] = presentaciones
    session["variantes_carrito"] = variantes
    _save_carrito(carrito)


@public_bp.route("/carrito/eliminar_linea", methods=["POST"])
def eliminar_linea_carrito():
    """Elimina una línea concreta del carrito por su `line_key` (firma)."""
    # Instrumentación diagnóstico: bug reportado "no se pueden eliminar
    # productos de socios". Registra qué llegó al backend para poder
    # rastrear si el bug es del JS (no envía line_key) o del backend.
    line_key = (request.form.get("line_key") or "").strip()
    carrito_actual = _get_carrito()
    current_app.logger.info(
        "eliminar_linea_carrito: line_key=%r keys_form=%s carrito_keys=%s",
        line_key, sorted(request.form.keys()), sorted(carrito_actual.keys()),
    )
    if not line_key:
        return (jsonify({"ok": False, "msg": "line_key requerida"}), 400) \
            if request.headers.get("X-Ajax") == "1" \
            else redirect(url_for("public.ver_carrito"))
    if line_key not in carrito_actual:
        current_app.logger.warning(
            "eliminar_linea_carrito: line_key=%r NO existe en carrito (keys=%s)",
            line_key, sorted(carrito_actual.keys()),
        )
    _eliminar_lineas([line_key])
    if request.headers.get("X-Ajax") == "1":
        return jsonify({"ok": True})
    return redirect(url_for("public.ver_carrito"))


@public_bp.route("/carrito/eliminar/<int:producto_id>", methods=["POST"])
def eliminar_carrito(producto_id):
    """Compat: elimina TODAS las líneas de un producto (sin importar sabor/tamaño).

    Endpoint viejo — nuevos flujos deben usar `/carrito/eliminar_linea` con la
    firma exacta para no borrar el resto de sabores del mismo producto.
    """
    carrito = _get_carrito()
    keys = [k for k in list(carrito.keys()) if producto_id_from_line_key(k) == producto_id]
    _eliminar_lineas(keys)
    if request.headers.get("X-Ajax") == "1":
        return jsonify({"ok": True})
    return redirect(url_for("public.ver_carrito"))


@public_bp.route("/carrito/repetir", methods=["GET"])
def repetir_pedido():
    """Pre-carga el carrito con el último pedido entregado del cliente.

    Se accede via un link firmado que el bot WhatsApp genera cuando el
    cliente escribe "repetir" (endpoint api_bot./pedido/repetir-link).
    Este endpoint valida la firma HMAC, verifica que el pedido siga
    perteneciendo al cliente y añade sus productos SIMPLES al carrito
    actual (los productos con extras/sabores/combo/variantes se omiten
    con aviso — reconstruir el estado de selecciones complejo es
    frágil y podría generar carritos inconsistentes; el cliente los
    rehace manualmente).

    Se hace merge con lo que YA tuviera el cliente en el carrito
    (no lo borra) — quizás estaba armando algo distinto en paralelo.
    Las cantidades se suman.
    """
    import hmac
    import hashlib

    try:
        pedido_id = int(request.args.get("pedido") or 0)
    except (TypeError, ValueError):
        pedido_id = 0
    telefono_raw = (request.args.get("tel") or "").strip()
    try:
        expiry_ts = int(request.args.get("exp") or 0)
    except (TypeError, ValueError):
        expiry_ts = 0
    signature = (request.args.get("sig") or "").strip()

    if not pedido_id or not telefono_raw or not expiry_ts or not signature:
        flash("Enlace incompleto. Vuelve a WhatsApp y pide *repetir* de nuevo.", "warning")
        return redirect(url_for("public.ver_carrito"))

    # Expiry: si el link tiene >5min, el bot debe generar uno nuevo.
    now_ts = int(datetime.utcnow().timestamp())
    if now_ts > expiry_ts:
        flash("El enlace expiró (dura 5 min). Escribe *repetir* de nuevo en WhatsApp.", "warning")
        return redirect(url_for("public.ver_carrito"))

    # Verificación HMAC en tiempo constante — sin exponer si es firma
    # inválida o pedido inexistente para no dar señal a un atacante
    # que pruebe combinaciones.
    api_key = str(SiteConfig.get("BOT_API_KEY", "") or "").encode("utf-8")
    if not api_key:
        current_app.logger.warning("repetir_pedido: BOT_API_KEY vacía; rechazo por seguridad")
        flash("El enlace no se puede validar ahora mismo.", "warning")
        return redirect(url_for("public.ver_carrito"))
    payload = f"{pedido_id}|{telefono_raw}|{expiry_ts}".encode("utf-8")
    expected = hmac.new(api_key, payload, "sha256").hexdigest()[:24]
    if not hmac.compare_digest(expected, signature):
        current_app.logger.warning(
            "repetir_pedido: firma inválida pedido=%s tel=***%s",
            pedido_id, telefono_raw[-3:],
        )
        flash("Enlace inválido o modificado.", "danger")
        return redirect(url_for("public.ver_carrito"))

    pedido = db.session.get(Order, pedido_id)
    if not pedido:
        flash("No encontré ese pedido para repetir.", "warning")
        return redirect(url_for("public.ver_carrito"))

    # Doble verificación de ownership: la firma ya vincula el teléfono
    # al pedido, pero comprobamos que el cliente actual del pedido siga
    # siendo el mismo (por si borrado/reasignado).
    from models import normalizar_telefono_cliente
    tel_norm = normalizar_telefono_cliente(telefono_raw)
    cliente_pedido = pedido.cliente
    tel_cliente = normalizar_telefono_cliente(
        (cliente_pedido.telefono_normalizado or cliente_pedido.telefono) if cliente_pedido else ""
    )
    if not tel_cliente or tel_cliente != tel_norm:
        current_app.logger.warning(
            "repetir_pedido: ownership mismatch pedido=%s tel_link=***%s tel_pedido=***%s",
            pedido_id, tel_norm[-3:] if tel_norm else "?",
            tel_cliente[-3:] if tel_cliente else "?",
        )
        flash("Este enlace no coincide con tu cuenta.", "danger")
        return redirect(url_for("public.ver_carrito"))

    return _reconstruir_carrito_desde_pedido(pedido)


def _reconstruir_carrito_desde_pedido(pedido):
    """Añade las líneas simples aún disponibles sin adivinar opciones."""
    # Reconstrucción del carrito — merge con lo que ya haya.
    carrito = session.get("carrito", {}) or {}
    if not isinstance(carrito, dict):
        carrito = {}

    añadidos = []
    omitidos_complejos = []
    omitidos_inactivos = []
    for item in pedido.items:
        producto = item.producto
        if not producto or not producto.activo:
            omitidos_inactivos.append(
                (producto.nombre if producto else None) or f"#{getattr(item, 'producto_id', '?')}"
            )
            continue
        # Detección de item "complejo": si tiene combo, extras, sabores,
        # variant o notas → no lo reconstruimos automáticamente.
        # La forma segura de detectarlo es leer metadata_json del snapshot.
        meta = item.get_metadata() if hasattr(item, "get_metadata") else {}
        meta = meta if isinstance(meta, dict) else {}
        tiene_combo = bool(meta.get("combo"))
        tiene_extras = bool(meta.get("extras"))
        tiene_sabores = bool(meta.get("sabores"))
        tiene_variante = bool(meta.get("variant_id") or getattr(item, "variant_id", None))
        es_complejo = tiene_combo or tiene_extras or tiene_sabores or tiene_variante
        if es_complejo:
            omitidos_complejos.append(producto.nombre)
            continue
        # Producto simple → line_key legacy = str(producto_id). Sumamos
        # cantidad al carrito existente si ya lo tenía.
        line_key = str(int(producto.id))
        prev = int(carrito.get(line_key, 0) or 0)
        nueva = prev + int(item.cantidad or 0)
        if nueva <= 0:
            continue
        carrito[line_key] = nueva
        añadidos.append(f"{producto.nombre} × {int(item.cantidad or 0)}")

    if not añadidos:
        flash(
            "No pudimos añadir productos del pedido anterior — puede que ya no estén "
            "disponibles o tuvieran personalizaciones. Explora el catálogo y pide algo nuevo.",
            "warning",
        )
        return redirect(url_for("public.index"))

    _save_carrito(carrito)
    session.modified = True

    resumen = ", ".join(añadidos[:6])
    mas = f" y {len(añadidos) - 6} más" if len(añadidos) > 6 else ""
    flash(
        f"✅ Añadido a tu carrito del pedido {pedido.numero_pedido}: {resumen}{mas}.",
        "success",
    )
    if omitidos_complejos:
        flash(
            "Tu pedido anterior tenía productos con personalizaciones "
            f"({', '.join(omitidos_complejos[:5])}) — por favor añádelos manualmente "
            "para elegir opciones (sabores, extras, etc.).",
            "info",
        )
    if omitidos_inactivos:
        flash(
            f"Algunos productos ya no están disponibles: {', '.join(omitidos_inactivos[:5])}.",
            "info",
        )
    return redirect(url_for("public.ver_carrito"))


@public_bp.route("/carrito")
def ver_carrito():
    carrito = _get_carrito()
    origen = _carrito_origen(carrito)
    proveedor_id = _proveedor_id_origen(origen)
    proveedor = db.session.get(Proveedor, proveedor_id) if proveedor_id else None
    establecimiento_abierto, mensaje_cierre = _establecimiento_abierto_checkout(
        origen or "propio", proveedor
    )
    items, subtotal = _build_items_from_carrito(carrito)
    cart_productos = [item["producto"] for item in items if item.get("producto")]
    zonas_activas = ZonaEntrega.query.filter_by(activo=True)\
        .order_by(ZonaEntrega.orden, ZonaEntrega.nombre).all()
    zona_principal = zonas_activas[0] if zonas_activas else None
    envio_desde = min((float(z.precio_envio or 0) for z in zonas_activas), default=None)
    envio_hasta = max((float(z.precio_envio or 0) for z in zonas_activas), default=None)
    tiempo_desde = min((int(z.tiempo_estimado_min or 0) for z in zonas_activas), default=None)
    tiempo_hasta = max((int(z.tiempo_estimado_min or 0) for z in zonas_activas), default=None)
    try:
        radio_entrega_km = max(0.0, float(SiteConfig.get("RADIO_ENTREGA_KM", "5") or 5))
    except (TypeError, ValueError):
        radio_entrega_km = 5.0
    cart_max_qty = _cart_max_qty()
    pedido_minimo = get_pedido_minimo()
    compat = _cart_compatibility(
        cart_productos,
        subtotal=subtotal,
        pedido_minimo=pedido_minimo,
        check_zone_availability=True,
    )
    fulfillment_options = compat["fulfillment_options"]
    fulfillment_unavailable = compat["fulfillment_unavailable"]
    option_issue = next(
        (item.get("product_options_error") for item in items if item.get("product_options_error")),
        None,
    )
    cart_issue = option_issue or (compat["message"] if items and not compat["ok"] else None)
    return render_template("public/carrito.html",
                           items=items, subtotal=subtotal,
                           pedido_minimo=pedido_minimo,
                           zona_principal=zona_principal,
                           zonas_activas=zonas_activas,
                           envio_desde=envio_desde,
                           envio_hasta=envio_hasta,
                           tiempo_desde=tiempo_desde,
                           tiempo_hasta=tiempo_hasta,
                           radio_entrega_km=radio_entrega_km,
                           fulfillment_options=fulfillment_options,
                           fulfillment_unavailable=fulfillment_unavailable,
                           fulfillment_badge=_product_fulfillment_badge,
                           fulfillment_mode_label=_fulfillment_mode_label,
                           cart_issue=cart_issue,
                           fecha_entrega_programada=compat.get("scheduled_date"),
                           cart_max_qty=cart_max_qty,
                           origen_actual=origen,
                           establecimiento_abierto=establecimiento_abierto,
                           mensaje_cierre=mensaje_cierre,
                           establecimiento=_establecimiento_para_origen(origen))


@public_bp.route("/carrito/canjear-puntos-quitar", methods=["POST"])
def quitar_puntos_carrito():
    session.pop("cart_puntos", None)
    session.pop("cart_producto_canje_id", None)
    session.modified = True
    return jsonify({"ok": True})


@public_bp.route("/carrito/set-producto-canje", methods=["POST"])
def set_producto_canje():
    if not _feature_enabled("puntos"):
        return jsonify({"ok": False, "msg": f'{get_loyalty_terms()["name"]} no está habilitado'}), 403
    data = request.get_json(silent=True) or {}
    prod_id = data.get("producto_id")
    if prod_id:
        try:
            prod_id = int(prod_id)
        except (ValueError, TypeError):
            return jsonify({"ok": False, "msg": "producto_id inválido"}), 400
        producto = db.session.get(Product, prod_id)
        origen = _carrito_origen()
        productos_carrito = _cart_products_from_carrito(_get_carrito())
        if (
            not producto
            or not origen
            or not _producto_canjeable_en_origen(producto, origen)
            or not _cart_compatibility(productos_carrito + [producto])["ok"]
        ):
            return jsonify({
                "ok": False,
                "msg": "Esta recompensa no es compatible con los productos del carrito",
            }), 400
        cart_puntos = session.get("cart_puntos") or {}
        puntos_disponibles = 0
        if cart_puntos.get("cliente_id") and cart_puntos.get("origen") == origen:
            puntos_disponibles = int(cart_puntos.get("puntos_totales") or 0)
        if int(producto.puntos_para_canje or 0) > puntos_disponibles:
            return jsonify({"ok": False, "msg": f'No tienes suficientes {get_loyalty_terms()["plural"]} para este producto'}), 400
        session["cart_producto_canje_id"] = prod_id
    else:
        session.pop("cart_producto_canje_id", None)
    session.modified = True
    return jsonify({"ok": True})


@public_bp.route("/api/producto/<int:producto_id>/opciones")
def api_producto_opciones(producto_id):
    """Payload público con presentaciones, sabores y política sabor↔tamaño.

    El frontend usa este JSON para filtrar sabores permitidos cuando el cliente
    cambia el tamaño, y para aplicar min/max por presentación. Fuente de verdad
    única: se calcula en el servidor a partir del catálogo del producto.
    """
    from product_options_service import (
        product_option_catalog_payload,
        flavor_policy_for_presentation,
    )
    producto = get_or_404(Product, producto_id)
    presentaciones = product_presentation_catalog_payload(producto)
    # Política sin tamaño (default) para productos sin presentaciones.
    if not presentaciones:
        default_policy = flavor_policy_for_presentation(producto, None)
    else:
        default_policy = None
    return jsonify({
        "ok": True,
        "producto_id": producto.id,
        "nombre": producto.nombre,
        "presentaciones": presentaciones,
        "opciones": product_option_catalog_payload(producto, None),
        "default_flavor_policy": default_policy,
    })


@public_bp.route("/api/public/cliente")
@limiter.limit("20 per minute") if limiter else (lambda f: f)
def buscar_cliente_publico():
    """Valida el formato sin revelar si el teléfono pertenece a un cliente."""
    telefono = _normalize_phone(request.args.get("telefono", ""))
    if not telefono or len(re.sub(r"\D", "", telefono)) < 7:
        return _json_no_store({"ok": False, "msg": "Telefono requerido"}, 400)

    return _json_no_store({
        "ok": True,
        "telefono": telefono,
    })


@public_bp.route("/puntos/consultar-saldo", methods=["POST"])
@limiter.limit("3 per minute") if limiter else (lambda f: f)
def consultar_saldo_puntos():
    """Envía el saldo al número consultado sin revelarlo en el navegador.

    Diseño: respuesta neutra (no revela si el número existe). Sí revela si el
    canal de mensajería está caído, para que el usuario reintente más tarde
    en vez de creer que llegará y no llegue nunca."""
    if not _feature_enabled("puntos"):
        return _json_no_store({"ok": False, "msg": f'{get_loyalty_terms()["name"]} no está habilitado'}, 403)
    from loyalty_service import messaging_service_available
    if not messaging_service_available():
        return _json_no_store({
            "ok": False,
            "service_available": False,
            "msg": "El servicio de WhatsApp no está disponible ahora mismo. Reintenta en unos minutos.",
        }, 503)
    data = request.get_json(silent=True) or {}
    cliente, _ = buscar_cliente_por_telefono(data.get("telefono", ""))
    if cliente:
        try:
            enviar_saldo_puntos(cliente)
        except Exception:
            current_app.logger.exception("No se pudo enviar el saldo de puntos")
    return _json_no_store({
        "ok": True,
        "service_available": True,
        "msg": f'Si el número tiene {get_loyalty_terms()["plural"]}, recibirá el saldo por WhatsApp.',
    })


# ─── CHECK DIRECCIÓN EN TIEMPO REAL (AJAX) ────────────────────

@public_bp.route("/api/check-address", methods=["POST"])
@csrf.exempt
@limiter.limit("30 per minute") if limiter else (lambda f: f)
def api_check_address():
    """Valida si una dirección está dentro del radio de entrega. Sin autenticación requerida."""
    data = request.get_json(silent=True) or {}
    if not _feature_enabled("delivery"):
        return jsonify({"ok": False, "mensaje": "El delivery no está habilitado."}), 403
    if data.get("lat") is not None and data.get("lng") is not None:
        resultado = validar_radio_entrega(
            (data.get("direccion") or "").strip(),
            lat=data.get("lat"),
            lon=data.get("lng"),
            precision_m=data.get("accuracy"),
            exigir_precision=True,
            exigir_direccion=True,
        )
        zona = (
            db.session.get(ZonaEntrega, resultado.get("zona_id"))
            if resultado.get("zona_id") else None
        )
        if zona:
            resultado["zona"] = {
                "id": zona.id,
                "nombre": zona.nombre,
                "precio_envio": float(zona.precio_envio or 0),
                "gratis_desde": (
                    float(zona.gratis_desde)
                    if zona.gratis_desde is not None else None
                ),
                "tiempo_estimado_min": zona.tiempo_estimado_min,
            }
        return jsonify(resultado)
    direccion = (data.get("direccion") or "").strip()
    if not direccion:
        return jsonify({"ok": True, "distancia_km": None, "mensaje": ""})
    if len(direccion) < 6:
        return jsonify({
            "ok": False,
            "distancia_km": None,
            "mensaje": "Escribe la dirección completa con calle y número.",
        })
    if len(direccion) > 220:
        return jsonify({"ok": False, "distancia_km": None, "mensaje": "Dirección demasiado larga"}), 400
    resultado = validar_radio_entrega(direccion)
    if resultado.get("ok"):
        zona = db.session.get(ZonaEntrega, resultado.get("zona_id")) if resultado.get("zona_id") else None
        if zona is None:
            zonas = ZonaEntrega.query.filter_by(activo=True).order_by(ZonaEntrega.orden, ZonaEntrega.nombre).all()
            zona = asignar_zona_por_direccion(direccion, zonas) if zonas else None
        if zona:
            resultado["zona"] = {
                "id": zona.id,
                "nombre": zona.nombre,
                "precio_envio": float(zona.precio_envio or 0),
                "gratis_desde": float(zona.gratis_desde) if zona.gratis_desde is not None else None,
                "tiempo_estimado_min": zona.tiempo_estimado_min,
            }
    return jsonify(resultado)


# ─── SUGERENCIAS DE DIRECCIÓN (autocompletado) ──────────────────
@public_bp.route("/api/geocode/suggest", methods=["GET"])
@csrf.exempt
@limiter.limit("30 per minute") if limiter else (lambda f: f)
def api_geocode_suggest():
    """Proxy Photon + Nominatim (OSM, ambos gratis) para autocompletado.

    Devuelve hasta 5 sugerencias acotadas al bbox del negocio (viewbox
    calculado desde CENTRO_LAT/CENTRO_LON + RADIO_ENTREGA_KM en SiteConfig).
    El cliente escribe → widget muestra sugerencias reales → al elegir una,
    el input queda relleno y sus coordenadas viajan como hidden fields en
    el POST del checkout. Así la validación server-side pasa siempre — no
    hay margen para "Calle Falsa 123".

    No cacheamos aquí porque el propio Nominatim ya cachea las respuestas
    populares y el rate-limit local (30/min) blinda contra abuso.
    """
    if not _feature_enabled("delivery"):
        return jsonify({"ok": False, "mensaje": "El delivery no está habilitado."}), 403
    q = (request.args.get("q") or "").strip()
    if len(q) < 4:
        return jsonify({"ok": True, "results": []})
    if len(q) > 220:
        return jsonify({"ok": False, "mensaje": "Consulta demasiado larga"}), 400
    try:
        import requests as _req
        import re as _re
        ciudad = (SiteConfig.get("CIUDAD_NEGOCIO", "") or "").strip()
        provincia = (SiteConfig.get("PROVINCIA_NEGOCIO", "") or "").strip()
        pais = (SiteConfig.get("PAIS_NEGOCIO", "") or "").strip()
        pais_iso = (SiteConfig.get("PAIS_CODIGO_ISO", "") or "").lower().strip()
        nombre_neg = SiteConfig.get("NOMBRE_NEGOCIO", "Oxidian")
        user_agent = f"{nombre_neg.replace(' ', '')}/1.0 (autocomplete)"
        try:
            centro_lat = float(SiteConfig.get("CENTRO_LAT", ""))
            centro_lon = float(SiteConfig.get("CENTRO_LON", ""))
            radio_km = float(SiteConfig.get("RADIO_ENTREGA_KM", "10") or 10)
        except (ValueError, TypeError):
            centro_lat = centro_lon = None
            radio_km = 10.0
        # Bbox × 2.0 (antes 1.3): garantiza que direcciones en el borde
        # de la zona de reparto salgan en el autocompletado. El bbox es
        # para SUGERENCIAS, la validación real de cobertura la hace
        # /api/check-address con el polígono estricto.
        import math
        bbox_params = None
        if centro_lat is not None and centro_lon is not None and radio_km > 0:
            delta_lat = (radio_km * 2.0) / 111.0
            delta_lon = (radio_km * 2.0) / (111.0 * max(0.2, math.cos(math.radians(centro_lat))))
            left = centro_lon - delta_lon
            right = centro_lon + delta_lon
            top = centro_lat + delta_lat
            bottom = centro_lat - delta_lat
            bbox_params = {"viewbox": f"{left},{top},{right},{bottom}", "bounded": 1}

        def _do_nominatim(params):
            merged = {"format": "json", "limit": 5, "addressdetails": 1, **params}
            if pais_iso:
                merged["countrycodes"] = pais_iso
            if bbox_params:
                merged.update(bbox_params)
            r = _req.get(
                "https://nominatim.openstreetmap.org/search",
                params=merged,
                headers={"User-Agent": user_agent, "Accept-Language": "es"},
                timeout=5,
            )
            return r.json() if r.ok else []

        def _do_photon(query):
            # Photon (photon.komoot.io) es gratis, opensource y está optimizado
            # para autocompletado en tiempo real. Sesga por lat/lon → puntúa mejor
            # las direcciones cercanas al negocio. Devuelve GeoJSON; lo mapeamos
            # al formato de Nominatim para reutilizar el pipeline de filtrado.
            # Photon soporta idiomas limitados (default/de/en/fr). No pasamos
            # `lang=es` porque devuelve 400; con default los nombres vienen
            # tal como están en OSM (calle Andalucía sale igual).
            params = {"q": query, "limit": 15}
            if centro_lat is not None and centro_lon is not None:
                params["lat"] = centro_lat
                params["lon"] = centro_lon
            try:
                r = _req.get(
                    "https://photon.komoot.io/api/",
                    params=params,
                    headers={"User-Agent": user_agent, "Accept-Language": "es"},
                    timeout=5,
                )
                if not r.ok:
                    return []
                data = r.json() or {}
            except Exception:
                return []
            out = []
            for feat in data.get("features", []) or []:
                props = feat.get("properties", {}) or {}
                coords = (feat.get("geometry") or {}).get("coordinates") or []
                if len(coords) < 2:
                    continue
                lon, lat = coords[0], coords[1]
                out.append({
                    "lat": lat, "lon": lon,
                    "display_name": ", ".join(filter(None, [
                        props.get("name"), props.get("street"),
                        props.get("housenumber"), props.get("city") or props.get("town"),
                    ])),
                    "address": {
                        "road": props.get("street") or props.get("name"),
                        "house_number": props.get("housenumber"),
                        "city": props.get("city"),
                        "town": props.get("town"),
                        "village": props.get("village"),
                        "municipality": props.get("county"),
                    },
                })
            return out

        # Estrategia en cascada — Photon (autocompletado real) primero,
        # Nominatim estructurada después (si hay número), Nominatim libre
        # como último recurso. Photon puntúa por proximidad al centro del
        # negocio así que ya llega ordenado por relevancia local.
        num_match = _re.search(r"(.+?)\s+(\d+[a-zA-Z]?)\s*$", q)
        hits = _do_photon(q) or []
        # Cuando el cliente escribió calle+número (ej. "Calle Andalucía 12"),
        # Photon a veces devuelve otra calle numerada cercana al bias point
        # (ej. "Calle Romero 12"). Filtramos por coincidencia parcial del
        # nombre de calle para que el repartidor no reciba una dirección que
        # no corresponde a lo escrito.
        if num_match and hits:
            import unicodedata as _ud
            STOP = {"calle", "c", "avenida", "avda", "av", "plaza", "pza",
                    "paseo", "camino", "carretera", "ctra", "callejon",
                    "travesia", "ronda", "glorieta", "alameda",
                    "de", "del", "la", "el", "los", "las", "y"}
            def _norm(s):
                s = _ud.normalize("NFD", s.casefold())
                return "".join(c for c in s if _ud.category(c) != "Mn")
            def _tokens(s):
                return {w for w in _re.findall(r"[a-z]+", _norm(s))
                        if len(w) > 2 and w not in STOP}
            palabras_query = _tokens(num_match.group(1))
            def _street_matches(h):
                addr = h.get("address", {}) or {}
                st = addr.get("road") or addr.get("pedestrian") or ""
                if not st: return False
                return bool(palabras_query & _tokens(st)) if palabras_query else True
            hits_filtrados = [h for h in hits if _street_matches(h)]
            hits = hits_filtrados or []
        if not hits and num_match:
            calle_raw = num_match.group(1).strip()
            numero = num_match.group(2).strip()
            structured = {"street": f"{numero} {calle_raw}"}
            if ciudad:
                structured["city"] = ciudad
            if provincia:
                structured["state"] = provincia
            if pais:
                structured["country"] = pais
            hits = _do_nominatim(structured) or []
        if not hits:
            hits = _do_nominatim({"q": q}) or []
    except Exception:
        current_app.logger.exception("api_geocode_suggest falló")
        return jsonify({"ok": False, "results": []}), 503

    # Compactamos la respuesta: sólo campos que el widget necesita.
    # Filtro geométrico: aceptamos cualquier calle a ≤ radio_km × 2 del centro
    # del negocio. Antes filtrábamos por `addr.city == CIUDAD_NEGOCIO`, pero
    # Photon/Nominatim etiquetan calles periféricas con la pedanía (Guadajoz,
    # etc.) o dejan `city` vacío, y se perdían direcciones reales de reparto.
    # La distancia al centro es el criterio verdadero: si el repartidor llega,
    # es válida. `/api/check-address` sigue aplicando el polígono estricto
    # después.
    def _dist_km(la, lo):
        import math as _m
        if centro_lat is None or centro_lon is None:
            return 0.0
        dlat = _m.radians(la - centro_lat)
        dlon = _m.radians(lo - centro_lon)
        a = _m.sin(dlat/2)**2 + _m.cos(_m.radians(centro_lat)) * _m.cos(_m.radians(la)) * _m.sin(dlon/2)**2
        return 6371.0 * 2 * _m.asin(min(1.0, _m.sqrt(a)))
    limite_km = radio_km * 2.0 if radio_km else 999.0

    def _shape(raw_hits):
        out = []
        seen_local = set()
        for h in raw_hits:
            try:
                lat = float(h.get("lat"))
                lon = float(h.get("lon"))
            except (TypeError, ValueError):
                continue
            addr = h.get("address", {}) or {}
            street = (addr.get("road") or addr.get("pedestrian") or "").strip()
            house = (addr.get("house_number") or "").strip()
            if not street:
                continue
            localidad = (addr.get("city") or addr.get("town") or addr.get("village")
                         or addr.get("municipality") or "").strip()
            if _dist_km(lat, lon) > limite_km:
                continue
            label_parts = [f"{street} {house}".strip() if house else street]
            if localidad and (not ciudad or localidad.casefold() != ciudad.casefold()):
                label_parts.append(localidad)
            elif ciudad and not localidad:
                label_parts.append(ciudad)
            label = ", ".join(label_parts) or (h.get("display_name") or "").strip()
            key = label.casefold()
            if key in seen_local:
                continue
            seen_local.add(key)
            out.append({
                "label": label, "lat": lat, "lon": lon, "value": label,
                "has_number": bool(house),
                "_dist": _dist_km(lat, lon),
            })
        return out

    results = _shape(hits)

    # Retry con solo el nombre de calle si el número mató todos los resultados
    # (OSM rara vez tiene números mapeados en pueblos pequeños). El widget deja
    # al cliente añadir el número al label sin perder coordenadas.
    if not results and num_match:
        try:
            calle_solo = num_match.group(1).strip()
            hits2 = _do_photon(calle_solo) or _do_nominatim({"q": calle_solo}) or []
            results = _shape(hits2)
        except Exception:
            current_app.logger.exception("api_geocode_suggest retry sin número falló")

    # Ranking: has_number primero (más útil para el repartidor), luego por
    # distancia al centro del negocio (calles del casco urbano antes que
    # periféricas).
    results.sort(key=lambda r: (0 if r.get("has_number") else 1, r.get("_dist", 0)))
    for r in results:
        r.pop("_dist", None)
    return jsonify({"ok": True, "results": results[:8]})


# ─── VALIDAR CUPÓN (AJAX) ────────────────────

def _cliente_id_actual():
    """Devuelve `current_user.id` si es cliente logueado, None si guest.
    Se usa para aplicar el límite por cliente en cupones/afiliados."""
    try:
        if current_user.is_authenticated and getattr(current_user, "rol", None) == "cliente":
            return current_user.id
    except Exception:
        pass
    return None


@public_bp.route("/carrito/cupon", methods=["POST"])
def validar_cupon():
    data = request.get_json(silent=True) or {}
    codigo = data.get("codigo", "").strip().upper()
    try:
        subtotal = float(data.get("subtotal", 0))
    except (ValueError, TypeError):
        subtotal = 0.0
    cupon = Coupon.query.filter_by(codigo=codigo).first()
    if not cupon:
        return jsonify({"ok": False, "msg": "Cupón no encontrado"})
    # Límite por cliente antes de calcular descuento: si el cliente ya usó
    # este cupón el máximo permitido, no tiene sentido mostrarle el monto.
    ok_cliente, msg_cliente = cupon.es_valido_para_cliente(_cliente_id_actual())
    if not ok_cliente:
        return jsonify({"ok": False, "msg": msg_cliente})
    try:
        descuento = cupon.calcular_descuento(subtotal)
        # Persistir en sesión para que checkout lo aplique automáticamente
        # sin obligar al cliente a reintroducirlo. `checkout()` sigue
        # aceptando el POST del formulario como override.
        session["cart_cupon"] = {"id": cupon.id, "codigo": cupon.codigo}
        session.modified = True
        return jsonify({"ok": True, "descuento": descuento, "cupon_id": cupon.id,
                        "descripcion": cupon.descripcion,
                        "codigo": cupon.codigo})
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)})


@public_bp.route("/carrito/cupon/quitar", methods=["POST"])
def quitar_cupon_sesion():
    """Limpia el cupón guardado en sesión (aplicado desde el carrito)."""
    session.pop("cart_cupon", None)
    session.modified = True
    return jsonify({"ok": True})


@public_bp.route("/carrito/afiliado", methods=["POST"])
def validar_afiliado():
    data = request.get_json(silent=True) or {}
    codigo = data.get("codigo", "").strip().upper()
    try:
        subtotal = float(data.get("subtotal", 0))
    except (ValueError, TypeError):
        subtotal = 0.0
    af = AffiliateCode.query.filter_by(codigo=codigo).first()
    if not af:
        session.pop("cart_afiliado", None)
        session.modified = True
        return jsonify({"ok": False, "msg": "Código de afiliado no encontrado"})
    ok, reason = af.es_valido_para_cliente(_cliente_id_actual())
    if not ok:
        session.pop("cart_afiliado", None)
        session.modified = True
        return jsonify({"ok": False, "msg": reason or "Código no válido o expirado"})
    # Misma fuente de verdad del pedido. Evita anunciar en pantalla un
    # descuento distinto al cap realmente aplicado al confirmar.
    descuento = calcular_precio([], subtotal, afiliado=af).descuento_afiliado
    session["cart_afiliado"] = {"codigo": af.codigo}
    session.modified = True
    return jsonify({"ok": True, "descuento": descuento, "codigo": af.codigo,
                    "descripcion": af.descripcion or af.codigo,
                    "descuento_tipo": af.descuento_tipo,
                    "descuento_valor": float(af.descuento_valor or 0)})


@public_bp.route("/carrito/afiliado/quitar", methods=["POST"])
def quitar_afiliado_sesion():
    session.pop("cart_afiliado", None)
    session.modified = True
    return jsonify({"ok": True})


@public_bp.route("/puntos/solicitar-codigo", methods=["POST"])
@limiter.limit("5 per minute") if limiter else (lambda f: f)
def solicitar_codigo_puntos():
    """Envía un código al WhatsApp que identifica al cliente."""
    if not _feature_enabled("puntos"):
        return jsonify({"ok": False, "msg": f'{get_loyalty_terms()["name"]} no está habilitado'}), 403
    data = request.get_json(silent=True) or {}
    telefono = data.get("telefono", "").strip()
    if not telefono:
        return jsonify({"ok": False, "msg": "Indica tu número de teléfono"})
    cliente, _ = buscar_cliente_por_telefono(telefono)
    respuesta_neutra = "Si el número está registrado, recibirá un código por WhatsApp."
    if not cliente or not cliente.telefono:
        return _json_no_store({"ok": True, "msg": respuesta_neutra})

    resultado = solicitar_codigo(cliente, permitir_sin_puntos=True)
    return jsonify({
        "ok": bool(resultado.get("ok")),
        "msg": respuesta_neutra,
    })


@public_bp.route("/puntos/verificar-codigo", methods=["POST"])
@limiter.limit("10 per minute") if limiter else (lambda f: f)
def verificar_codigo_puntos():
    """Verifica el código de puntos."""
    if not _feature_enabled("puntos"):
        return jsonify({"ok": False, "msg": f'{get_loyalty_terms()["name"]} no está habilitado'}), 403
    msg_invalido = "No se pudo verificar el código. Revisa el WhatsApp y el código recibido."
    data = request.get_json(silent=True) or {}
    telefono = data.get("telefono", "").strip()
    codigo = data.get("codigo", "").strip()
    if telefono:
        cliente, _ = buscar_cliente_por_telefono(telefono)
    else:
        return _json_no_store({"ok": False, "msg": msg_invalido})

    if not cliente:
        return _json_no_store({"ok": False, "msg": msg_invalido})

    from loyalty_service import bloquear_cliente_puntos
    cliente = bloquear_cliente_puntos(cliente)
    # Primero autenticamos sin consumir. Así un carrito inválido no quema un
    # código correcto, pero un atacante sin OTP sólo recibe respuesta neutra.
    if not cliente.verificar_cod_puntos(codigo, consumir=False):
        db.session.commit()  # persiste incremento de intentos fallidos
        return _json_no_store({"ok": False, "msg": msg_invalido})

    origen = _carrito_origen()
    if not origen:
        db.session.rollback()
        return jsonify({"ok": False, "msg": "El carrito no tiene un origen de inventario válido"})
    items, _ = _build_items_from_carrito(_get_carrito())
    productos_carrito = [item["producto"] for item in items if item.get("producto")]
    # Diseño: los puntos SOLO se canjean por productos canjeables (nunca como
    # descuento en euros). Ignoramos cualquier `puntos_usar` suelto sin producto
    # asociado y forzamos que el consumo de puntos venga ligado a un product_id.
    producto_canje_id = data.get("producto_canje_id")
    if producto_canje_id:
        try:
            producto_canje_id = int(producto_canje_id)
        except (ValueError, TypeError):
            producto_canje_id = None

    # Valida primero el contexto del canje. Un producto/cart inválido no debe
    # consumir un OTP correcto ni dejar una sesión verificada a medias.
    producto_canje = None
    if producto_canje_id:
        producto_canje = db.session.get(Product, producto_canje_id)
        if (
            not producto_canje
            or not _producto_canjeable_en_origen(producto_canje, origen)
        ):
            db.session.rollback()
            return jsonify({"ok": False, "msg": "Producto de canje no válido"})
        puntos_producto = int(producto_canje.puntos_para_canje or 0)
        if puntos_producto <= 0 or puntos_producto > int(cliente.puntos or 0):
            db.session.rollback()
            return jsonify({"ok": False, "msg": f'No tienes suficientes {get_loyalty_terms()["plural"]} para este producto'})

    # Mismo lock, mismo OTP: la segunda verificación lo consume de forma
    # atómica una vez que todo el contexto resultó válido.
    if not cliente.verificar_cod_puntos(codigo, consumir=True):
        db.session.rollback()
        return _json_no_store({"ok": False, "msg": msg_invalido})

    # El lock y el commit hacen el OTP de un solo uso incluso con dos requests
    # simultáneas del navegador.
    db.session.commit()

    puntos_usar = 0
    descuento = 0.0  # los puntos nunca reducen el total en euros
    if producto_canje_id:
        puntos_producto = int(producto_canje.puntos_para_canje or 0)
        session["cart_producto_canje_id"] = producto_canje_id
        # Sólo registramos los puntos del producto canjeado; sin descuento monetario.
        puntos_usar = puntos_producto
    else:
        session.pop("cart_producto_canje_id", None)

    # Guardar en sesión del carrito para usarlo en checkout
    session["cart_puntos"] = {
        "cliente_id": cliente.id,
        "telefono": cliente.telefono,
        "puntos_usados": puntos_usar,
        "descuento": descuento,
        "puntos_totales": cliente.puntos,
        "verificado": True,
        "origen": origen,
    }
    session.modified = True

    payload = _canjeables_payload(cliente, origen, productos_carrito)
    return jsonify({"ok": True, "puntos_verificados": puntos_usar, "descuento": descuento,
                    "msg": "✓ WhatsApp verificado", "puntos_totales": cliente.puntos, **payload})


# ─── CHECKOUT ────────────────────────────────

# Rate limit del POST del checkout: previene doble-tap y scripts que crean
# pedidos duplicados. Combinado con la idempotency key del carrito, es la
# doble defensa: idempotency evita el duplicado exacto, el limiter frena la
# tormenta. Aplicamos por IP del cliente (default de flask-limiter) que es
# suficiente para el caso legítimo (usuario impaciente clickando). El GET
# no está afectado — la página del checkout puede recargarse tranquilamente.
@public_bp.route("/checkout", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"]) if limiter else (lambda f: f)
def checkout():
    if current_user.is_authenticated:
        flash("Las cuentas internas no compran desde la tienda pública. Usa el módulo POS.", "warning")
        return redirect(url_for("public.index"))

    carrito = _get_carrito()
    if not carrito:
        flash("Tu carrito está vacío.", "warning")
        return redirect(url_for("public.ver_carrito"))

    origen = _carrito_origen(carrito)
    establecimiento = _establecimiento_para_origen(origen)
    if not origen or not establecimiento:
        flash("El carrito no es válido. Vacíalo y vuelve a añadir los productos.", "warning")
        return redirect(url_for("public.ver_carrito"))
    proveedor_id = _proveedor_id_origen(origen)
    proveedor = db.session.get(Proveedor, proveedor_id) if proveedor_id else None
    if proveedor_id and (not proveedor or not proveedor.disponible_para_venta):
        flash("El establecimiento de este pedido está cerrado o ya no está activo.", "warning")
        return redirect(establecimiento["url"])
    establecimiento_abierto, mensaje_cierre = _establecimiento_abierto_checkout(
        origen, proveedor
    )

    items, subtotal = _build_items_from_carrito(carrito)
    if not items:
        flash("Los productos del carrito ya no están disponibles.", "warning")
        # Vaciado COMPLETO usando el helper canónico. Antes se hacían pops
        # parciales de solo 3 keys y quedaban huérfanas notas_combo,
        # presentaciones_carrito, variantes_carrito, cart_puntos y
        # cart_producto_canje_id — misma clase de bug que arregló PR #12
        # para modificar_cantidades.
        _save_carrito({})
        return redirect(url_for("public.index"))
    if len(items) != len(carrito):
        flash(
            "Uno o más productos cambiaron de disponibilidad o stock. "
            "Revisa el carrito antes de confirmar.",
            "warning",
        )
        return redirect(url_for("public.ver_carrito"))
    option_issue = next(
        (item.get("product_options_error") for item in items if item.get("product_options_error")),
        None,
    )
    if option_issue:
        flash(option_issue, "warning")
        return redirect(url_for("public.ver_carrito"))
    # Compatibilidad de origen: comparamos el origen LOGÍSTICO (el que
    # despacha la tienda) — no el origen de inventario crudo. Un producto
    # de socio-capital (`modelo_acuerdo="socio_porcentaje"`) tiene inventario
    # en `proveedor:X` pero se despacha como "propio" porque la tienda lo
    # opera y solo cobra comisión al socio. Antes usábamos
    # `pertenece_a_origen(origen)` que compara estrictamente por
    # `proveedor_despachador_id` y bloqueaba con "Hay productos
    # incompatibles..." cualquier carrito que mezclara propios con
    # socio-capital (o incluso 1 producto de socio solo), redirigiendo
    # silenciosamente al carrito sin poder llegar a checkout. La
    # coerción vía `_origen_logistico(_origen_inventario_producto(item))`
    # replica exactamente la misma normalización que `_carrito_origen`
    # aplica al guardar el carrito, así el chequeo queda consistente.
    incompat = [
        item["producto"]
        for item in items
        if _origen_logistico(_origen_inventario_producto(item["producto"])) != origen
    ]
    if incompat:
        current_app.logger.warning(
            "checkout incompat: carrito_origen=%r items_offending=%s",
            origen,
            [(p.id, _origen_inventario_producto(p)) for p in incompat],
        )
        flash("Hay productos incompatibles con el origen de inventario del carrito.", "warning")
        return redirect(url_for("public.ver_carrito"))
    pedido_minimo = get_pedido_minimo()
    cart_productos = [item["producto"] for item in items if item.get("producto")]
    compat = _cart_compatibility(
        cart_productos,
        subtotal=subtotal,
        pedido_minimo=pedido_minimo,
        check_zone_availability=True,
    )
    if not compat["ok"]:
        flash(compat["message"], compat["issues"][0].get("severity", "warning"))
        return redirect(url_for("public.ver_carrito"))
    fulfillment_options = compat["fulfillment_options"]
    fulfillment_unavailable = compat["fulfillment_unavailable"]
    fulfillment_default = "delivery" if "delivery" in fulfillment_options else fulfillment_options[0]
    zonas = ZonaEntrega.query.filter_by(activo=True)\
        .order_by(ZonaEntrega.orden, ZonaEntrega.nombre).all()

    # Los productos son inmediatos o programados; no se solicita fecha manual de encargo.
    tiene_encargos = False

    # Los puntos solo se habilitan después de verificar el WhatsApp en esta sesión.
    puntos_habilitados = _feature_enabled("puntos")
    cart_puntos_sesion = session.get("cart_puntos", {}) if puntos_habilitados else {}
    if cart_puntos_sesion.get("origen") == origen:
        puntos_cliente = cart_puntos_sesion.get("puntos_totales", 0)
    else:
        cart_puntos_sesion = {}
        puntos_cliente = 0
        session.pop("cart_producto_canje_id", None)

    canjeables = [
        p for p in _productos_canjeables_disponibles(origen, cart_productos)
        if int(p.puntos_para_canje or 0) <= int(puntos_cliente or 0)
    ] if puntos_habilitados and puntos_cliente > 0 else []
    canje_seleccionado = session.get("cart_producto_canje_id")
    if canje_seleccionado and not any(p.id == canje_seleccionado for p in canjeables):
        # El catálogo, stock o carrito pudo cambiar después de seleccionar. No
        # conservamos una recompensa que la interfaz ya no puede explicar.
        session.pop("cart_producto_canje_id", None)
        session.modified = True
    if request.method == "POST":
        if request.form.get("acepta_condiciones") != "1":
            flash(
                "Para confirmar el pedido debes aceptar las condiciones de compra y declarar que has leído la información de privacidad.",
                "warning",
            )
            return redirect(url_for("public.checkout"))
        # ── Idempotency guard ──────────────────────────────────────
        # Evita que un double-click o un retry del cliente cree dos pedidos.
        # Si la misma combinación (user/telefono + body) llegó hace <30 s,
        # devolvemos el pedido ya creado en vez de duplicarlo.
        auto_seed = (
            (request.form.get("telefono_invitado") or "") + ":" + (request.remote_addr or "")
        )
        idem_key = request_idempotency_key("checkout_web", auto_seed=auto_seed)
        body_h = request_body_hash()
        prev = IdempotencyKey.query.filter_by(scope="checkout_web", key=idem_key).first()
        if prev:
            if prev.request_hash != body_h:
                flash(
                    "Detectamos un envío duplicado con datos distintos. "
                    "Recarga la página antes de volver a intentarlo.",
                    "warning",
                )
                return redirect(url_for("public.ver_carrito"))
            if prev.order_id:
                try:
                    cached = json.loads(prev.response_body or "{}")
                except (TypeError, json.JSONDecodeError):
                    cached = {}
                token = cached.get("token")
                if token:
                    guest_tokens = session.get("guest_order_tokens", {})
                    guest_tokens[str(prev.order_id)] = {
                        "token": token,
                        "exp": int(datetime.utcnow().timestamp()) + GUEST_ORDER_TOKEN_TTL_S,
                    }
                    session["guest_order_tokens"] = guest_tokens
                    session["last_guest_order_id"] = prev.order_id
                    session["last_guest_order_token"] = token
                    session.modified = True
                flash("Este pedido ya se había procesado. Te lo mostramos aquí.", "info")
                confirm_args = {"pedido_id": prev.order_id}
                if token:
                    confirm_args["token"] = token
                return redirect(url_for("public.pedido_confirmado", **confirm_args))

        # Atajo exclusivo de pruebas automatizadas. Una variable accidental en
        # producción nunca puede desactivar la cobertura.
        _skip_val = bool(current_app.testing and current_app.config.get(
            "SKIP_DELIVERY_VALIDATION", False
        ))
        abierto, msg_cierre = _establecimiento_abierto_checkout(origen, proveedor)
        if not _skip_val and not abierto:
            flash(msg_cierre, "warning")
            return redirect(url_for("public.checkout"))
        if proveedor_id:
            proveedor = db.session.get(Proveedor, proveedor_id)
            if not proveedor or not proveedor.disponible_para_venta:
                flash("El establecimiento cerró antes de confirmar el pedido. Tu carrito se conserva.", "warning")
                return redirect(establecimiento["url"])

        tipo_entrega_cliente = _fulfillment_from_request(fulfillment_default, fulfillment_options)
        # Franja horaria opcional (módulo delivery_franjas_activo). Solo aplica
        # cuando el cliente eligió delivery; recogida y otros modos la ignoran.
        # La reserva efectiva del cupo se hace tras crear el pedido para poder
        # asociar pedido.id ↔ slot. Aquí solo capturamos el valor bruto.
        _slot_id_bruto = (request.form.get("slot_id") or "").strip()
        slot_id_solicitado: int | None = None
        if _slot_id_bruto:
            try:
                slot_id_solicitado = int(_slot_id_bruto)
            except (TypeError, ValueError):
                slot_id_solicitado = None
        if not tipo_entrega_cliente:
            solicitado = (request.form.get("tipo_entrega_cliente") or "").strip().lower()
            blockers = _fulfillment_blockers_for_mode([item["producto"] for item in items], solicitado)
            if blockers:
                nombres = ", ".join(f"«{p.nombre}»" for p in blockers[:5])
                flash(
                    f"No se puede confirmar {_fulfillment_mode_label(solicitado).lower()}: "
                    f"{nombres} no admite{'n' if len(blockers) > 1 else ''} esa modalidad.",
                    "danger",
                )
            else:
                flash("La modalidad seleccionada ya no está disponible.", "danger")
            return redirect(url_for("public.ver_carrito"))
        direccion = request.form.get("direccion", "").strip()
        # Campo separado "piso/puerta/referencias". Se mantiene APARTE de la
        # dirección de calle+número que se pasa al geocoder — Nominatim NO
        # reconoce "1º bajo" ni "portal B" (son datos internos del edificio,
        # no del callejero). La composición final para el pedido se hace
        # tras la validación de zona → así el repartidor recibe la dirección
        # completa "Calle Andalucía 20, 1º bajo" pero la validación pasa
        # sólo por "Calle Andalucía 20".
        direccion_detalles = (request.form.get("direccion_detalles") or "").strip()
        if len(direccion_detalles) > 120:
            direccion_detalles = direccion_detalles[:120]
        ubicacion_lat = request.form.get("direccion_lat")
        ubicacion_lng = request.form.get("direccion_lng")
        ubicacion_precision = request.form.get("direccion_precision_m")
        if tipo_entrega_cliente == "recogida":
            direccion = ""
            direccion_detalles = ""
            ubicacion_lat = ubicacion_lng = ubicacion_precision = None
        metodo_pago = normalizar_metodo_pago(request.form.get("metodo_pago"))
        # Todos los pagos se cobran al entregar. El cliente sólo indica la
        # preferencia para que el repartidor lleve el instrumento adecuado
        # (dinero para cambio, teléfono para Bizum, o datáfono para tarjeta).
        if metodo_pago == "efectivo" and SiteConfig.get("EFECTIVO_HABILITADO", "1") != "1":
            flash("El pago en efectivo no está habilitado ahora mismo. Elige otro método.", "danger")
            return redirect(url_for("public.checkout"))
        if metodo_pago == "bizum":
            if SiteConfig.get("BIZUM_HABILITADO", "1") != "1" or not SiteConfig.get("BIZUM_TELEFONO", ""):
                flash("El pago mediante Bizum no está disponible ahora mismo. Elige otro método.", "danger")
                return redirect(url_for("public.checkout"))
        if metodo_pago == "tarjeta" and SiteConfig.get("TARJETA_HABILITADA", "1") != "1":
            flash("El pago con tarjeta (datáfono) no está disponible ahora mismo. Elige otro método.", "danger")
            return redirect(url_for("public.checkout"))
        # Defensa: si el cliente manda un método vacío o inválido, forzamos el
        # primer habilitado como fallback en vez de bloquear el checkout.
        if not metodo_pago:
            for _mp, _flag in (
                ("efectivo", SiteConfig.get("EFECTIVO_HABILITADO", "1") == "1"),
                ("bizum", SiteConfig.get("BIZUM_HABILITADO", "1") == "1" and SiteConfig.get("BIZUM_TELEFONO", "")),
                ("tarjeta", SiteConfig.get("TARJETA_HABILITADA", "1") == "1"),
            ):
                if _flag:
                    metodo_pago = _mp
                    break
        if not metodo_pago:
            flash("No hay métodos de pago disponibles ahora mismo. Contacta con la tienda.", "danger")
            return redirect(url_for("public.checkout"))
        notas = request.form.get("notas", "").strip()[:1000]
        # Agregar personalizaciones de combos a las notas
        notas_combo = session.get("notas_combo", {})
        if notas_combo:
            notas_combo_txt = " | ".join(f"Combo {k}: {v}" for k, v in notas_combo.items())
            notas = (notas + " [" + notas_combo_txt + "]").strip() if notas else "[" + notas_combo_txt + "]"
        cupon_id = request.form.get("cupon_id", type=int)
        cupon_codigo = request.form.get("cupon_codigo", "").strip().upper()
        # Fallback a la sesión para conservar una validación previa del propio
        # checkout ante recargas o retornos del navegador. Form-value gana.
        if not cupon_id:
            _sess_cupon = session.get("cart_cupon") or {}
            if _sess_cupon.get("id"):
                cupon_id = int(_sess_cupon["id"])
                cupon_codigo = _sess_cupon.get("codigo") or cupon_codigo
        zona_id = request.form.get("zona_id", type=int)
        nombre_invitado = request.form.get("nombre_invitado", "").strip()[:100]
        telefono_invitado_raw = request.form.get("telefono_invitado", "")
        # Sin prefijo configurado, aceptar un número local crea dos identidades:
        # checkout guarda +6… y WhatsApp responde desde +<país>6…. Es preferible
        # detener el pedido y explicar cómo corregirlo antes de perder su vínculo.
        if telefono_local_ambiguo(telefono_invitado_raw):
            flash(
                "Escribe tu teléfono con prefijo internacional (por ejemplo, +34…) "
                "para que podamos identificar tu pedido por WhatsApp.",
                "danger",
            )
            return redirect(url_for("public.checkout"))
        telefono_invitado = _normalize_phone(telefono_invitado_raw)
        codigo_afiliado_str = request.form.get("codigo_afiliado", "").strip().upper()
        # Fallback a la sesión (igual que cupón) ante recarga del checkout.
        if not codigo_afiliado_str:
            _sess_afil = session.get("cart_afiliado") or {}
            if _sess_afil.get("codigo"):
                codigo_afiliado_str = _sess_afil["codigo"]
        producto_canje_raw = request.form.get("producto_canje_id")
        producto_canje_id = None
        if producto_canje_raw not in (None, ""):
            try:
                producto_canje_id = int(producto_canje_raw)
            except (TypeError, ValueError):
                flash("Producto de canje no válido.", "danger")
                return redirect(url_for("public.checkout"))
        for item in items:
            producto = item["producto"]
            if tipo_entrega_cliente not in _product_fulfillment_modes(producto):
                flash(
                    f"«{producto.nombre}» no admite {_fulfillment_mode_label(tipo_entrega_cliente).lower()}. "
                    "Retíralo o elige una modalidad compatible.",
                    "danger",
                )
                return redirect(url_for("public.ver_carrito"))
            if _delivery_family(producto) == "programado" and not _feature_enabled("pedidos_programados"):
                flash("Los pedidos por fecha se han desactivado. Retira esos productos del carrito.", "warning")
                return redirect(url_for("public.ver_carrito"))
            if not _producto_disponible_en_origen(producto, origen, item["cantidad"]):
                flash(
                    f"'{producto.nombre}' ya no está disponible en {establecimiento['nombre']}.",
                    "danger",
                )
                return redirect(url_for("public.ver_carrito"))
            if _delivery_family(producto) == "programado":
                from business_time import business_today
                if not producto.fecha_llegada or producto.fecha_llegada < business_today():
                    flash(
                        f"'{producto.nombre}' ya no tiene una fecha de entrega válida. "
                        "Retíralo del carrito o espera una nueva fecha.",
                        "danger",
                    )
                    return redirect(url_for("public.ver_carrito"))
        # Validar teléfono de invitado (mínimo 7, máximo 20 dígitos/caracteres)
        if telefono_invitado and not telefono_valido(telefono_invitado):
            flash("Teléfono inválido. Usa el prefijo internacional de tu país.", "danger")
            return redirect(url_for("public.checkout"))

        if tipo_entrega_cliente == "delivery" and not zonas:
            flash("No hay zonas de entrega activas. Contacta con el negocio.", "danger")
            return redirect(url_for("public.checkout"))

        # Dirección obligatoria y dentro del área de cobertura
        if tipo_entrega_cliente == "delivery" and not direccion and not _skip_val:
            flash("Indica la dirección de entrega.", "danger")
            return redirect(url_for("public.checkout"))
        geo = None
        if tipo_entrega_cliente == "delivery" and direccion:
            geo = validar_radio_entrega(
                direccion,
                lat=ubicacion_lat,
                lon=ubicacion_lng,
                precision_m=ubicacion_precision,
                exigir_precision=bool(ubicacion_lat or ubicacion_lng),
                exigir_direccion=True,
            )
            if not geo["ok"]:
                if _skip_val and geo.get("distancia_km") is None:
                    geo = {"ok": True, "distancia_km": None, "mensaje": ""}
                else:
                    flash(geo["mensaje"], "danger")
                    return redirect(url_for("public.checkout"))

        # Asignación de zona: la decide el servidor matcheando coordenadas. Si
        # alguna zona tiene geodata configurada, intentamos cuadrar al cliente
        # ahí; si ninguna zona tiene geodata, se usa el legacy zonas[0].
        zona_asignada = (
            db.session.get(ZonaEntrega, geo.get("zona_id"))
            if geo and geo.get("zona_id") else None
        )
        if zona_asignada is None and tipo_entrega_cliente == "delivery" and direccion:
            zona_asignada = asignar_zona_por_direccion(direccion, zonas)
        if zona_asignada:
            zona_id = zona_asignada.id
        else:
            if tipo_entrega_cliente == "delivery" and not _skip_val:
                # Si la recogida en local está activa, ofrecemos ese camino
                # como escape en lugar de dejar al cliente sin salida.
                if _feature_enabled("recogida"):
                    flash(
                        "Tu dirección está fuera de nuestra cobertura de delivery. "
                        "Puedes seleccionar «Recogida en local» como alternativa, "
                        "o comprueba la dirección.",
                        "warning",
                    )
                else:
                    flash(
                        "Tu dirección está fuera de todas nuestras zonas de cobertura. "
                        "Comprueba la dirección o contacta con el negocio.",
                        "danger",
                    )
                return redirect(url_for("public.checkout"))
            # Defensa en profundidad: incluso en modo testing con
            # SKIP_DELIVERY_VALIDATION=1 sólo asignamos zona por defecto
            # cuando la dirección PASÓ la validación geo (zona_asignada
            # habría sido no-None) o cuando explícitamente hay geo.ok
            # con distancia_km calculada. Antes: se asignaba zonas[0]
            # aunque la dirección fuera inválida, permitiendo crear
            # pedidos "en zona 1" con direcciones inventadas si alguien
            # ponía la flag por error en prod.
            valid_geo = bool(geo and geo.get("ok"))
            zona_id = (
                zonas[0].id
                if tipo_entrega_cliente == "delivery"
                    and zonas
                    and _skip_val
                    and valid_geo
                else None
            )

        # ── Resolver cliente ────────────────────────────────────────────
        # ValueError se lanza para errores de validación (mensaje ya legible).
        # SQLAlchemyError puede aparecer si otro request creó al mismo cliente
        # a la vez (race condition en unique constraint sobre teléfono).
        # Persistimos la dirección COMPLETA (calle+número + detalles) para
        # que el cliente no tenga que reescribir el piso en el próximo pedido.
        _direccion_persistir = (
            f"{direccion}, {direccion_detalles}"
            if direccion and direccion_detalles else direccion
        )
        try:
            cliente = _resolve_checkout_customer(nombre_invitado, telefono_invitado, _direccion_persistir)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("public.checkout"))
        except Exception as exc:  # pragma: no cover — defensivo
            db.session.rollback()
            try:
                from flask import current_app as _capp
                _capp.logger.error("Error resolviendo cliente en checkout: %s", exc)
            except Exception:
                pass
            flash("Hubo un error al procesar tus datos. Vuelve a intentarlo.", "danger")
            return redirect(url_for("public.checkout"))
        if not cliente:
            flash("Para compra sin registro, indica nombre y teléfono.", "danger")
            return redirect(url_for("public.checkout"))
        cliente = bloquear_cliente_puntos(cliente)

        # ── Resolver zona ────────────────────────────────────────────────
        zona = None
        es_entrega_epicentro = True
        if tipo_entrega_cliente == "delivery" and zona_id:
            zona = db.session.get(ZonaEntrega, zona_id)
            if not zona or not zona.activo:
                flash("Zona de entrega no válida.", "danger")
                return redirect(url_for("public.checkout"))
            es_entrega_epicentro = bool(zona.es_epicentro)

        # ── Resolver cupón y afiliado (objetos, aún sin registrar uso) ──
        cupon = None
        if cupon_id:
            cupon = Coupon.query.filter_by(id=cupon_id, codigo=cupon_codigo).first()
            if not cupon:
                flash("Cupón no válido.", "danger")
                return redirect(url_for("public.checkout"))
            if cupon:
                # Valida global + límite por cliente (si el cliente está
                # identificado). Ver `Coupon.es_valido_para_cliente`.
                ok_c, msg_c = cupon.es_valido_para_cliente(cliente.id if cliente else None)
                if not ok_c:
                    flash(f"Cupón no válido: {msg_c}", "danger")
                    return redirect(url_for("public.checkout"))

        afiliado_codigo = None
        if codigo_afiliado_str:
            afiliado_codigo = (
                AffiliateCode.query
                .filter_by(codigo=codigo_afiliado_str)
                .with_for_update()
                .first()
            )
            if not afiliado_codigo:
                session.pop("cart_afiliado", None)
                flash("El código de afiliado ya no existe. Revísalo antes de continuar.", "danger")
                return redirect(url_for("public.checkout"))
            ok_a, msg_a = afiliado_codigo.es_valido_para_cliente(cliente.id if cliente else None)
            if not ok_a:
                session.pop("cart_afiliado", None)
                flash(f"Código de afiliado no válido: {msg_a}", "danger")
                return redirect(url_for("public.checkout"))

        # ── Puntos verificados en sesión ─────────────────────────────────
        # Diseño: los puntos NO reducen el total en euros. Solo se consumen al
        # canjearlos por un producto canjeable dentro del carrito. Cualquier
        # `puntos_usar` suelto del formulario se ignora silenciosamente.
        puntos_a_canjear = 0  # sin descuento libre; los puntos del producto se cargan más abajo
        cart_puntos = session.get("cart_puntos", {})
        if not puntos_habilitados:
            # Limpiar cualquier residuo de una sesión previa
            session.pop("cart_puntos", None)
            session.pop("cart_producto_canje_id", None)

        # Producto canje desde sesión solo si el formulario no envió decisión explícita.
        if producto_canje_raw is None and not producto_canje_id:
            producto_canje_id = session.get("cart_producto_canje_id")
        # Blindaje contra ID inválido en sesión (ej. corrupción por versión
        # anterior de la app). Si no es un entero coercible, ignorar el canje.
        try:
            producto_canje_id = int(producto_canje_id) if producto_canje_id else None
        except (ValueError, TypeError):
            producto_canje_id = None
            session.pop("cart_producto_canje_id", None)
        producto_canje = db.session.get(Product, producto_canje_id) if producto_canje_id else None
        if producto_canje_id:
            if not puntos_habilitados:
                flash(f'{get_loyalty_terms()["name"]} no está habilitado en esta tienda.', "danger")
                return redirect(url_for("public.checkout"))
            if (not cart_puntos or cart_puntos.get("cliente_id") != cliente.id
                    or not cart_puntos.get("verificado")
                    or cart_puntos.get("origen") != origen):
                flash(f'Verifica tu WhatsApp antes de canjear productos con {get_loyalty_terms()["plural"]}.', "danger")
                return redirect(url_for("public.checkout"))
            if (
                not producto_canje
                or not _producto_canjeable_en_origen(producto_canje, origen)
            ):
                flash("Producto de canje no válido.", "danger")
                return redirect(url_for("public.checkout"))
            puntos_producto = int(producto_canje.puntos_para_canje or 0)
            if puntos_producto > int(cliente.puntos or 0):
                flash(f'No tienes suficientes {get_loyalty_terms()["plural"]} para canjear este producto.', "danger")
                return redirect(url_for("public.checkout"))
            compat_canje = _cart_compatibility(cart_productos + [producto_canje])
            if not compat_canje["ok"]:
                flash(compat_canje["message"], compat_canje["issues"][0].get("severity", "danger"))
                return redirect(url_for("public.checkout"))
            if tipo_entrega_cliente not in _product_fulfillment_modes(producto_canje):
                flash("El producto de canje no admite la modalidad elegida.", "danger")
                return redirect(url_for("public.checkout"))

        # ── Motor de pricing único ───────────────────────────────────────
        try:
            precio = calcular_precio(
                items, subtotal,
                cupon=cupon,
                afiliado=afiliado_codigo,
                zona=zona,
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("public.checkout"))

        descuento          = precio.descuento_total
        descuento_afiliado = precio.descuento_afiliado
        total              = precio.total
        puntos_a_canjear   = precio.puntos_usados
        puntos_ganados     = calcular_puntos_ganados(total)
        service_fee = get_service_commission(total)

        # Registrar uso del cupón — envio_gratis aplica aunque descuento_cupon sea 0
        if cupon:
            try:
                cupon.registrar_uso()
            except ValueError:
                flash("El cupón ya no está disponible. Inténtalo sin cupón.", "danger")
                return redirect(url_for("public.checkout"))

        # Componemos la dirección final para persistir: calle+número + detalles.
        # La validación de zona ya pasó con solo la calle+número (Nominatim
        # no reconoce "1º bajo"). El repartidor sí necesita el detalle.
        direccion_entrega_final = direccion
        if direccion and direccion_detalles:
            direccion_entrega_final = f"{direccion}, {direccion_detalles}"

        pedido = Order(
            numero_pedido=Order.generar_numero("online"),
            cliente_id=cliente.id,
            estado="pendiente",
            origen="online",
            subtotal=subtotal,
            descuento=descuento,
            total=total,
            service_commission_pct=service_fee["pct"],
            service_commission_amount=service_fee["amount"],
            merchant_net_amount=service_fee["merchant_net"],
            cupon_id=cupon.id if cupon else None,
            puntos_usados=0,
            puntos_ganados=puntos_ganados,
            metodo_pago=metodo_pago,
            tipo_entrega_cliente=tipo_entrega_cliente,
            direccion_entrega=direccion_entrega_final,
            direccion_lat=(
                Decimal(str(geo["lat"]))
                if geo and geo.get("lat") is not None else None
            ),
            direccion_lng=(
                Decimal(str(geo["lon"]))
                if geo and geo.get("lon") is not None else None
            ),
            direccion_precision_m=(
                Decimal(str(geo["precision_m"]))
                if geo and geo.get("precision_m") is not None else None
            ),
            notas=notas,
            zona_id=zona.id if zona else None,
            es_entrega_epicentro=es_entrega_epicentro,
            afiliado_codigo_id=afiliado_codigo.id if afiliado_codigo else None,
        )
        aplicar_snapshot_zona_pedido(pedido, zona, precio.costo_envio)
        db.session.add(pedido)
        db.session.flush()

        # ── Reserva de franja horaria (módulo delivery_franjas_activo) ──
        # Solo cuando el cliente eligió delivery + envió slot_id + módulo activo.
        # La reserva es atómica dentro de la misma transacción del pedido: si
        # el cupo se agotó entre carga del selector y submit, revertimos el
        # pedido para no dejar huérfano y devolvemos al cliente al carrito
        # con mensaje claro para elegir otra franja.
        if slot_id_solicitado and tipo_entrega_cliente == "delivery":
            from store_config import get_store_value
            franjas_activo = str(
                get_store_value("delivery_franjas_activo", "0")
            ).strip() in ("1", "true", "True")
            if franjas_activo:
                from delivery_slots_service import (
                    reservar_franja, ResultadoReserva,
                )
                _reserva = reservar_franja(slot_id_solicitado, pedido)
                if _reserva.tipo != ResultadoReserva.RESERVADA:
                    db.session.rollback()
                    _mensajes = {
                        ResultadoReserva.LLENA: (
                            "La franja horaria que elegiste acaba de llenarse. "
                            "Elige otra en el checkout."
                        ),
                        ResultadoReserva.CERRADA: (
                            "La franja horaria ya está cerrada para nuevos pedidos. "
                            "Elige una franja posterior."
                        ),
                        ResultadoReserva.INACTIVA: (
                            "La franja horaria seleccionada ya no está disponible."
                        ),
                        ResultadoReserva.NO_EXISTE: (
                            "La franja horaria seleccionada no existe."
                        ),
                    }
                    flash(_mensajes.get(_reserva.tipo, "No se pudo reservar la franja."), "danger")
                    return redirect(url_for("public.ver_carrito"))
        registrar_pedido_creado(
            pedido,
            actor_id=cliente.id,
            canal="web",
            detalle="checkout web",
            metadata={
                "zona_id": zona.id if zona else None,
                "zona_nombre": pedido.zona_nombre_snapshot,
                "costo_envio": pedido.costo_envio_aplicado,
                "tipo_entrega_cliente": tipo_entrega_cliente,
                "condiciones_compra_aceptadas": True,
                "version_legal": SiteConfig.get("LEGAL_VERSION", "1.0"),
                "ubicacion_cliente": bool(
                    pedido.direccion_lat is not None
                    and pedido.direccion_lng is not None
                ),
            },
        )

        try:
            for item in items:
                precio_venta = item.get("precio_unit", item["producto"].precio_final)
                # notas por línea: combo_resumen (auto) + nota del cliente (manual)
                _partes_notas = []
                if item.get("combo_resumen"):
                    _partes_notas.append(item["combo_resumen"])
                if item.get("nota_cliente"):
                    _partes_notas.append("👤 " + item["nota_cliente"])
                item_metadata = dict(item.get("metadata") or {})
                if _delivery_family(item["producto"]) == "programado":
                    # Congelar de forma explícita la fecha canónica del carrito.
                    # El snapshot del producto también la conserva, pero esta
                    # clave es el contrato común con POS y API del chatbot.
                    item_metadata["entrega_programada"] = compat["scheduled_date"].isoformat()
                oi = OrderItem(
                    pedido_id=pedido.id,
                    producto_id=item["producto"].id,
                    cantidad=item["cantidad"],
                    precio_unit=precio_venta,
                    subtotal=round(precio_venta * item["cantidad"], 2),
                    notas=" | ".join(_partes_notas) if _partes_notas else None,
                    metadata_json=json.dumps(
                        _metadata_item_con_origen(
                            item["producto"],
                            item_metadata,
                            item["origen"],
                        ),
                        ensure_ascii=False,
                    ),
                )
                db.session.add(oi)
                if item["producto"].tipo_entrega == "inmediato":
                    # Row lock del Product antes de descontar. Sin él, dos
                    # checkouts concurrentes podían leer stock desde la carga
                    # inicial de `_build_items_from_carrito` (línea 3099,
                    # bulk SELECT sin lock) y ambos completar el descuento,
                    # dejando inventario negativo. `with_for_update=True`
                    # emite SELECT ... FOR UPDATE en Postgres; en SQLite las
                    # transacciones se serializan por default → no-op.
                    # El lock se libera al final del `with` (commit/rollback).
                    db.session.get(Product, item["producto"].id, with_for_update=True)
                    _descontar_stock_en_origen(
                        item["producto"],
                        item["origen"],
                        item["cantidad"],
                        item.get("combo_seleccion_ids") or [],
                    )
                # ── Reserva atómica de tandas en ProductBatch ──
                # Cuando el producto se vende por lote (cantidad_por_lote > 0)
                # y tiene fecha de entrega, `item["cantidad"]` representa
                # TANDAS (no unidades). Aquí buscamos/creamos el batch y
                # reservamos el cupo con UPDATE condicional. Un rechazo
                # significa que otro cliente concurrente consumió las
                # últimas tandas → abortamos el checkout con mensaje claro
                # para que el cliente ajuste cantidad o elija otra fecha.
                _prod = item["producto"]
                _por_lote = int(_prod.cantidad_por_lote or 0)
                _fecha_lote = _prod.fecha_llegada
                if _por_lote > 0 and _fecha_lote and _prod.tipo_entrega == "programado":
                    from models import ProductBatch
                    batch = ProductBatch.query.filter_by(
                        producto_id=_prod.id, fecha_entrega=_fecha_lote,
                    ).first()
                    if batch is None:
                        # Auto-crea el batch la primera vez que un producto
                        # por-lote publicado recibe un pedido. `cantidad_maxima_tandas`
                        # queda NULL (ilimitado) hasta que el admin lo tope
                        # desde el panel. Alternativa: exigir batch pre-creado.
                        batch = ProductBatch(
                            producto_id=_prod.id,
                            fecha_entrega=_fecha_lote,
                            cantidad_por_tanda=_por_lote,
                            cantidad_maxima_tandas=None,
                        )
                        db.session.add(batch)
                        db.session.flush()
                    tandas_pedidas = int(item["cantidad"])
                    if not batch.reservar_tandas(tandas_pedidas):
                        db.session.rollback()
                        disp = batch.tandas_disponibles()
                        flash(
                            f"«{_prod.nombre}» del {_fecha_lote.strftime('%d/%m')}: "
                            f"solo quedan {disp} tandas disponibles. "
                            f"Ajusta la cantidad o elige otra fecha.",
                            "warning",
                        )
                        return redirect(url_for("public.ver_carrito"))
                    # Trace en metadata para poder devolver tandas al cancelar.
                    _meta = json.loads(oi.metadata_json or "{}")
                    _meta["batch_id"] = batch.id
                    _meta["tandas_reservadas"] = tandas_pedidas
                    oi.metadata_json = json.dumps(_meta, ensure_ascii=False)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("public.ver_carrito"))

        # ── IVA total (España, exportación fiscal) ─────────────────────
        # Se calcula desde el snapshot congelado en cada OrderItem — así el
        # importe reportado no depende de cambios de tasa posteriores.
        try:
            from fiscal_utils import base_e_iva_desde_total
            from models import _resolver_iva_pct_producto
            iva_acumulado = Decimal("0.00")
            for oi in pedido.items:
                meta = oi.get_metadata() or {}
                snap_iva = (meta.get("producto") or {}).get("iva_pct")
                # Fallback: si el snapshot no traía iva_pct (pedidos previos a
                # Fase 9), resolver desde el producto vivo → SiteConfig → default.
                # Nunca cae a 0 salvo que el producto se haya borrado, para no
                # subreportar IVA a Hacienda.
                if snap_iva in (None, ""):
                    iva_pct = _resolver_iva_pct_producto(oi.producto) if oi.producto else 0
                else:
                    iva_pct = snap_iva
                _, iva_importe = base_e_iva_desde_total(oi.subtotal or 0, iva_pct)
                iva_acumulado += iva_importe
            pedido.iva_total = iva_acumulado
        except Exception:
            # No bloqueamos el checkout si algo va mal calculando IVA, pero
            # dejamos rastro para poder diagnosticar en producción.
            current_app.logger.exception(
                "checkout: fallo calculando IVA pedido=%s", pedido.id,
            )
            pedido.iva_total = 0

        # ── Canje de puntos unificado via loyalty_service ───────────────
        # Único punto de deducción — garantiza idempotencia
        try:
            aplicar_canje_en_pedido(
                cliente, pedido,
                producto_canje_id=producto_canje_id,
                origen_operativo=origen,
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("public.ver_carrito"))
        db.session.flush()
        sincronizar_proveedores_pedido(pedido)
        db.session.flush()
        # El primer pedido no entra en operación hasta que el cliente confirme
        # el número desde WhatsApp. La confirmación activa después asignación y
        # notificaciones en services.marcar_pedido_confirmado().
        if pedido.confirmacion_estado != "pending":
            encolar_notificaciones_proveedores_pedido(pedido)

        # Los puntos se otorgan al entregar (repartidor.confirmar_entrega → award_points_on_delivery)
        # No se suman aquí para evitar que pedidos cancelados o no entregados acumulen puntos

        # Registrar uso de afiliado + generar StaffPayment de comisión automáticamente
        if afiliado_codigo:
            registrar_uso_afiliado(afiliado_codigo, pedido, cliente, descuento_afiliado)

        if pedido.confirmacion_estado != "pending":
            distribuir_pedido(pedido)

        token = uuid.uuid4().hex
        guest_tokens = session.get("guest_order_tokens", {})
        # TTL 24h — evita que el token quede accesible indefinidamente en la
        # sesión del navegador (protege info sensible del pedido).
        guest_tokens[str(pedido.id)] = {
            "token": token,
            "exp": int(datetime.utcnow().timestamp()) + GUEST_ORDER_TOKEN_TTL_S,
        }
        session["guest_order_tokens"] = guest_tokens
        session["last_guest_order_id"] = pedido.id
        session["last_guest_order_token"] = token
        session["push_cliente_id"] = cliente.id

        # La notificación queda en la misma transacción del pedido.
        enviar_whatsapp_estado(pedido)

        # Registrar idempotency key APUNTANDO al pedido recién creado para que
        # un retry inmediato no abra un segundo pedido idéntico.
        db.session.add(IdempotencyKey(
            scope="checkout_web",
            key=idem_key,
            request_hash=body_h,
            response_status=302,
            response_body=json.dumps({"order_id": pedido.id, "numero": pedido.numero_pedido, "token": token}),
            order_id=pedido.id,
            user_id=None,
            expira_en=_utcnow() + IDEMPOTENCY_TTL,
        ))

        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("checkout commit fallido: %s", exc)
            flash("Error al procesar tu pedido. Por favor, inténtalo de nuevo.", "danger")
            return redirect(url_for("public.checkout"))

        # Vaciado COMPLETO tras crear pedido: helper canónico limpia las 8
        # claves de sesión ligadas al carrito. Antes se hacían pops parciales
        # (6 de 8) y quedaban huérfanas `extras_selecciones`,
        # `presentaciones_carrito` y `variantes_carrito` — se filtraban al
        # siguiente pedido del mismo cliente en la misma sesión.
        _save_carrito({})

        # Notificación push: alertar a admins del nuevo pedido
        try:
            from push_service import notify_new_order
            notify_new_order(pedido)
        except Exception:
            current_app.logger.exception("No se pudo enviar push de nuevo pedido web %s", pedido.id)

        return redirect(url_for("public.pedido_confirmado", pedido_id=pedido.id, token=token))

    precio_preview = calcular_precio(items, subtotal)
    checkout_items = MenuConfig.query.filter_by(pagina="checkout", activo=True)\
        .order_by(MenuConfig.orden.asc(), MenuConfig.id.asc()).all()
    try:
        radio_entrega_km = max(0.0, float(SiteConfig.get("RADIO_ENTREGA_KM", "5") or 5))
    except (TypeError, ValueError):
        radio_entrega_km = 5.0
    return render_template("public/checkout.html", items=items, subtotal=subtotal,
                           zonas=zonas,
                           tiene_encargos=tiene_encargos,
                           canjeables=canjeables,
                           puntos_habilitados=puntos_habilitados,
                           fulfillment_options=fulfillment_options,
                           fulfillment_unavailable=fulfillment_unavailable,
                           fulfillment_mode_label=_fulfillment_mode_label,
                           fulfillment_default=fulfillment_default,
                           fecha_entrega_programada=compat.get("scheduled_date"),
                           checkout_items=checkout_items,
                           origen_actual=origen,
                           establecimiento=establecimiento,
                           establecimiento_abierto=establecimiento_abierto,
                           mensaje_cierre=mensaje_cierre,
                           radio_entrega_km=radio_entrega_km,
                           cobertura_por_zonas=any(z.tiene_geo for z in zonas),
                           puntos_sesion=cart_puntos_sesion,
                           producto_canje_seleccionado=session.get("cart_producto_canje_id"))


def _token_pedido_sesion(pedido_id: int) -> str:
    """Token opaco vigente que autoriza operaciones del pedido en este navegador."""
    guest_tokens = session.get("guest_order_tokens", {})
    slot = guest_tokens.get(str(pedido_id))
    if isinstance(slot, dict):
        expected = slot.get("token", "")
        exp = int(slot.get("exp") or 0)
        if exp and exp < int(datetime.utcnow().timestamp()):
            return ""
        return str(expected or "")
    # Compatibilidad de lectura para sesiones emitidas antes del TTL.
    return str(slot or "")


def _sesion_autoriza_pedido(pedido_id: int, supplied_token: str = "") -> bool:
    expected = _token_pedido_sesion(pedido_id)
    return bool(expected and supplied_token and secrets.compare_digest(expected, supplied_token))


@public_bp.route("/pedido/<int:pedido_id>/confirmado")
def pedido_confirmado(pedido_id):
    pedido = get_or_404(Order, pedido_id)
    expected = _token_pedido_sesion(pedido_id)
    # Un push no debe incluir secretos en su URL. Para abrir un pedido anterior
    # del mismo dispositivo recuperamos su token específico de la sesión; antes
    # se usaba siempre el token del último pedido y los avisos antiguos fallaban.
    token = request.args.get("token", "") or expected
    if not _sesion_autoriza_pedido(pedido_id, token):
        flash("Acceso denegado.", "danger")
        return redirect(url_for("public.index"))
    if pedido.estado in {"cancelado", "entregado"}:
        slots = session.get("guest_order_tokens", {})
        slots.pop(str(pedido.id), None)
        session["guest_order_tokens"] = slots
        session.modified = True
        flash(
            "Ese pedido ya fue cancelado." if pedido.estado == "cancelado"
            else "Ese pedido ya finalizó. Gracias por tu compra.",
            "info",
        )
        return redirect(url_for("public.index"))
    return render_template(
        "public/pedido_confirmado.html",
        pedido=pedido,
        requiere_confirmacion_whatsapp=(pedido.confirmacion_estado == "pending"),
        pedido_token=token,
        puede_cancelar=(
            pedido.estado == "pendiente"
            and not (pedido.metodo_pago == "bizum" and pedido.pago_confirmado)
        ),
    )


@public_bp.get("/pedido/<int:pedido_id>/estado")
def estado_pedido_web(pedido_id):
    """Estado mínimo y actual, autorizado para refrescar el ticket digital."""
    token = str(request.args.get("token") or "")
    if not _sesion_autoriza_pedido(pedido_id, token):
        return jsonify({"ok": False}), 403
    pedido = get_or_404(Order, pedido_id)
    labels = {
        "pendiente": "Recibido", "armando": "En preparación", "listo": "Listo",
        "en_ruta": "En reparto", "entregado": "Finalizado", "cancelado": "Cancelado",
    }
    return jsonify({
        "ok": True, "status": pedido.estado,
        "status_label": labels.get(pedido.estado, pedido.estado.replace("_", " ").title()),
        "active": pedido.estado not in {"entregado", "cancelado"},
        "redirect_url": url_for("public.index"),
    })


@public_bp.post("/pedido/<int:pedido_id>/cancelar")
def cancelar_pedido_web(pedido_id):
    """Cancelación autoservicio segura para el navegador que creó el pedido."""
    token = str(request.form.get("token") or "")
    if not _sesion_autoriza_pedido(pedido_id, token):
        flash("No pudimos verificar que este pedido te pertenece.", "danger")
        return redirect(url_for("public.index"))
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.estado != "pendiente" or (pedido.metodo_pago == "bizum" and pedido.pago_confirmado):
        flash("El pedido ya requiere revisión del equipo. Solicítala desde el chat.", "warning")
        return redirect(url_for("public.pedido_confirmado", pedido_id=pedido.id, token=token))
    try:
        cancelar_pedido_operativo(
            pedido,
            actor_id=pedido.cliente_id,
            canal="chat_web",
            detalle="cancelación solicitada por el cliente desde el seguimiento web",
        )
        db.session.commit()
        flash(f"El pedido {pedido.numero_pedido} fue cancelado correctamente.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("cancelar_pedido_web: fallo pedido=%s", pedido_id)
        flash("No pudimos cancelar el pedido. Solicita ayuda desde el chat.", "danger")
    return redirect(url_for("public.pedido_confirmado", pedido_id=pedido.id, token=token))


# ─── CLUB DE CLIENTES ────────────────────────


@public_bp.route("/club")
def club():
    if not _feature_enabled("puntos"):
        flash(f'{get_loyalty_terms()["name"]} no está habilitado en esta tienda.', "info")
        return redirect(url_for("public.index"))
    # Vitrina completa: incluye tanto recompensas exclusivas como productos
    # normales marcados como canjeables. El checkout usa exactamente el mismo
    # flag, por lo que no ocultamos aquí opciones que sí pueden canjearse.
    # Filtro por nicho activo → un canje retail no aparece en comida y viceversa.
    canjeables = [
        p for p in (
            Product.query.filter_by(activo=True, canjeable_con_puntos=True)
            .filter(Product.puntos_para_canje.isnot(None), Product.puntos_para_canje > 0)
            .order_by(Product.puntos_para_canje.asc(), Product.nombre.asc())
            .all()
        )
        if _producto_pertenece_al_vertical(p)
    ]
    # Saldo del cliente cuando esté autenticado. Antes: la página SIEMPRE
    # obligaba a "enviar mi saldo por WhatsApp" aunque el cliente ya
    # hubiera hecho login. Ahora: si es cliente autenticado, se muestra
    # su saldo real y cada canjeable revela "✓ disponible" o "faltan X".
    saldo_cliente = None
    if current_user.is_authenticated and getattr(current_user, "rol", None) == "cliente":
        try:
            saldo_cliente = int(current_user.puntos or 0)
        except (TypeError, ValueError):
            saldo_cliente = None
    return render_template(
        "public/puntos_consulta.html",
        canjeables=canjeables,
        saldo_cliente=saldo_cliente,
    )


# ─── HELPERS ─────────────────────────────────

def _parse_combo_selection(producto, form, cantidad=1, origen=None):
    if not producto.es_combo:
        return {}, None

    componentes = ComboItem.query.filter_by(combo_id=producto.id)\
        .order_by(ComboItem.orden.asc(), ComboItem.id.asc()).all()
    seleccionables = [item for item in componentes if item.es_seleccionable]
    grupos = {}
    for item in seleccionables:
        grupos.setdefault(item.grupo.nombre_publico if item.grupo else (item.grupo_seleccion or "Seleccion"), []).append(item)

    seleccion = {}
    for grupo, opciones in grupos.items():
        field_template = f"combo_item_{(grupo or 'Seleccion').replace(' ', '_')}"
        max_sel = max(1, opciones[0].max_selecciones or 1)
        def _item_disponible(item):
            return producto.combo_item_stock_disponible(item, cantidad, origen=origen)

        validos = {item.id for item in opciones if _item_disponible(item)}
        # Guard: si el grupo tiene opciones pero NINGUNA tiene stock, el combo
        # entero no es fabricable ahora. Antes: aceptábamos empty select y
        # dábamos error confuso "stock insuficiente" en downstream. Ahora
        # devolvemos error claro y bloqueamos el add-to-cart.
        if opciones and not validos:
            return {}, (
                f"El combo «{producto.nombre}» no está disponible ahora: "
                f"todas las opciones del grupo «{grupo}» están sin stock."
            )

        if max_sel == 1:
            valores = form.getlist(field_template)
            if not valores:
                valores = form.getlist(f"combo_item_{_combo_group_key(grupo)}")
            item_ids = []
            for val in valores:
                try:
                    item_ids.append(int(val))
                except (TypeError, ValueError):
                    continue
            elegidos = [item_id for item_id in item_ids if item_id in validos]
            disponibles = [item for item in opciones if item.id in validos]
            # Debe elegir al menos una opción si hay disponibles, y no puede elegir más de max_sel
            if disponibles and len(elegidos) == 0:
                return {}, f"Debes elegir al menos 1 opción de «{grupo}» para el combo."
            if len(elegidos) > max_sel:
                return {}, f"No puedes elegir más de {max_sel} opción(es) de «{grupo}» para el combo."
            seleccion[grupo] = {item_id: 1 for item_id in elegidos}
        else:
            qty_map = {}
            total_selecciones = 0
            valores = form.getlist(field_template)
            if not valores:
                valores = form.getlist(f"combo_item_{_combo_group_key(grupo)}")
            for val in valores:
                try:
                    item_id = int(val)
                except (TypeError, ValueError):
                    continue
                if item_id in validos:
                    qty_map[item_id] = qty_map.get(item_id, 0) + 1
                    total_selecciones += 1
            for item in opciones:
                raw_qty = form.get(f"combo_item_qty_{item.id}")
                if not raw_qty:
                    continue
                try:
                    qty = max(0, min(max_sel, int(raw_qty)))
                except (TypeError, ValueError):
                    qty = 0
                if qty > 0 and item.id in validos:
                    qty_map[item.id] = qty
                    total_selecciones = sum(qty_map.values())
            disponibles = [item for item in opciones if item.id in validos]
            # Si hay opciones disponibles, requerimos al menos una selección y no permitir más que max_sel
            if disponibles and total_selecciones == 0:
                return {}, f"Debes elegir al menos 1 opción de «{grupo}» para el combo."
            if total_selecciones > max_sel:
                return {}, f"No puedes elegir más de {max_sel} opción(es) de «{grupo}» para el combo."
            seleccion[grupo] = qty_map

    def _component_units(item):
        if not item.es_seleccionable:
            return max(1, int(item.cantidad or 1))
        group_name = (
            item.grupo.nombre_publico
            if item.grupo else (item.grupo_seleccion or "Seleccion")
        )
        group_selection = seleccion.get(group_name, {})
        return max(0, int(group_selection.get(item.id, 0) or 0)) * max(
            1, int(item.cantidad or 1)
        )

    # ── Configuración por unidad ──
    # Un componente fijo con varias unidades puede mezclar tamaños y sabores:
    # p.ej. Festival pequeña/fresa + Festival grande/chocolate. El par queda
    # unido en el snapshot; no se reconstruye después a partir de dos totales.
    units_map = {}
    unit_managed_items = set()
    for item in componentes:
        units = _component_units(item)
        opts = list(item.presentaciones_disponibles or [])
        supports_unit_choice = (
            not item.es_seleccionable
            and units > 1
            and (
                len(opts) > 1
                or getattr(item, "permite_sabor_cliente", False) is True
                or getattr(item, "fixed_flavor_option_id", None)
            )
        )
        if not supports_unit_choice:
            continue
        has_unit_fields = any(
            f"combo_unit_presentation_{item.id}_{index}" in form
            or f"combo_unit_flavor_{item.id}_{index}" in form
            for index in range(1, units + 1)
        )
        if not has_unit_fields:
            continue  # compatibilidad con formularios cacheados/antiguos

        valid_presentations = {presentation.id: presentation for presentation in opts}
        fixed_flavor = getattr(item, "fixed_flavor_option", None)
        rows = []
        for index in range(1, units + 1):
            if len(opts) > 1:
                try:
                    presentation_id = int(
                        form.get(f"combo_unit_presentation_{item.id}_{index}")
                    )
                except (TypeError, ValueError):
                    presentation_id = 0
                presentation = valid_presentations.get(presentation_id)
                if presentation is None:
                    return {}, (
                        f"Elige un tamaño válido para la unidad {index} de "
                        f"«{item.componente.nombre}»."
                    )
            else:
                presentation = item.presentacion or (opts[0] if opts else None)
                presentation_id = presentation.id if presentation else None

            available = item.sabores_disponibles_para(presentation)
            available_by_id = {option.id: option for option in available}
            flavor_id = None
            if fixed_flavor is not None:
                if fixed_flavor.id not in available_by_id:
                    return {}, (
                        f"El sabor incluido de «{item.componente.nombre}» no "
                        f"está disponible en la unidad {index}."
                    )
                flavor_id = fixed_flavor.id
            elif getattr(item, "permite_sabor_cliente", False) is True:
                try:
                    flavor_id = int(
                        form.get(f"combo_unit_flavor_{item.id}_{index}")
                    )
                except (TypeError, ValueError):
                    flavor_id = 0
                if flavor_id not in available_by_id:
                    return {}, (
                        f"Elige un sabor válido para la unidad {index} de "
                        f"«{item.componente.nombre}» y su tamaño."
                    )
            row = {}
            if presentation_id:
                row["presentation_id"] = int(presentation_id)
            if flavor_id:
                row["flavor_option_id"] = int(flavor_id)
            rows.append(row)
        units_map[str(item.id)] = rows
        unit_managed_items.add(item.id)
    if units_map:
        seleccion["__units__"] = units_map

    # ── Tamaños por componente (contrato anterior y componentes unitarios) ──
    # Se resuelven antes que los sabores: la presentación determina qué sabores
    # existen y cuántas unidades se pueden distribuir.
    pres_map = {}
    resolved_presentations = {}
    for item in componentes:
        if item.id in unit_managed_items:
            continue
        opts = list(item.presentaciones_disponibles or [])
        if len(opts) < 2:
            resolved_presentations[item.id] = item.presentacion
            continue
        valid_ids = {p.id for p in opts}
        raw = form.get(f"combo_presentation_{item.id}")
        try:
            chosen = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            chosen = None
        if chosen not in valid_ids:
            return {}, (
                f"Elige un tamaño válido para «{item.componente.nombre}» "
                f"dentro del combo."
            )
        pres_map[str(item.id)] = chosen
        resolved_presentations[item.id] = next(p for p in opts if p.id == chosen)
    if pres_map:
        seleccion["__presentations__"] = pres_map

    # ── Sabores por componente ──
    # El valor persistido es {item_id: {option_id: cantidad}}. Se conserva
    # lectura de los campos antiguos durante la transición de PWA/cache.
    flavors_map = {}
    for item in componentes:
        if item.id in unit_managed_items:
            continue
        raw_fixed_flavor_id = getattr(item, "fixed_flavor_option_id", 0)
        fixed_flavor_id = (
            int(raw_fixed_flavor_id)
            if isinstance(raw_fixed_flavor_id, int) and raw_fixed_flavor_id > 0
            else 0
        )
        fixed_flavor = (
            getattr(item, "fixed_flavor_option", None)
            if fixed_flavor_id > 0 else None
        )
        if (
            getattr(item, "permite_sabor_cliente", False) is not True
            and fixed_flavor is None
        ):
            continue
        units = _component_units(item)
        if units <= 0:
            continue
        presentation = resolved_presentations.get(item.id, item.presentacion)
        available = item.sabores_disponibles_para(presentation)
        available_ids = {opt.id for opt in available}
        if not available_ids:
            return {}, (
                f"«{item.componente.nombre}» no tiene sabores disponibles "
                "para el tamaño seleccionado."
            )
        if fixed_flavor is not None:
            if fixed_flavor.id not in available_ids:
                return {}, (
                    f"El sabor incluido de «{item.componente.nombre}» ya no "
                    "está disponible para el tamaño seleccionado."
                )
            flavors_map[str(item.id)] = {str(fixed_flavor.id): units}
            continue
        qty_map = {}
        for oid in available_ids:
            raw_qty = form.get(f"combo_flavor_qty_{item.id}_{oid}")
            if raw_qty not in (None, ""):
                try:
                    qty = int(raw_qty)
                except (TypeError, ValueError):
                    return {}, "La distribución de sabores no tiene un formato válido."
                if qty < 0 or qty > units:
                    return {}, f"Cantidad inválida para un sabor de «{item.componente.nombre}»."
                if qty:
                    qty_map[str(oid)] = qty

        # Compatibilidad temporal con formularios almacenados por una PWA vieja.
        if not qty_map:
            for val in form.getlist(f"combo_flavor_{item.id}"):
                try:
                    oid = int(val)
                except (TypeError, ValueError):
                    continue
                if oid in available_ids:
                    qty_map[str(oid)] = qty_map.get(str(oid), 0) + 1
            for oid in available_ids:
                if form.get(f"combo_flavor_{item.id}_{oid}"):
                    qty_map[str(oid)] = qty_map.get(str(oid), 0) + 1

        total = sum(qty_map.values())
        if total != units:
            return {}, (
                f"Distribuye las {units} unidad(es) de «{item.componente.nombre}» "
                "entre los sabores disponibles."
            )
        flavors_map[str(item.id)] = qty_map
    if flavors_map:
        seleccion["__flavors__"] = flavors_map

    return seleccion, None


def _combo_group_key(grupo):
    return "".join(ch if ch.isalnum() else "_" for ch in (grupo or "Seleccion")).strip("_") or "Seleccion"


def _parse_product_extras(producto, form, presentation=None):
    groups = ProductExtraGroup.query.filter_by(producto_id=producto.id, activo=True).all()
    raw_selected = {}
    for group in groups:
        active_options = group.opciones.filter_by(activo=True).all()
        if group.tipo == "sabor":
            found_quantity = False
            for option in active_options:
                try:
                    flavor_qty = int(form.get(f"flavor_qty_{option.id}", 0) or 0)
                except (TypeError, ValueError):
                    return {}, f"La cantidad de «{option.nombre}» no es válida."
                if flavor_qty < 0:
                    return {}, f"La cantidad de «{option.nombre}» no es válida."
                if flavor_qty:
                    raw_selected[str(option.id)] = flavor_qty
                    found_quantity = True
            # Compatibilidad con formularios/cache PWA anteriores al selector
            # por cantidades. El servidor lo normaliza al contrato nuevo.
            if not found_quantity:
                raw_flavor = form.get(f"flavor_group_{group.id}")
                if raw_flavor not in (None, ""):
                    try:
                        flavor_id = int(raw_flavor)
                    except (TypeError, ValueError):
                        return {}, f"El sabor elegido en «{group.nombre}» no es válido."
                    if not any(option.id == flavor_id for option in active_options):
                        return {}, f"El sabor elegido en «{group.nombre}» ya no está disponible."
                    raw_selected[str(flavor_id)] = 1
        else:
            for option in active_options:
                try:
                    qty = int(form.get(f"extra_qty_{option.id}", 0) or 0)
                except (TypeError, ValueError):
                    qty = 0
                if qty < 0 or qty > option.max_cantidad:
                    return {}, f"Cantidad inválida para «{option.nombre}»."
                if qty:
                    raw_selected[str(option.id)] = qty
    selected, _, _, error = validate_product_option_selection(
        producto, raw_selected, presentation
    )
    return selected, error


def _product_extras_payload(producto, selected):
    _, rows, total, error = validate_product_option_selection(producto, selected)
    return ([], 0.0) if error else (rows, total)


def _combo_selection_ids_from_saved(seleccion_guardada):
    ids = []
    if not isinstance(seleccion_guardada, dict):
        return ids
    for grupo_key, qty_map in seleccion_guardada.items():
        if isinstance(grupo_key, str) and grupo_key.startswith("__"):
            continue
        if not isinstance(qty_map, dict):
            continue
        for item_id, qty in qty_map.items():
            try:
                item_id = int(item_id)
                qty = max(0, int(qty))
            except (TypeError, ValueError):
                continue
            ids.extend([item_id] * qty)
    return ids


def _combo_selection_payload(producto, seleccion_guardada):
    if not producto.es_combo:
        return [], "", {}

    componentes = ComboItem.query.filter_by(combo_id=producto.id)\
        .order_by(ComboItem.orden.asc(), ComboItem.id.asc()).all()
    fijos = [item for item in componentes if not item.es_seleccionable]
    seleccionables = [item for item in componentes if item.es_seleccionable]
    by_id = {item.id: item for item in componentes}

    seleccion_ids = []
    resumen = []
    grupos_meta = []

    # Sabores elegidos por el cliente por-componente ({item_id: [opt_id,...]}).
    saved_flavors_raw = (seleccion_guardada or {}).get("__flavors__") if isinstance(seleccion_guardada, dict) else None
    saved_flavors = {}
    if isinstance(saved_flavors_raw, dict):
        for k, v in saved_flavors_raw.items():
            try:
                item_key = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, dict):
                saved_flavors[item_key] = {
                    int(option_id): int(qty)
                    for option_id, qty in v.items()
                    if str(option_id).lstrip("-").isdigit()
                    and str(qty).lstrip("-").isdigit()
                    and int(qty) > 0
                }
            elif isinstance(v, list):
                # Formato legacy: cada ID representaba una unidad.
                quantities = {}
                for option_id in v:
                    if str(option_id).lstrip("-").isdigit():
                        option_id = int(option_id)
                        quantities[option_id] = quantities.get(option_id, 0) + 1
                saved_flavors[item_key] = quantities

    def _flavor_meta_for(item):
        if (
            not getattr(item, "permite_sabor_cliente", False)
            and not getattr(item, "fixed_flavor_option_id", None)
        ):
            return None
        chosen = saved_flavors.get(item.id) or {}
        if not chosen:
            return None
        presentation = _resolved_presentation_for(item)
        opts_by_id = {
            opt.id: opt for opt in item.sabores_disponibles_para(presentation)
        }
        out = []
        for oid, qty in chosen.items():
            opt = opts_by_id.get(int(oid))
            if opt:
                out.append({
                    "opt_id": opt.id,
                    "nombre": opt.nombre,
                    "cantidad": int(qty),
                })
        return out or None

    # Tamaños elegidos por el cliente por componente ({item_id: presentation_id}).
    saved_pres_raw = (seleccion_guardada or {}).get("__presentations__") if isinstance(seleccion_guardada, dict) else None
    saved_presentations = {}
    if isinstance(saved_pres_raw, dict):
        for k, v in saved_pres_raw.items():
            try:
                saved_presentations[int(k)] = int(v)
            except (TypeError, ValueError):
                continue

    saved_units_raw = (
        (seleccion_guardada or {}).get("__units__")
        if isinstance(seleccion_guardada, dict) else None
    )
    saved_units = {}
    if isinstance(saved_units_raw, dict):
        for raw_item_id, rows in saved_units_raw.items():
            try:
                item_id = int(raw_item_id)
            except (TypeError, ValueError):
                continue
            if isinstance(rows, list):
                saved_units[item_id] = [
                    row for row in rows if isinstance(row, dict)
                ]

    def _resolved_presentation_for(item):
        chosen_id = saved_presentations.get(item.id)
        if chosen_id:
            return db.session.get(ProductPresentation, chosen_id)
        return item.presentacion

    def _presentation_meta_for(item):
        opts = list(item.presentaciones_disponibles or [])
        if len(opts) < 2:
            return None  # modo fijo → el snapshot ya lleva la presentacion default
        chosen_id = saved_presentations.get(item.id)
        chosen = next((p for p in opts if p.id == chosen_id), opts[0])
        return {
            "id": chosen.id,
            "tamaño": chosen.tamaño,
            "label": chosen.label,
            "extra": chosen.precio_extra_float,
        }

    def _unit_meta_for(item):
        rows = saved_units.get(item.id) or []
        if not rows:
            return None
        presentations = {
            presentation.id: presentation
            for presentation in item.presentaciones_disponibles
        }
        output = []
        for index, row in enumerate(rows, start=1):
            try:
                presentation_id = int(row.get("presentation_id") or 0)
            except (TypeError, ValueError):
                presentation_id = 0
            presentation = presentations.get(presentation_id, item.presentacion)
            available_flavors = {
                option.id: option
                for option in item.sabores_disponibles_para(presentation)
            }
            try:
                flavor_id = int(row.get("flavor_option_id") or 0)
            except (TypeError, ValueError):
                flavor_id = 0
            flavor = available_flavors.get(flavor_id)
            unit = {"unidad": index}
            if presentation:
                unit["presentacion"] = {
                    "id": presentation.id,
                    "tamaño": presentation.tamaño,
                    "label": presentation.label,
                    "extra": presentation.precio_extra_float,
                }
            if flavor:
                unit["sabor"] = {
                    "opt_id": flavor.id,
                    "nombre": flavor.nombre,
                }
            output.append(unit)
        return output or None

    for item in fijos:
        resumen.append(f"{item.cantidad}x {item.componente.nombre}")

    grupos = {}
    for item in seleccionables:
        grupos.setdefault(item.grupo.nombre_publico if item.grupo else (item.grupo_seleccion or "Seleccion"), []).append(item)

    for grupo, opciones in grupos.items():
        ids = []
        qty_map = {}
        saved = (seleccion_guardada or {}).get(grupo, {})
        if isinstance(saved, dict):
            for item_id, qty in saved.items():
                try:
                    item_id = int(item_id)
                    qty = max(0, int(qty))
                except (TypeError, ValueError):
                    continue
                if item_id in by_id and qty > 0:
                    qty_map[item_id] = qty_map.get(item_id, 0) + qty
        else:
            for item_id in (saved or []):
                try:
                    item_id = int(item_id)
                except (TypeError, ValueError):
                    continue
                if item_id in by_id:
                    qty_map[item_id] = qty_map.get(item_id, 0) + 1

        if not qty_map:
            max_sel = max(1, opciones[0].max_selecciones or 1)
            min_sel = max(1, int(opciones[0].grupo.min_selecciones if opciones[0].grupo else 1))
            elegidas = [item for item in opciones if item.es_predeterminado][:max_sel]
            if len(elegidas) < min_sel:
                restantes = sorted(
                    [item for item in opciones if item not in elegidas],
                    key=lambda item: (float(item.componente.precio_final) if item.componente else float("inf"), item.orden or 0),
                )
                elegidas.extend(restantes[:min_sel - len(elegidas)])
            for item in elegidas:
                qty_map[item.id] = qty_map.get(item.id, 0) + 1

        for item_id, qty in qty_map.items():
            ids.extend([item_id] * qty)

        nombres = []
        opciones_meta = []
        for item_id, qty in qty_map.items():
            if item_id not in by_id:
                continue
            componente = by_id[item_id].componente
            extra_unit = float(by_id[item_id].precio_extra or 0)
            extra_total = round(extra_unit * qty, 2)
            extra_txt = f" +€{extra_total:.2f}" if extra_total > 0 else ""
            if qty == 1:
                nombres.append(f"{componente.nombre}{extra_txt}")
            else:
                nombres.append(f"{componente.nombre} ×{qty}{extra_txt}")
            _sabor_meta = _flavor_meta_for(by_id[item_id])
            _pres_meta = _presentation_meta_for(by_id[item_id])
            _units_meta = _unit_meta_for(by_id[item_id])
            _base_snap = metadata_componente_combo(by_id[item_id], producto.proveedor_despachador_id)
            # Cuando el cliente eligió tamaño, sobrescribimos el snapshot de
            # `presentacion` del combo item con el elegido — la cocina + el
            # ticket verán la presentación real solicitada, no el default del
            # combo. Retro-compat: si el cliente no eligió (modo fijo), se
            # queda el snapshot original.
            if _pres_meta:
                _base_snap = {**_base_snap, "presentacion": _pres_meta}
            opciones_meta.append({
                **_base_snap,
                "combo_item_id": item_id,
                "grupo_id": by_id[item_id].combo_group_id,
                "producto_id": by_id[item_id].producto_id,
                "nombre": componente.nombre,
                "cantidad": by_id[item_id].cantidad * qty,
                "qty": qty,
                "grupo_orden": by_id[item_id].grupo.orden if by_id[item_id].grupo else 0,
                "precio_extra": extra_unit,
                "extra_total": extra_total,
                "notas_preparacion": by_id[item_id].notas_preparacion or "",
                **({"sabor_cliente": _sabor_meta} if _sabor_meta else {}),
                **({"presentation_cliente": _pres_meta} if _pres_meta else {}),
                **({"unidades_cliente": _units_meta} if _units_meta else {}),
            })

        seleccion_ids.extend(ids)
        if nombres:
            resumen.append(f"{grupo}: {', '.join(nombres)}")
            grupo_obj = opciones[0].grupo if opciones and opciones[0].grupo else None
            grupos_meta.append({
                "grupo_id": grupo_obj.id if grupo_obj else None,
                "grupo": grupo,
                "tipo": "seleccion",
                "orden": grupo_obj.orden if grupo_obj else 0,
                "max_selecciones": max(1, opciones[0].max_selecciones or 1),
                "opciones": opciones_meta,
            })

    grupos_meta.sort(key=lambda g: (g.get("orden") or 0, g.get("grupo") or ""))
    extras_total = round(sum(
        option.get("extra_total", 0)
        for group in grupos_meta
        for option in (group.get("opciones") or [])
    ), 2)
    def _fijo_meta(item):
        base = {
            **metadata_componente_combo(item, producto.proveedor_despachador_id),
            "combo_item_id": item.id,
            "grupo_id": item.combo_group_id,
            "producto_id": item.producto_id,
            "nombre": item.componente.nombre,
            "cantidad": item.cantidad,
            "fijo": not item.es_seleccionable,
            "grupo": item.grupo.nombre_publico if item.grupo else "Base incluida",
            "grupo_orden": item.grupo.orden if item.grupo else 0,
            "notas_preparacion": item.notas_preparacion or "",
        }
        sabor_meta = _flavor_meta_for(item)
        units_meta = _unit_meta_for(item)
        if units_meta:
            base["unidades_cliente"] = units_meta
        elif sabor_meta:
            base["sabor_cliente"] = sabor_meta
        pres_meta = _presentation_meta_for(item)
        if pres_meta and not units_meta:
            base["presentation_cliente"] = pres_meta
            # Reflejo también en el snapshot de presentacion para que el
            # ticket + cocina lean el mismo objeto independiente de qué
            # canal muestra el pedido.
            base["presentacion"] = pres_meta
        return base

    metadata = {"combo": {"extras_total": extras_total, "componentes": [
        _fijo_meta(item) for item in fijos
    ], "selecciones": grupos_meta}}
    return seleccion_ids, " | ".join(resumen), metadata


def _combo_display_items(combo_items, metadata):
    combo_meta = (metadata or {}).get("combo", {})
    selected_ids = set()
    flavor_by_item = {}
    presentation_by_item = {}
    units_by_item = {}
    for group in combo_meta.get("selecciones", []):
        for option in group.get("opciones", []):
            try:
                cid = int(option.get("combo_item_id"))
            except (TypeError, ValueError):
                continue
            selected_ids.add(cid)
            if option.get("sabor_cliente"):
                flavor_by_item[cid] = option.get("sabor_cliente")
            if option.get("presentation_cliente"):
                presentation_by_item[cid] = option.get("presentation_cliente")
            if option.get("unidades_cliente"):
                units_by_item[cid] = option.get("unidades_cliente")
    for comp in combo_meta.get("componentes", []):
        try:
            cid = int(comp.get("combo_item_id"))
        except (TypeError, ValueError):
            continue
        if comp.get("sabor_cliente"):
            flavor_by_item[cid] = comp.get("sabor_cliente")
        if comp.get("presentation_cliente"):
            presentation_by_item[cid] = comp.get("presentation_cliente")
        if comp.get("unidades_cliente"):
            units_by_item[cid] = comp.get("unidades_cliente")

    rows = []
    for item in combo_items:
        if not item.es_seleccionable:
            rows.append({"item": item, "tipo": "Fijo", "seleccionado": False,
                         "sabor_cliente": flavor_by_item.get(item.id),
                         "presentation_cliente": presentation_by_item.get(item.id),
                         "unidades_cliente": units_by_item.get(item.id)})
        elif item.id in selected_ids:
            rows.append({
                "item": item,
                "tipo": item.grupo.nombre_publico if item.grupo else (item.grupo_seleccion or "Selección"),
                "seleccionado": True,
                "sabor_cliente": flavor_by_item.get(item.id),
                "presentation_cliente": presentation_by_item.get(item.id),
                "unidades_cliente": units_by_item.get(item.id),
            })
    return rows


def _build_items_from_carrito(carrito):
    """
    Construye la lista de items desde el carrito de sesión.
    Usa precio_final (que incluye promoción rápida del producto)
    para que carrito y checkout sean siempre consistentes.
    """
    if not carrito:
        return [], 0.0

    ids = iter_producto_ids(carrito)
    if not ids:
        return [], 0.0

    productos_map = {p.id: p for p in Product.query.filter(Product.id.in_(ids)).all()}
    origen_logistico = _carrito_origen(carrito)
    if not origen_logistico:
        return [], 0.0

    items = []
    subtotal = 0.0
    selecciones_combo = session.get("combo_selecciones", {})
    extras_selecciones = session.get("extras_selecciones", {})
    notas_cliente_map = session.get("notas_combo", {})  # notas por línea
    presentaciones_map = session.get("presentaciones_carrito", {})  # tamaño por línea
    variantes_map_session = session.get("variantes_carrito", {})
    ids_desaparecidos = []
    for line_key, cantidad in carrito.items():
        pid = producto_id_from_line_key(line_key)
        try:
            qty = int(cantidad)
        except (ValueError, TypeError):
            continue
        if pid is None:
            ids_desaparecidos.append(line_key)
            continue
        p = productos_map.get(pid)
        producto_id_str = line_key  # alias para las lecturas de dicts paralelos
        if p is None:
            # Producto borrado en admin mientras estaba en carrito. Se marca
            # para limpieza posterior — no hacemos pop dentro del loop para
            # no mutar la sesión mientras iteramos.
            ids_desaparecidos.append(producto_id_str)
            continue
        origen_item = _origen_inventario_producto(p)
        if not _producto_disponible_en_origen(p, origen_item, qty):
            # Producto desactivado/agotado en admin mientras estaba en carrito.
            # Se marca para limpieza igual que si se hubiese borrado, para que la
            # sesión no arrastre un ID inválido y el cliente vea claramente que
            # se quitó (evita "carrito invisible" en checkout).
            ids_desaparecidos.append(producto_id_str)
            continue
        combo_items = ComboItem.query.filter_by(combo_id=p.id)\
            .order_by(ComboItem.orden.asc(), ComboItem.id.asc()).all() if p.es_combo else []
        seleccion_ids, combo_resumen, metadata = _combo_selection_payload(
            p, selecciones_combo.get(producto_id_str, {})
        )
        presentacion_tamaño = presentaciones_map.get(producto_id_str) or ""
        presentation_error = None
        pr = None
        if presentacion_tamaño:
            pr, presentation_error = validate_product_presentation_selection(
                p, presentacion_tamaño
            )
        _, option_rows, product_options_unit, product_options_error = (
            validate_product_option_selection(
                p, extras_selecciones.get(producto_id_str, {}), pr
            )
        )
        flavor_rows = [row for row in option_rows if row.get("tipo") == "sabor"]
        extras_rows = [row for row in option_rows if row.get("tipo") != "sabor"]
        if extras_rows:
            metadata["extras"] = {
                "total_unitario": product_options_unit,
                "opciones": extras_rows,
            }
        if flavor_rows:
            metadata["sabores"] = {"opciones": flavor_rows}
        try:
            if p.es_combo:
                p.validar_stock_combo_seleccion(qty, seleccion_ids, origen=origen_item)
            elif not p.disponible_para_venta_en_origen(origen_item, qty):
                raise ValueError("stock")
        except ValueError:
            # No renderiza pero además lo marca para cleanup: si el combo
            # perdió stock (admin desactivó un componente), no debe quedar
            # como zombie en la sesión.
            ids_desaparecidos.append(producto_id_str)
            continue
        combo_extras_unit = (
            float((metadata.get("combo") or {}).get("extras_total") or 0)
            if p.es_combo else 0.0
        )
        precio = (
            float(p.precio_combo_para_seleccion(
                seleccion_ids,
                (
                    selecciones_combo.get(producto_id_str, {}).get("__presentations__", {})
                    if isinstance(selecciones_combo.get(producto_id_str, {}), dict)
                    else {}
                ),
                (
                    selecciones_combo.get(producto_id_str, {}).get("__units__", {})
                    if isinstance(selecciones_combo.get(producto_id_str, {}), dict)
                    else {}
                ),
            ))
            if p.es_combo else float(p.precio_final or 0)
        ) + product_options_unit
        # Presentación (tamaño) opt-in: aplicar precio_extra + registrar tamaño
        presentacion_extra = 0.0
        if presentacion_tamaño:
            if pr and not presentation_error:
                presentacion_extra = pr.precio_extra_float
                precio += presentacion_extra
                metadata["presentacion"] = presentation_metadata(pr)
            else:
                presentacion_tamaño = ""
        precio = round(precio, 2)
        item_total = round(precio * qty, 2)
        subtotal += item_total
        nota_cliente_item = (notas_cliente_map.get(producto_id_str) or "").strip()[:240]
        items.append({"line_key": line_key,
                      "producto": p, "cantidad": qty, "subtotal": item_total,
                      "origen": origen_item,
                      "precio_unit": precio,
                      "combo_extra_unit": combo_extras_unit,
                      "combo_items": combo_items,
                      "combo_display_items": _combo_display_items(combo_items, metadata),
                      "combo_seleccion_ids": seleccion_ids,
                      "combo_resumen": combo_resumen,
                      "nota_cliente": nota_cliente_item,
                      "extras": extras_rows,
                      "sabores": flavor_rows,
                      "product_options_error": (
                          f"{p.nombre}: {product_options_error or presentation_error}"
                          if product_options_error or presentation_error else None
                      ),
                      "presentacion_tamaño": presentacion_tamaño,
                      "presentacion_extra": presentacion_extra,
                      "metadata": metadata})

    # Limpieza de productos borrados que quedaron huérfanos en la sesión.
    # Evita que el carrito quede "invisiblemente vacío" al usuario (item
    # no aparece pero session["carrito"] aún lo tiene). Registramos en log
    # para auditoría — puede indicar que el admin borró un producto activo.
    if ids_desaparecidos:
        try:
            from flask import current_app as _capp
            _capp.logger.info(
                "Carrito limpió %d producto(s) borrado(s): %s",
                len(ids_desaparecidos), ids_desaparecidos,
            )
        except Exception:
            pass
        for _k in ids_desaparecidos:
            carrito.pop(_k, None)
            for _s in ("combo_selecciones", "extras_selecciones",
                       "notas_combo", "presentaciones_carrito"):
                _map = session.get(_s) or {}
                if _k in _map:
                    _map.pop(_k, None)
                    session[_s] = _map
        _save_carrito(carrito)
    return items, round(subtotal, 2)


def _resolve_checkout_customer(nombre_invitado, telefono_invitado, direccion, nif=None):
    """
    Identifica al cliente por teléfono (identificador principal).
    Busca el registro interno por teléfono o crea uno nuevo. Estos registros
    no son cuentas autenticables y no tienen panel público.

    Si `nif` viene informado, se guarda/actualiza en el registro del cliente
    para poder emitir facturas fiscales españolas.
    """
    if not telefono_invitado:
        return None

    # Buscar cliente existente por teléfono (identificador único)
    invitado, telefono_normalizado = buscar_cliente_por_telefono(telefono_invitado)
    telefono_invitado = telefono_normalizado or telefono_invitado
    if not invitado:
        # Puede existir el teléfono con otro rol (admin/super_admin operando
        # como cliente). El unique constraint es global, así que reutilizamos.
        invitado = User.query.filter_by(telefono_normalizado=telefono_invitado).first()
    if invitado:
        # Actualizar dirección si se proveyó nueva
        if direccion and direccion != invitado.direccion:
            invitado.direccion = direccion
        if nombre_invitado and (not invitado.nombre or invitado.nombre.startswith("Cliente ")):
            invitado.nombre = nombre_invitado
        if nif:
            invitado.nif = nif
        return invitado

    # Cliente nuevo: crear con teléfono como identificador
    nombre = nombre_invitado or f"Cliente {telefono_invitado[-4:]}"
    email = internal_customer_email(telefono_invitado)
    existing_email = User.query.filter_by(email=email).first()
    if existing_email:
        email = internal_customer_email(telefono_invitado, uuid.uuid4().hex[:4])

    invitado = User(
        nombre=nombre,
        email=email,
        rol="cliente",
        telefono=telefono_invitado,
        telefono_normalizado=telefono_invitado,
        direccion=direccion or None,
        nif=nif or None,
        activo=True,
    )
    invitado.set_password(uuid.uuid4().hex)
    db.session.add(invitado)
    try:
        db.session.flush()
    except IntegrityError:
        # Race o coincidencia por unique(telefono_normalizado). Rehidratamos.
        db.session.rollback()
        invitado = User.query.filter_by(telefono_normalizado=telefono_invitado).first()
        if not invitado:
            raise
        if direccion and direccion != invitado.direccion:
            invitado.direccion = direccion
    return invitado


# ═══════════════════════════════════════════════════════════════════
# Módulo delivery por franjas horarias — endpoint público.
# Toggle: delivery_franjas_activo. Devuelve 404 limpio si está OFF.
# La reserva efectiva del cupo ocurre en checkout (integración
# pendiente en commit de UI). Este endpoint es de solo lectura para
# que el selector del cliente pinte disponibilidad en vivo.
# ═══════════════════════════════════════════════════════════════════

@public_bp.route("/delivery/preview", methods=["GET"])
def delivery_franjas_preview():
    """Vista previa del selector de franjas para el cliente.

    Página autónoma (no dentro del checkout) que permite al equipo y al
    fundador verificar el aspecto y el flujo del selector antes de
    integrarlo en el checkout real. Si el módulo está apagado la UI
    muestra un aviso amable en lugar de datos.
    """
    from store_config import get_store_value
    nombre = get_store_value("NOMBRE_NEGOCIO", "El Parcerito") or "El Parcerito"
    return render_template("public/delivery_franjas_preview.html",
                           nombre_negocio=nombre)


@public_bp.route("/api/delivery/franjas-disponibles", methods=["GET"])
def api_delivery_franjas_disponibles():
    from store_config import get_store_value

    activo = str(get_store_value("delivery_franjas_activo", "0")).strip() in ("1", "true", "True")
    if not activo:
        return jsonify({"error": "not_found"}), 404

    from delivery_slots_service import listar_franjas_cliente
    from datetime import date as _date

    try:
        horizonte = int(get_store_value("delivery_franjas_horizonte_cliente_dias", "7"))
    except (TypeError, ValueError):
        horizonte = 7

    franjas = listar_franjas_cliente(_date.today(), horizonte_dias=horizonte)
    return jsonify({"horizonte_dias": horizonte, "franjas": franjas})
