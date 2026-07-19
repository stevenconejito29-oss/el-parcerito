import json
import os
import uuid
import random
import re
import inspect
import unicodedata


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

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import current_user
from flask import current_app
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from extensions import db, get_or_404, limiter, csrf
from models import (Product, Categoria, Order, OrderItem, Review, Coupon,
                     ComboItem, ProductExtraGroup, ProductExtraOption, SiteConfig,
                     ZonaEntrega, MenuConfig, User, Proveedor, normalizar_metodo_pago,
                     AffiliateCode, IdempotencyKey, metadata_componente_combo,
                     metadata_item_pedido, utcnow as _utcnow,
                     internal_customer_email)
from idempotency import (request_idempotency_key, request_body_hash,
                          IDEMPOTENCY_TTL)
from services import (buscar_cliente_por_telefono, distribuir_pedido,
                       calcular_puntos_ganados,
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
from product_presentations_service import (
    presentation_metadata,
    validate_product_presentation_selection,
)

public_bp = Blueprint("public", __name__)

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
    return None


def _proveedor_id_origen(origen):
    origen = _normalizar_origen(origen)
    if not origen or origen == "propio":
        return None
    return int(origen.split(":", 1)[1])


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
            "abierto": bool(proveedor.activo and proveedor.esta_abierto_ahora),
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
    return bool(
        producto
        and producto.activo
        and producto.visible_ahora
        and producto.pertenece_a_origen(origen)
        and producto.disponible_para_venta_en_origen(origen, cantidad)
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
    proveedor_id = _proveedor_id_origen(origen)
    if proveedor_id:
        proveedor = proveedor or db.session.get(Proveedor, proveedor_id)
        return bool(proveedor and proveedor.activo and proveedor.esta_abierto_ahora), (
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
    return False, mensaje or f"La tienda está cerrada ahora. Horario: {apertura}–{cierre}."


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
    cfg = get_puntos_config()
    ratio = max(1, int(cfg["ratio"]))
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
        "ratio": ratio,
        "valor_euros": round(puntos / ratio, 2),
        "canjeables": [_prod(p) for p in canjeables],
        "proximo_canje": _prod(proximo) if proximo else None,
    }


# ─── CATÁLOGO ────────────────────────────────

@public_bp.route("/")
def index():
    return _render_catalogo("propio")


@public_bp.route("/informacion-legal")
def informacion_legal():
    """Resumen accesible de privacidad, cookies y condiciones de compra."""
    return render_template("public/informacion_legal.html")


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
    projection = build_catalog_projection(todos, origen)
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
            and item.producto.pertenece_a_origen(origen)
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
        "abierto": proveedor.esta_abierto_ahora if proveedor else True,
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
    return render_template("public/producto.html",
                           producto=producto, reviews=reviews, combo_items=combo_items,
                           extra_groups=extra_groups,
                           combo_fixed_base=round(combo_fixed_base, 2),
                           cart_max_qty=_cart_max_qty(),
                           origen_actual=origen,
                           establecimiento_abierto=proveedor.esta_abierto_ahora if proveedor else True,
                           volver_url=url_for("public.menu_bar", proveedor_id=proveedor.id)
                           if proveedor else url_for("public.index"),
                           stock_en_origen=_stock_en_origen,
                           fulfillment_badge=_product_fulfillment_badge)


# ─── CARRITO (sesión Flask) ──────────────────

def _get_carrito():
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
    if origen or not carrito:
        return origen

    # Compatibilidad para sesiones creadas antes de que el origen fuera explícito.
    origenes = _cart_origins(carrito)
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
    return bool(fecha and fecha < date.today())


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
    ids = [int(pid) for pid in (carrito or {})
           if str(pid) != str(exclude_key) and str(pid).isdigit()]
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
    ids = [
        int(pid) for pid in carrito.keys()
        if str(pid) != str(exclude_key) and str(pid).isdigit()
    ]
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
    if proveedor_id and (
        not proveedor or not proveedor.activo or not proveedor.esta_abierto_ahora
    ):
        return _err("El establecimiento que prepara este producto está cerrado ahora.")
    if not proveedor_id and not current_app.config.get("SKIP_DELIVERY_VALIDATION", False):
        # Bloqueo temprano cuando la tienda propia está cerrada por horario:
        # antes solo se detectaba en checkout, dejando llenar el carrito en vano.
        abierto_local, msg_cierre = _establecimiento_abierto_checkout(origen_solicitado, None)
        if not abierto_local:
            return _err(msg_cierre or "La tienda está cerrada ahora, no podemos añadir productos, parce.")
    cart_max_qty = _cart_max_qty()
    try:
        cantidad = max(1, min(cart_max_qty, int(request.form.get("cantidad", 1))))
    except (ValueError, TypeError):
        cantidad = 1
    carrito = _get_carrito()
    origen_carrito = _carrito_origen(carrito)
    if origen_carrito and origen_solicitado != origen_carrito:
        return _err(
            f"Tu {cart_name} contiene productos de un origen de inventario incompatible. "
            "Vacíalo y vuelve a añadir los productos."
        )
    key = str(producto_id)

    productos_candidato = _cart_products_from_carrito(carrito, exclude_key=key) + [producto]
    compat = _cart_compatibility(productos_candidato)
    hay_otros_productos = any(
        str(pid) != key and str(pid).isdigit() and int(qty or 0) > 0
        for pid, qty in carrito.items()
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

    nueva_cantidad_total = int(carrito.get(key, 0) or 0) + cantidad
    if nueva_cantidad_total > cart_max_qty:
        return _err(f"No puedes añadir más de {cart_max_qty} unidades por producto.")
    if not producto.disponible_para_venta_en_origen(origen_solicitado, nueva_cantidad_total):
        return _err("No hay stock suficiente para esa cantidad.")

    # Variantes retail opt-in: si el producto tiene variantes activas,
    # el cliente debe elegir una. Sigue el patrón de `presentaciones_carrito`:
    # dict paralelo en session, validación server-side aunque el UI la fuerce.
    if getattr(producto, "tiene_variantes", False):
        from models import ProductVariant
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
        if variante.stock is not None and nueva_cantidad_total > variante.stock:
            return _err(
                f"No hay stock suficiente de «{variante.label_publico}» "
                f"(quedan {variante.stock})."
            )
        variantes_carrito = session.get("variantes_carrito", {})
        anterior_variant = variantes_carrito.get(key)
        if carrito.get(key) and anterior_variant and int(anterior_variant) != variant_id:
            return _err(
                f"Este producto ya está en tu {cart_name} con otra variante. "
                "Elimínalo antes de cambiar la selección."
            )
        variantes_carrito[key] = variant_id
        session["variantes_carrito"] = variantes_carrito

    # Presentaciones opt-in: si el producto define presentaciones activas,
    # el cliente debe elegir una. Si no define ninguna → tamaño único (skip).
    # Comparación case-insensitive: retail guarda "S","M","L","XL"; comida
    # guarda "pequeño","mediano","grande". El form puede enviar cualquier case.
    presentation_size_raw = (request.form.get("presentation_size") or "").strip()
    presentation, presentation_error = validate_product_presentation_selection(
        producto, presentation_size_raw
    )
    if presentation_error:
        return _err(presentation_error)
    if presentation:
        presentation_canonico = presentation.tamaño
        presentaciones_carrito = session.get("presentaciones_carrito", {})
        anterior_pres = presentaciones_carrito.get(key)
        if carrito.get(key) and anterior_pres and anterior_pres != presentation_canonico:
            return _err(
                f"Este producto ya está en tu {cart_name} con otro tamaño. "
                "Elimínalo antes de cambiar la presentación."
            )
        presentaciones_carrito[key] = presentation_canonico
        session["presentaciones_carrito"] = presentaciones_carrito
    elif presentation_size_raw:
        # El form envió tamaño pero el producto no tiene presentaciones — ignorar
        pass
    if producto.es_combo:
        seleccion, error = _parse_combo_selection(
            producto,
            request.form,
            nueva_cantidad_total,
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
        try:
            producto.validar_stock_combo_seleccion(
                nueva_cantidad_total,
                _combo_selection_ids_from_saved(seleccion),
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
        selecciones_combo = session.get("combo_selecciones", {})
        selecciones_combo[key] = seleccion
        session["combo_selecciones"] = selecciones_combo
    extras, extras_error = _parse_product_extras(producto, request.form)
    if extras_error:
        return _err(extras_error, "danger")
    extras_guardados = session.get("extras_selecciones", {})
    anterior = extras_guardados.get(key, {})
    if carrito.get(key) and anterior != extras:
        return _err(
            f"Este producto ya está en tu {cart_name} con otra personalización. "
            "Elimínalo para cambiar su configuración."
        )
    if extras:
        extras_guardados[key] = extras
    else:
        extras_guardados.pop(key, None)
    session["extras_selecciones"] = extras_guardados
    carrito[key] = nueva_cantidad_total
    _set_carrito_origen(origen_solicitado)
    _save_carrito(carrito)
    notas_personalizacion = request.form.get("notas_personalizacion", "").strip()
    if notas_personalizacion:
        notas_combo = session.get("notas_combo", {})
        notas_combo[key] = notas_personalizacion
        session["notas_combo"] = notas_combo
        session.modified = True
    if _ajax:
        return jsonify({
            "ok": True,
            "nombre": producto.nombre,
            # Conteo de líneas, igual al badge renderizado por Jinja. El cliente
            # no debe inferirlo sumando porque repetir un producto no crea otra línea.
            "cart_count": len(carrito),
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
            producto = db.session.get(Product, int(key)) if str(key).isdigit() else None
            if not _producto_disponible_en_origen(producto, origen):
                del carrito[key]
                _cleanup_key(key)
                continue
            try:
                if producto.es_combo:
                    producto.validar_stock_combo_seleccion(
                        nueva_cantidad,
                        _combo_selection_ids_from_saved(selecciones_combo.get(key, {})),
                        origen=origen,
                    )
                elif not producto.disponible_para_venta_en_origen(origen, nueva_cantidad):
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


@public_bp.route("/carrito/eliminar/<int:producto_id>", methods=["POST"])
def eliminar_carrito(producto_id):
    key = str(producto_id)
    carrito = _get_carrito()
    carrito.pop(key, None)
    selecciones_combo = session.get("combo_selecciones", {})
    notas_combo = session.get("notas_combo", {})
    selecciones_combo.pop(key, None)
    extras = session.get("extras_selecciones", {})
    extras.pop(key, None)
    presentaciones = session.get("presentaciones_carrito", {})
    presentaciones.pop(key, None)
    session["presentaciones_carrito"] = presentaciones
    variantes = session.get("variantes_carrito", {})
    variantes.pop(key, None)
    session["variantes_carrito"] = variantes
    session["extras_selecciones"] = extras
    notas_combo.pop(key, None)
    session["combo_selecciones"] = selecciones_combo
    session["notas_combo"] = notas_combo
    _save_carrito(carrito)
    if request.headers.get("X-Ajax") == "1":
        return jsonify({"ok": True})
    return redirect(url_for("public.ver_carrito"))


@public_bp.route("/carrito")
def ver_carrito():
    carrito = _get_carrito()
    origen = _carrito_origen(carrito)
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
        zonas = ZonaEntrega.query.filter_by(activo=True).order_by(ZonaEntrega.orden, ZonaEntrega.nombre).all()
        zona, distancia = asignar_zona_por_coordenadas(data.get("lat"), data.get("lng"), zonas)
        if not zona:
            return jsonify({"ok": False, "distancia_km": None,
                            "mensaje": "Tu ubicación está fuera de las zonas configuradas."})
        return jsonify({"ok": True, "distancia_km": distancia, "mensaje": "Ubicación comprobada.",
                        "zona": {"id": zona.id, "nombre": zona.nombre,
                                 "precio_envio": float(zona.precio_envio or 0),
                                 "gratis_desde": float(zona.gratis_desde) if zona.gratis_desde is not None else None,
                                 "tiempo_estimado_min": zona.tiempo_estimado_min}})
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
            if zona is None and resultado.get("validacion_desactivada") and zonas:
                zona = zonas[0]
        if zona:
            resultado["zona"] = {
                "id": zona.id,
                "nombre": zona.nombre,
                "precio_envio": float(zona.precio_envio or 0),
                "gratis_desde": float(zona.gratis_desde) if zona.gratis_desde is not None else None,
                "tiempo_estimado_min": zona.tiempo_estimado_min,
            }
    return jsonify(resultado)


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
    try:
        puntos_usar = int(data.get("puntos", 0))
    except (ValueError, TypeError):
        puntos_usar = 0

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

    ratio = max(1, get_puntos_config()["ratio"])

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

@public_bp.route("/checkout", methods=["GET", "POST"])
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
    if proveedor_id and (
        not proveedor or not proveedor.activo or not proveedor.esta_abierto_ahora
    ):
        flash("El establecimiento de este pedido está cerrado o ya no está activo.", "warning")
        return redirect(establecimiento["url"])

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
    if any(not item["producto"].pertenece_a_origen(origen) for item in items):
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

        _skip_val = current_app.config.get("SKIP_DELIVERY_VALIDATION", False)
        abierto, msg_cierre = _establecimiento_abierto_checkout(origen, proveedor)
        if not _skip_val and not abierto:
            flash(msg_cierre, "warning")
            return redirect(url_for("public.checkout"))
        if proveedor_id:
            proveedor = db.session.get(Proveedor, proveedor_id)
            if not proveedor or not proveedor.activo or not proveedor.esta_abierto_ahora:
                flash("El bar cerró antes de confirmar el pedido. Tu carrito se conserva.", "warning")
                return redirect(establecimiento["url"])

        tipo_entrega_cliente = _fulfillment_from_request(fulfillment_default, fulfillment_options)
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
        if tipo_entrega_cliente == "recogida":
            direccion = ""
        metodo_pago = normalizar_metodo_pago(request.form.get("metodo_pago"))
        if metodo_pago == "efectivo" and SiteConfig.get("EFECTIVO_HABILITADO", "1") != "1":
            flash("El pago en efectivo no está habilitado.", "danger")
            return redirect(url_for("public.checkout"))
        if metodo_pago == "bizum":
            if SiteConfig.get("BIZUM_HABILITADO", "1") != "1" or not SiteConfig.get("BIZUM_TELEFONO", ""):
                flash("El pago mediante Bizum no está habilitado.", "danger")
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
                if not producto.fecha_llegada or producto.fecha_llegada < datetime.now().date():
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
            geo = validar_radio_entrega(direccion)
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
        if zona_asignada is None and geo and geo.get("validacion_desactivada") and zonas:
            zona_asignada = zonas[0]
        if zona_asignada:
            zona_id = zona_asignada.id
        else:
            cualquier_geo = any(z.tiene_geo for z in zonas if z.activo)
            if tipo_entrega_cliente == "delivery" and cualquier_geo and not _skip_val:
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
            zona_id = zonas[0].id if tipo_entrega_cliente == "delivery" and zonas else None

        # ── Resolver cliente ────────────────────────────────────────────
        # ValueError se lanza para errores de validación (mensaje ya legible).
        # SQLAlchemyError puede aparecer si otro request creó al mismo cliente
        # a la vez (race condition en unique constraint sobre teléfono).
        try:
            cliente = _resolve_checkout_customer(nombre_invitado, telefono_invitado, direccion)
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
        puntos_cfg = get_puntos_config()
        ratio = puntos_cfg["ratio"]
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
                puntos_usar=puntos_a_canjear,
                zona=zona,
                ratio_puntos=ratio,
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
            direccion_entrega=direccion,
            notas=notas,
            zona_id=zona.id if zona else None,
            es_entrega_epicentro=es_entrega_epicentro,
            afiliado_codigo_id=afiliado_codigo.id if afiliado_codigo else None,
        )
        aplicar_snapshot_zona_pedido(pedido, zona, precio.costo_envio)
        db.session.add(pedido)
        db.session.flush()
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
                            origen,
                        ),
                        ensure_ascii=False,
                    ),
                )
                db.session.add(oi)
                if item["producto"].tipo_entrega == "inmediato":
                    _descontar_stock_en_origen(
                        item["producto"],
                        origen,
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
                puntos_usar=puntos_a_canjear,
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

    precio_preview = calcular_precio(items, subtotal, ratio_puntos=get_puntos_config()["ratio"])
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
                           radio_entrega_km=radio_entrega_km,
                           cobertura_por_zonas=any(z.tiene_geo for z in zonas),
                           puntos_sesion=cart_puntos_sesion,
                           producto_canje_seleccionado=session.get("cart_producto_canje_id"))


@public_bp.route("/pedido/<int:pedido_id>/confirmado")
def pedido_confirmado(pedido_id):
    pedido = get_or_404(Order, pedido_id)
    token = request.args.get("token", "") or session.get("last_guest_order_token", "")
    guest_tokens = session.get("guest_order_tokens", {})
    slot = guest_tokens.get(str(pedido_id))
    # Compat: valores antiguos guardaban str; los nuevos guardan dict con TTL.
    if isinstance(slot, dict):
        expected = slot.get("token", "")
        exp = int(slot.get("exp") or 0)
        if exp and exp < int(datetime.utcnow().timestamp()):
            flash("Este enlace de pedido ya expiró.", "warning")
            return redirect(url_for("public.index"))
    else:
        expected = slot
    if not token or token != expected:
        flash("Acceso denegado.", "danger")
        return redirect(url_for("public.index"))
    return render_template("public/pedido_confirmado.html", pedido=pedido)


# ─── CLUB DE CLIENTES ────────────────────────


@public_bp.route("/club")
def club():
    if not _feature_enabled("puntos"):
        flash(f'{get_loyalty_terms()["name"]} no está habilitado en esta tienda.', "info")
        return redirect(url_for("public.index"))
    # Vitrina de canje: productos solo_canje visibles en el catálogo,
    # ordenados por puntos ascendentes para mostrar primero lo más accesible.
    # Filtro por nicho activo → un canje retail no aparece en comida y viceversa.
    canjeables = [
        p for p in (
            Product.query.filter_by(activo=True, solo_canje=True)
            .filter(Product.puntos_para_canje.isnot(None))
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
    return seleccion, None


def _combo_group_key(grupo):
    return "".join(ch if ch.isalnum() else "_" for ch in (grupo or "Seleccion")).strip("_") or "Seleccion"


def _parse_product_extras(producto, form):
    groups = ProductExtraGroup.query.filter_by(producto_id=producto.id, activo=True).all()
    raw_selected = {}
    for group in groups:
        active_options = group.opciones.filter_by(activo=True).all()
        if group.tipo == "sabor":
            raw_flavor = form.get(f"flavor_group_{group.id}")
            if raw_flavor not in (None, ""):
                try:
                    flavor_id = int(raw_flavor)
                except (TypeError, ValueError):
                    return {}, f"El sabor elegido en «{group.nombre}» no es válido."
                flavor = next((option for option in active_options if option.id == flavor_id), None)
                if not flavor:
                    return {}, f"El sabor elegido en «{group.nombre}» ya no está disponible."
                raw_selected[str(flavor.id)] = 1
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
    selected, _, _, error = validate_product_option_selection(producto, raw_selected)
    return selected, error


def _product_extras_payload(producto, selected):
    _, rows, total, error = validate_product_option_selection(producto, selected)
    return ([], 0.0) if error else (rows, total)


def _combo_selection_ids_from_saved(seleccion_guardada):
    ids = []
    if not isinstance(seleccion_guardada, dict):
        return ids
    for qty_map in seleccion_guardada.values():
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
            opciones_meta.append({
                **metadata_componente_combo(by_id[item_id], producto.proveedor_despachador_id),
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
    metadata = {"combo": {"extras_total": extras_total, "componentes": [
        {
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
        for item in fijos
    ], "selecciones": grupos_meta}}
    return seleccion_ids, " | ".join(resumen), metadata


def _combo_display_items(combo_items, metadata):
    combo_meta = (metadata or {}).get("combo", {})
    selected_ids = set()
    for group in combo_meta.get("selecciones", []):
        for option in group.get("opciones", []):
            try:
                selected_ids.add(int(option.get("combo_item_id")))
            except (TypeError, ValueError):
                continue

    rows = []
    for item in combo_items:
        if not item.es_seleccionable:
            rows.append({"item": item, "tipo": "Fijo", "seleccionado": False})
        elif item.id in selected_ids:
            rows.append({
                "item": item,
                "tipo": item.grupo.nombre_publico if item.grupo else (item.grupo_seleccion or "Selección"),
                "seleccionado": True,
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

    try:
        ids = [int(pid) for pid in carrito.keys()]
    except (ValueError, TypeError):
        return [], 0.0

    productos_map = {p.id: p for p in Product.query.filter(Product.id.in_(ids)).all()}
    origen = _carrito_origen(carrito)
    if not origen:
        return [], 0.0

    items = []
    subtotal = 0.0
    selecciones_combo = session.get("combo_selecciones", {})
    extras_selecciones = session.get("extras_selecciones", {})
    notas_cliente_map = session.get("notas_combo", {})  # notas por producto: "sin cebolla", etc.
    presentaciones_map = session.get("presentaciones_carrito", {})  # tamaño S/M/L por producto
    ids_desaparecidos = []
    for producto_id_str, cantidad in carrito.items():
        try:
            pid = int(producto_id_str)
            qty = int(cantidad)
        except (ValueError, TypeError):
            continue
        p = productos_map.get(pid)
        if p is None:
            # Producto borrado en admin mientras estaba en carrito. Se marca
            # para limpieza posterior — no hacemos pop dentro del loop para
            # no mutar la sesión mientras iteramos.
            ids_desaparecidos.append(producto_id_str)
            continue
        if not _producto_disponible_en_origen(p, origen, qty):
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
        _, option_rows, product_options_unit, product_options_error = (
            validate_product_option_selection(
                p, extras_selecciones.get(producto_id_str, {})
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
                p.validar_stock_combo_seleccion(qty, seleccion_ids, origen=origen)
            elif not p.disponible_para_venta_en_origen(origen, qty):
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
            float(p.precio_combo_para_seleccion(seleccion_ids))
            if p.es_combo else float(p.precio_final or 0)
        ) + product_options_unit
        # Presentación (tamaño) opt-in: aplicar precio_extra + registrar tamaño
        presentacion_tamaño = presentaciones_map.get(producto_id_str) or ""
        presentacion_extra = 0.0
        presentation_error = None
        if presentacion_tamaño:
            pr, presentation_error = validate_product_presentation_selection(
                p, presentacion_tamaño
            )
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
        items.append({"producto": p, "cantidad": qty, "subtotal": item_total,
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
