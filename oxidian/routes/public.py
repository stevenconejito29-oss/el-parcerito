import json
import os
import uuid
import random
import re
import inspect
from urllib.parse import quote
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import current_user
from flask import current_app
from sqlalchemy.orm import joinedload
from extensions import db, get_or_404, limiter, csrf
from models import (Product, Categoria, Order, OrderItem, Review, Coupon,
                     ComboItem, ProductExtraGroup, ProductExtraOption, SiteConfig,
                     ZonaEntrega, MenuConfig, User, Proveedor, normalizar_metodo_pago,
                     AffiliateCode, IdempotencyKey, metadata_componente_combo,
                     metadata_item_pedido, utcnow as _utcnow)
from idempotency import (request_idempotency_key, request_body_hash,
                          IDEMPOTENCY_TTL)
from services import (distribuir_pedido,
                       enviar_whatsapp_estado, validar_radio_entrega,
                       asignar_zona_por_direccion,
                       registrar_uso_afiliado, get_puntos_config,
                       registrar_pedido_creado, sincronizar_proveedores_pedido,
                       encolar_notificaciones_proveedores_pedido,
                       tienda_abierta_en_horario)
from pricing_service import calcular_precio
from loyalty_service import (
    aplicar_canje_en_pedido,
    bloquear_cliente_puntos,
    enviar_saldo_puntos,
    solicitar_codigo,
)
from phone_utils import normalizar_telefono_cliente, telefono_valido
from store_config import get_store_features, get_service_commission, is_service_mode

public_bp = Blueprint("public", __name__)


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


def _find_cliente_by_phone(raw, allow_fuzzy=True):
    telefono = _normalize_phone(raw)
    if not telefono_valido(telefono):
        return None, telefono
    cliente = User.query.filter_by(
        rol="cliente",
        telefono_normalizado=telefono,
    ).first()
    return cliente, telefono


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


def _producto_disponible_en_origen(producto, origen, cantidad=1):
    if producto and _delivery_family(producto) == "programado" and not _feature_enabled("pedidos_programados"):
        return False
    if producto and not _fulfillment_options([producto]):
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


def _fulfillment_options(productos=None):
    features = get_store_features()
    allowed = set()
    if features["delivery"]:
        allowed.add("delivery")
    if features["recogida"]:
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
        SiteConfig.clave.in_(["HORARIO_APERTURA", "HORARIO_CIERRE", "TIENDA_FORZAR_CERRADA", "TIENDA_MENSAJE_CIERRE"])
    ).all()}
    apertura = cfg.get("HORARIO_APERTURA", "09:00")
    cierre = cfg.get("HORARIO_CIERRE", "22:30")
    forzada = str(cfg.get("TIENDA_FORZAR_CERRADA", "0")).lower() in ("1", "true", "yes", "on")
    ahora = datetime.now().strftime("%H:%M")
    if tienda_abierta_en_horario(apertura, cierre, ahora, forzada):
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


def _canjeables_payload(cliente, origen=None):
    puntos = max(0, int(cliente.puntos or 0)) if cliente else 0
    cfg = get_puntos_config()
    ratio = max(1, int(cfg["ratio"]))
    origen = _normalizar_origen(origen)
    base_q = Product.query.filter_by(activo=True, canjeable_con_puntos=True)\
                          .filter(Product.puntos_para_canje.isnot(None))\
                          .filter(Product.precio.isnot(None), Product.precio > 0)\
                          .order_by(Product.puntos_para_canje.asc(), Product.nombre.asc())
    candidatos = base_q.all()
    candidatos = [
        p for p in candidatos
        if origen
        and _producto_canjeable_en_origen(p, origen)
    ]
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


def _variantes_catalogo_unificadas(productos, origen_preferido=None):
    """Compatibilidad para scripts antiguos: ya no colapsa ni sustituye variantes."""
    return list(productos)


# ─── CATÁLOGO ────────────────────────────────

@public_bp.route("/")
def index():
    return _render_catalogo("propio")


@public_bp.route("/bar/<int:proveedor_id>")
def menu_bar(proveedor_id):
    flash("Esta tienda funciona como un único establecimiento.", "info")
    return redirect(url_for("public.index"))


def _render_catalogo(origen, proveedor=None):
    categorias = Categoria.query.filter_by(activo=True).all()
    categoria_id = request.args.get("categoria", type=int)
    busqueda = request.args.get("q", "").strip()

    base_query = Product.query.filter_by(activo=True)
    if busqueda:
        # Eliminar wildcards de LIKE para evitar escaneos no intencionados
        busqueda_q = re.sub(r"[%_\\]", "", busqueda)
        if busqueda_q:
            base_query = base_query.filter(Product.nombre.ilike(f"%{busqueda_q}%"))

    todos = base_query.all()
    productos_catalogo = [
        p for p in todos
        if _producto_disponible_en_origen(p, origen)
    ]
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
            and item.producto.disponible_para_venta_en_origen(origen)
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

    return render_template("public/index.html",
                           productos=productos, categorias=categorias,
                           categoria_counts=categoria_counts,
                           categoria_activa=categoria_id,
                           busqueda=busqueda,
                           menu_items=menu_items,
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
                           stock_en_origen=_stock_en_origen)


@public_bp.route("/whatsapp")
def whatsapp():
    """Enlace publico unico del dominio hacia el chatbot de WhatsApp."""
    telefono = SiteConfig.get("TELEFONO_NEGOCIO", "") or os.environ.get("OWNER_NUMBER", "")
    digits = _whatsapp_phone_digits(telefono)
    if not digits:
        flash("WhatsApp no esta configurado todavia.", "warning")
        return redirect(url_for("public.index"))

    nombre = SiteConfig.get("NOMBRE_NEGOCIO", "") or "Mi tienda"
    public_url = (
        SiteConfig.get("TIENDA_URL", "")
        or SiteConfig.get("OXIDIAN_PUBLIC_URL", "")
        or request.url_root.rstrip("/")
    ).rstrip("/")
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
                           stock_en_origen=_stock_en_origen)


# ─── CARRITO (sesión Flask) ──────────────────

def _get_carrito():
    return session.get("carrito", {})

def _save_carrito(carrito):
    session["carrito"] = carrito
    if not carrito:
        session.pop("carrito_origen", None)
        session.pop("cart_puntos", None)
        session.pop("cart_producto_canje_id", None)
        session.pop("combo_selecciones", None)
        session.pop("extras_selecciones", None)
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
        session.pop("cart_puntos", None)
        session.pop("cart_producto_canje_id", None)
    if origen:
        session["carrito_origen"] = origen
    else:
        session.pop("carrito_origen", None)
    session.modified = True


def _delivery_family(producto):
    tipo = (getattr(producto, "tipo_entrega", None) or "inmediato").strip().lower()
    return "programado" if tipo in ("programado", "encargo") else "inmediato"


def _prep_family(producto):
    """Canal de preparación: 'cocina' | 'almacen'."""
    return (getattr(producto, "canal_preparacion", None) or "cocina").strip().lower()


def _cart_delivery_families(carrito, exclude_key=None):
    if not carrito:
        return set()
    ids = []
    for pid in carrito.keys():
        if str(pid) == str(exclude_key):
            continue
        try:
            ids.append(int(pid))
        except (TypeError, ValueError):
            continue
    if not ids:
        return set()
    productos = Product.query.filter(Product.id.in_(ids), Product.activo == True).all()
    return {_delivery_family(p) for p in productos}


def _cart_prep_families(carrito, exclude_key=None):
    """Devuelve el conjunto de canales de preparación en el carrito actual."""
    if not carrito:
        return set()
    ids = [int(pid) for pid in carrito.keys()
           if str(pid) != str(exclude_key) and str(pid).isdigit()]
    if not ids:
        return set()
    productos = Product.query.filter(Product.id.in_(ids), Product.activo == True).all()
    return {_prep_family(p) for p in productos}


def _cart_fulfillment_options(carrito, exclude_key=None):
    ids = [int(pid) for pid in (carrito or {})
           if str(pid) != str(exclude_key) and str(pid).isdigit()]
    productos = Product.query.filter(Product.id.in_(ids), Product.activo == True).all() if ids else []
    return _fulfillment_options(productos)

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


def _items_delivery_families(items):
    return {_delivery_family(item["producto"]) for item in items if item.get("producto")}


def _items_origins(items):
    return {
        item["producto"].origen_operativo_key
        for item in items if item.get("producto")
    }


@public_bp.route("/carrito/agregar/<int:producto_id>", methods=["POST"])
def agregar_carrito(producto_id):
    _ajax = request.headers.get("X-Ajax") == "1"

    def _err(msg, category="warning"):
        if _ajax:
            return jsonify({"ok": False, "msg": msg}), 200
        flash(msg, category)
        return redirect(request.referrer or url_for("public.index"))

    producto = get_or_404(Product, producto_id)
    origen_solicitado = _normalizar_origen(request.form.get("origen"))
    if not origen_solicitado:
        origen_solicitado = "propio"
    proveedor_id = _proveedor_id_origen(origen_solicitado)
    proveedor = db.session.get(Proveedor, proveedor_id) if proveedor_id else None
    if not _producto_disponible_en_origen(producto, origen_solicitado):
        return _err("Este producto no está disponible ahora.")
    if proveedor_id and (
        not proveedor or not proveedor.activo or not proveedor.esta_abierto_ahora
    ):
        return _err("El establecimiento que prepara este producto está cerrado ahora.")
    cart_max_qty = _cart_max_qty()
    try:
        cantidad = max(1, min(cart_max_qty, int(request.form.get("cantidad", 1))))
    except (ValueError, TypeError):
        cantidad = 1
    carrito = _get_carrito()
    origen_carrito = _carrito_origen(carrito)
    if origen_carrito and origen_solicitado != origen_carrito:
        return _err(
            "El carrito contiene productos de un origen de inventario incompatible. "
            "Vacíalo y vuelve a añadir los productos."
        )
    key = str(producto_id)

    # Bloqueo 1: no mezclar inmediato con programado
    familias_actuales = _cart_delivery_families(carrito, exclude_key=key)
    familia_producto = _delivery_family(producto)
    if familias_actuales and familia_producto not in familias_actuales:
        return _err(
            "Para evitar errores de preparación y despacho, haz pedidos separados: "
            "uno para delivery inmediato y otro para productos con fecha fija."
        )

    # Bloqueo 2: no mezclar productos de cocina con productos de almacén
    canales_actuales = _cart_prep_families(carrito, exclude_key=key)
    canal_producto = _prep_family(producto)
    if canales_actuales and canal_producto not in canales_actuales:
        if canal_producto == "almacen":
            return _err(
                "Las bebidas y productos envasados requieren un pedido independiente. "
                "Finaliza tu pedido actual y luego añade estos productos."
            )
        return _err(
            "Los productos de cocina se preparan al momento y requieren un pedido independiente. "
            "Finaliza primero tu pedido de almacén."
        )

    opciones_actuales = set(_cart_fulfillment_options(carrito, exclude_key=key))
    opciones_producto = set(_fulfillment_options([producto]))
    if not opciones_producto:
        return _err("Este producto no tiene una modalidad de entrega habilitada ahora.")
    if opciones_actuales and not (opciones_actuales & opciones_producto):
        return _err(
            "Este producto no admite la misma modalidad que el resto del carrito. "
            "Haz un pedido para delivery y otro para recogida."
        )

    nueva_cantidad_total = int(carrito.get(key, 0) or 0) + cantidad
    if nueva_cantidad_total > cart_max_qty:
        return _err(f"No puedes añadir más de {cart_max_qty} unidades por producto.")
    if not producto.disponible_para_venta_en_origen(origen_solicitado, nueva_cantidad_total):
        return _err("No hay stock suficiente para esa cantidad.")
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
        return _err("Este producto ya está en el carrito con otros extras. Elimínalo para cambiar su configuración.")
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
        return jsonify({"ok": True, "nombre": producto.nombre}), 200
    flash(f"'{producto.nombre}' agregado al carrito.", "success")
    return redirect(request.referrer or url_for("public.index"))


@public_bp.route("/carrito/actualizar", methods=["POST"])
def actualizar_carrito():
    carrito = _get_carrito()
    origen = _carrito_origen(carrito)
    selecciones_combo = session.get("combo_selecciones", {})
    notas_combo = session.get("notas_combo", {})
    cart_max_qty = _cart_max_qty()
    for key in list(carrito.keys()):
        try:
            nueva_cantidad = max(0, min(cart_max_qty, int(request.form.get(f"cantidad_{key}", 0))))
        except (ValueError, TypeError):
            nueva_cantidad = 0
        if nueva_cantidad <= 0:
            del carrito[key]
            selecciones_combo.pop(key, None)
            extras = session.get("extras_selecciones", {})
            extras.pop(key, None)
            session["extras_selecciones"] = extras
            notas_combo.pop(key, None)
        else:
            producto = db.session.get(Product, int(key)) if str(key).isdigit() else None
            if not _producto_disponible_en_origen(producto, origen):
                del carrito[key]
                selecciones_combo.pop(key, None)
                notas_combo.pop(key, None)
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
    session["extras_selecciones"] = extras
    notas_combo.pop(key, None)
    session["combo_selecciones"] = selecciones_combo
    session["notas_combo"] = notas_combo
    _save_carrito(carrito)
    return redirect(url_for("public.ver_carrito"))


@public_bp.route("/carrito")
def ver_carrito():
    carrito = _get_carrito()
    origen = _carrito_origen(carrito)
    items, subtotal = _build_items_from_carrito(carrito)
    puntos_sesion = session.get("cart_puntos", {})
    if puntos_sesion.get("origen") != origen:
        puntos_sesion = {}
    puntos_verificados = int(puntos_sesion.get("puntos_totales", 0) or 0)
    puntos_visibles = puntos_verificados
    puntos_habilitados = _feature_enabled("puntos")
    if not puntos_habilitados:
        puntos_sesion = {}
        puntos_verificados = 0
        puntos_visibles = 0
    todos_canjeables = [
        p for p in Product.query.filter_by(canjeable_con_puntos=True, activo=True)
        .filter(Product.puntos_para_canje.isnot(None))
        .order_by(Product.puntos_para_canje.asc(), Product.nombre.asc()).all()
        if _producto_canjeable_en_origen(p, origen)
        and origen
    ] if puntos_habilitados else []
    descuento_puntos = puntos_sesion.get("descuento", 0.0)
    puntos_cfg = get_puntos_config()
    zona_principal = ZonaEntrega.query.filter_by(activo=True)\
        .order_by(ZonaEntrega.orden, ZonaEntrega.nombre).first()
    try:
        radio_entrega_km = max(0.0, float(SiteConfig.get("RADIO_ENTREGA_KM", "5") or 5))
    except (TypeError, ValueError):
        radio_entrega_km = 5.0
    cart_max_qty = _cart_max_qty()
    fulfillment_options = _fulfillment_options([item["producto"] for item in items])
    return render_template("public/carrito.html",
                           items=items, subtotal=subtotal,
                           canjeables=todos_canjeables,
                           puntos_sesion=puntos_sesion,
                           descuento_puntos=descuento_puntos,
                           puntos_ratio=puntos_cfg["ratio"],
                           puntos_por_euro=puntos_cfg["por_euro"],
                           puntos_visibles=puntos_visibles,
                           puntos_habilitados=puntos_habilitados,
                           zona_principal=zona_principal,
                           radio_entrega_km=radio_entrega_km,
                           fulfillment_options=fulfillment_options,
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
        return jsonify({"ok": False, "msg": "El club de puntos no está habilitado"}), 403
    data = request.get_json(silent=True) or {}
    prod_id = data.get("producto_id")
    if prod_id:
        try:
            prod_id = int(prod_id)
        except (ValueError, TypeError):
            return jsonify({"ok": False, "msg": "producto_id inválido"}), 400
        producto = db.session.get(Product, prod_id)
        origen = _carrito_origen()
        if (
            not producto
            or not origen
            or not _producto_canjeable_en_origen(producto, origen)
        ):
            return jsonify({"ok": False, "msg": "Producto no canjeable"}), 400
        cart_puntos = session.get("cart_puntos") or {}
        puntos_disponibles = 0
        if cart_puntos.get("cliente_id") and cart_puntos.get("origen") == origen:
            puntos_disponibles = int(cart_puntos.get("puntos_totales") or 0)
        if int(producto.puntos_para_canje or 0) > puntos_disponibles:
            return jsonify({"ok": False, "msg": "No tienes puntos suficientes para este producto"}), 400
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
    """Envía el saldo al número consultado sin revelarlo en el navegador."""
    if not _feature_enabled("puntos"):
        return _json_no_store({"ok": False, "msg": "El club de puntos no está habilitado"}, 403)
    data = request.get_json(silent=True) or {}
    cliente, _ = _find_cliente_by_phone(data.get("telefono", ""), allow_fuzzy=False)
    if cliente:
        try:
            enviar_saldo_puntos(cliente)
        except Exception:
            current_app.logger.exception("No se pudo enviar el saldo de puntos")
    return _json_no_store({
        "ok": True,
        "msg": "Si el número tiene puntos, recibirá el saldo por WhatsApp.",
    })


# ─── CHECK DIRECCIÓN EN TIEMPO REAL (AJAX) ────────────────────

@public_bp.route("/api/check-address", methods=["POST"])
@csrf.exempt
@limiter.limit("30 per minute") if limiter else (lambda f: f)
def api_check_address():
    """Valida si una dirección está dentro del radio de entrega. Sin autenticación requerida."""
    data = request.get_json(silent=True) or {}
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


# ─── VALIDAR CUPÓN (AJAX) ────────────────────

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
    try:
        descuento = cupon.calcular_descuento(subtotal)
        return jsonify({"ok": True, "descuento": descuento, "cupon_id": cupon.id,
                        "descripcion": cupon.descripcion})
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)})


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
        return jsonify({"ok": False, "msg": "Código de afiliado no encontrado"})
    ok, reason = af.es_valido()
    if not ok:
        return jsonify({"ok": False, "msg": reason or "Código no válido o expirado"})
    descuento = 0.0
    if af.descuento_tipo == "porcentaje" and af.descuento_valor:
        descuento = round(subtotal * float(af.descuento_valor) / 100, 2)
    elif af.descuento_tipo == "monto_fijo" and af.descuento_valor:
        descuento = min(float(af.descuento_valor), subtotal)
    return jsonify({"ok": True, "descuento": descuento, "codigo": af.codigo,
                    "descripcion": af.descripcion or af.codigo,
                    "descuento_tipo": af.descuento_tipo,
                    "descuento_valor": float(af.descuento_valor or 0)})


@public_bp.route("/puntos/solicitar-codigo", methods=["POST"])
@limiter.limit("5 per minute") if limiter else (lambda f: f)
def solicitar_codigo_puntos():
    """Envía un código al WhatsApp que identifica al cliente."""
    if not _feature_enabled("puntos"):
        return jsonify({"ok": False, "msg": "El club de puntos no está habilitado"}), 403
    data = request.get_json(silent=True) or {}
    telefono = data.get("telefono", "").strip()
    if not telefono:
        return jsonify({"ok": False, "msg": "Indica tu número de teléfono"})
    cliente, _ = _find_cliente_by_phone(telefono)
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
        return jsonify({"ok": False, "msg": "El club de puntos no está habilitado"}), 403
    data = request.get_json(silent=True) or {}
    telefono = data.get("telefono", "").strip()
    codigo = data.get("codigo", "").strip()
    try:
        puntos_usar = int(data.get("puntos", 0))
    except (ValueError, TypeError):
        puntos_usar = 0

    if telefono:
        cliente, _ = _find_cliente_by_phone(telefono)
    else:
        return jsonify({"ok": False, "msg": "Teléfono requerido"})

    if not cliente:
        return jsonify({"ok": False, "msg": "Cliente no encontrado"})

    if not cliente.verificar_cod_puntos(codigo):
        db.session.commit()  # persiste incremento de intentos fallidos
        return jsonify({"ok": False, "msg": "Código incorrecto o expirado"})

    # OTP válido: commit inmediato para que no pueda reutilizarse antes del checkout
    db.session.commit()

    ratio = max(1, get_puntos_config()["ratio"])
    origen = _carrito_origen()
    if not origen:
        return jsonify({"ok": False, "msg": "El carrito no tiene un origen de inventario válido"})
    _, subtotal = _build_items_from_carrito(_get_carrito())
    max_puntos_por_carrito = int(max(subtotal, 0) * ratio)
    puntos_usar = min(max(puntos_usar, 0), cliente.puntos, max_puntos_por_carrito)
    producto_canje_id = data.get("producto_canje_id")
    if producto_canje_id:
        try:
            producto_canje_id = int(producto_canje_id)
        except (ValueError, TypeError):
            producto_canje_id = None
    descuento = round(puntos_usar / ratio, 2)
    if producto_canje_id:
        producto_canje = db.session.get(Product, producto_canje_id)
        if (
            not producto_canje
            or not _producto_canjeable_en_origen(producto_canje, origen)
        ):
            return jsonify({"ok": False, "msg": "Producto de canje no válido"})
        if puntos_usar + int(producto_canje.puntos_para_canje or 0) > int(cliente.puntos or 0):
            return jsonify({"ok": False, "msg": "Los puntos no alcanzan para descuento y producto a la vez"})
        session["cart_producto_canje_id"] = producto_canje_id
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

    payload = _canjeables_payload(cliente, origen)
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
        session.pop("carrito", None)
        session.pop("combo_selecciones", None)
        session.pop("extras_selecciones", None)
        return redirect(url_for("public.index"))
    if len(items) != len(carrito):
        flash(
            "Uno o más productos cambiaron de disponibilidad o stock. "
            "Revisa el carrito antes de confirmar.",
            "warning",
        )
        return redirect(url_for("public.ver_carrito"))
    if len(_items_delivery_families(items)) > 1:
        flash(
            "Tu carrito mezcla delivery inmediato con productos de fecha fija. "
            "Sepáralos en dos pedidos para que cocina, preparación y reparto trabajen bien.",
            "warning",
        )
        return redirect(url_for("public.ver_carrito"))
    canales_prep = {_prep_family(item["producto"]) for item in items if item.get("producto")}
    if len(canales_prep) > 1:
        flash(
            "Tu carrito mezcla productos de cocina con productos de almacén. "
            "Haz pedidos separados para garantizar calidad.",
            "warning",
        )
        return redirect(url_for("public.ver_carrito"))
    if any(not item["producto"].pertenece_a_origen(origen) for item in items):
        flash("Hay productos incompatibles con el origen de inventario del carrito.", "warning")
        return redirect(url_for("public.ver_carrito"))
    fulfillment_options = _fulfillment_options([item["producto"] for item in items])
    if not fulfillment_options:
        flash(
            "Los productos del carrito no comparten una modalidad válida. "
            "Separa los productos de delivery y recogida.",
            "warning",
        )
        return redirect(url_for("public.ver_carrito"))
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
        puntos_cliente = 0

    canjeables = [
        p for p in Product.query.filter_by(canjeable_con_puntos=True, activo=True)
        .filter(Product.puntos_para_canje <= puntos_cliente).all()
        if _producto_canjeable_en_origen(p, origen)
    ] if puntos_habilitados and puntos_cliente > 0 else []
    canjeables_data = [
        {
            "id": p.id,
            "nombre": p.nombre,
            "puntos": int(p.puntos_para_canje or 0),
            "categoria": p.categoria.nombre if p.categoria else "",
            "origen": p.origen_pais or "",
        }
        for p in canjeables
    ]

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
                    guest_tokens[str(prev.order_id)] = token
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
        puntos_a_canjear = request.form.get("puntos_usar", 0, type=int)
        zona_id = request.form.get("zona_id", type=int)
        nombre_invitado = request.form.get("nombre_invitado", "").strip()[:100]
        telefono_invitado = _normalize_phone(request.form.get("telefono_invitado", ""))
        codigo_afiliado_str = request.form.get("codigo_afiliado", "").strip().upper()
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
                flash(f"'{producto.nombre}' no admite esta modalidad de entrega.", "danger")
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
        zona_asignada = asignar_zona_por_direccion(direccion, zonas) if tipo_entrega_cliente == "delivery" and direccion else None
        if zona_asignada:
            zona_id = zona_asignada.id
        else:
            cualquier_geo = any(z.tiene_geo for z in zonas if z.activo)
            if tipo_entrega_cliente == "delivery" and cualquier_geo and not _skip_val:
                flash(
                    "Tu dirección está fuera de todas nuestras zonas de cobertura. "
                    "Comprueba la dirección o contacta con el negocio.",
                    "danger",
                )
                return redirect(url_for("public.checkout"))
            zona_id = zonas[0].id if tipo_entrega_cliente == "delivery" and zonas else None

        # ── Resolver cliente ────────────────────────────────────────────
        try:
            cliente = _resolve_checkout_customer(nombre_invitado, telefono_invitado, direccion)
        except ValueError as exc:
            flash(str(exc), "danger")
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
                ok_c, msg_c = cupon.es_valido()
                if not ok_c:
                    flash(f"Cupón no válido: {msg_c}", "danger")
                    return redirect(url_for("public.checkout"))

        afiliado_codigo = None
        if codigo_afiliado_str:
            afiliado_codigo = AffiliateCode.query.filter_by(codigo=codigo_afiliado_str).first()
            if afiliado_codigo:
                ok_a, _ = afiliado_codigo.es_valido()
                if not ok_a:
                    afiliado_codigo = None

        # ── Puntos verificados en sesión ─────────────────────────────────
        puntos_cfg = get_puntos_config()
        ratio = puntos_cfg["ratio"]
        puntos_por_euro = puntos_cfg["por_euro"]
        puntos_a_canjear = 0
        cart_puntos = session.get("cart_puntos", {})
        if (puntos_habilitados and cart_puntos and cart_puntos.get("cliente_id") == cliente.id
                and cart_puntos.get("verificado")
                and cart_puntos.get("origen") == origen):
            pts = min(max(0, int(request.form.get("puntos_usar", 0) or 0)), cliente.puntos)
            puntos_a_canjear = pts

        # Producto canje desde sesión solo si el formulario no envió decisión explícita.
        if producto_canje_raw is None and not producto_canje_id:
            producto_canje_id = session.get("cart_producto_canje_id")
        producto_canje = db.session.get(Product, producto_canje_id) if producto_canje_id else None
        if producto_canje_id:
            if not puntos_habilitados:
                flash("El club de puntos no está habilitado en esta tienda.", "danger")
                return redirect(url_for("public.checkout"))
            if (not cart_puntos or cart_puntos.get("cliente_id") != cliente.id
                    or not cart_puntos.get("verificado")
                    or cart_puntos.get("origen") != origen):
                flash("Verifica tu WhatsApp antes de canjear productos con puntos.", "danger")
                return redirect(url_for("public.checkout"))
            if (
                not producto_canje
                or not _producto_canjeable_en_origen(producto_canje, origen)
            ):
                flash("Producto de canje no válido.", "danger")
                return redirect(url_for("public.checkout"))
            puntos_producto = int(producto_canje.puntos_para_canje or 0)
            if puntos_a_canjear + puntos_producto > int(cliente.puntos or 0):
                flash("Tus puntos no alcanzan para el descuento y el producto elegido a la vez.", "danger")
                return redirect(url_for("public.checkout"))
            familias_entrega = _items_delivery_families(items)
            canales_preparacion = {
                _prep_family(item["producto"]) for item in items if item.get("producto")
            }
            if (
                familias_entrega
                and _delivery_family(producto_canje) not in familias_entrega
            ) or (
                canales_preparacion
                and _prep_family(producto_canje) not in canales_preparacion
            ):
                flash(
                    "El producto de canje requiere un pedido separado por su preparación.",
                    "danger",
                )
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
        puntos_ganados     = int(total * puntos_por_euro) if puntos_habilitados else 0
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
        db.session.add(pedido)
        db.session.flush()
        registrar_pedido_creado(
            pedido,
            actor_id=cliente.id,
            canal="web",
            detalle="checkout web",
            metadata={
                "zona_id": zona.id if zona else None,
                "tipo_entrega_cliente": tipo_entrega_cliente,
            },
        )

        try:
            for item in items:
                precio_venta = item.get("precio_unit", item["producto"].precio_final)
                oi = OrderItem(
                    pedido_id=pedido.id,
                    producto_id=item["producto"].id,
                    cantidad=item["cantidad"],
                    precio_unit=precio_venta,
                    subtotal=round(precio_venta * item["cantidad"], 2),
                    notas=item.get("combo_resumen"),
                    metadata_json=json.dumps(
                        _metadata_item_con_origen(
                            item["producto"],
                            item.get("metadata") or {},
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
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("public.ver_carrito"))

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
        encolar_notificaciones_proveedores_pedido(pedido)

        # Los puntos se otorgan al entregar (repartidor.confirmar_entrega → award_points_on_delivery)
        # No se suman aquí para evitar que pedidos cancelados o no entregados acumulen puntos

        # Registrar uso de afiliado + generar StaffPayment de comisión automáticamente
        if afiliado_codigo and descuento_afiliado > 0:
            registrar_uso_afiliado(afiliado_codigo, pedido, cliente, descuento_afiliado)

        distribuir_pedido(pedido)

        token = uuid.uuid4().hex
        guest_tokens = session.get("guest_order_tokens", {})
        guest_tokens[str(pedido.id)] = token
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

        session.pop("carrito", None)
        session.pop("cart_puntos", None)
        session.pop("cart_producto_canje_id", None)
        session.pop("notas_combo", None)
        session.pop("combo_selecciones", None)
        session.pop("carrito_origen", None)

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
    puntos_cfg = get_puntos_config()
    return render_template("public/checkout.html", items=items, subtotal=subtotal,
                           puntos_disponibles=puntos_cliente,
                           puntos_ratio=puntos_cfg["ratio"],
                           zonas=zonas,
                           tiene_encargos=tiene_encargos,
                           canjeables=canjeables,
                           puntos_habilitados=puntos_habilitados,
                           fulfillment_options=fulfillment_options,
                           fulfillment_default=fulfillment_default,
                           checkout_items=checkout_items,
                           origen_actual=origen,
                           establecimiento=establecimiento,
                           puntos_sesion=cart_puntos_sesion
                           if cart_puntos_sesion.get("origen") == origen else {},
                           canjeables_data=canjeables_data,
                           producto_canje_seleccionado=session.get("cart_producto_canje_id"))


@public_bp.route("/pedido/<int:pedido_id>/confirmado")
def pedido_confirmado(pedido_id):
    pedido = get_or_404(Order, pedido_id)
    token = request.args.get("token", "") or session.get("last_guest_order_token", "")
    guest_tokens = session.get("guest_order_tokens", {})
    expected = guest_tokens.get(str(pedido_id))
    if not token or token != expected:
        flash("Acceso denegado.", "danger")
        return redirect(url_for("public.index"))
    return render_template("public/pedido_confirmado.html", pedido=pedido)


# ─── CLUB DE CLIENTES ────────────────────────


@public_bp.route("/club")
def club():
    if not _feature_enabled("puntos"):
        flash("El club de puntos no está habilitado en esta tienda.", "info")
        return redirect(url_for("public.index"))
    return render_template("public/puntos_consulta.html")


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
    selected = {}
    for group in groups:
        total = 0
        for option in group.opciones.filter_by(activo=True).all():
            try:
                qty = int(form.get(f"extra_qty_{option.id}", 0) or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty < 0 or qty > option.max_cantidad:
                return {}, f"Cantidad inválida para «{option.nombre}»."
            if qty:
                selected[str(option.id)] = qty
                total += qty
        if total < group.min_selecciones:
            return {}, f"Elige al menos {group.min_selecciones} opción(es) en «{group.nombre}»."
        if total > group.max_selecciones:
            return {}, f"Puedes elegir hasta {group.max_selecciones} opción(es) en «{group.nombre}»."
    return selected, None


def _product_extras_payload(producto, selected):
    selected = selected if isinstance(selected, dict) else {}
    option_ids = []
    for raw, qty in selected.items():
        try:
            if int(qty) > 0:
                option_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    options = ProductExtraOption.query.join(ProductExtraGroup).filter(
        ProductExtraOption.id.in_(option_ids), ProductExtraOption.activo.is_(True),
        ProductExtraGroup.producto_id == producto.id, ProductExtraGroup.activo.is_(True),
    ).all() if option_ids else []
    by_id = {o.id: o for o in options}
    rows, total = [], 0.0
    for raw, raw_qty in selected.items():
        try:
            option, qty = by_id.get(int(raw)), int(raw_qty)
        except (TypeError, ValueError):
            continue
        if not option or qty < 1 or qty > option.max_cantidad:
            continue
        amount = round(option.precio_float * qty, 2)
        total += amount
        rows.append({"id": option.id, "grupo": option.grupo.nombre, "nombre": option.nombre,
                     "cantidad": qty, "precio_unit": option.precio_float, "subtotal": amount})
    return rows, round(total, 2)


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
    for producto_id_str, cantidad in carrito.items():
        try:
            pid = int(producto_id_str)
            qty = int(cantidad)
        except (ValueError, TypeError):
            continue
        p = productos_map.get(pid)
        if not _producto_disponible_en_origen(p, origen, qty):
            continue
        combo_items = ComboItem.query.filter_by(combo_id=p.id)\
            .order_by(ComboItem.orden.asc(), ComboItem.id.asc()).all() if p.es_combo else []
        seleccion_ids, combo_resumen, metadata = _combo_selection_payload(
            p, selecciones_combo.get(producto_id_str, {})
        )
        extras_rows, extras_unit = _product_extras_payload(p, extras_selecciones.get(producto_id_str, {}))
        if extras_rows:
            metadata["extras"] = {"total_unitario": extras_unit, "opciones": extras_rows}
        try:
            if p.es_combo:
                p.validar_stock_combo_seleccion(qty, seleccion_ids, origen=origen)
            elif not p.disponible_para_venta_en_origen(origen, qty):
                raise ValueError("stock")
        except ValueError:
            continue
        extra_unit = float((metadata.get("combo") or {}).get("extras_total") or 0) if p.es_combo else 0.0
        precio = (float(p.precio_combo_para_seleccion(seleccion_ids)) if p.es_combo else float(p.precio_final or 0)) + extras_unit
        precio = round(precio, 2)
        item_total = round(precio * qty, 2)
        subtotal += item_total
        items.append({"producto": p, "cantidad": qty, "subtotal": item_total,
                      "precio_unit": precio,
                      "combo_extra_unit": extra_unit,
                      "combo_items": combo_items,
                      "combo_display_items": _combo_display_items(combo_items, metadata),
                      "combo_seleccion_ids": seleccion_ids,
                      "combo_resumen": combo_resumen,
                      "extras": extras_rows,
                      "metadata": metadata})
    return items, round(subtotal, 2)


def _resolve_checkout_customer(nombre_invitado, telefono_invitado, direccion):
    """
    Identifica al cliente por teléfono (identificador principal).
    Busca el registro interno por teléfono o crea uno nuevo. Estos registros
    no son cuentas autenticables y no tienen panel público.
    """
    if not telefono_invitado:
        return None

    # Buscar cliente existente por teléfono (identificador único)
    invitado, telefono_normalizado = _find_cliente_by_phone(telefono_invitado)
    telefono_invitado = telefono_normalizado or telefono_invitado
    if invitado:
        # Actualizar dirección si se proveyó nueva
        if direccion and direccion != invitado.direccion:
            invitado.direccion = direccion
        if nombre_invitado and (not invitado.nombre or invitado.nombre.startswith("Cliente ")):
            invitado.nombre = nombre_invitado
        return invitado

    # Cliente nuevo: crear con teléfono como identificador
    nombre = nombre_invitado or f"Cliente {telefono_invitado[-4:]}"
    _dom = SiteConfig.get("BOT_EMAIL_DOMAIN", "wa.internal")
    email = f"tel.{telefono_invitado}@{_dom}"
    existing_email = User.query.filter_by(email=email).first()
    if existing_email:
        email = f"tel.{telefono_invitado}.{uuid.uuid4().hex[:4]}@{_dom}"

    invitado = User(
        nombre=nombre,
        email=email,
        rol="cliente",
        telefono=telefono_invitado,
        telefono_normalizado=telefono_invitado,
        direccion=direccion or None,
        activo=True,
    )
    invitado.set_password(uuid.uuid4().hex)
    db.session.add(invitado)
    db.session.flush()
    return invitado
