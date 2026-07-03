"""
API REST para el bot de WhatsApp (contenedor Node.js externo).
Autenticación: header X-Bot-Key o X-API-Key.
"""
import json
import logging
import os
import re
import uuid
import threading
import hmac
import hashlib
import urllib.request
import urllib.error
from functools import wraps
from datetime import datetime, timedelta, date

from flask import Blueprint, jsonify, request, current_app
from extensions import db, get_or_404
from models import (User, Product, Categoria, Order, OrderItem, OrderProviderStatus,
                    ProveedorProducto,
                    Coupon, ComboItem,
                    AffiliateCode, ZonaEntrega,
                    PointsLog, SiteConfig, IdempotencyKey, normalizar_metodo_pago,
                    BotAiUsage, BotAiMessage, AdminFeature,
                    PriceHistory, metadata_componente_combo,
                    metadata_item_pedido, utcnow as _utcnow)
from idempotency import request_idempotency_key, request_body_hash, IDEMPOTENCY_TTL
from services import (distribuir_pedido, registrar_uso_afiliado,
                      get_puntos_config, enviar_whatsapp_estado, mensaje_estado_pedido,
                      registrar_pedido_creado, encolar_whatsapp_generico,
                      validar_radio_entrega, tienda_abierta_en_horario,
                      cancelar_pedido_operativo, lineas_proveedor_pedido,
                      encolar_notificaciones_proveedores_pedido)
from pricing_service import calcular_precio
from loyalty_service import aplicar_canje_en_pedido, bloquear_cliente_puntos, solicitar_codigo
from phone_utils import normalizar_telefono_cliente, telefono_valido
from store_config import get_service_commission, get_store_features, is_service_mode

api_bot_bp = Blueprint("api_bot", __name__)
logger = logging.getLogger(__name__)


def _cliente_por_telefono(value):
    telefono = normalizar_telefono_cliente(value)
    if not telefono_valido(telefono):
        return None, telefono
    cliente = User.query.filter_by(
        telefono_normalizado=telefono,
        rol="cliente",
    ).first()
    return cliente, telefono


def _delivery_family(producto):
    tipo = (getattr(producto, "tipo_entrega", None) or "inmediato").strip().lower()
    return "programado" if tipo in ("programado", "encargo") else "inmediato"


def _order_group(producto):
    key = getattr(producto, "grupo_pedido_key", None)
    if key:
        return key
    value = " ".join(str(getattr(producto, "grupo_pedido", None) or "").split()).casefold()
    return value or "__general__"


def _product_fulfillment_modes(producto):
    mode = (getattr(producto, "modalidad_entrega", None) or "ambas").strip().lower()
    if mode == "delivery": return {"delivery"}
    if mode == "recogida": return {"recogida"}
    return {"delivery", "recogida"}


def notificar_bot_sync():
    """Notifica al bot que debe re-sincronizar el catálogo (disparo asíncrono)."""
    bot_url = SiteConfig.get("BOT_API_URL", os.environ.get("BOT_API_URL", "http://127.0.0.1:3000"))
    panel_key = SiteConfig.get("BOT_PANEL_KEY", "") or SiteConfig.get("BOT_API_KEY", "")
    if not bot_url or not panel_key:
        return

    def _fire():
        try:
            req = urllib.request.Request(
                f"{bot_url.rstrip('/')}/api/oxidian/sync",
                method="POST",
                headers={"Content-Type": "application/json", "X-Panel-Key": panel_key},
                data=b"{}",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Bot no disponible para sincronizacion en %s: %s", bot_url, exc)
        except Exception:
            logger.exception("Error inesperado notificando sync al bot")
    threading.Thread(target=_fire, daemon=True).start()


# ─── AUTH ─────────────────────────────────────

def _get_api_key():
    if current_app.config.get("TESTING"):
        return current_app.config.get("BOT_API_KEY", "")
    # ENV siempre gana: permite sincronizar la key sin tocar la BD
    key = os.environ.get("BOT_API_KEY", "").strip()
    if not key:
        key = SiteConfig.get("BOT_API_KEY") or ""
    if not key:
        key = current_app.config.get("BOT_API_KEY", "")
    return key


def bot_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "api_key" in request.args:
            logger.warning("Rechazada API key del bot en query string desde %s", request.remote_addr)
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        key = (
            request.headers.get("X-Bot-Key")
            or request.headers.get("X-API-Key")
            or ""
        )
        expected = _get_api_key()
        if not expected or not key or not hmac.compare_digest(str(key), str(expected)):
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def _config_bool(clave, default="0"):
    raw = SiteConfig.get(clave, current_app.config.get(clave, default))
    return str(raw or default).strip().lower() in {"1", "true", "yes", "on"}


def _json_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _bot_order_create_enabled():
    """Los pedidos se crean exclusivamente en la tienda web o POS."""
    return False


@api_bot_bp.route("/ai/config")
@bot_required
def ai_config():
    """Configuración opcional del asistente; desactivada sin clave explícita."""
    provider = (SiteConfig.get("BOT_AI_PROVIDER", "") or "").strip().lower()
    api_key = SiteConfig.get("BOT_AI_API_KEY", "") or ""
    enabled = _config_bool("BOT_AI_ENABLED") and provider in {"openai", "groq"} and bool(api_key)
    return jsonify({
        "ok": True, "habilitado": enabled, "proveedor": provider,
        "api_key": api_key if enabled else "",
        "modelo": SiteConfig.get("BOT_AI_MODEL", "") or "",
        "temperature": 0.2, "max_tokens": 220,
        "reglas_extra": SiteConfig.get("BOT_AI_RULES", "") or "",
        "memoria_mensajes": 4,
        "system_prompt": (
            "Eres el asistente informativo de {NEGOCIO}. No tomas ni creas pedidos. "
            "Para comprar, dirige siempre a {TIENDA_URL}. No inventes información."
        ),
        "placeholders": {
            "NEGOCIO": SiteConfig.get("NOMBRE_NEGOCIO", "la tienda") or "la tienda",
            "TIENDA_URL": SiteConfig.get("TIENDA_URL", "") or SiteConfig.get("OXIDIAN_PUBLIC_URL", ""),
        },
    })


def _ai_phone_hash(telefono):
    normalizado = normalizar_telefono_cliente(telefono)
    if not telefono_valido(normalizado):
        return None, normalizado
    secret = str(current_app.config.get("SECRET_KEY") or "oxidian-ai")
    digest = hmac.new(secret.encode(), normalizado.encode(), hashlib.sha256).hexdigest()
    return digest, normalizado


def _ai_limit(name, default):
    try:
        return max(1, int(SiteConfig.get(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


@api_bot_bp.route("/ai/usage", methods=["POST"])
@bot_required
def ai_usage():
    payload = request.get_json(silent=True) or {}
    phone_hash, _ = _ai_phone_hash(payload.get("telefono"))
    if not phone_hash:
        return jsonify({"ok": False, "error": "telefono inválido"}), 400
    inicio = datetime.combine(date.today(), datetime.min.time())
    global_count = BotAiUsage.query.filter(BotAiUsage.creado_en >= inicio).count()
    client_count = BotAiUsage.query.filter(
        BotAiUsage.creado_en >= inicio,
        BotAiUsage.telefono_hash == phone_hash,
    ).count()
    global_limit = _ai_limit("BOT_AI_DAILY_GLOBAL", 500)
    client_limit = _ai_limit("BOT_AI_DAILY_CLIENT", 20)
    tokens_in = max(0, min(int(payload.get("tokens_in") or 0), 100000))
    tokens_out = max(0, min(int(payload.get("tokens_out") or 0), 100000))
    exceeded_global = global_count >= global_limit
    exceeded_client = client_count >= client_limit
    # El preflight (0/0) solo consulta; una llamada real se registra una vez.
    if (tokens_in or tokens_out) and not exceeded_global and not exceeded_client:
        db.session.add(BotAiUsage(
            telefono_hash=phone_hash,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        ))
        db.session.commit()
        global_count += 1
        client_count += 1
    return jsonify({
        "ok": True,
        "count_today_global": global_count,
        "count_today_client": client_count,
        "exceeded_global": exceeded_global,
        "exceeded_client": exceeded_client,
    })


@api_bot_bp.route("/ai/memory", methods=["GET", "POST"])
@bot_required
def ai_memory():
    payload = (request.get_json(silent=True) or {}) if request.method == "POST" else {}
    phone_hash, _ = _ai_phone_hash(payload.get("telefono") or request.args.get("telefono"))
    if not phone_hash:
        return jsonify({"ok": False, "error": "telefono inválido"}), 400
    cutoff = _utcnow() - timedelta(days=7)
    if request.method == "POST":
        rol = (payload.get("rol") or "").strip().lower()
        contenido = (payload.get("contenido") or "").strip()[:1200]
        if rol not in {"user", "assistant"} or not contenido:
            return jsonify({"ok": False, "error": "mensaje inválido"}), 400
        db.session.add(BotAiMessage(telefono_hash=phone_hash, rol=rol, contenido=contenido))
        db.session.query(BotAiMessage).filter(BotAiMessage.creado_en < cutoff).delete(synchronize_session=False)
        db.session.commit()
    rows = BotAiMessage.query.filter(
        BotAiMessage.telefono_hash == phone_hash,
        BotAiMessage.creado_en >= cutoff,
    ).order_by(BotAiMessage.id.desc()).limit(4).all()
    return jsonify({
        "ok": True,
        "messages": [{"role": row.rol, "content": row.contenido} for row in reversed(rows)],
    })


@api_bot_bp.route("/ai/cliente-context")
@bot_required
def ai_cliente_context():
    cliente, _ = _cliente_por_telefono(request.args.get("telefono"))
    features = get_store_features()
    apertura = (SiteConfig.get("HORARIO_APERTURA", "") or "").strip()
    cierre = (SiteConfig.get("HORARIO_CIERRE", "") or "").strip()
    metodos_pago = []
    if _config_bool("EFECTIVO_HABILITADO", "1"):
        metodos_pago.append("efectivo")
    if _config_bool("BIZUM_HABILITADO", "1"):
        metodos_pago.append("Bizum")
    negocio = {
        "nombre": SiteConfig.get("NOMBRE_NEGOCIO", "Mi tienda"),
        "direccion": SiteConfig.get("DIRECCION_NEGOCIO", ""),
        "horario": f"{apertura}-{cierre}" if apertura and cierre else "",
        "metodos_pago": metodos_pago,
        "delivery": features["delivery"],
        "recogida": features["recogida"],
        "puntos": features["puntos"],
        "programados": features["pedidos_programados"],
    }
    if not cliente:
        return jsonify({"ok": True, "cliente": None, "negocio": negocio})
    pedidos = cliente.pedidos.order_by(Order.creado_en.desc()).limit(3).all()
    return jsonify({
        "ok": True,
        "negocio": negocio,
        "cliente": {
            "nombre": cliente.nombre,
            "puntos": int(cliente.puntos or 0) if features["puntos"] else None,
            "total_pedidos": cliente.pedidos.count(),
            "pedidos_recientes": [
                {"numero": p.numero_pedido, "estado": p.estado, "total": float(p.total or 0)}
                for p in pedidos
            ],
        },
    })


@api_bot_bp.route("/security/admin-pin-hash")
@bot_required
def admin_pin_hash():
    """Sincroniza únicamente el hash; nunca expone un PIN en texto claro."""
    return jsonify({"ok": True, "hash": SiteConfig.get("BOT_ADMIN_PIN_HASH", "") or ""})


@api_bot_bp.route("/branding")
@bot_required
def branding():
    features = get_store_features()
    whatsapp_roles = []
    for user in User.query.filter(
        User.activo.is_(True),
        User.rol.in_(["admin", "super_admin"]),
    ).all():
        telefono = normalizar_telefono_cliente(user.telefono_normalizado or user.telefono)
        if not telefono_valido(telefono):
            continue
        if user.rol == "super_admin":
            capabilities = [
                "status", "store", "products", "points", "admins", "handoff",
                "sync", "security", "emergency", "risks", "client_mode",
            ]
        else:
            enabled = {
                row.feature for row in AdminFeature.query.filter_by(
                    user_id=user.id, activo=True
                ).all()
            }
            capabilities = ["status", "store", "risks", "client_mode"]
            if "productos" in enabled:
                capabilities.extend(["products", "sync"])
            if features["puntos"] and "marketing" in enabled:
                capabilities.append("points")
            if "whatsapp" in enabled:
                capabilities.extend(["handoff", "security"])
        whatsapp_roles.append({
            "telefono": telefono,
            "rol": user.rol,
            "capabilities": sorted(set(capabilities)),
        })
    return jsonify({
        "ok": True,
        "nombre": SiteConfig.get("NOMBRE_NEGOCIO", "Mi tienda"),
        "slogan": SiteConfig.get("SLOGAN_NEGOCIO", ""),
        "descripcion": SiteConfig.get("DESCRIPCION_NEGOCIO", ""),
        "telefono": SiteConfig.get("TELEFONO_NEGOCIO", ""),
        "direccion": SiteConfig.get("DIRECCION_NEGOCIO", ""),
        "ciudad": SiteConfig.get("CIUDAD_NEGOCIO", ""),
        "tenant_mode": features["modo_tienda"],
        "suspended": str(SiteConfig.get("TIENDA_FORZAR_CERRADA", "0")).lower() in {"1", "true", "yes", "on"},
        "delivery_enabled": features["delivery"],
        "pickup_enabled": features["recogida"],
        "scheduled_enabled": features["pedidos_programados"],
        "points_enabled": features["puntos"],
        "bizum_enabled": _config_bool("BIZUM_HABILITADO", "1"),
        "cash_enabled": _config_bool("EFECTIVO_HABILITADO", "1"),
        "horario_apertura": SiteConfig.get("HORARIO_APERTURA", ""),
        "horario_cierre": SiteConfig.get("HORARIO_CIERRE", ""),
        "whatsapp_roles": whatsapp_roles,
    })


def _combo_order_payload(producto, seleccion_item_ids):
    if not producto.es_combo:
        return "", {}

    seleccion_item_ids = {int(i) for i in (seleccion_item_ids or [])}
    componentes = ComboItem.query.filter_by(combo_id=producto.id).all()
    fijos = [item for item in componentes if not item.es_seleccionable]
    seleccionables = [item for item in componentes if item.es_seleccionable]

    resumen = [f"{item.cantidad}x {item.componente.nombre}" for item in fijos if item.componente]
    grupos_meta = []
    grupos = {}
    for item in seleccionables:
        grupos.setdefault(item.grupo_seleccion or "Seleccion", []).append(item)

    for grupo, opciones in grupos.items():
        max_sel = max(1, opciones[0].max_selecciones or 1)
        elegidos = [item for item in opciones if item.id in seleccion_item_ids]
        if not elegidos:
            elegidos = [
                item for item in opciones
                if item.es_predeterminado and item.componente
                and producto.combo_item_stock_disponible(item)
            ][:max_sel]
            if not elegidos:
                disponibles = sorted(
                    [
                        item for item in opciones
                        if item.componente and producto.combo_item_stock_disponible(item)
                    ],
                    key=lambda item: (float(item.componente.precio_final), item.orden or 0),
                )
                elegidos = disponibles[:1]
        if elegidos:
            resumen.append(f"{grupo}: {', '.join(item.componente.nombre for item in elegidos if item.componente)}")
            grupos_meta.append({
                "grupo": grupo,
                "opciones": [
                    {
                            **metadata_componente_combo(item, producto.proveedor_despachador_id),
                        "combo_item_id": item.id,
                        "producto_id": item.producto_id,
                        "nombre": item.componente.nombre if item.componente else "",
                        "cantidad": item.cantidad,
                    }
                    for item in elegidos
                ],
            })

    metadata = {
        "combo": {
            "componentes": [
                {
                        **metadata_componente_combo(item, producto.proveedor_despachador_id),
                    "combo_item_id": item.id,
                    "producto_id": item.producto_id,
                    "nombre": item.componente.nombre if item.componente else "",
                    "cantidad": item.cantidad,
                    "fijo": True,
                }
                for item in fijos
            ],
            "selecciones": grupos_meta,
        }
    }
    return " | ".join(resumen), metadata


def _producto_disponible_para_bot(producto):
    if not producto:
        return False
    return bool(
        producto
        and producto.activo
        and producto.visible_ahora
        and not producto.proveedor_despachador_id
        and producto.disponible_para_venta()
    )

def _catalogo_unificado_para_bot():
    candidatos = Product.query.filter_by(activo=True).all()
    candidatos.sort(key=lambda p: (
        0 if p.es_combo else 1,
        0 if not p.proveedor_despachador_id else 1,
        p.nombre,
        p.id,
    ))
    productos_por_clave = {}
    for producto in candidatos:
        if _producto_disponible_para_bot(producto):
            productos_por_clave.setdefault(producto.clave_catalogo, producto)
    return list(productos_por_clave.values())


def _motivos_no_disponible(producto):
    motivos = []
    if not producto.activo:
        motivos.append("inactivo")
    if not producto.visible_ahora:
        motivos.append("fuera_de_horario")
    if not producto.disponible_para_venta():
        motivos.append("sin_stock")
    return motivos


def _combo_items_payload(producto, incluir_stock=False):
    items = []
    for ci in ComboItem.query.filter_by(combo_id=producto.id).all():
        componente = ci.componente
        item = {
            "combo_item_id": ci.id,
            "producto_id": ci.producto_id,
            "nombre": componente.nombre if componente else "",
            "cantidad": ci.cantidad,
            "es_seleccionable": bool(ci.es_seleccionable),
            "grupo_seleccion": ci.grupo_seleccion,
            "max_selecciones": ci.max_selecciones,
        }
        if incluir_stock:
            item.update({
                "componente_activo": bool(componente.activo) if componente else False,
                "stock_componente": componente.stock_total if componente else 0,
                "capacidad": (componente.stock_total // max(1, ci.cantidad)) if componente else 0,
            })
        items.append(item)
    return items


def _producto_catalogo_payload(producto, incluir_diagnostico=False):
    disponible = _producto_disponible_para_bot(producto)
    payload = {
        "id": producto.id,
        "nombre": producto.nombre,
        "descripcion": producto.descripcion or "",
        "precio": float(producto.precio),
        "precio_final": float(producto.precio_final),
        "precio_costo": float(producto.precio_costo) if producto.precio_costo is not None else None,
        "tipo_producto": getattr(producto, "tipo_producto", None) or "simple",
        "tipo_entrega": getattr(producto, "tipo_entrega", None) or "inmediato",
        "modalidad_entrega": getattr(producto, "modalidad_entrega", None) or "ambas",
        "fecha_llegada": producto.fecha_llegada.isoformat() if producto.fecha_llegada else None,
        "dias_anticipacion_encargo": producto.dias_anticipacion_encargo,
        "es_combo": bool(producto.es_combo),
        "combo_precio_modo": producto.combo_precio_modo_normalizado if producto.es_combo else None,
        "combo_descuento_pct": float(producto.combo_descuento_pct or 0) if producto.es_combo else 0,
        "combo_precio_base": float(producto.combo_precio_base or 0) if producto.es_combo else 0,
        "combo_stock_disponible": int(producto.combo_stock_total) if producto.es_combo else None,
        "combo_items": _combo_items_payload(producto, incluir_stock=incluir_diagnostico) if producto.es_combo else [],
        "atributos": producto.get_atributos() if hasattr(producto, "get_atributos") else {},
        "categoria_id": producto.categoria_id,
        "categoria_nombre": producto.categoria.nombre if producto.categoria else "",
        "stock_disponible": producto.stock_operativo_total,
        "stock_mostrar_en_web": bool(producto.stock_mostrar_en_web),
        "imagen_url": producto.imagen_url or "",
        "canjeable_con_puntos": bool(producto.canjeable_con_puntos),
        "puntos_para_canje": producto.puntos_para_canje,
        "badges": producto.badge_info,
    }
    if incluir_diagnostico:
        payload.update({
            "activo": bool(producto.activo),
            "visible_ahora": bool(producto.visible_ahora),
            "disponible_para_venta": bool(producto.disponible_para_venta()),
            "vendible_bot": bool(disponible),
            "motivos_no_disponible": _motivos_no_disponible(producto),
            "hora_inicio_visibilidad": producto.hora_inicio_visibilidad.isoformat() if producto.hora_inicio_visibilidad else None,
            "hora_fin_visibilidad": producto.hora_fin_visibilidad.isoformat() if producto.hora_fin_visibilidad else None,
            "dias_semana_json": producto.dias_semana_json,
            "alergenos": producto.get_alergenos() if hasattr(producto, "get_alergenos") else [],
            "es_hipoalergenico": bool(producto.es_hipoalergenico),
        })
    return payload


def _pedido_bot_payload(pedido):
    # Identifica el responsable operativo sin exponer teléfonos privados.
    from services import _coalesce_proveedor_id, _snapshot_producto_item
    from models import Proveedor as _Prov
    proveedor_ids = set()
    for item in pedido.items:
        snapshot = _snapshot_producto_item(item)
        pid = _coalesce_proveedor_id(snapshot, item)
        proveedor_ids.add(pid)  # incluye None si hay items propios
    bar_contacto = None
    nombre_general = SiteConfig.get("NOMBRE_NEGOCIO", "Mi tienda")

    def _contacto_general():
        return {
            "tipo": "propio",
            "nombre": nombre_general,
        }

    if proveedor_ids == {None} or None in proveedor_ids:
        bar_contacto = _contacto_general()
    elif len(proveedor_ids) == 1:
        from models import ProveedorProducto as _ProvProd
        prov = db.session.get(_Prov, next(iter(proveedor_ids)))
        # El contacto se resuelve mediante User.proveedor_id en el endpoint de
        # handoff; Proveedor.telefono nunca se entrega al cliente.
        bar_activo = bool(prov and prov.activo)
        if bar_activo:
            tiene_skus = _ProvProd.query.filter_by(
                proveedor_id=prov.id, activo=True
            ).first() is not None
            bar_activo = bar_activo and tiene_skus
        if bar_activo:
            bar_contacto = {
                "tipo": "bar",
                "id": prov.id,
                "nombre": prov.nombre,
            }
        else:
            bar_contacto = _contacto_general()

    return {
        "id": pedido.id,
        "numero": pedido.numero_pedido,
        "estado": pedido.estado,
        "estado_label": {
            "pendiente": "Recibido",
            "armando": "En preparacion",
            "listo": "Listo",
            "en_ruta": "En camino",
            "entregado": "Entregado",
            "cancelado": "Cancelado",
        }.get(pedido.estado, pedido.estado),
        "total": float(pedido.total),
        "metodo_pago": pedido.metodo_pago,
        "pago_confirmado": bool(pedido.pago_confirmado),
        "codigo_confirmacion": None,
        "repartidor_id": pedido.repartidor_id,
        "en_punto_encuentro": bool(getattr(pedido, "en_punto_encuentro", False)),
        "creado_en": pedido.creado_en.isoformat() if pedido.creado_en else None,
        "entregado_en": pedido.entregado_en.isoformat() if pedido.entregado_en else None,
        "mensaje_cliente": mensaje_estado_pedido(pedido),
        "bar_contacto": bar_contacto,
        "items": [
            {
                "nombre": oi.producto.nombre if oi.producto else f"Producto #{oi.producto_id}",
                "cantidad": oi.cantidad,
                "precio_unit": float(oi.precio_unit),
                "subtotal": float(oi.subtotal),
                "notas": oi.notas or "",
                "metadata": oi.get_metadata(),
            }
            for oi in pedido.items
        ],
    }


# ─── CATÁLOGO ────────────────────────────────

@api_bot_bp.route("/catalogo")
@bot_required
def catalogo():
    try:
        nombre_negocio = SiteConfig.get("NOMBRE_NEGOCIO", "Mi tienda")
        categorias = Categoria.query.filter_by(activo=True).all()
        productos = _catalogo_unificado_para_bot()
        return jsonify({
            "ok": True,
            "negocio": nombre_negocio,
            "categorias": [
                {"id": c.id, "nombre": c.nombre}
                for c in categorias
            ],
            "productos": [
                _producto_catalogo_payload(p)
                for p in productos
                if _producto_disponible_para_bot(p)
            ],
        })
    except Exception as e:
        current_app.logger.error(f"api_bot.catalogo: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/producto/<int:producto_id>")
@bot_required
def detalle_producto(producto_id):
    try:
        p = get_or_404(Product, producto_id)
        if not _producto_disponible_para_bot(p):
            return jsonify({"ok": False, "error": "Producto no disponible"}), 404
        return jsonify({
            "ok": True,
            "producto": _producto_catalogo_payload(p)
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/catalogo/simulador")
@bot_required
def catalogo_simulador():
    """
    Catalogo interno para pruebas de matriz: incluye productos vendibles y bloqueados,
    motivos de bloqueo, dependencias de combos, zonas y configuracion relevante.
    """
    try:
        productos = Product.query.order_by(Product.id).all()
        categorias = Categoria.query.order_by(Categoria.id).all()
        zonas = ZonaEntrega.query.order_by(ZonaEntrega.orden, ZonaEntrega.id).all()
        site_keys = [
            "NOMBRE_NEGOCIO", "BOT_API_URL",
            "TIENDA_URL", "OXIDIAN_PUBLIC_URL", "PUNTOS_POR_EURO",
            "PUNTOS_CANJE_RATIO",
        ]
        return jsonify({
            "ok": True,
            "total_productos": len(productos),
            "total_vendibles_bot": sum(1 for p in productos if _producto_disponible_para_bot(p)),
            "categorias": [
                {"id": c.id, "nombre": c.nombre, "activo": bool(c.activo)}
                for c in categorias
            ],
            "zonas": [
                {
                    "id": z.id,
                    "nombre": z.nombre,
                    "activo": bool(z.activo),
                    "precio_envio": float(z.precio_envio),
                    "gratis_desde": float(z.gratis_desde) if z.gratis_desde is not None else None,
                    "tiempo_estimado_min": z.tiempo_estimado_min,
                    "es_epicentro": bool(z.es_epicentro),
                    "orden": z.orden,
                }
                for z in zonas
            ],
            "site_config": {key: SiteConfig.get(key, "") for key in site_keys},
            "productos": [
                _producto_catalogo_payload(p, incluir_diagnostico=True)
                for p in productos
            ],
        })
    except Exception as e:
        current_app.logger.error(f"api_bot.catalogo_simulador: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── ZONAS ───────────────────────────────────

@api_bot_bp.route("/zonas")
@bot_required
def zonas():
    try:
        zonas = ZonaEntrega.query.filter_by(activo=True)\
                                 .order_by(ZonaEntrega.orden).all()
        return jsonify({
            "ok": True,
            "zonas": [
                {
                    "id": z.id,
                    "nombre": z.nombre,
                    "precio_envio": float(z.precio_envio),
                    "tiempo_estimado_min": z.tiempo_estimado_min,
                    "gratis_desde": float(z.gratis_desde) if z.gratis_desde is not None else None,
                    "es_epicentro": bool(z.es_epicentro),
                }
                for z in zonas
            ]
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── CLIENTES ────────────────────────────────

@api_bot_bp.route("/cliente")
@bot_required
def buscar_cliente():
    try:
        cliente, telefono = _cliente_por_telefono(request.args.get("telefono", ""))
        if not telefono_valido(telefono):
            return jsonify({"ok": False, "error": "Parámetro telefono requerido"}), 400
        if cliente:
            return jsonify({
                "ok": True,
                "existe": True,
                "cliente": {"id": cliente.id, "nombre": cliente.nombre, "puntos": cliente.puntos}
            })
        return jsonify({"ok": True, "existe": False})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/cliente/registrar", methods=["POST"])
@bot_required
def registrar_cliente():
    try:
        data = request.json or {}
        nombre = data.get("nombre", "").strip()
        existente, telefono = _cliente_por_telefono(data.get("telefono", ""))
        if not nombre or not telefono_valido(telefono):
            return jsonify({"ok": False, "error": "nombre y telefono requeridos"}), 400
        if existente:
            return jsonify({
                "ok": True,
                "cliente_id": existente.id,
                "cliente": {"id": existente.id, "nombre": existente.nombre, "puntos": existente.puntos}
            })
        _bot_dom = SiteConfig.get("BOT_EMAIL_DOMAIN", "wa.internal")
        email = f"wa.{telefono}@{_bot_dom}"
        if User.query.filter_by(email=email).first():
            email = f"wa.{telefono}.{uuid.uuid4().hex[:6]}@{_bot_dom}"
        cliente = User(
            nombre=nombre,
            email=email,
            telefono=telefono,
            rol="cliente",
            activo=True,
        )
        cliente.set_password(str(uuid.uuid4()))
        db.session.add(cliente)
        db.session.commit()
        return jsonify({
            "ok": True,
            "cliente_id": cliente.id,
            "cliente": {"id": cliente.id, "nombre": cliente.nombre, "puntos": 0}
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── PUNTOS ──────────────────────────────────

@api_bot_bp.route("/puntos")
@bot_required
def consultar_puntos():
    try:
        cliente, _telefono = _cliente_por_telefono(request.args.get("telefono", ""))
        if not cliente:
            return jsonify({
                "ok": True,
                "existe": False,
                "puntos": 0,
                "valor_euro": 0,
            })
        ratio = get_puntos_config()["ratio"]
        return jsonify({
            "ok": True,
            "existe": True,
            "puntos": cliente.puntos,
            "valor_euro": round(cliente.puntos / ratio, 2),
            "ratio": ratio,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── VALIDAR CUPÓN ───────────────────────────

@api_bot_bp.route("/validar-cupon", methods=["POST"])
@bot_required
def validar_cupon():
    try:
        data = request.json or {}
        codigo = data.get("codigo", "").strip().upper()
        subtotal = float(data.get("subtotal", 0))

        # Intentar Coupon primero
        cupon = Coupon.query.filter_by(codigo=codigo).first()
        if cupon:
            try:
                descuento = cupon.calcular_descuento(subtotal)
                return jsonify({
                    "ok": True, "tipo": "cupon",
                    "descuento": descuento,
                    "descripcion": cupon.descripcion or "",
                    "msg": "OK"
                })
            except ValueError as e:
                return jsonify({"ok": False, "msg": str(e)}), 422

        # Intentar AffiliateCode
        af = AffiliateCode.query.filter_by(codigo=codigo).first()
        if af:
            try:
                descuento = af.calcular_descuento(subtotal)
                return jsonify({
                    "ok": True, "tipo": "afiliado",
                    "descuento": descuento,
                    "descripcion": af.descripcion or "",
                    "msg": "OK"
                })
            except ValueError as e:
                return jsonify({"ok": False, "msg": str(e)}), 422

        return jsonify({"ok": False, "msg": "Código no encontrado"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── CREAR PEDIDO ────────────────────────────
# Compatibilidad interna: desactivado por defecto para clientes de WhatsApp.

@api_bot_bp.route("/pedido/crear", methods=["POST"])
@bot_required
def crear_pedido():
    try:
        if not _bot_order_create_enabled():
            return jsonify({
                "ok": False,
                "code": "BOT_ORDER_CREATE_DISABLED",
                "error": "La creación de pedidos por chatbot está desactivada. Usa la tienda online.",
            }), 403
        features = get_store_features()
        if not features["delivery"]:
            return jsonify({
                "ok": False,
                "code": "DELIVERY_DISABLED",
                "error": "El delivery está desactivado para esta tienda.",
            }), 403

        # ── Idempotency guard ────────────────────────────────────
        # El bot DEBE enviar Idempotency-Key (UUID por intento). Si no la envía,
        # caemos a una key automática que agrupa POSTs idénticos del mismo bot
        # en una ventana corta — pero el bot debería siempre enviarla.
        idem_key = request_idempotency_key("bot", auto_seed=request.remote_addr or "bot")
        body_h = request_body_hash()
        prev = IdempotencyKey.query.filter_by(scope="bot", key=idem_key).first()
        if prev:
            if prev.request_hash != body_h:
                return jsonify({
                    "ok": False,
                    "error": "Idempotency-Key reutilizada con un body distinto",
                }), 409
            try:
                cached = json.loads(prev.response_body or "{}")
            except (json.JSONDecodeError, TypeError):
                cached = {}
            return jsonify(cached), prev.response_status

        data = request.get_json(silent=True)
        if not data:
            return jsonify({"ok": False, "error": "JSON body requerido"}), 400

        cliente, telefono = _cliente_por_telefono(
            data.get("telefono_cliente") or data.get("telefono")
        )
        items_data = data.get("items")
        metodo_pago = normalizar_metodo_pago(data.get("metodo_pago"))
        direccion = (data.get("direccion_entrega") or "").strip()
        zona_id = data.get("zona_id")
        notas = (data.get("notas") or "").strip()
        cupon_codigo = (data.get("cupon_codigo") or "").strip().upper()
        producto_canje_id = data.get("producto_canje_id")
        try:
            puntos_solicitados = int(data.get("puntos_usar", 0) or 0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "puntos_usar inválido"}), 400
        if puntos_solicitados > 0 or producto_canje_id:
            return jsonify({
                "ok": False,
                "code": "POINTS_REQUIRE_WEB_VERIFICATION",
                "error": "Los canjes con puntos requieren verificación por WhatsApp en el carrito web.",
            }), 403

        if not telefono_valido(telefono):
            return jsonify({"ok": False, "error": "telefono_cliente requerido"}), 400
        if not direccion:
            return jsonify({"ok": False, "error": "direccion_entrega requerida"}), 400
        if not isinstance(items_data, list) or not items_data:
            return jsonify({"ok": False, "error": "items debe ser una lista no vacía"}), 400

        if not cliente:
            # Auto-crear cliente identificado por teléfono (misma lógica que web checkout)
            nombre = (data.get("nombre_cliente") or f"WA {telefono[-4:]}").strip()[:100]
            _bot_dom = SiteConfig.get("BOT_EMAIL_DOMAIN", "wa.internal")
            email = f"wa.{telefono}@{_bot_dom}"
            existing_email = User.query.filter_by(email=email).first()
            if existing_email:
                email = f"wa.{telefono}.{uuid.uuid4().hex[:4]}@{_bot_dom}"
            cliente = User(
                nombre=nombre,
                email=email,
                rol="cliente",
                telefono=telefono,
                activo=True,
            )
            cliente.set_password(uuid.uuid4().hex)
            db.session.add(cliente)
            try:
                db.session.flush()
            except Exception as exc:
                db.session.rollback()
                return jsonify({"ok": False, "error": f"Error al crear cliente: {exc}"}), 500

        # ── Procesar items usando precio_final (respeta promociones de producto) ──
        items_procesados = []
        subtotal = 0.0
        for item_d in items_data:
            try:
                pid = int(item_d.get("producto_id", 0))
                cantidad = int(item_d.get("cantidad", 1))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "Formato de ítem inválido"}), 400
            if cantidad <= 0:
                continue
            p = db.session.get(Product, pid)
            if not p or not p.activo:
                return jsonify({"ok": False, "error": f"Producto {pid} no disponible"}), 400
            if "delivery" not in _product_fulfillment_modes(p):
                return jsonify({
                    "ok": False,
                    "code": "PRODUCT_NOT_FOR_DELIVERY",
                    "error": f"'{p.nombre}' solo está disponible para recogida.",
                }), 400
            if _delivery_family(p) == "programado" and not features["pedidos_programados"]:
                return jsonify({
                    "ok": False,
                    "code": "SCHEDULED_ORDERS_DISABLED",
                    "error": "Los pedidos por fecha están desactivados.",
                }), 403
            if p.tipo_entrega == "inmediato" and not p.disponible_para_venta(cantidad):
                return jsonify({"ok": False, "error": f"Stock insuficiente para '{p.nombre}'"}), 400
            if p.tipo_entrega in ("programado", "encargo"):
                fecha_str = item_d.get("fecha_entrega") or data.get("fecha_entrega")
                if not fecha_str:
                    return jsonify({"ok": False, "error": "fecha_entrega requerida"}), 400
                try:
                    fecha_ent = datetime.fromisoformat(str(fecha_str)).date()
                    dias_min = p.dias_anticipacion_encargo or 1
                    dias_hasta = (fecha_ent - date.today()).days
                    if dias_hasta < dias_min:
                        fecha_min = (date.today() + timedelta(days=dias_min)).isoformat()
                        return jsonify({
                            "ok": False,
                            "error": (
                                f"'{p.nombre}' requiere {dias_min} día(s) de anticipación. "
                                f"Fecha mínima: {fecha_min}"
                            ),
                        }), 400
                except (ValueError, TypeError):
                    return jsonify({"ok": False, "error": "fecha_entrega inválida (usa ISO 8601: YYYY-MM-DD)"}), 400
            combo_item_ids = item_d.get("combo_item_ids") or []
            if p.es_combo:
                try:
                    p.validar_stock_combo_seleccion(cantidad, combo_item_ids)
                except ValueError as exc:
                    return jsonify({"ok": False, "error": str(exc)}), 400
            precio_unit = (
                float(p.precio_combo_para_seleccion(combo_item_ids))
                if p.es_combo else float(p.precio_final)
            )
            item_total = round(precio_unit * cantidad, 2)
            subtotal += item_total
            items_procesados.append({"producto": p, "cantidad": cantidad, "subtotal": item_total,
                                     "precio_unit": precio_unit,
                                     "combo_item_ids": combo_item_ids,
                                     "fecha_entrega": fecha_ent.isoformat() if p.tipo_entrega in ("programado", "encargo") else None})

        if not items_procesados:
            return jsonify({"ok": False, "error": "No hay ítems válidos en el pedido"}), 400

        familias_entrega = {_delivery_family(item["producto"]) for item in items_procesados}
        if len(familias_entrega) > 1:
            return jsonify({
                "ok": False,
                "code": "MIXED_DELIVERY_TYPES",
                "error": (
                    "No mezcles delivery inmediato con productos de fecha fija en el mismo pedido. "
                    "Crea un pedido para cada flujo."
                ),
            }), 400
        grupos_pedido = {_order_group(item["producto"]) for item in items_procesados}
        if len(grupos_pedido) > 1:
            return jsonify({
                "ok": False,
                "code": "MIXED_ORDER_GROUPS",
                "error": "Los productos seleccionados requieren pedidos separados.",
            }), 400
        origenes = {
            item["producto"].origen_operativo_key
            for item in items_procesados
        }
        if len(origenes) > 1:
            return jsonify({
                "ok": False,
                "code": "MIXED_FULFILLMENT_ORIGINS",
                "error": (
                    "Cada pedido debe salir completo de un solo establecimiento. "
                    "Crea un pedido independiente para cada origen."
                ),
            }), 400

        if producto_canje_id:
            try:
                producto_canje_id = int(producto_canje_id)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "producto_canje_id inválido"}), 400
            producto_canje = db.session.get(Product, producto_canje_id)
            if not producto_canje or not producto_canje.canje_directo_disponible():
                return jsonify({"ok": False, "error": "Producto no válido para canje"}), 400
            if producto_canje.puntos_para_canje > cliente.puntos:
                return jsonify({
                    "ok": False,
                    "error": f"Puntos insuficientes. Necesitas {producto_canje.puntos_para_canje}",
                }), 400

        geo = validar_radio_entrega(direccion)
        if not geo.get("ok"):
            return jsonify({
                "ok": False,
                "error": geo.get("mensaje") or "Dirección fuera de cobertura",
                "distancia_km": geo.get("distancia_km"),
            }), 422

        # ── Resolver cupón / afiliado (no registrar uso todavía) ──
        cupon_obj = None
        afiliado_obj = None
        if cupon_codigo:
            cupon_obj = Coupon.query.filter_by(codigo=cupon_codigo).first()
            if cupon_obj:
                ok_c, msg_c = cupon_obj.es_valido()
                if not ok_c:
                    return jsonify({"ok": False, "error": msg_c}), 400
            else:
                afiliado_obj = AffiliateCode.query.filter_by(codigo=cupon_codigo).first()
                if afiliado_obj:
                    ok_a, msg_a = afiliado_obj.es_valido()
                    if not ok_a:
                        return jsonify({"ok": False, "error": msg_a}), 400
                else:
                    return jsonify({"ok": False, "error": "Código no válido"}), 400

        # ── Zona de entrega — auto-selecciona la primera si el bot no envía zona_id ──
        zona = None
        es_entrega_epicentro = True
        tiempo_estimado = 30
        if zona_id:
            zona = db.session.get(ZonaEntrega, zona_id)
            if not zona or not zona.activo:
                zona = None
        if zona is None:
            zona = ZonaEntrega.query.filter_by(activo=True)\
                .order_by(ZonaEntrega.orden, ZonaEntrega.nombre).first()
        if zona is None:
            return jsonify({"ok": False, "error": "No hay zonas de entrega disponibles"}), 422
        if zona:
            es_entrega_epicentro = bool(zona.es_epicentro)
            tiempo_estimado = zona.tiempo_estimado_min

        # ── Motor de pricing unificado (mismas reglas que web) ──
        cliente = bloquear_cliente_puntos(cliente)
        puntos_cfg = get_puntos_config()
        ratio = puntos_cfg["ratio"]
        puntos_por_euro = puntos_cfg["por_euro"]
        puntos_usar = min(max(0, int(data.get("puntos_usar", 0))), int(cliente.puntos or 0))

        try:
            precio = calcular_precio(
                items_procesados, subtotal,
                cupon=cupon_obj,
                afiliado=afiliado_obj,
                puntos_usar=puntos_usar,
                zona=zona,
                ratio_puntos=ratio,
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        total = precio.total
        costo_envio = precio.costo_envio
        puntos_a_canjear = precio.puntos_usados
        puntos_ganados = int(total * puntos_por_euro)
        service_fee = get_service_commission(total)

        # Registrar uso del cupón (incluye envio_gratis donde descuento_cupon puede ser 0)
        if cupon_obj:
            try:
                cupon_obj.registrar_uso()
            except ValueError as e:
                return jsonify({"ok": False, "error": str(e)}), 400

        # ── Crear pedido ──
        pedido = Order(
            numero_pedido=Order.generar_numero("whatsapp"),
            cliente_id=cliente.id,
            estado="pendiente",
            origen="whatsapp",
            subtotal=subtotal,
            descuento=precio.descuento_total,
            total=total,
            service_commission_pct=service_fee["pct"],
            service_commission_amount=service_fee["amount"],
            merchant_net_amount=service_fee["merchant_net"],
            cupon_id=cupon_obj.id if cupon_obj else None,
            puntos_usados=0,
            puntos_ganados=puntos_ganados,
            metodo_pago=metodo_pago,
            direccion_entrega=direccion,
            notas=notas,
            zona_id=zona.id if zona else None,
            afiliado_codigo_id=afiliado_obj.id if afiliado_obj else None,
            es_entrega_epicentro=es_entrega_epicentro,
        )
        db.session.add(pedido)
        db.session.flush()
        registrar_pedido_creado(
            pedido,
            canal="bot",
            detalle="pedido creado por API bot compat",
            metadata={"telefono": telefono, "zona_id": zona.id if zona else None},
        )

        for item in items_procesados:
            item_notas = None
            item_metadata = None
            if item["producto"].es_combo:
                item_notas, item_metadata = _combo_order_payload(
                    item["producto"],
                    item.get("combo_item_ids") or [],
                )
            item_metadata = dict(item_metadata or {})
            if item.get("fecha_entrega"):
                item_metadata["entrega_programada"] = item["fecha_entrega"]
            oi = OrderItem(
                pedido_id=pedido.id,
                producto_id=item["producto"].id,
                cantidad=item["cantidad"],
                precio_unit=item["precio_unit"],
                subtotal=item["subtotal"],
                notas=item_notas,
                metadata_json=json.dumps(
                    metadata_item_pedido(item["producto"], item_metadata or {}),
                    ensure_ascii=False,
                ),
            )
            db.session.add(oi)
            if item["producto"].tipo_entrega == "inmediato":
                try:
                    if item["producto"].es_combo:
                        item["producto"].descontar_stock_combo(item["cantidad"], item.get("combo_item_ids") or [])
                    else:
                        item["producto"].descontar_stock(item["cantidad"])
                except ValueError as stock_exc:
                    # Mensaje específico al cliente del bot, no genérico 500.
                    db.session.rollback()
                    return jsonify({
                        "ok": False,
                        "error": str(stock_exc),
                        "code": "STOCK_INSUFICIENTE",
                    }), 409

        # ── Puntos: vía loyalty_service (único punto de deducción) ──
        # El canje se aplica ahora; los puntos GANADOS se otorgan al entregar (repartidor.confirmar_entrega)
        aplicar_canje_en_pedido(
            cliente,
            pedido,
            puntos_usar=puntos_a_canjear,
            producto_canje_id=producto_canje_id,
        )
        db.session.flush()
        from services import sincronizar_proveedores_pedido
        sincronizar_proveedores_pedido(pedido)
        db.session.flush()
        encolar_notificaciones_proveedores_pedido(pedido)

        distribuir_pedido(pedido)

        if afiliado_obj and precio.descuento_afiliado > 0:
            registrar_uso_afiliado(afiliado_obj, pedido, cliente, precio.descuento_afiliado)

        enviado_wa = enviar_whatsapp_estado(pedido)

        respuesta_payload = {
            "ok": True,
            "pedido_id": pedido.id,
            "numero_pedido": pedido.numero_pedido,
            "total": float(total),
            "subtotal": float(subtotal),
            "descuento": float(precio.descuento_total),
            "costo_envio": float(costo_envio),
            "puntos_ganados": puntos_ganados,
            "estado": pedido.estado,
            "tiempo_estimado_min": tiempo_estimado,
            "mensaje_cliente": mensaje_estado_pedido(pedido),
            "confirmacion_whatsapp_enviada": bool(enviado_wa),
        }
        db.session.add(IdempotencyKey(
            scope="bot",
            key=idem_key,
            request_hash=body_h,
            response_status=200,
            response_body=json.dumps(respuesta_payload, default=str),
            order_id=pedido.id,
            user_id=cliente.id if cliente else None,
            expira_en=_utcnow() + IDEMPOTENCY_TTL,
        ))
        db.session.commit()
        try:
            from push_service import notify_new_order
            notify_new_order(pedido)
        except Exception:
            current_app.logger.exception("No se pudo enviar push de nuevo pedido bot %s", pedido.id)

        return jsonify(respuesta_payload)

    except Exception as e:
        db.session.rollback()
        current_app.logger.error("api_bot.crear_pedido: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "Error interno al crear el pedido"}), 500


# ─── ESTADO PEDIDO ───────────────────────────

def _normalizar_tel_match(telefono_raw):
    """Devuelve dos formas comparables: dígitos puros y +prefijo+dígitos."""
    if not telefono_raw:
        return "", ""
    raw = str(telefono_raw).strip()
    digits = "".join(c for c in raw if c.isdigit())
    plus = "+" + digits if digits else ""
    return digits, plus


def _operador_bar_por_telefono(_telefono_raw):
    """LEGACY desactivado: no existe rol proveedor/bar en el flujo vigente."""
    return None, None


ESTADOS_PEDIDO_ACTIVO_HANDOFF = ("pendiente", "armando", "listo", "en_ruta")


def _telefonos_usuarios_handoff(query):
    telefonos = []
    vistos = set()
    for usuario in query.order_by(User.id.asc()).all():
        telefono = normalizar_telefono_cliente(usuario.telefono_normalizado or usuario.telefono)
        if telefono and telefono_valido(telefono) and telefono not in vistos:
            vistos.add(telefono)
            telefonos.append("".join(c for c in telefono if c.isdigit()))
    return telefonos


def _proveedor_congelado_pedido(pedido):
    """Devuelve el proveedor solo si todo el pedido pertenece al mismo tercero."""
    from services import _coalesce_proveedor_id, _snapshot_producto_item

    proveedor_ids = {
        _coalesce_proveedor_id(_snapshot_producto_item(item), item)
        for item in pedido.items
    }
    proveedor_ids.discard(None)
    if len(proveedor_ids) != 1:
        return None

    proveedor_id = next(iter(proveedor_ids))
    # Si alguna línea es propia, el soporte corresponde al equipo global.
    if any(
        _coalesce_proveedor_id(_snapshot_producto_item(item), item) is None
        for item in pedido.items
    ):
        return None
    return proveedor_id


def _destino_handoff_cliente(telefono_raw):
    cliente, telefono = _cliente_por_telefono(telefono_raw)
    pedido = None
    if cliente:
        pedido = (
            Order.query
            .filter(
                Order.cliente_id == cliente.id,
                Order.estado.in_(ESTADOS_PEDIDO_ACTIVO_HANDOFF),
            )
            .order_by(Order.creado_en.desc(), Order.id.desc())
            .first()
        )

    agentes = _telefonos_usuarios_handoff(
        User.query.filter_by(rol="super_admin", activo=True)
    )
    return {
        "scope": "global",
        "provider_id": None,
        "order_id": pedido.id if pedido else None,
        "order_number": pedido.numero_pedido if pedido else None,
        "agents": agentes,
        "phone": telefono,
    }


@api_bot_bp.route("/handoff/destination")
@bot_required
def handoff_destination():
    """Resuelve agentes internos sin exponer teléfonos al cliente."""
    telefono = (request.args.get("telefono") or "").strip()
    if not telefono:
        return jsonify({"ok": False, "error": "telefono requerido"}), 400
    destino = _destino_handoff_cliente(telefono)
    destino.pop("phone", None)
    return jsonify({"ok": True, **destino})


@api_bot_bp.route("/bar/identify")
@bot_required
def identify_bar():
    """El menú bar/proveedor del bot queda desactivado en tienda única."""
    return jsonify({"ok": True, "es_bar": False, "bar": None})


@api_bot_bp.route("/bar/pedidos")
@bot_required
def bar_pedidos_activos():
    """Pedidos pendientes del bar identificado por su teléfono operador."""
    operador, bar = _operador_bar_por_telefono(request.args.get("telefono") or "")
    if not request.args.get("telefono"):
        return jsonify({"ok": False, "error": "telefono requerido"}), 400
    if not bar:
        return jsonify({"ok": False, "error": "No eres operador de ningún bar"}), 403

    estados_q = (request.args.get("estados") or "pendiente,armando").strip()
    estados = [e.strip() for e in estados_q.split(",") if e.strip()]
    pedidos = (
        Order.query
        .join(OrderProviderStatus, OrderProviderStatus.pedido_id == Order.id)
        .filter(OrderProviderStatus.proveedor_id == bar.id)
        .filter(Order.estado.in_(estados))
        .order_by(Order.creado_en.asc())
        .limit(20)
        .all()
    )
    return jsonify({
        "ok": True,
        "bar": {"id": bar.id, "nombre": bar.nombre},
        "pedidos": [{
            "id": p.id,
            "numero": p.numero_pedido,
            "estado": p.estado,
            "total": float(p.total or 0),
            "items": [{
                "nombre": linea["nombre"],
                "cantidad": linea["cantidad"],
                "componentes": linea["componentes"],
            } for linea in lineas_proveedor_pedido(p, bar.id)],
        } for p in pedidos],
    })


@api_bot_bp.route("/bar/sku-list")
@bot_required
def bar_sku_list():
    """Inventario operativo del bar identificado por su operador WhatsApp."""
    telefono = request.args.get("telefono") or ""
    operador, bar = _operador_bar_por_telefono(telefono)
    if not telefono:
        return jsonify({"ok": False, "error": "telefono requerido"}), 400
    if not bar:
        return jsonify({"ok": False, "error": "No autorizado"}), 403

    filas = (
        ProveedorProducto.query
        .join(Product, Product.id == ProveedorProducto.producto_id)
        .filter(ProveedorProducto.proveedor_id == bar.id)
        .order_by(Product.nombre.asc(), ProveedorProducto.id.asc())
        .limit(200)
        .all()
    )
    return jsonify({
        "ok": True,
        "bar": {"id": bar.id, "nombre": bar.nombre},
        "items": [{
            "pp_id": fila.id,
            "producto_id": fila.producto_id,
            "nombre": fila.producto.nombre if fila.producto else f"Producto #{fila.producto_id}",
            "precio": float(fila.producto.precio_final if fila.producto else 0),
            "precio_costo": float(fila.precio_costo or 0),
            "stock": int(fila.stock or 0),
            "activo": bool(fila.activo),
            "agotado": (not fila.activo) or int(fila.stock or 0) <= 0,
        } for fila in filas],
    })


@api_bot_bp.route("/bar/estado-tienda", methods=["POST"])
@bot_required
def bar_estado_tienda():
    """Permite al operador activar/desactivar temporalmente su sección."""
    data = request.get_json(silent=True) or {}
    telefono = data.get("telefono") or request.args.get("telefono") or ""
    operador, bar = _operador_bar_por_telefono(telefono)
    if not telefono:
        return jsonify({"ok": False, "error": "telefono requerido"}), 400
    if not bar:
        return jsonify({"ok": False, "error": "No autorizado"}), 403

    abierta = data.get("abierta", None)
    if abierta is not None:
        bar.activo = _json_bool(abierta)
        db.session.commit()
    return jsonify({
        "ok": True,
        "bar": {"id": bar.id, "nombre": bar.nombre},
        "abierta": bool(bar.activo and bar.esta_abierto_ahora),
        "activo": bool(bar.activo),
        "modo": "manual" if abierta is not None else "auto",
    })


@api_bot_bp.route("/bar/producto/<int:pp_id>/agotado", methods=["POST"])
@bot_required
def bar_producto_agotado(pp_id):
    """Marca un SKU del bar como agotado/disponible sin tocar stock propio."""
    data = request.get_json(silent=True) or {}
    telefono = data.get("telefono") or request.args.get("telefono") or ""
    operador, bar = _operador_bar_por_telefono(telefono)
    if not telefono:
        return jsonify({"ok": False, "error": "telefono requerido"}), 400
    if not bar:
        return jsonify({"ok": False, "error": "No autorizado"}), 403

    fila = ProveedorProducto.query.filter_by(id=pp_id, proveedor_id=bar.id).with_for_update().first()
    if not fila:
        return jsonify({"ok": False, "error": "SKU no pertenece a tu bar"}), 404

    agotado = _json_bool(data.get("agotado", True))
    stock_raw = data.get("stock", None)
    stock_nuevo = None
    if stock_raw is not None:
        try:
            stock_nuevo = max(0, int(stock_raw))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "stock inválido"}), 400

    if agotado:
        fila.stock = 0
        fila.activo = False
    else:
        fila.activo = True
        fila.stock = stock_nuevo if stock_nuevo is not None else max(1, int(fila.stock or 0))
    db.session.commit()
    return jsonify({
        "ok": True,
        "producto": {
            "pp_id": fila.id,
            "producto_id": fila.producto_id,
            "nombre": fila.producto.nombre if fila.producto else f"Producto #{fila.producto_id}",
            "stock": int(fila.stock or 0),
            "activo": bool(fila.activo),
            "agotado": (not fila.activo) or int(fila.stock or 0) <= 0,
        },
    })


@api_bot_bp.route("/bar/producto/<int:pp_id>/precio", methods=["POST"])
@bot_required
def bar_producto_precio(pp_id):
    """El modelo actual no tiene precio de venta por bar; no mutamos Product global."""
    data = request.get_json(silent=True) or {}
    telefono = data.get("telefono") or request.args.get("telefono") or ""
    operador, bar = _operador_bar_por_telefono(telefono)
    if not telefono:
        return jsonify({"ok": False, "error": "telefono requerido"}), 400
    if not bar:
        return jsonify({"ok": False, "error": "No autorizado"}), 403
    fila = ProveedorProducto.query.filter_by(id=pp_id, proveedor_id=bar.id).first()
    if not fila:
        return jsonify({"ok": False, "error": "SKU no pertenece a tu bar"}), 404
    return jsonify({
        "ok": False,
        "code": "PRICE_OVERRIDE_UNSUPPORTED",
        "error": "El precio de venta es global; debe cambiarse desde productos por superadmin.",
    }), 409


@api_bot_bp.route("/bar/pedido/<int:pedido_id>/preparado", methods=["POST"])
@bot_required
def bar_marcar_preparado(pedido_id):
    """El operador del bar marca un pedido como preparado desde WhatsApp."""
    data = request.get_json(silent=True) or {}
    telefono = data.get("telefono") or request.args.get("telefono") or ""
    operador, bar = _operador_bar_por_telefono(telefono)
    if not telefono:
        return jsonify({"ok": False, "error": "telefono requerido"}), 400
    if not bar:
        return jsonify({"ok": False, "error": "No autorizado"}), 403

    pedido = get_or_404(Order, pedido_id)
    if pedido.estado not in ("pendiente", "armando", "listo"):
        return jsonify({"ok": False, "error": "El pedido ya está cerrado"}), 409
    estado = OrderProviderStatus.query.filter_by(
        pedido_id=pedido.id, proveedor_id=bar.id
    ).with_for_update().first()
    if not estado:
        return jsonify({"ok": False, "error": "Este pedido no es de tu bar"}), 404
    if estado.preparado:
        return jsonify({"ok": True, "ya_preparado": True, "numero": pedido.numero_pedido})

    from services import (
        registrar_evento_pedido, es_pedido_solo_bar, distribuir_repartidor,
    )
    estado.preparado = True
    estado.preparado_en = _utcnow()
    db.session.flush()
    registrar_evento_pedido(
        pedido,
        "proveedor_preparado",
        actor_id=operador.id,
        estado_anterior=pedido.estado,
        estado_nuevo=pedido.estado,
        canal="bot_bar",
        metadata={"proveedor_ids": [bar.id], "via": "whatsapp_bar"},
    )
    db.session.expire(pedido, ["estados_proveedor"])
    avanzado = False
    if (not pedido.proveedores_pendientes
            and es_pedido_solo_bar(pedido)
            and pedido.estado in ("pendiente", "armando")):
        pedido.estado = "listo"
        try:
            distribuir_repartidor(pedido)
        except Exception:
            pass
        avanzado = True
    db.session.commit()
    return jsonify({
        "ok": True,
        "numero": pedido.numero_pedido,
        "avanzado_a_listo": avanzado,
    })


@api_bot_bp.route("/bar/incidencias")
@bot_required
def bar_incidencias():
    """Incidencias abiertas de los pedidos del bar."""
    from models import OrderEvent
    telefono = request.args.get("telefono") or ""
    operador, bar = _operador_bar_por_telefono(telefono)
    if not telefono:
        return jsonify({"ok": False, "error": "telefono requerido"}), 400
    if not bar:
        return jsonify({"ok": False, "error": "No autorizado"}), 403

    eventos = (
        OrderEvent.query
        .filter(OrderEvent.tipo == "cliente_reporto_novedad")
        .join(Order, OrderEvent.pedido_id == Order.id)
        .join(OrderProviderStatus,
              (OrderProviderStatus.pedido_id == Order.id) &
              (OrderProviderStatus.proveedor_id == bar.id))
        .order_by(OrderEvent.creado_en.desc())
        .limit(10)
        .all()
    )
    atendidos = set()
    atendidos_eventos = (
        OrderEvent.query
        .filter(OrderEvent.tipo == "incidencia_atendida")
        .all()
    )
    for e in atendidos_eventos:
        try:
            iid = (e.get_metadata() or {}).get("incidencia_id")
        except Exception:
            iid = None
        if iid:
            atendidos.add(iid)
    return jsonify({
        "ok": True,
        "incidencias": [{
            "id": e.id,
            "atendida": e.id in atendidos,
            "pedido": e.pedido.numero_pedido if e.pedido else None,
            "texto": e.detalle or "",
            "creado_en": e.creado_en.isoformat() if e.creado_en else None,
        } for e in eventos],
    })


@api_bot_bp.route("/pedido/<int:pedido_id>")
@bot_required
def estado_pedido(pedido_id):
    try:
        pedido = get_or_404(Order, pedido_id)
        cliente, _ = _cliente_por_telefono(request.args.get("telefono") or "")
        if not cliente or cliente.id != pedido.cliente_id:
            return jsonify({"ok": False, "error": "No autorizado"}), 403
        return jsonify({"ok": True, "pedido": _pedido_bot_payload(pedido)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/pedido/<int:pedido_id>/incidencia", methods=["POST"])
@bot_required
def reportar_incidencia(pedido_id):
    """Cliente reporta una novedad sobre un pedido desde el chatbot.
    La incidencia queda visible en /proveedor/incidencias si el pedido es del
    bar, o en el panel admin si es propio."""
    try:
        pedido = get_or_404(Order, pedido_id)
        data = request.get_json(silent=True) or {}
        texto = (data.get("texto") or data.get("mensaje") or "").strip()
        if not texto:
            return jsonify({"ok": False, "error": "Texto requerido"}), 400
        if len(texto) > 2000:
            texto = texto[:2000]
        telefono = (data.get("telefono") or "").strip() or None
        cliente, _ = _cliente_por_telefono(telefono or "")
        if not cliente or cliente.id != pedido.cliente_id:
            return jsonify({"ok": False, "error": "No autorizado"}), 403

        from services import registrar_evento_pedido
        registrar_evento_pedido(
            pedido,
            "cliente_reporto_novedad",
            actor_id=pedido.cliente_id,
            estado_anterior=pedido.estado,
            estado_nuevo=pedido.estado,
            canal="cliente_whatsapp",
            detalle=texto[:500],
            metadata={
                "texto_completo": texto,
                "telefono": telefono,
            },
        )
        db.session.commit()
        return jsonify({
            "ok": True,
            "pedido": pedido.numero_pedido,
            "mensaje": "Incidencia registrada — el equipo responsable la recibirá.",
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error("reportar_incidencia: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/pedido/estado")
@bot_required
def estado_pedido_buscar():
    """Busca estado por id o numero_pedido; telefono limita la consulta al cliente."""
    try:
        pedido_id = request.args.get("pedido_id", type=int)
        numero = (request.args.get("numero") or request.args.get("numero_pedido") or "").strip()
        telefono = normalizar_telefono_cliente(request.args.get("telefono"))

        query = Order.query
        if pedido_id:
            query = query.filter(Order.id == pedido_id)
        elif numero:
            query = query.filter(Order.numero_pedido == numero)
        elif telefono:
            cliente, telefono = _cliente_por_telefono(telefono)
            if not cliente:
                return jsonify({"ok": False, "error": "Cliente no encontrado"}), 404
            query = query.filter(Order.cliente_id == cliente.id).order_by(Order.creado_en.desc())
        else:
            return jsonify({"ok": False, "error": "Indica pedido_id, numero_pedido o telefono"}), 400

        if telefono and (pedido_id or numero):
            cliente_ids = [
                row.id for row in User.query.with_entities(User.id)
                .filter_by(telefono_normalizado=telefono, rol="cliente")
                .all()
            ]
            if not cliente_ids:
                return jsonify({"ok": False, "error": "Cliente no encontrado"}), 404
            query = query.filter(Order.cliente_id.in_(cliente_ids))

        pedido = query.first()
        if not pedido:
            return jsonify({"ok": False, "error": "Pedido no encontrado"}), 404
        return jsonify({"ok": True, "pedido": _pedido_bot_payload(pedido)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/pedido/<int:pedido_id>/cancelar", methods=["POST"])
@bot_required
def cancelar_pedido_cliente(pedido_id):
    """Cancela un pedido propio únicamente antes de iniciar preparación."""
    data = request.get_json(silent=True) or {}
    cliente, telefono = _cliente_por_telefono(data.get("telefono"))
    if not cliente or not telefono_valido(telefono):
        return jsonify({"ok": False, "error": "Cliente no encontrado"}), 404

    pedido = Order.query.filter_by(
        id=pedido_id,
        cliente_id=cliente.id,
    ).with_for_update().first()
    if not pedido:
        return jsonify({"ok": False, "error": "Pedido no encontrado"}), 404
    if pedido.estado != "pendiente":
        return jsonify({
            "ok": False,
            "error": "El pedido ya entró en preparación y requiere atención humana.",
            "estado": pedido.estado,
            "requiere_agente": True,
        }), 409
    if pedido.metodo_pago == "bizum" and pedido.pago_confirmado:
        return jsonify({
            "ok": False,
            "error": "El Bizum ya fue confirmado; un agente debe gestionar la devolución.",
            "requiere_agente": True,
        }), 409

    try:
        cancelar_pedido_operativo(
            pedido,
            canal="chatbot",
            detalle="cancelación solicitada por el cliente en WhatsApp",
        )
        enviar_whatsapp_estado(pedido)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Error cancelando pedido %s desde chatbot",
            pedido_id,
        )
        return jsonify({"ok": False, "error": "No se pudo cancelar el pedido"}), 500

    return jsonify({
        "ok": True,
        "pedido": _pedido_bot_payload(pedido),
        "mensaje": f"Pedido {pedido.numero_pedido} cancelado.",
    })


# ─── ENVIAR MENSAJE AL CLIENTE (Oxidian → Bot) ─

@api_bot_bp.route("/message", methods=["POST"])
@bot_required
def enviar_mensaje():
    """
    Oxidian llama este endpoint para que el bot envíe un WhatsApp al cliente.
    Body: { "telefono": "612345678", "mensaje": "Tu pedido está en camino..." }
    El bot de Node.js expone el mismo path en su puerto 3000.
    Este endpoint es el RECEPTOR en el lado de Oxidian (para logs/auditoría).
    También actúa como proxy si se llama desde el propio sistema.
    """
    try:
        data = request.json or {}
        telefono = data.get("telefono", "").strip()
        mensaje   = data.get("mensaje", "").strip()
        if not telefono or not mensaje:
            return jsonify({"ok": False, "error": "telefono y mensaje requeridos"}), 400
        return jsonify({"ok": True, "recibido": True,
                        "telefono": telefono, "chars": len(mensaje)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── BROADCAST (campaña masiva desde Oxidian) ─

@api_bot_bp.route("/broadcast", methods=["POST"])
@bot_required
def broadcast():
    """
    Oxidian envía un mensaje a múltiples teléfonos vía el bot de WhatsApp.
    Body: { "mensajes": [{"telefono":"612..","mensaje":"..."}] }
    El envío se hace en hilo de fondo para no bloquear la respuesta.
    """
    try:
        data = request.json or {}
        mensajes = data.get("mensajes", [])
        if not mensajes:
            return jsonify({"ok": False, "error": "mensajes[] requerido"}), 400

        validos = [
            m for m in mensajes
            if (m.get("telefono") or "").strip() and (m.get("mensaje") or "").strip()
        ]
        if not validos:
            return jsonify({"ok": False, "error": "Ningún mensaje tiene telefono y mensaje válidos"}), 400

        encolados = 0
        for m in validos:
            job = encolar_whatsapp_generico(
                m["telefono"].strip(),
                m["mensaje"].strip(),
                evento="broadcast",
            )
            if job:
                encolados += 1
        db.session.commit()

        return jsonify({"ok": True, "total": encolados, "estado": "encolado"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── CATÁLOGO para BOT con campos nuevos ─────

@api_bot_bp.route("/catalogo/completo")
@bot_required
def catalogo_completo():
    """
    Versión extendida del catálogo que incluye tipo_entrega, horario, promo y combos.
    El bot usa esto para mostrar información precisa al cliente.
    """
    try:
        visibles = _catalogo_unificado_para_bot()
        return jsonify({
            "ok": True,
            "total": len(visibles),
            "productos": [
                {
                    "id":                    p.id,
                    "nombre":                p.nombre,
                    "descripcion":           p.descripcion or "",
                    "precio":                float(p.precio_final),
                    "tipo_entrega":          p.tipo_entrega or "inmediato",
                    "modalidad_entrega":     p.modalidad_entrega or "ambas",
                    "fecha_llegada":         p.fecha_llegada.isoformat() if p.fecha_llegada else None,
                    "categoria":             p.categoria.nombre if p.categoria else "",
                    "stock":                 p.stock_operativo_total,
                    "es_combo":              bool(p.es_combo),
                    "combo_items": [
                        {
                            "combo_item_id":  ci.id,
                            "producto_id":    ci.producto_id,
                            "nombre":         ci.componente.nombre if ci.componente else "",
                            "cantidad":       ci.cantidad,
                            "es_seleccionable": bool(ci.es_seleccionable),
                        }
                        for ci in ComboItem.query.filter_by(combo_id=p.id).all()
                    ] if p.es_combo else [],
                    "canjeable":             bool(p.canjeable_con_puntos),
                    "puntos_canje":          p.puntos_para_canje,
                    "badges":                p.badge_info,
                }
                for p in visibles
            ]
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── PROMOCIONES ACTIVAS ─────────────────────

@api_bot_bp.route("/promociones")
@bot_required
def promociones():
    productos = Product.query.filter(
        Product.activo == True,
        Product.es_combo == True,
        Product.combo_precio_modo == "descuento_porcentaje",
        Product.combo_descuento_pct > 0,
    ).order_by(Product.combo_descuento_pct.desc(), Product.nombre.asc()).all()
    promociones_activas = [
        {
            "id": producto.id,
            "nombre": producto.nombre,
            "descripcion": producto.descripcion or "",
            "precio": float(producto.precio_final),
            "precio_base": float(producto.combo_precio_base or 0),
            "descuento_porcentaje": float(producto.combo_descuento_pct or 0),
            "tipo_entrega": producto.tipo_entrega or "inmediato",
            "modalidad_entrega": producto.modalidad_entrega or "ambas",
            "categoria": producto.categoria.nombre if producto.categoria else "",
        }
        for producto in productos
        if _producto_disponible_para_bot(producto)
    ]
    return jsonify({
        "ok": True,
        "total": len(promociones_activas),
        "promociones": promociones_activas,
    })


# ─── RESEÑA DE PEDIDO (desde bot WhatsApp) ───

@api_bot_bp.route("/pedido/<int:pedido_id>/resena", methods=["POST"])
@bot_required
def guardar_resena(pedido_id):
    try:
        from models import Review
        data = request.json or {}
        calificacion = data.get("calificacion")
        comentario   = (data.get("comentario") or "").strip() or None
        telefono     = (data.get("telefono") or "").strip()

        if calificacion is None or not (1 <= int(calificacion) <= 5):
            return jsonify({"ok": False, "error": "calificacion debe ser 1-5"}), 400

        pedido = get_or_404(Order, pedido_id)

        # Verificar que el pedido fue entregado
        if pedido.estado != "entregado":
            return jsonify({"ok": False, "error": "Solo se pueden reseñar pedidos entregados"}), 400

        # Verificar pertenencia al cliente (si se provee teléfono)
        if telefono:
            from routes.public import _find_cliente_by_phone
            cliente_match, _ = _find_cliente_by_phone(telefono)
            if not cliente_match or pedido.cliente_id != cliente_match.id:
                return jsonify({"ok": False, "error": "Este pedido no pertenece al cliente"}), 403
        pedido.resena_calificacion = int(calificacion)
        pedido.resena_comentario   = comentario

        # Crear/actualizar registro Review para moderación en el panel admin
        review_existente = Review.query.filter_by(pedido_id=pedido_id).first()
        items_list = list(pedido.items)
        if not review_existente and pedido.cliente_id and items_list:
            # Asociar con el producto de mayor subtotal del pedido
            item_principal = max(items_list, key=lambda i: float(i.subtotal or 0))
            nueva_review = Review(
                producto_id  = item_principal.producto_id,
                cliente_id   = pedido.cliente_id,
                pedido_id    = pedido_id,
                calificacion = int(calificacion),
                comentario   = comentario,
                aprobada     = False,  # pendiente de moderación del admin
            )
            db.session.add(nueva_review)
        elif review_existente:
            review_existente.calificacion = int(calificacion)
            review_existente.comentario   = comentario
            review_existente.aprobada     = False  # re-moderar si cambia

        db.session.commit()
        current_app.logger.info(f"Reseña WhatsApp guardada: pedido={pedido_id} rating={calificacion}")
        return jsonify({"ok": True, "pedido_id": pedido_id, "calificacion": int(calificacion)})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"api_bot.guardar_resena: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── HISTORIAL PEDIDOS CLIENTE ───────────────

@api_bot_bp.route("/pedidos")
@bot_required
def pedidos_cliente():
    try:
        cliente, _telefono = _cliente_por_telefono(request.args.get("telefono", ""))
        limit = request.args.get("limit", 5, type=int)
        if not cliente:
            return jsonify({"ok": False, "error": "Cliente no encontrado"}), 404
        estados_raw = (request.args.get("estados") or "").strip()
        estados = [e.strip() for e in estados_raw.split(",") if e.strip()] if estados_raw else None

        q = Order.query.filter_by(cliente_id=cliente.id)
        if estados:
            q = q.filter(Order.estado.in_(estados))
        pedidos = q.order_by(Order.creado_en.desc()).limit(limit).all()

        # Si el caller (bot) pide `con_contacto=1`, devolvemos el payload
        # completo (incluye bar_contacto). Si no, mantenemos el resumen
        # minimal para retrocompatibilidad con otros consumidores.
        completo = request.args.get("con_contacto", "1").strip().lower() not in ("0", "false", "no")
        if completo:
            payload = [_pedido_bot_payload(p) for p in pedidos]
        else:
            payload = [
                {
                    "id": p.id,
                    "numero": p.numero_pedido,
                    "estado": p.estado,
                    "total": float(p.total),
                    "creado_en": p.creado_en.isoformat() if p.creado_en else None,
                }
                for p in pedidos
            ]
        return jsonify({"ok": True, "pedidos": payload})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── COBERTURA / DISTANCIA ───────────────────────────────────────────────────

@api_bot_bp.route("/cobertura", methods=["GET", "POST"])
@bot_required
def cobertura_delivery():
    """Valida cobertura con la misma regla del checkout, protegida para WhatsApp."""
    try:
        data = request.get_json(silent=True) or {}
        direccion = (request.args.get("direccion") or data.get("direccion") or "").strip()
        if not direccion:
            return jsonify({
                "ok": False,
                "cobertura": {"ok": False, "distancia_km": None, "mensaje": "Dirección requerida"},
                "error": "Dirección requerida",
            }), 400
        if len(direccion) > 220:
            return jsonify({
                "ok": False,
                "cobertura": {
                    "ok": False,
                    "distancia_km": None,
                    "mensaje": "Dirección demasiado larga",
                },
                "error": "Dirección demasiado larga",
            }), 400

        resultado = validar_radio_entrega(direccion)
        return jsonify({
            "ok": bool(resultado.get("ok")),
            "cobertura": resultado,
            "validacion_activa": _config_bool("VALIDAR_RADIO_ENTREGA", "1"),
            "bloqueo_no_verificada": _config_bool("BLOQUEAR_DIRECCION_NO_VERIFICADA", "1"),
            "radio_km": SiteConfig.get("RADIO_ENTREGA_KM", "5"),
            "ciudad": SiteConfig.get("CIUDAD_NEGOCIO", ""),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── FLUJO DE MENÚ DEL BOT (script paso a paso) ──────────────────────────────

@api_bot_bp.route("/asistente")
@bot_required
def asistente_bot():
    """Contrato compacto para el bot: opciones, endpoints y textos base."""
    try:
        nombre = SiteConfig.get("NOMBRE_NEGOCIO", "Mi tienda")
        tienda_url = SiteConfig.get("TIENDA_URL", "") or SiteConfig.get("OXIDIAN_PUBLIC_URL", "")
        telefono = SiteConfig.get("TELEFONO_NEGOCIO", "")
        return jsonify({
            "ok": True,
            "negocio": {
                "nombre": nombre,
                "telefono": telefono,
                "tienda_url": tienda_url,
                "horario": {
                    "apertura": SiteConfig.get("HORARIO_APERTURA", "09:00"),
                    "cierre": SiteConfig.get("HORARIO_CIERRE", "22:30"),
                },
            },
            "menu": [
                {"key": "1", "label": "Ver menu y combos", "endpoint": "GET /api/bot/catalogo/completo"},
                {"key": "2", "label": "Estado de pedido", "endpoint": "GET /api/bot/pedido/estado"},
                {"key": "3", "label": "Puntos", "endpoint": "GET /api/bot/puntos?telefono="},
                {"key": "4", "label": "Cobertura delivery", "endpoint": "GET /api/bot/cobertura?direccion="},
                {"key": "5", "label": "Abrir tienda online", "endpoint": None, "url": tienda_url},
                {"key": "6", "label": "Horarios y contacto", "endpoint": "GET /api/bot/negocio"},
                {"key": "7", "label": "Hablar con agente", "action": "handoff"},
            ],
            "reglas": {
                "identificar_cliente": "Usa telefono como identificador principal.",
                "estado_pedido": "Consulta por numero_pedido y telefono cuando el cliente lo tenga; si no, por telefono para traer el ultimo pedido.",
                "pedido": "No crees pedidos por chatbot. Para comprar, envia al cliente a tienda_url.",
                "pagos": "Efectivo y Bizum son confirmaciones manuales, no pasarela bancaria.",
                "agente": "Si hay duda, queja, direccion confusa o pago no claro, deriva al telefono del negocio.",
            },
            "respuestas": {
                "saludo": f"Hola, soy el asistente de {nombre}. ¿Que necesitas?",
                "sin_pedido": "No encuentro ese pedido. Revisa el numero o dime el telefono usado al comprar.",
                "agente": f"Te paso con una persona. Telefono: {telefono or 'no configurado'}",
            },
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/menu-flow")
@bot_required
def menu_flow():
    """
    Devuelve el script completo del flujo conversacional del bot.
    El bot Node.js usa esto para construir los menús paso a paso.
    """
    try:
        nombre = SiteConfig.get("NOMBRE_NEGOCIO", "Mi tienda")
        telefono_negocio = SiteConfig.get("TELEFONO_NEGOCIO", "")
        horario_ap = SiteConfig.get("HORARIO_APERTURA", "09:00")
        horario_ci = SiteConfig.get("HORARIO_CIERRE", "22:30")

        # Construir categorías disponibles
        categorias = Categoria.query.filter_by(activo=True).order_by(Categoria.id).all()
        cats_menu = []
        # Iconos por categoría — palabras neutrales que aplican a cualquier tienda.
        emojis_cat = {
            "bebida": "🥤", "refresco": "🥤", "cerveza": "🥤",
            "dulce": "🍮", "postre": "🍮", "helado": "🍮",
            "combo": "🎁", "pack": "🎁", "menu": "🎁",
            "pizza": "🍕", "burger": "🍔", "hambur": "🍔",
            "ensalada": "🥗", "sopa": "🍲", "caldo": "🍲",
        }
        for i, cat in enumerate(categorias, 1):
            emoji = "🍽️"
            nombre_lower = cat.nombre.lower()
            for kw, em in emojis_cat.items():
                if kw in nombre_lower:
                    emoji = em
                    break
            cats_menu.append({
                "key": str(i),
                "emoji": emoji,
                "label": cat.nombre,
                "categoria_id": cat.id,
            })
        cats_menu.append({"key": str(len(cats_menu) + 1), "emoji": "📋", "label": "Ver todo el menú", "categoria_id": None})

        return jsonify({
            "ok": True,
            "negocio": nombre,
            "horario": f"{horario_ap} – {horario_ci}",
            "telefono_agente": telefono_negocio,

            # ── MENÚ PRINCIPAL ──
            "menu_principal": {
                "bienvenida": f"¡Hola parce! 🥟 Bienvenido a *{nombre}*\n\n¿En qué te ayudo hoy?",
                "opciones": [
                    {"key": "1", "emoji": "🍽️", "label": "Ver el menú",          "action": "menu_catalogo"},
                    {"key": "2", "emoji": "🔥", "label": "Promociones activas",   "action": "promociones"},
                    {"key": "3", "emoji": "🎟️", "label": "Mis cupones",           "action": "cupones"},
                    {"key": "4", "emoji": "⭐", "label": "Consultar mis puntos",  "action": "puntos_consulta"},
                    {"key": "5", "emoji": "🛒", "label": "Hacer un pedido",       "action": "pedido_inicio"},
                    {"key": "6", "emoji": "📦", "label": "Estado de mi pedido",   "action": "estado_pedido"},
                    {"key": "7", "emoji": "👨‍💼", "label": "Hablar con un agente", "action": "agente"},
                ]
            },

            # ── MENÚ CATÁLOGO (por categoría) ──
            "menu_catalogo": {
                "pregunta": "¿Qué se te antoja hoy? 🤤\nElige una categoría:",
                "categorias": cats_menu,
                "nota": "Escribe el número o nombre de la categoría"
            },

            # ── FLUJO DE PUNTOS ──
            "flujo_puntos": {
                "paso_1": {
                    "mensaje": "⭐ *Sistema de puntos*\n\n¿Cuál es tu número de WhatsApp?\n_(es tu identificación; no necesitas registrarte)_",
                    "accion": "pedir_telefono",
                },
                "paso_2_con_puntos": {
                    "mensaje": "¡Tienes *{puntos}* puntos! 🌟\nEquivalen a *€{valor_euro}* de descuento.\n\nEscribe *CANJEAR* para ver los productos que puedes conseguir con tus puntos\nO *MENU* para volver al inicio",
                    "accion": "mostrar_opciones_puntos",
                },
                "paso_2_sin_puntos": {
                    "mensaje": "Aún no tienes puntos acumulados 😅\nPero en cada compra ganas *1 punto por €* gastado.\n¿Quieres ver el menú para pedir? 🛒",
                    "accion": "ir_al_menu",
                },
                "paso_3_productos": {
                    "mensaje": "🎁 *Productos que puedes canjear:*\n{lista_productos}\n\nArma tu pedido en la web y elige el canje durante la confirmación.",
                    "accion": "abrir_tienda",
                },
                "paso_4_confirmar": {
                    "mensaje": "🔐 Para confirmar el canje de *{puntos_necesarios}* puntos por *{producto_nombre}*, te enviaremos un código a este WhatsApp.\n\n¿Lo confirmas? Responde *SÍ* para recibir el código",
                    "accion": "pedir_confirmacion_canje",
                },
                "paso_5_codigo": {
                    "mensaje": "📱 Te hemos enviado un código de 6 dígitos.\nEscríbelo aquí para confirmar el canje:",
                    "accion": "pedir_codigo_verificacion",
                },
                "paso_6_exito": {
                    "mensaje": "✅ ¡Listo parce! *{puntos_descontados}* puntos canjeados.\nTu *{producto_nombre}* está incluido en tu próximo pedido.\n\nTe quedan *{puntos_restantes}* puntos 🌟",
                    "accion": "canje_completado",
                },
            },

            # ── FLUJO DE PEDIDO ──
            "flujo_pedido": {
                "paso_1": {
                    "mensaje": "🛒 *Hacer un pedido*\n\nLas compras se realizan en la tienda web. Tu WhatsApp identifica automáticamente tus pedidos y puntos.",
                    "accion": "pedir_telefono",
                },
                "paso_2": {
                    "mensaje": "¡Hola *{nombre}*! 👋\n\nPide visitando nuestra tienda web:\n🌐 {tienda_url}\n\nO dime qué quieres y te ayudo paso a paso 📝",
                    "accion": "mostrar_opciones_pedido",
                },
            },

            # ── MENSAJES DE ESTADO ──
            "mensajes_estado": {
                "pendiente":  "✅ Tu pedido *{num}* fue recibido. Total: €{total}. ¡Ya lo estamos preparando!",
                "armando":    "👨‍🍳 Estamos preparando tu pedido *{num}*. En breve sale.",
                "listo":      "📦 Tu pedido *{num}* está listo y pronto sale a entregarse.",
                "en_ruta":    "🚀 Tu pedido *{num}* está en camino. Cuando el repartidor llegue te enviaremos el código de entrega.",
                "entregado":  "🎉 ¡Pedido *{num}* entregado! Gracias parce 💛\nGanaste *{puntos}* puntos ⭐",
                "cancelado":  "❌ Tu pedido *{num}* fue cancelado. Si tienes dudas contáctanos.",
            },

            # ── INSTRUCCIONES PARA EL AGENTE BOT ──
            "instrucciones_bot": {
                "filtros_catalogo": [
                    "Para filtrar por categoría: GET /api/bot/catalogo?categoria_id=X",
                    "Para ver solo productos en promoción: GET /api/bot/promociones",
                    "Para ver combos: filtra categoría con 'combo'",
                    "Para bebidas: filtra categoría con 'bebida'",
                    "Para dulces: filtra categoría con 'dulce'",
                ],
                "flujo_puntos": [
                    "1. Usar el teléfono del propio chat como identidad",
                    "2. GET /api/bot/puntos?telefono=X → informar saldo",
                    "3. GET /api/bot/puntos/productos-canjeables?telefono=X → informar opciones",
                    "4. Enviar al cliente a la tienda web para realizar el pedido y aplicar el canje",
                ],
                "palabras_clave": {
                    "menu": ["menú", "carta", "productos", "que tienen", "que hay"],
                    "promociones": ["promo", "oferta", "descuento", "rebaja", "barato"],
                    "cupones": ["cupón", "código descuento", "voucher"],
                    "puntos": ["puntos", "estrellas", "fidelidad", "mis puntos", "saldo"],
                    "pedido": ["pedir", "quiero", "comprar", "encargar", "ordenar"],
                    "agente": ["agente", "persona", "humano", "ayuda", "hablar con"],
                    "estado": ["mi pedido", "donde está", "estado", "cuando llega"],
                },
            },
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── CATÁLOGO FILTRADO POR CATEGORÍA ─────────────────────────────────────────

@api_bot_bp.route("/catalogo/categoria/<int:categoria_id>")
@bot_required
def catalogo_por_categoria(categoria_id):
    """Productos de una categoría específica para el bot."""
    try:
        cat = db.session.get(Categoria, categoria_id)
        if not cat or not cat.activo:
            return jsonify({"ok": False, "error": "Categoría no encontrada"}), 404

        productos = Product.query.filter_by(activo=True, categoria_id=categoria_id).all()
        disponibles = [p for p in productos if _producto_disponible_para_bot(p)]

        return jsonify({
            "ok": True,
            "categoria": {"id": cat.id, "nombre": cat.nombre},
            "total": len(disponibles),
            "productos": [
                {
                    "id": p.id,
                    "nombre": p.nombre,
                    "descripcion": (p.descripcion or "")[:120],
                    "precio": float(p.precio),
                    "origen_pais": p.origen_pais or "",
                    "tipo_entrega": p.tipo_entrega or "inmediato",
                    "modalidad_entrega": p.modalidad_entrega or "ambas",
                    "stock": p.stock_total,
                    "canjeable_con_puntos": bool(p.canjeable_con_puntos),
                    "puntos_para_canje": p.puntos_para_canje or 0,
                }
                for p in disponibles
            ]
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── CUPONES ACTIVOS (para consulta del bot) ──────────────────────────────────

@api_bot_bp.route("/cupones/info")
@bot_required
def cupones_info():
    """Devuelve cupones activos con información pública para el bot."""
    try:
        cupones = Coupon.query.filter_by(activo=True).all()
        activos = [c for c in cupones if c.es_valido()[0]]

        return jsonify({
            "ok": True,
            "total": len(activos),
            "cupones": [
                {
                    "codigo": c.codigo,
                    "descripcion": c.descripcion or "",
                    "tipo": c.tipo,
                    "valor": float(c.valor),
                    "minimo_pedido": float(c.minimo_pedido) if c.minimo_pedido else 0,
                    "usos_restantes": (c.usos_maximos - c.usos_actuales) if c.usos_maximos else None,
                }
                for c in activos
            ],
            "mensaje_bot": "🎟️ *Cupones disponibles:*\n" + "\n".join(
                f"• *{c.codigo}* — {c.descripcion or c.tipo}{' (mín. €' + str(float(c.minimo_pedido)) + ')' if c.minimo_pedido else ''}"
                for c in activos[:5]
            ) if activos else "No hay cupones activos ahora mismo, ¡pero revisa más tarde! 😊"
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── PUNTOS: PRODUCTOS CANJEABLES ────────────────────────────────────────────

@api_bot_bp.route("/puntos/productos-canjeables")
@bot_required
def productos_canjeables():
    """
    Devuelve los productos que el cliente puede canjear con sus puntos actuales.
    Requiere telefono como query param.
    """
    try:
        cliente, telefono = _cliente_por_telefono(request.args.get("telefono", ""))
        if not telefono_valido(telefono):
            return jsonify({"ok": False, "error": "Parámetro telefono requerido"}), 400
        if not cliente:
            return jsonify({"ok": False, "error": "Cliente no encontrado", "puntos": 0}), 404

        puntos = cliente.puntos
        ratio = get_puntos_config()["ratio"]

        # Productos marcados como canjeables con los puntos suficientes
        productos = Product.query.filter_by(
            activo=True, canjeable_con_puntos=True
        ).filter(Product.puntos_para_canje <= puntos).all()
        productos = [p for p in productos if p.canje_directo_disponible()]
        productos = [p for p in productos if _producto_disponible_para_bot(p)]

        lista_texto = ""
        for i, p in enumerate(productos, 1):
            lista_texto += f"\n{i}. {p.nombre} — *{p.puntos_para_canje} puntos*"

        return jsonify({
            "ok": True,
            "cliente": {"nombre": cliente.nombre, "puntos": puntos, "valor_euro": round(puntos / ratio, 2)},
            "puede_canjear": len(productos) > 0,
            "productos_canjeables": [
                {
                    "id": p.id,
                    "nombre": p.nombre,
                    "puntos_necesarios": p.puntos_para_canje,
                    "puntos_restantes_tras_canje": puntos - p.puntos_para_canje,
                }
                for p in productos
            ],
            "mensaje_bot": (
                f"⭐ Tienes *{puntos} puntos* 🌟\n"
                f"*Puedes canjear en tu próximo pedido:*{lista_texto}\n\n"
                "Abre la tienda web, arma el carrito y verifica este WhatsApp al confirmar."
            ) if productos else (
                f"⭐ Tienes *{puntos} puntos*.\n"
                f"Aún no alcanzas para canjear ningún producto.\n"
                f"Sigue comprando para acumular más 💪"
            )
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── PUNTOS: SOLICITAR CÓDIGO DE VERIFICACIÓN ────────────────────────────────

@api_bot_bp.route("/puntos/solicitar-codigo", methods=["POST"])
@bot_required
def bot_solicitar_codigo_puntos():
    """
    Genera y envía código de verificación por WhatsApp para confirmar el canje de puntos.
    Body: {telefono, producto_id}
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"ok": False, "error": "JSON body requerido"}), 400
        cliente, telefono = _cliente_por_telefono(data.get("telefono"))
        producto_id = data.get("producto_id")

        if not telefono_valido(telefono):
            return jsonify({"ok": False, "error": "telefono requerido"}), 400
        if not cliente:
            return jsonify({"ok": False, "error": "Cliente no encontrado"}), 404

        producto = None
        if producto_id:
            try:
                producto = db.session.get(Product, int(producto_id))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "producto_id inválido"}), 400
            if not producto:
                return jsonify({"ok": False, "error": "Producto no encontrado"}), 404

        resultado = solicitar_codigo(cliente, producto=producto)
        if not resultado.get("ok"):
            return jsonify({"ok": False, "error": resultado.get("msg", "No se pudo enviar el código")}), 400

        return jsonify({
            "ok": True,
            "mensaje": "Código enviado por WhatsApp",
            "enviado_wa": True,
            "producto": {"id": producto.id, "nombre": producto.nombre, "puntos": producto.puntos_para_canje} if producto else None,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── PUNTOS: VERIFICAR CÓDIGO Y CANJEAR ──────────────────────────────────────

@api_bot_bp.route("/puntos/verificar-codigo", methods=["POST"])
@bot_required
def bot_verificar_codigo_puntos():
    """
    Verifica el código para informar sobre un producto canjeable.
    Body: {telefono, codigo, producto_id}
    El canje real solo se aplica al confirmar un pedido en la tienda web.
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"ok": False, "error": "JSON body requerido"}), 400
        cliente, telefono = _cliente_por_telefono(data.get("telefono"))
        codigo = (data.get("codigo") or "").strip()
        producto_id = data.get("producto_id")

        if not telefono_valido(telefono) or not codigo:
            return jsonify({"ok": False, "error": "telefono y codigo son requeridos"}), 400
        if not cliente:
            return jsonify({"ok": False, "error": "Cliente no encontrado"}), 404

        if not cliente.verificar_cod_puntos(codigo):
            db.session.commit()  # persiste incremento de intentos fallidos
            return jsonify({"ok": False, "error": "Código incorrecto o expirado. Solicita uno nuevo."}), 400

        # OTP válido: commit inmediato para invalidarlo y no permitir reutilización
        db.session.commit()

        if not producto_id:
            # Solo verificación, sin canje
            return jsonify({"ok": True, "verificado": True, "puntos": cliente.puntos})

        producto = db.session.get(Product, int(producto_id))
        if not producto or not producto.canje_directo_disponible():
            return jsonify({"ok": False, "error": "Producto no válido para canje"})

        if producto.puntos_para_canje > cliente.puntos:
            return jsonify({
                "ok": False,
                "error": f"Puntos insuficientes. Necesitas {producto.puntos_para_canje}"
            })

        return jsonify({
            "ok": True,
            "canje_exitoso": False,
            "verificado": True,
            "producto": {"id": producto.id, "nombre": producto.nombre},
            "producto_canje_id": producto.id,
            "puntos_necesarios": producto.puntos_para_canje,
            "puntos_restantes": cliente.puntos,
            "mensaje_bot": (
                f"✅ Código verificado para *{producto.nombre}*.\n"
                f"Abre la tienda, arma el pedido y elige este regalo en el checkout. "
                f"Los *{producto.puntos_para_canje} puntos* solo se descontarán al confirmar 🛒"
            )
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── PUNTOS MEJORADO: CON PRODUCTOS CANJEABLES ───────────────────────────────

@api_bot_bp.route("/puntos/saldo")
@bot_required
def puntos_saldo_completo():
    """
    Versión extendida de /puntos con info de canje y productos disponibles.
    Reemplaza a /puntos para el bot cuando necesita info completa.
    """
    try:
        cliente, telefono = _cliente_por_telefono(request.args.get("telefono", ""))
        if not telefono_valido(telefono):
            return jsonify({"ok": False, "error": "Parámetro telefono requerido"}), 400
        if not cliente:
            return jsonify({
                "ok": True,
                "existe": False,
                "mensaje_bot": (
                    "No encontramos una cuenta con ese número 😕\n"
                    "Los puntos se acumulan automáticamente al hacer pedidos.\n"
                    "¡Haz tu primer pedido y empieza a ganar! ⭐"
                )
            })

        ratio = get_puntos_config()["ratio"]
        puntos = cliente.puntos
        valor_euro = round(puntos / ratio, 2)

        # Productos canjeables
        canjeables = Product.query.filter_by(
            activo=True, canjeable_con_puntos=True
        ).filter(Product.puntos_para_canje <= puntos).all() if puntos > 0 else []
        canjeables = [p for p in canjeables if p.canje_directo_disponible() and _producto_disponible_para_bot(p)]

        # Próximo producto a alcanzar (motivación)
        proximos = Product.query.filter_by(
            activo=True, canjeable_con_puntos=True
        ).filter(Product.puntos_para_canje > puntos)\
         .order_by(Product.puntos_para_canje.asc()).all() if puntos >= 0 else []
        proximo = next((p for p in proximos if p.canje_directo_disponible() and _producto_disponible_para_bot(p)), None)

        historial = PointsLog.query.filter_by(cliente_id=cliente.id)\
                                    .order_by(PointsLog.creado_en.desc()).limit(3).all()

        mensaje = f"⭐ *{cliente.nombre}*, tienes *{puntos} puntos*"
        if valor_euro > 0:
            mensaje += f" (€{valor_euro} de descuento)"
        mensaje += "\n"

        if canjeables:
            mensaje += f"\n🎁 *Puedes canjear ahora:*\n"
            for i, p in enumerate(canjeables[:3], 1):
                mensaje += f"  {i}. {p.nombre} ({p.puntos_para_canje} pts)\n"
        elif proximo:
            faltan = proximo.puntos_para_canje - puntos
            mensaje += f"\n💪 Te faltan solo *{faltan} puntos* para canjear *{proximo.nombre}*"

        return jsonify({
            "ok": True,
            "existe": True,
            "cliente": {
                "id": cliente.id,
                "nombre": cliente.nombre,
                "puntos": puntos,
                "valor_euro": valor_euro,
            },
            "puede_canjear": len(canjeables) > 0,
            "productos_canjeables": [
                {"id": p.id, "nombre": p.nombre, "puntos_necesarios": p.puntos_para_canje}
                for p in canjeables
            ],
            "proximo_objetivo": {
                "nombre": proximo.nombre,
                "puntos_necesarios": proximo.puntos_para_canje,
                "puntos_faltan": proximo.puntos_para_canje - puntos
            } if proximo else None,
            "ultimos_movimientos": [
                {
                    "tipo": h.tipo,
                    "cantidad": h.cantidad,
                    "descripcion": h.descripcion or "",
                    "fecha": h.creado_en.strftime("%d/%m/%Y") if h.creado_en else ""
                }
                for h in historial
            ],
            "mensaje_bot": mensaje,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── INFO DEL NEGOCIO ─────────────────────────────────────────────────────────

@api_bot_bp.route("/negocio")
@bot_required
def info_negocio():
    """Info pública del negocio para el bot, incluyendo estado de apertura en tiempo real."""
    try:
        tienda_url = SiteConfig.get("TIENDA_URL", "")
        apertura   = SiteConfig.get("HORARIO_APERTURA", "09:00")
        cierre     = SiteConfig.get("HORARIO_CIERRE", "22:30")
        forzada    = str(SiteConfig.get("TIENDA_FORZAR_CERRADA", "0")).strip().lower() in {"1", "true", "yes", "on"}
        ahora_str  = datetime.now().strftime("%H:%M")
        is_open    = tienda_abierta_en_horario(apertura, cierre, ahora=ahora_str, forzada_cerrada=forzada)
        features = get_store_features()
        metodos_pago = []
        if _config_bool("EFECTIVO_HABILITADO", "1"):
            metodos_pago.append("efectivo")
        if _config_bool("BIZUM_HABILITADO", "1"):
            metodos_pago.append("Bizum")
        capacidades = ["consultas", "estado del pedido"]
        if features["puntos"]:
            capacidades.append("puntos")
        return jsonify({
            "ok": True,
            "nombre": SiteConfig.get("NOMBRE_NEGOCIO", "Mi tienda"),
            "direccion": SiteConfig.get("DIRECCION_NEGOCIO", ""),
            "telefono": SiteConfig.get("TELEFONO_NEGOCIO", ""),
            "whatsapp_country_code": SiteConfig.get("WHATSAPP_COUNTRY_CODE", ""),
            "ciudad": SiteConfig.get("CIUDAD_NEGOCIO", ""),
            "horario_apertura": apertura,
            "horario_cierre": cierre,
            "is_open": is_open,
            "forzar_cerrada": forzada,
            "hora_actual": ahora_str,
            "mensaje_cierre": SiteConfig.get("TIENDA_MENSAJE_CIERRE", "") if not is_open else "",
            "metodos_pago": metodos_pago,
            "delivery_enabled": features["delivery"],
            "pickup_enabled": features["recogida"],
            "points_enabled": features["puntos"],
            "tienda_url": tienda_url,
            "mensaje_pedido": (
                f"🛒 Para hacer tu pedido visita:\n{tienda_url}\n\n"
                f"Por WhatsApp puedo ayudarte con {', '.join(capacidades)} o pasarte con una persona."
            ) if tienda_url else (
                "🛒 La tienda online no esta configurada ahora mismo. "
                "Por WhatsApp puedo ayudarte con consultas o pasarte con una persona."
            )
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/tienda/status")
@bot_required
def tienda_status():
    """Respuesta rápida: ¿está abierta la tienda ahora mismo?"""
    try:
        apertura = SiteConfig.get("HORARIO_APERTURA", "09:00")
        cierre   = SiteConfig.get("HORARIO_CIERRE", "22:30")
        forzada  = str(SiteConfig.get("TIENDA_FORZAR_CERRADA", "0")).strip().lower() in {"1", "true", "yes", "on"}
        ahora    = datetime.now().strftime("%H:%M")
        is_open  = tienda_abierta_en_horario(apertura, cierre, ahora=ahora, forzada_cerrada=forzada)
        return jsonify({
            "ok": True,
            "is_open": is_open,
            "hora_actual": ahora,
            "horario": f"{apertura} – {cierre}",
            "mensaje": "Abierto ahora" if is_open else SiteConfig.get("TIENDA_MENSAJE_CIERRE", f"Cerrado. Horario: {apertura}–{cierre}"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _normalizar_telefono_bot(raw):
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 9 and digits[0] in "6789":
        digits = "34" + digits
    return digits


def _buscar_cliente_por_telefono(raw):
    telefono = _normalizar_telefono_bot(raw)
    if not telefono:
        return None, telefono
    candidates = {telefono, f"+{telefono}"}
    if telefono.startswith("34") and len(telefono) == 11:
        local = telefono[2:]
        candidates.update({local, f"+34{local}"})
    cliente = User.query.filter(User.rol == "cliente", User.telefono.in_(candidates)).first()
    if not cliente:
        clean_db_phone = db.func.regexp_replace(User.telefono, r"\D", "", "g")
        cliente = User.query.filter(User.rol == "cliente", clean_db_phone == telefono).first()
    return cliente, telefono


def _producto_admin_payload(producto):
    return {
        "id": producto.id,
        "nombre": producto.nombre,
        "precio": float(producto.precio or 0),
        "activo": bool(producto.activo),
        "es_combo": bool(producto.es_combo),
        "tipo_entrega": producto.tipo_entrega or "inmediato",
        "modalidad_entrega": producto.modalidad_entrega or "ambas",
        "categoria": producto.categoria.nombre if producto.categoria else "",
        "stock": int(producto.combo_stock_total if producto.es_combo else producto.stock_total),
    }


def _bot_admin_actor_allowed(data, capability):
    telefono = normalizar_telefono_cliente((data or {}).get("actor_telefono"))
    if not telefono_valido(telefono):
        return False
    digits = re.sub(r"\D", "", telefono)
    privileged = {
        re.sub(r"\D", "", raw)
        for raw in [
            os.environ.get("OWNER_NUMBER", ""),
            *os.environ.get("SUPERADMINS", "").split(","),
        ]
        if raw
    }
    if digits in privileged:
        return True
    user = User.query.filter_by(
        telefono_normalizado=telefono,
        activo=True,
    ).filter(User.rol.in_(["admin", "super_admin"])).first()
    if not user:
        return False
    if user.rol == "super_admin":
        return True
    feature = {
        "products": "productos",
        "points": "marketing",
        "handoff": "whatsapp",
        "security": "whatsapp",
    }.get(capability)
    return True if capability == "store" else bool(
        feature and AdminFeature.tiene_acceso(user.id, feature)
    )


def _bot_actor_forbidden():
    return jsonify({
        "ok": False,
        "code": "ADMIN_CAPABILITY_DENIED",
        "error": "El administrador no tiene permiso para esta acción.",
    }), 403


def _pedido_admin_riesgo_payload(pedido, now):
    creado = pedido.creado_en or now
    edad_min = max(0, int((now - creado).total_seconds() // 60))
    cliente = getattr(pedido, "cliente", None)
    return {
        "id": pedido.id,
        "numero": pedido.numero_pedido,
        "estado": pedido.estado,
        "edad_min": edad_min,
        "total": float(pedido.total or 0),
        "origen": pedido.origen,
        "cliente": cliente.nombre if cliente else "",
        "telefono": cliente.telefono if cliente else "",
        "preparador": pedido.preparador.nombre if getattr(pedido, "preparador", None) else "",
        "repartidor": pedido.repartidor.nombre if getattr(pedido, "repartidor", None) else "",
        "zona": pedido.zona.nombre if pedido.zona else "",
        "creado_en": creado.isoformat() if creado else None,
    }


@api_bot_bp.route("/admin/tienda", methods=["GET", "POST"])
@bot_required
def bot_admin_tienda():
    """Consulta o cambia apertura forzada de tienda desde WhatsApp admin."""
    try:
        if request.method == "GET":
            cerrada = str(SiteConfig.get("TIENDA_FORZAR_CERRADA", "0")).strip().lower() in {"1", "true", "yes", "on"}
            return jsonify({
                "ok": True,
                "forzar_cerrada": cerrada,
                "estado": "cerrada" if cerrada else "abierta",
                "mensaje_cierre": SiteConfig.get("TIENDA_MENSAJE_CIERRE", ""),
            })

        data = request.get_json(silent=True) or {}
        if not _bot_admin_actor_allowed(data, "store"):
            return _bot_actor_forbidden()
        if "forzar_cerrada" not in data:
            return jsonify({"ok": False, "error": "forzar_cerrada requerido"}), 400
        cerrada = _json_bool(data.get("forzar_cerrada"))
        mensaje = str(data.get("mensaje_cierre") or "").strip()[:240]
        SiteConfig.set("TIENDA_FORZAR_CERRADA", "1" if cerrada else "0",
                       descripcion="Cierre temporal controlado por bot admin")
        if mensaje or cerrada:
            SiteConfig.set("TIENDA_MENSAJE_CIERRE", mensaje,
                           descripcion="Mensaje de cierre temporal")
        db.session.commit()
        return jsonify({
            "ok": True,
            "forzar_cerrada": cerrada,
            "estado": "cerrada" if cerrada else "abierta",
            "mensaje_cierre": mensaje,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/admin/resumen-hoy")
@bot_required
def bot_admin_resumen_hoy():
    """Resumen operativo del día: pedidos, ventas, activos, productos sin stock."""
    try:
        from datetime import datetime, time as dtime
        from sqlalchemy import func
        hoy = datetime.now().date()
        inicio = datetime.combine(hoy, dtime.min)
        fin = datetime.combine(hoy, dtime.max)
        pedidos_hoy_q = Order.query.filter(
            Order.creado_en >= inicio, Order.creado_en <= fin
        )
        pedidos_hoy = pedidos_hoy_q.count()
        entregados = pedidos_hoy_q.filter(Order.estado == "entregado").count()
        cancelados = pedidos_hoy_q.filter(Order.estado == "cancelado").count()
        ventas_hoy = float(db.session.query(func.coalesce(func.sum(Order.total), 0))
                           .filter(Order.creado_en >= inicio,
                                   Order.creado_en <= fin,
                                   Order.estado != "cancelado").scalar() or 0)
        activos = Order.query.filter(
            Order.estado.in_(["pendiente", "armando", "listo", "en_ruta"])
        ).count()
        # Productos sin stock visible (solo los que gestionan stock en web)
        sin_stock = Product.query.filter(
            Product.activo.is_(True),
            Product.es_combo.is_(False),
            Product.stock_mostrar_en_web.is_(True),
        ).all()
        agotados = [p for p in sin_stock if p.stock_para_origen("propio") <= 0]
        return jsonify({
            "ok": True,
            "fecha": hoy.isoformat(),
            "pedidos_hoy": pedidos_hoy,
            "entregados": entregados,
            "cancelados": cancelados,
            "ventas_hoy": round(ventas_hoy, 2),
            "activos": activos,
            "productos_sin_stock": [
                {"id": p.id, "nombre": p.nombre} for p in agotados[:20]
            ],
            "total_sin_stock": len(agotados),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── Endpoints exclusivos super_admin desde el bot ──────────────
# El bot ya autentica al remitente como super_admin antes de invocarlos
# (isSuperAdminJid). El bot_required guarda la llamada bot→oxidian con la
# clave X-Bot-Key. Ambas capas son necesarias — sin cualquiera, 403.

@api_bot_bp.route("/admin/modo-tienda/toggle", methods=["POST"])
@bot_required
def bot_admin_toggle_modo_tienda():
    """Alterna entre modo 'propia' y 'bar_servicio' desde el bot."""
    from store_config import get_store_features
    features = get_store_features()
    actual = features.get("modo_tienda", "propia")
    nuevo = "bar_servicio" if actual == "propia" else "propia"
    SiteConfig.set("MODO_TIENDA", nuevo, descripcion="Toggle desde bot super_admin")
    db.session.commit()
    return jsonify({
        "ok": True,
        "modo": nuevo,
        "modo_label": "servicio" if nuevo == "bar_servicio" else "propio",
        "es_servicio": nuevo == "bar_servicio",
    })


@api_bot_bp.route("/admin/modulos/toggle", methods=["POST"])
@bot_required
def bot_admin_toggle_modulo():
    """Activa/desactiva un módulo (delivery, recogida, programados, puntos)."""
    data = request.get_json(silent=True) or {}
    claves = {
        "delivery": "FEATURE_DELIVERY",
        "recogida": "FEATURE_RECOGIDA",
        "programados": "FEATURE_PEDIDOS_PROGRAMADOS",
        "puntos": "FEATURE_PUNTOS",
    }
    clave = claves.get(str(data.get("modulo", "")).lower())
    enabled = str(data.get("enabled", "0"))
    if not clave or enabled not in ("0", "1"):
        return jsonify({"ok": False, "error": "modulo o enabled inválido"}), 400
    # Guard: no permitir apagar delivery Y recogida a la vez
    if enabled == "0":
        otra_clave = "FEATURE_RECOGIDA" if clave == "FEATURE_DELIVERY" else \
                     "FEATURE_DELIVERY" if clave == "FEATURE_RECOGIDA" else None
        if otra_clave and SiteConfig.get(otra_clave, "1") == "0":
            return jsonify({"ok": False, "error": "Debe quedar delivery o recogida activo"}), 400
    SiteConfig.set(clave, enabled, descripcion="Toggle desde bot super_admin")
    db.session.commit()
    return jsonify({"ok": True, "clave": clave, "enabled": enabled})


@api_bot_bp.route("/admin/tienda/forzar-cierre", methods=["POST"])
@bot_required
def bot_admin_forzar_cierre():
    """Fuerza cierre / reapertura de la tienda al vuelo."""
    data = request.get_json(silent=True) or {}
    cerrada = bool(data.get("cerrada"))
    SiteConfig.set("TIENDA_FORZAR_CERRADA", "1" if cerrada else "0",
                   descripcion="Forzar cierre desde bot super_admin")
    db.session.commit()
    return jsonify({"ok": True, "cerrada": cerrada})


@api_bot_bp.route("/admin/salud")
@bot_required
def bot_admin_salud():
    """Snapshot rápido de salud del sistema para el super_admin."""
    from datetime import date
    try:
        pedidos_hoy = Order.query.filter(
            db.func.date(Order.creado_en) == date.today()
        ).count()
        pedidos_pend = Order.query.filter(
            Order.estado.in_(("pendiente", "armando"))
        ).count()
        clientes = User.query.filter_by(rol="cliente", activo=True).count()
        db_ok = True
    except Exception:
        pedidos_hoy = pedidos_pend = clientes = 0
        db_ok = False
    from store_config import get_store_features
    features = get_store_features()
    return jsonify({
        "ok": True,
        "pedidos_hoy": pedidos_hoy,
        "pedidos_pendientes": pedidos_pend,
        "clientes": clientes,
        "db_ok": db_ok,
        "bot_ok": True,
        "modo_tienda": features.get("modo_tienda", "propia"),
        "uptime": "activo",
    })


@api_bot_bp.route("/admin/pedidos/pendientes")
@bot_required
def bot_admin_pedidos_pendientes():
    """Cola operativa: pedidos activos (pendiente/armando/listo) para mostrar
    al admin desde el bot. Ordenados por más antiguo primero."""
    estados_activos = ("pendiente", "armando", "listo", "en_ruta")
    pedidos = Order.query.filter(Order.estado.in_(estados_activos)) \
        .order_by(Order.creado_en.asc()).limit(30).all()
    ESTADO_LABEL = {
        "pendiente": "⏳ Recibido",
        "armando": "🔥 Preparando",
        "listo": "✅ Listo",
        "en_ruta": "🛵 En ruta",
    }
    return jsonify({
        "ok": True,
        "pedidos": [{
            "id": p.id,
            "numero": p.numero_pedido,
            "estado": p.estado,
            "estado_label": ESTADO_LABEL.get(p.estado, p.estado),
            "total": float(p.total or 0),
            "creado_en": p.creado_en.isoformat() if p.creado_en else None,
            "tipo_entrega": p.tipo_entrega_cliente,
        } for p in pedidos]
    })


@api_bot_bp.route("/admin/pedidos/riesgo")
@bot_required
def bot_admin_pedidos_riesgo():
    """Pedidos que requieren atención operativa rápida desde WhatsApp admin."""
    try:
        now = datetime.utcnow()
        pending_min = max(5, min(180, request.args.get("pending_min", 20, type=int)))
        armando_min = max(5, min(180, request.args.get("armando_min", 35, type=int)))
        listo_min = max(5, min(180, request.args.get("listo_min", 15, type=int)))
        ruta_min = max(5, min(240, request.args.get("ruta_min", 45, type=int)))

        pendientes_lentos = Order.query.filter(
            Order.estado == "pendiente",
            Order.creado_en <= now - timedelta(minutes=pending_min),
        ).order_by(Order.creado_en.asc()).limit(8).all()
        armando_lentos = Order.query.filter(
            Order.estado == "armando",
            Order.creado_en <= now - timedelta(minutes=armando_min),
        ).order_by(Order.creado_en.asc()).limit(8).all()
        sin_preparador = Order.query.filter(
            Order.estado.in_(["pendiente", "armando"]),
            Order.preparador_id.is_(None),
        ).order_by(Order.creado_en.asc()).limit(8).all()
        sin_repartidor = Order.query.filter(
            Order.estado == "listo",
            Order.repartidor_id.is_(None),
        ).order_by(Order.creado_en.asc()).limit(8).all()
        listos_lentos = Order.query.filter(
            Order.estado == "listo",
            Order.creado_en <= now - timedelta(minutes=listo_min),
        ).order_by(Order.creado_en.asc()).limit(8).all()
        ruta_lentos = Order.query.filter(
            Order.estado == "en_ruta",
            Order.creado_en <= now - timedelta(minutes=ruta_min),
        ).order_by(Order.creado_en.asc()).limit(8).all()

        def payload(rows):
            return [_pedido_admin_riesgo_payload(p, now) for p in rows]

        return jsonify({
            "ok": True,
            "thresholds": {
                "pending_min": pending_min,
                "armando_min": armando_min,
                "listo_min": listo_min,
                "ruta_min": ruta_min,
            },
            "counts": {
                "pendientes_lentos": len(pendientes_lentos),
                "armando_lentos": len(armando_lentos),
                "sin_preparador": len(sin_preparador),
                "sin_repartidor": len(sin_repartidor),
                "listos_lentos": len(listos_lentos),
                "ruta_lentos": len(ruta_lentos),
            },
            "pendientes_lentos": payload(pendientes_lentos),
            "armando_lentos": payload(armando_lentos),
            "sin_preparador": payload(sin_preparador),
            "sin_repartidor": payload(sin_repartidor),
            "listos_lentos": payload(listos_lentos),
            "ruta_lentos": payload(ruta_lentos),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/admin/productos/buscar")
@bot_required
def bot_admin_buscar_productos():
    try:
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"ok": False, "error": "q requerido"}), 400
        query = Product.query
        if q.isdigit():
            query = query.filter(Product.id == int(q))
        else:
            query = query.filter(Product.nombre.ilike(f"%{q[:80]}%"))
        productos = query.order_by(Product.activo.desc(), Product.nombre.asc()).limit(8).all()
        return jsonify({"ok": True, "productos": [_producto_admin_payload(p) for p in productos]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/admin/productos/<int:producto_id>/precio", methods=["POST"])
@bot_required
def bot_admin_cambiar_precio(producto_id):
    try:
        data = request.get_json(silent=True) or {}
        if not _bot_admin_actor_allowed(data, "products"):
            return _bot_actor_forbidden()
        producto = get_or_404(Product, producto_id)
        nuevo_precio = float(data.get("precio") or 0)
        motivo = str(data.get("motivo") or "Cambio por WhatsApp admin").strip()[:200]
        if nuevo_precio <= 0 or nuevo_precio > 1000:
            return jsonify({"ok": False, "error": "Precio inválido"}), 400
        anterior = float(producto.precio or 0)
        db.session.add(PriceHistory(
            producto_id=producto.id,
            precio_anterior=producto.precio,
            precio_nuevo=nuevo_precio,
            motivo=motivo,
        ))
        producto.precio = round(nuevo_precio, 2)
        if producto.es_combo:
            producto.combo_precio_modo = "fijo"
            producto.combo_descuento_pct = 0
        db.session.commit()
        notificar_bot_sync()
        return jsonify({
            "ok": True,
            "producto": _producto_admin_payload(producto),
            "precio_anterior": anterior,
            "precio_nuevo": float(producto.precio),
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/admin/productos/<int:producto_id>/activo", methods=["POST"])
@bot_required
def bot_admin_producto_activo(producto_id):
    try:
        data = request.get_json(silent=True) or {}
        if not _bot_admin_actor_allowed(data, "products"):
            return _bot_actor_forbidden()
        producto = get_or_404(Product, producto_id)
        if "activo" not in data:
            return jsonify({"ok": False, "error": "activo requerido"}), 400
        activo = _json_bool(data.get("activo"))
        if not activo and ComboItem.query.filter_by(producto_id=producto.id).count() > 0:
            return jsonify({
                "ok": False,
                "error": "Este producto es componente de un combo. Quita el componente antes de desactivarlo.",
            }), 400
        producto.activo = activo
        db.session.commit()
        notificar_bot_sync()
        return jsonify({"ok": True, "producto": _producto_admin_payload(producto)})
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/admin/clientes/buscar")
@bot_required
def bot_admin_buscar_cliente():
    try:
        telefono_raw = request.args.get("telefono") or request.args.get("q") or ""
        cliente, telefono = _buscar_cliente_por_telefono(telefono_raw)
        if not cliente:
            return jsonify({"ok": False, "error": "Cliente no encontrado", "telefono": telefono}), 404
        return jsonify({
            "ok": True,
            "cliente": {
                "id": cliente.id,
                "nombre": cliente.nombre,
                "telefono": cliente.telefono,
                "email": cliente.email,
                "puntos": int(cliente.puntos or 0),
                "activo": bool(cliente.activo),
            },
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/admin/clientes/<int:cliente_id>/puntos", methods=["POST"])
@bot_required
def bot_admin_ajustar_puntos(cliente_id):
    try:
        data = request.get_json(silent=True) or {}
        if not _bot_admin_actor_allowed(data, "points"):
            return _bot_actor_forbidden()
        cliente = get_or_404(User, cliente_id)
        if cliente.rol != "cliente":
            return jsonify({"ok": False, "error": "Solo se pueden ajustar puntos de clientes"}), 400
        delta = int(data.get("delta") or 0)
        motivo = str(data.get("motivo") or "Ajuste manual por WhatsApp admin").strip()[:200]
        if delta == 0 or abs(delta) > 10000:
            return jsonify({"ok": False, "error": "Cantidad de puntos inválida"}), 400
        puntos_antes = int(cliente.puntos or 0)
        puntos_despues = puntos_antes + delta
        if puntos_despues < 0:
            return jsonify({"ok": False, "error": "El saldo no puede quedar negativo"}), 400
        cliente.puntos = puntos_despues
        db.session.add(PointsLog(
            cliente_id=cliente.id,
            tipo="ajuste",
            cantidad=delta,
            descripcion=motivo,
        ))
        db.session.commit()
        return jsonify({
            "ok": True,
            "cliente": {
                "id": cliente.id,
                "nombre": cliente.nombre,
                "telefono": cliente.telefono,
                "puntos": int(cliente.puntos or 0),
            },
            "puntos_antes": puntos_antes,
            "puntos_despues": puntos_despues,
            "delta": delta,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_bot_bp.route("/admin/clientes/<int:cliente_id>/puntos/historial")
@bot_required
def bot_admin_historial_puntos(cliente_id):
    try:
        cliente = get_or_404(User, cliente_id)
        historial = PointsLog.query.filter_by(cliente_id=cliente.id)\
            .order_by(PointsLog.creado_en.desc()).limit(5).all()
        return jsonify({
            "ok": True,
            "cliente": {"id": cliente.id, "nombre": cliente.nombre, "puntos": int(cliente.puntos or 0)},
            "historial": [
                {
                    "tipo": h.tipo,
                    "cantidad": int(h.cantidad or 0),
                    "descripcion": h.descripcion or "",
                    "fecha": h.creado_en.isoformat() if h.creado_en else None,
                }
                for h in historial
            ],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
