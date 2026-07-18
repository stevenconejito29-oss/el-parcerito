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
from extensions import db, get_or_404, limiter
from models import (User, Product, Categoria, Order, OrderItem, OrderProviderStatus,
                    ProveedorProducto,
                    Coupon, ComboItem,
                    AffiliateCode, ZonaEntrega,
                    ESTADOS_ACTIVOS, ESTADOS_EN_PREPARACION, ESTADOS_EN_REPARTO,
                    PointsLog, SiteConfig, IdempotencyKey, normalizar_metodo_pago,
                    BotAiUsage, BotAiMessage, AdminFeature,
                    PriceHistory, metadata_componente_combo,
                    metadata_item_pedido, utcnow as _utcnow,
                    AuditLog, internal_customer_email)
from idempotency import request_idempotency_key, request_body_hash, IDEMPOTENCY_TTL
from services import (distribuir_pedido, registrar_uso_afiliado,
                      calcular_puntos_ganados, get_puntos_config,
                      enviar_whatsapp_estado, mensaje_estado_pedido,
                      registrar_pedido_creado, encolar_whatsapp_generico,
                      aplicar_snapshot_zona_pedido,
                      validar_radio_entrega, tienda_abierta_en_horario,
                      cancelar_pedido_operativo, lineas_proveedor_pedido,
                      encolar_notificaciones_proveedores_pedido)
from pricing_service import calcular_precio
from loyalty_service import aplicar_canje_en_pedido, bloquear_cliente_puntos, solicitar_codigo
from phone_utils import (
    normalizar_telefono_cliente,
    solo_digitos,
    telefono_valido,
)
from store_config import (
    get_public_store_url,
    get_service_commission,
    get_store_features,
    is_service_mode,
)
from product_options_service import (
    product_option_catalog_payload,
    validate_product_option_selection,
)

api_bot_bp = Blueprint("api_bot", __name__)
logger = logging.getLogger(__name__)


# ── Handlers globales del blueprint ──────────────────────────────────
# Blindaje: cualquier HTTPException (abort(404), etc.) se serializa como
# JSON en vez de HTML. Cualquier excepción NO controlada se loggea con
# stacktrace server-side y devuelve un mensaje neutro al cliente (sin
# filtrar str(e) que podía leakear rutas de código, valores de DB, etc.).
from werkzeug.exceptions import HTTPException as _HTTPExc


@api_bot_bp.errorhandler(_HTTPExc)
def _api_bot_http_error(exc):
    code = exc.code or 500
    msg = {
        404: "Recurso no encontrado",
        403: "Sin permiso",
        405: "Método no permitido",
        400: "Solicitud inválida",
    }.get(code, exc.description or "Error HTTP")
    return jsonify({"ok": False, "error": msg}), code


@api_bot_bp.errorhandler(Exception)
def _api_bot_generic_error(exc):
    # Si ya se manejó y devolvió jsonify explícito, este handler no se ejecuta.
    # Solo aquí cuando algo escapó del try/except del endpoint.
    logger.exception("api_bot unhandled: %s", exc)
    return jsonify({"ok": False, "error": "Error interno del bot"}), 500


def _cliente_por_telefono(value):
    """Localiza al usuario que compra bajo este teléfono.

    Prioriza rol='cliente' pero cae al match sin filtro si no existe uno
    puro. Esto es crítico para el flujo "empleado/admin hace su propio
    pedido con su número": sin el fallback, `crear_pedido` intentaría
    crear un cliente shadow que colisiona con el UNIQUE global sobre
    `telefono_normalizado` y devolvía 500 al empleado.

    Delega en `services.buscar_cliente_por_telefono` (fuente única).
    """
    from services import buscar_cliente_por_telefono
    return buscar_cliente_por_telefono(value)


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


# notificar_bot_sync se movió a services.py. Se re-exporta para preservar
# el punto de entrada histórico usado por rutas admin y llamadas internas.
from services import notificar_bot_sync  # noqa: E402,F401


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


def _is_internal_request() -> bool:
    """El bot Node vive en la misma red docker que Oxidian. Los endpoints
    con datos sensibles (teléfonos de admin, PIN hash) rechazan orígenes
    externos aunque tengan la BOT_API_KEY correcta. Defensa en profundidad
    ante leak de key: un atacante remoto no puede llegar aquí porque el
    gateway nginx bloquea `/api/bot/*` con 404 público.
    """
    remote = (request.remote_addr or "").strip()
    if not remote:
        return False
    # Localhost y bucle IPv6.
    if remote in {"127.0.0.1", "::1"}:
        return True
    # Redes privadas RFC 1918 (Docker suele asignar 172.16.0.0/12 y 10.0.0.0/8).
    try:
        from ipaddress import ip_address
        addr = ip_address(remote)
        return addr.is_private or addr.is_loopback
    except Exception:
        return False


def _hmac_phone(phone: str) -> str:
    """Hash HMAC-SHA256 de un teléfono con la BOT_API_KEY como secreto.

    Uso: el bot recibe la lista de `phone_hash` de admins en /branding y
    compara con el HMAC del JID entrante. Si /branding se filtra sin la
    key, un atacante no puede revertir el hash para obtener los números
    reales — una lista de hashes es poco útil sin la key correspondiente
    (que rotaría junto con la key). Migración: el bot puede consumir
    `phone_hash` en paralelo a `telefono` hasta que la app deprecate el
    campo en claro.
    """
    key = str(_get_api_key() or "").encode("utf-8")
    if not key:
        return ""
    return hmac.new(key, str(phone or "").encode("utf-8"), "sha256").hexdigest()[:32]


def _mask_phone(phone: str) -> str:
    """Enmascara para debug UI: `+34600XXX789`."""
    p = str(phone or "")
    if len(p) < 6:
        return "***"
    return p[:4] + "X" * max(0, len(p) - 7) + p[-3:]


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
    """El chatbot de cliente nunca crea pedidos: toda compra termina en la web."""
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
            "TIENDA_URL": get_public_store_url(request.url_root),
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


# ─── AUTO-ROUTER IA ────────────────────────────────────────────
# Centraliza en el back la decisión de si un mensaje del cliente debe:
#   - dejarse pasar al flujo estándar (comando / intent conocido)  → route: noop
#   - responderse con IA sin exigir `!ia`                          → route: ai
#   - devolver el menú principal                                   → route: menu
#   - derivar a un humano (rate limit o petición explícita)        → route: handoff
#
# El bot Node solo enruta; NO decide. Así evitamos duplicar lógica de
# rate limits, patrones de comando o keywords de intent en el JS.

# Comandos “duros” del cliente (matchean SIN pasar por IA).
_BOT_HARD_COMMANDS = {
    "menu", "menú", "inicio", "hola", "start", "0",
    "estado", "cancelar", "reportar", "agente", "humano", "asesor", "persona",
    "salir",
    "1", "2", "3", "4", "5", "6", "7",
}

# Prefijos que se consideran comando aunque venga texto adicional.
_BOT_COMMAND_PREFIXES = (
    "menu", "menú", "estado", "cancelar", "reportar",
    "incidencia", "problema", "novedad", "queja",
    "agente", "humano",
)

# Palabras de arranque que sugieren pregunta en español (auto-router IA).
_BOT_QUESTION_STARTERS = (
    "como", "cómo", "que", "qué", "donde", "dónde",
    "cuando", "cuándo", "quien", "quién", "por que", "por qué",
    "porque", "cuanto", "cuánto",
    "tienen", "teneis", "tenéis", "hay",
    "puedo", "puede", "podria", "podría", "podrian", "podrían",
    "quiero saber", "quisiera",
    "me gustaria", "me gustaría",
    "necesito saber", "sabes",
)


def _strip_accents_lower(text):
    import unicodedata
    if not text:
        return ""
    normalized = unicodedata.normalize("NFD", str(text))
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn").lower().strip()


def _bot_intent_keywords():
    """Keywords que ya cubre `detectClientIntent` en bot.js.
    Reutilizamos el mapeo para NO invocar IA si el cliente pregunta algo que
    el flujo estándar ya resuelve (pedidos, puntos, cobertura, etc.).
    """
    return {
        "pedido": ("pedido", "pedir", "ordenar", "comprar", "compra"),
        "estado": ("estado", "seguimiento", "donde esta", "dónde está", "mi pedido"),
        "cancelar": ("cancelar", "anular"),
        "puntos": ("puntos", "fidelidad", "canje", "canjear"),
        "cobertura": ("cobertura", "zona", "llegan", "reparto", "envio", "envío"),
        "horario": ("horario", "abren", "cierran", "abierto", "cerrado"),
        "agente": ("agente", "humano", "persona", "asesor", "operador"),
    }


def _looks_like_question(text):
    """True si el texto parece una pregunta (>10 chars y patrón interrogativo).

    Reglas (todas modificables extendiendo _BOT_QUESTION_STARTERS):
      - Contiene '?' o '¿'.
      - Empieza por: cómo/qué/dónde/cuándo/quién/por qué/tienen/hay/puedo/quisiera/quiero saber…
    """
    raw = (text or "").strip()
    if len(raw) <= 10:
        return False
    if "?" in raw or "¿" in raw:
        return True
    norm = _strip_accents_lower(raw)
    for starter in _BOT_QUESTION_STARTERS:
        s = _strip_accents_lower(starter)
        if norm.startswith(s + " ") or norm == s:
            return True
    return False


def _matches_hard_command(text):
    norm = _strip_accents_lower(text)
    if not norm:
        return False
    if norm in _BOT_HARD_COMMANDS:
        return True
    for prefix in _BOT_COMMAND_PREFIXES:
        p = _strip_accents_lower(prefix)
        if norm == p or norm.startswith(p + " "):
            return True
    return False


def _matches_client_intent(text):
    """Espejo mínimo de `detectClientIntent` (bot.js) para decisiones back."""
    norm = _strip_accents_lower(text)
    if not norm:
        return False
    # Match numérico 1-7 → menú directo, ya cubierto como hard command.
    for _opt, keywords in _bot_intent_keywords().items():
        for kw in keywords:
            k = _strip_accents_lower(kw)
            if not k:
                continue
            # keyword corta → palabra entera; larga → substring.
            if len(k) <= 4:
                if re.search(rf"\b{re.escape(k)}\b", norm):
                    return True
            elif k in norm:
                return True
    return False


def _ai_hourly_limit_client():
    return _ai_limit("AI_MAX_MESSAGES_PER_HOUR", 20)


def _client_ai_hourly_count(phone_hash):
    since = _utcnow() - timedelta(hours=1)
    return BotAiUsage.query.filter(
        BotAiUsage.creado_en >= since,
        BotAiUsage.telefono_hash == phone_hash,
    ).count()


@api_bot_bp.route("/ai/route", methods=["POST"])
@bot_required
def ai_route():
    """Decide qué debe hacer el bot ante un mensaje del cliente.

    Payload: {"telefono": str, "mensaje": str, "force_ai": bool?}

    Respuesta:
        {"ok": true, "route": "menu"|"noop"|"ai"|"handoff",
         "reason": str, "message": str?}

    Reglas de decisión (en orden):
      1. force_ai (equivalente a `!ia` explícito) → route=ai, salta rate limit.
      2. Mensaje vacío o solo whitespace → noop.
      3. Comando duro (MENU, ESTADO, CANCELAR, …, 1-7) → menu (deja que el
         router del bot lo procese; no hay IA).
      4. Intent conocido (detectClientIntent-espejo) → noop.
      5. IA no configurada → noop.
      6. No parece pregunta (≤10 chars o sin patrón) → noop.
      7. Rate limit por cliente excedido → handoff.
      8. Todo lo demás → ai.
    """
    payload = request.get_json(silent=True) or {}
    mensaje = str(payload.get("mensaje") or "").strip()
    force_ai = bool(payload.get("force_ai") or payload.get("forzar_ia"))
    phone_hash, telefono_norm = _ai_phone_hash(payload.get("telefono"))

    if not telefono_norm or not telefono_valido(telefono_norm):
        return jsonify({"ok": False, "error": "telefono inválido"}), 400

    if not mensaje:
        return jsonify({"ok": True, "route": "noop", "reason": "empty"})

    # 1. Override manual (equivale a `!ia` de admin): salta rate limit
    #    pero exige IA configurada.
    if force_ai:
        provider = (SiteConfig.get("BOT_AI_PROVIDER", "") or "").strip().lower()
        api_key = SiteConfig.get("BOT_AI_API_KEY", "") or ""
        ai_enabled = (
            _config_bool("BOT_AI_ENABLED")
            and provider in {"openai", "groq"}
            and bool(api_key)
        )
        if not ai_enabled:
            return jsonify({
                "ok": True, "route": "noop", "reason": "ai_disabled",
                "message": "no entendí, escribe MENU para ver opciones",
            })
        return jsonify({"ok": True, "route": "ai", "reason": "force_ai"})

    # 3. Comando duro → devolvemos "menu" para que el bot procese sin IA.
    if _matches_hard_command(mensaje):
        return jsonify({"ok": True, "route": "menu", "reason": "hard_command"})

    # 4. Intent conocido → dejamos pasar al flujo estándar (no IA).
    if _matches_client_intent(mensaje):
        return jsonify({"ok": True, "route": "noop", "reason": "client_intent"})

    # 5. IA no configurada → noop + mensaje de fallback amable.
    provider = (SiteConfig.get("BOT_AI_PROVIDER", "") or "").strip().lower()
    api_key = SiteConfig.get("BOT_AI_API_KEY", "") or ""
    ai_enabled = (
        _config_bool("BOT_AI_ENABLED")
        and provider in {"openai", "groq"}
        and bool(api_key)
    )
    if not ai_enabled:
        return jsonify({
            "ok": True, "route": "noop", "reason": "ai_disabled",
            "message": "no entendí, escribe MENU para ver opciones",
        })

    # 6. No parece pregunta → noop (deja pasar el flujo estándar, que decidirá
    #    saludo/FAQ/fallback como hoy).
    if not _looks_like_question(mensaje):
        return jsonify({"ok": True, "route": "noop", "reason": "not_question"})

    # 7. Rate limit por cliente (mensajes IA en la última hora).
    if phone_hash:
        limit_hour = _ai_hourly_limit_client()
        count_hour = _client_ai_hourly_count(phone_hash)
        if count_hour >= limit_hour:
            return jsonify({
                "ok": True, "route": "handoff", "reason": "rate_limit",
                "message": (
                    "Estamos recibiendo muchas consultas ahora mismo. "
                    "Te contactamos en breve por este mismo chat."
                ),
                "count_last_hour": count_hour,
                "limit_last_hour": limit_hour,
            })

    # 8. Todo OK → IA responde.
    return jsonify({"ok": True, "route": "ai", "reason": "auto"})


@api_bot_bp.route("/security/admin-pin-hash")
@bot_required
def admin_pin_hash():
    """Sincroniza únicamente el hash; nunca expone un PIN en texto claro."""
    return jsonify({"ok": True, "hash": SiteConfig.get("BOT_ADMIN_PIN_HASH", "") or ""})


@api_bot_bp.route("/branding")
@bot_required
def branding():
    # Doble gate: además de BOT_API_KEY, el request debe venir de red
    # interna (127.0.0.1 o subred privada Docker). Si la key leakase, un
    # atacante remoto aún no podría enumerar admins por este endpoint.
    if not _is_internal_request():
        logger.warning("Rechazado /branding externo desde %s", request.remote_addr)
        return jsonify({"ok": False, "error": "Solo red interna"}), 403
    features = get_store_features()
    tienda_url = get_public_store_url(request.url_root)
    # `expose_full_phone` controla si mandamos el teléfono en claro (para
    # compat con bots antiguos que aún no consumen `phone_hash`). Default
    # `1` para no romper hoy; poner `0` cuando el bot Node migre al hash.
    expose_full = _config_bool("BRANDING_EXPOSE_FULL_PHONE", "1")
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
                "sync", "security", "emergency", "risks", "client_mode", "ai",
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
            if "reportes" in enabled:
                capabilities.append("ai")
        entry = {
            "rol": user.rol,
            "capabilities": sorted(set(capabilities)),
            # `phone_hash` es HMAC-SHA256(BOT_API_KEY, teléfono). El bot
            # calcula el mismo HMAC del JID entrante y compara — sin la
            # key, la lista de hashes es opaca para un atacante.
            "phone_hash": _hmac_phone(telefono),
            # Enmascarado para UI/logs del bot (nunca telemetría cruda).
            "phone_masked": _mask_phone(telefono),
        }
        if expose_full:
            # Deprecado: se retirará cuando el bot Node consuma phone_hash.
            entry["telefono"] = telefono
        whatsapp_roles.append(entry)
    _tipo_tienda = (SiteConfig.get("TIPO_TIENDA", "comida") or "comida").lower()
    return jsonify({
        "ok": True,
        "tienda_url": tienda_url,
        "nombre": SiteConfig.get("NOMBRE_NEGOCIO", "Mi tienda"),
        "slogan": SiteConfig.get("SLOGAN_NEGOCIO", ""),
        "descripcion": SiteConfig.get("DESCRIPCION_NEGOCIO", ""),
        "telefono": SiteConfig.get("TELEFONO_NEGOCIO", ""),
        "direccion": SiteConfig.get("DIRECCION_NEGOCIO", ""),
        "ciudad": SiteConfig.get("CIUDAD_NEGOCIO", ""),
        "tipo_tienda": _tipo_tienda,
        "es_comida": _tipo_tienda == "comida",
        "vertical_label": "Menú" if _tipo_tienda == "comida" else "Catálogo",
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
        # Límites operativos del bot admin. Antes hardcoded en bot.js
        # (1000, 9999, 10000); ahora ida-vuelta desde SiteConfig para que
        # los ajustes en /superadmin/config surtan efecto sin redeploy
        # del contenedor chat.
        "bot_max_price_eur": SiteConfig.get("BOT_MAX_PRICE_EUR", "9999"),
        "bot_max_points_adjust": SiteConfig.get("BOT_MAX_POINTS_ADJUST", "10000"),
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
    # Coherencia con feature flags globales: si el módulo está OFF, el bot
    # NO debe mostrar productos que dependen de él (evita que el cliente pida
    # algo que el checkout luego rechaza).
    try:
        features = get_store_features()
    except Exception:
        features = {
            "pedidos_programados": True,
            "puntos": True,
            "delivery": True,
            "recogida": True,
        }
    # Producto programado y feature apagada → invisible al bot.
    tipo_ent = (getattr(producto, "tipo_entrega", "") or "").lower()
    if tipo_ent in ("programado", "encargo") and not features.get("pedidos_programados", True):
        return False
    # Producto solo-canje y puntos apagados → invisible al bot.
    if getattr(producto, "solo_canje", False) and not features.get("puntos", True):
        return False
    modalidad = (getattr(producto, "modalidad_entrega", "") or "ambas").strip().lower()
    if modalidad == "delivery" and not features.get("delivery", True):
        return False
    if modalidad == "recogida" and not features.get("recogida", True):
        return False
    if modalidad == "ambas" and not (
        features.get("delivery", True) or features.get("recogida", True)
    ):
        return False
    # Filtro estricto por nicho: comida y retail son tiendas separadas.
    # Un producto solo aparece si su vertical coincide con TIPO_TIENDA.
    # Legacy `ambos` queda invisible (migración de deploy lo convierte).
    v = (getattr(producto, "vertical", None) or "").strip().lower()
    tt = (SiteConfig.get("TIPO_TIENDA", "comida") or "comida").lower()
    if v != tt:
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


def _lote_tandas_disponibles(producto):
    """Devuelve cuántas tandas quedan libres en el batch activo del producto.

    Retorna:
        * None si el producto no es un encargo por lote.
        * Entero grande (10**9) si el batch no tiene tope de tandas.
        * Entero con las tandas restantes en caso normal.
    """
    if not (producto.cantidad_por_lote and producto.fecha_llegada
            and (producto.tipo_entrega or "").lower() == "programado"):
        return None
    from models import ProductBatch
    batch = ProductBatch.query.filter_by(
        producto_id=producto.id, fecha_entrega=producto.fecha_llegada
    ).first()
    if batch is None:
        # Sin batch todavía → capacidad ilimitada hasta que llegue el 1er pedido
        return 10 ** 9
    return batch.tandas_disponibles()


def _producto_catalogo_payload(producto, incluir_diagnostico=False):
    disponible = _producto_disponible_para_bot(producto)
    option_groups = product_option_catalog_payload(producto)
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
        # Encargo por lote: cantidad por tanda + tandas disponibles del batch
        # activo (si existe). Permite al bot mostrar "4 empanadas por 5€ para
        # el día 18 — quedan 3 tandas".
        "cantidad_por_lote": int(producto.cantidad_por_lote or 0) or None,
        "lote_tandas_disponibles": _lote_tandas_disponibles(producto),
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
        "personalizaciones": option_groups,
        "requiere_sabor": any(
            group["tipo"] == "sabor" and group["min"] > 0 for group in option_groups
        ),
        "sabores": [
            option
            for group in option_groups if group["tipo"] == "sabor"
            for option in group["opciones"]
        ],
    }
    # Variantes retail (talla/color) — solo si el producto las admite y tiene ≥1 activa.
    if producto.tiene_variantes:
        payload["variantes"] = [
            {
                "id": v.id,
                "label": v.label_publico,
                "talla": v.talla,
                "color": v.color,
                "color_hex": v.color_hex,
                "sku": v.sku,
                "precio": float(v.precio_efectivo),
                "stock": int(v.stock or 0),
                "imagen_url": v.imagen_url or "",
            }
            for v in producto.variantes_activas
        ]
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

    fecha_programada = pedido.fecha_entrega_programada
    return {
        "id": pedido.id,
        "numero": pedido.numero_pedido,
        "estado": pedido.estado,
        "estado_label": {
            "pendiente": "Recibido — pendiente de preparación",
            "armando": "En preparacion",
            "listo": "Listo",
            "en_ruta": "En camino",
            "entregado": "Entregado",
            "cancelado": "Cancelado",
        }.get(pedido.estado, pedido.estado),
        "total": float(pedido.total),
        "metodo_pago": pedido.metodo_pago,
        "pago_confirmado": bool(pedido.pago_confirmado),
        "confirmacion_estado": pedido.confirmacion_estado,
        "requiere_confirmacion": pedido.confirmacion_estado == "pending",
        # El código de entrega nunca viaja al chatbot. El cliente no lo
        # necesita para consultar estado y exponerlo aquí ampliaba el alcance
        # de un secreto que solo corresponde a la entrega presencial.
        "repartidor_id": pedido.repartidor_id,
        "en_punto_encuentro": bool(getattr(pedido, "en_punto_encuentro", False)),
        "creado_en": pedido.creado_en.isoformat() if pedido.creado_en else None,
        "entregado_en": pedido.entregado_en.isoformat() if pedido.entregado_en else None,
        "es_programado": pedido.es_programado,
        "fecha_entrega": (
            fecha_programada.isoformat() if fecha_programada else None
        ),
        "mensaje_cliente": mensaje_estado_pedido(pedido),
        "bar_contacto": bar_contacto,
        "items": [
            {
                "nombre": oi.display_nombre,
                "cantidad": oi.cantidad,
                "precio_unit": float(oi.precio_unit),
                "subtotal": float(oi.subtotal),
                "notas": oi.notas or "",
                "tipo_entrega": oi.display_tipo_entrega,
                "fecha_entrega": (
                    oi.display_fecha_entrega.isoformat()
                    if oi.display_fecha_entrega else None
                ),
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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


@api_bot_bp.route("/producto/<int:producto_id>")
@bot_required
def detalle_producto(producto_id):
    from werkzeug.exceptions import HTTPException
    try:
        p = get_or_404(Product, producto_id)
        if not _producto_disponible_para_bot(p):
            return jsonify({"ok": False, "error": "Producto no disponible"}), 404
        return jsonify({
            "ok": True,
            "producto": _producto_catalogo_payload(p)
        })
    except HTTPException as http_exc:
        # get_or_404 lanza NotFound (404). Retornar como JSON coherente en vez
        # de que el except Exception genérico lo capture y devuelva 500.
        code = http_exc.code or 500
        msg = "No encontrado" if code == 404 else (http_exc.description or "Error HTTP")
        return jsonify({"ok": False, "error": msg}), code
    except Exception as e:
        logger.exception("detalle_producto pid=%s falló", producto_id)
        return jsonify({"ok": False, "error": "Error interno consultando producto"}), 500


@api_bot_bp.route("/cliente/buscar-producto")
@bot_required
def cliente_buscar_producto():
    """Búsqueda accent-insensitive para preguntas del cliente por WhatsApp.

    Casos: "¿hay pizza?", "¿cuánto vale la margarita?", "tenéis coca cola?".
    Devuelve top N productos que matchean por nombre o descripción, con precio,
    disponibilidad y URL directa a la web para pedir.
    Query params: `q` (texto libre), `limit` (default 5, max 10).
    """
    import unicodedata

    def _strip_accents(s: str) -> str:
        if not s:
            return ""
        return "".join(
            c for c in unicodedata.normalize("NFD", s)
            if unicodedata.category(c) != "Mn"
        ).lower()

    q_raw = (request.args.get("q") or "").strip()
    if not q_raw:
        return jsonify({"ok": False, "error": "Falta parámetro q"}), 400
    try:
        limit = min(max(1, int(request.args.get("limit", 5))), 10)
    except (TypeError, ValueError):
        limit = 5
    q_norm = _strip_accents(q_raw)
    tokens = [t for t in q_norm.split() if len(t) >= 2]
    if not tokens:
        return jsonify({"ok": True, "resultados": [], "consulta": q_raw})

    productos = _catalogo_unificado_para_bot()
    resultados = []
    for p in productos:
        if not _producto_disponible_para_bot(p):
            continue
        nombre_norm = _strip_accents(p.nombre or "")
        desc_norm = _strip_accents(p.descripcion or "")
        # score: token en nombre vale 3, en descripción vale 1
        score = 0
        for t in tokens:
            if t in nombre_norm:
                score += 3
            elif t in desc_norm:
                score += 1
        if score > 0:
            resultados.append((score, p))
    resultados.sort(key=lambda x: (-x[0], x[1].nombre or ""))
    resultados = resultados[:limit]
    tienda_url = get_public_store_url(request.url_root)
    return jsonify({
        "ok": True,
        "consulta": q_raw,
        "count": len(resultados),
        "tienda_url": tienda_url,
        "resultados": [
            {
                "id": p.id,
                "nombre": p.nombre,
                "precio": float(p.precio_final or 0),
                "es_combo": bool(p.es_combo),
                # Canje con puntos: los productos solo_canje NO tienen precio
                # en euros; el cliente los obtiene entregando puntos.
                "solo_canje": bool(getattr(p, "solo_canje", False)),
                "canjeable_con_puntos": bool(getattr(p, "canjeable_con_puntos", False)),
                "puntos_para_canje": int(getattr(p, "puntos_para_canje", 0) or 0),
                "descripcion": (p.descripcion or "")[:140],
                "url": f"{tienda_url.rstrip('/')}/producto/{p.id}" if tienda_url else f"/producto/{p.id}",
                "score": score,
            }
            for score, p in resultados
        ],
    })


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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


@api_bot_bp.route("/cliente/registrar", methods=["POST"])
@bot_required
def registrar_cliente():
    from sqlalchemy.exc import IntegrityError
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
        email = internal_customer_email(telefono)
        if User.query.filter_by(email=email).first():
            email = internal_customer_email(telefono, uuid.uuid4().hex[:6])
        cliente = User(
            nombre=nombre,
            email=email,
            telefono=telefono,
            rol="cliente",
            activo=True,
        )
        cliente.set_password(str(uuid.uuid4()))
        db.session.add(cliente)
        try:
            db.session.commit()
        except IntegrityError:
            # Race condition: otro request registró el mismo teléfono/email
            # entre nuestro check y el commit. Recuperamos el cliente que ganó
            # y lo devolvemos como si fuera el nuestro — el bot es idempotente.
            db.session.rollback()
            existente_race, _ = _cliente_por_telefono(telefono)
            if existente_race:
                return jsonify({
                    "ok": True,
                    "cliente_id": existente_race.id,
                    "cliente": {
                        "id": existente_race.id,
                        "nombre": existente_race.nombre,
                        "puntos": existente_race.puntos,
                    }
                })
            # Ni siquiera existía tras el race → error de otra restricción
            logger.exception("registrar_cliente IntegrityError sin cliente existente tel=%s", _mask_phone(telefono))
            return jsonify({"ok": False, "error": "No se pudo registrar el cliente. Intenta de nuevo."}), 409
        return jsonify({
            "ok": True,
            "cliente_id": cliente.id,
            "cliente": {"id": cliente.id, "nombre": cliente.nombre, "puntos": 0}
        })
    except Exception:
        db.session.rollback()
        logger.exception("registrar_cliente falló")
        return jsonify({"ok": False, "error": "Error interno registrando cliente"}), 500


# ─── PUNTOS ──────────────────────────────────

@api_bot_bp.route("/puntos")
@bot_required
def consultar_puntos():
    try:
        if not get_store_features().get("puntos", True):
            return jsonify({
                "ok": False,
                "error": "El club de puntos no está habilitado.",
                "code": "FEATURE_DISABLED",
                "puntos": 0,
                "valor_euro": 0,
            }), 403
        cliente, _telefono = _cliente_por_telefono(request.args.get("telefono", ""))
        if not cliente:
            return jsonify({
                "ok": True,
                "existe": False,
                "puntos": 0,
                "valor_euro": 0,
            })
        ratio = get_puntos_config()["ratio"]
        # Consultar el saldo es una operación de lectura. El OTP se emite
        # exclusivamente al iniciar un canje desde checkout y nunca se
        # devuelve el secreto dentro de una respuesta API.
        return jsonify({
            "ok": True,
            "existe": True,
            "nombre": cliente.nombre or "",
            "puntos": cliente.puntos,
            "valor_euro": round(cliente.puntos / ratio, 2),
            "ratio": ratio,
        })
    except Exception as e:
        # Log el error real con traceback para debugging server-side,
        # pero devuelve mensaje genérico al bot (no leakea internals).
        import traceback
        current_app.logger.error(
            "consultar_puntos fallo — %s\n%s", e, traceback.format_exc()
        )
        return jsonify({"ok": False, "error": "Error interno consultando puntos"}), 500


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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


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
        # caemos a una key automática que agrupa POSTs idénticos del MISMO
        # teléfono en una ventana corta. Incluir el teléfono en el auto_seed
        # evita colisiones cuando dos clientes hacen pedidos idénticos por casualidad
        # dentro de la misma ventana (todos los requests del bot vienen del mismo IP).
        _peek = request.get_json(silent=True) or {}
        _tel_seed = (_peek.get("telefono_cliente") or _peek.get("telefono") or "").strip()
        idem_key = request_idempotency_key(
            "bot",
            auto_seed=f"{request.remote_addr or 'bot'}|{_tel_seed}",
        )
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
            email = internal_customer_email(telefono)
            existing_email = User.query.filter_by(email=email).first()
            if existing_email:
                email = internal_customer_email(telefono, uuid.uuid4().hex[:4])
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
            fecha_ent = None
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
                fecha_ent = p.fecha_llegada
                if not fecha_ent:
                    return jsonify({
                        "ok": False,
                        "code": "SCHEDULED_DATE_MISSING",
                        "error": f"'{p.nombre}' todavía no tiene una fecha de entrega definida.",
                    }), 409
                if fecha_ent < date.today():
                    return jsonify({
                        "ok": False,
                        "code": "SCHEDULED_DATE_EXPIRED",
                        "error": f"La fecha de entrega de '{p.nombre}' ya venció.",
                    }), 409
                # La fecha pertenece al producto/reserva y no es un dato libre
                # del consumidor. Se acepta en el payload por compatibilidad,
                # pero nunca puede sobrescribir la fecha canónica del catálogo.
                fecha_str = item_d.get("fecha_entrega") or data.get("fecha_entrega")
                if fecha_str:
                    try:
                        solicitada = datetime.fromisoformat(str(fecha_str)).date()
                    except (ValueError, TypeError):
                        return jsonify({
                            "ok": False,
                            "code": "SCHEDULED_DATE_INVALID",
                            "error": "fecha_entrega inválida (usa YYYY-MM-DD)",
                        }), 400
                    if solicitada != fecha_ent:
                        return jsonify({
                            "ok": False,
                            "code": "SCHEDULED_DATE_MISMATCH",
                            "error": (
                                f"'{p.nombre}' está reservado para "
                                f"{fecha_ent.strftime('%d/%m/%Y')}."
                            ),
                        }), 409
            combo_item_ids = item_d.get("combo_item_ids") or []
            if p.es_combo:
                try:
                    p.validar_stock_combo_seleccion(cantidad, combo_item_ids)
                except ValueError as exc:
                    return jsonify({"ok": False, "error": str(exc)}), 400
            raw_product_options = item_d.get("opciones_producto") or {}
            if not isinstance(raw_product_options, dict):
                return jsonify({"ok": False, "error": "opciones_producto debe ser un objeto"}), 400
            raw_product_options = dict(raw_product_options)
            if item_d.get("sabor_id") not in (None, ""):
                raw_product_options[str(item_d.get("sabor_id"))] = 1
            option_selection, option_rows, option_total, option_error = (
                validate_product_option_selection(p, raw_product_options)
            )
            if option_error:
                return jsonify({
                    "ok": False,
                    "code": "PRODUCT_OPTION_REQUIRED",
                    "error": f"{p.nombre}: {option_error}",
                    "personalizaciones": product_option_catalog_payload(p),
                }), 400
            precio_unit = (
                float(p.precio_combo_para_seleccion(combo_item_ids))
                if p.es_combo else float(p.precio_final)
            ) + option_total
            item_total = round(precio_unit * cantidad, 2)
            subtotal += item_total
            items_procesados.append({"producto": p, "cantidad": cantidad, "subtotal": item_total,
                                     "precio_unit": precio_unit,
                                     "combo_item_ids": combo_item_ids,
                                     "option_selection": option_selection,
                                     "option_rows": option_rows,
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
        fechas_programadas = {
            item["fecha_entrega"] for item in items_procesados if item.get("fecha_entrega")
        }
        if len(fechas_programadas) > 1:
            return jsonify({
                "ok": False,
                "code": "MIXED_SCHEDULED_DATES",
                "error": "Los productos tienen fechas distintas. Crea un pedido para cada fecha de entrega.",
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
                afiliado_obj = (
                    AffiliateCode.query
                    .filter_by(codigo=cupon_codigo)
                    .with_for_update()
                    .first()
                )
                if afiliado_obj:
                    ok_a, msg_a = afiliado_obj.es_valido_para_cliente(cliente.id)
                    if not ok_a:
                        return jsonify({"ok": False, "error": msg_a}), 400
                else:
                    return jsonify({"ok": False, "error": "Código no válido"}), 400

        # La cobertura calculada por el servidor es autoritativa. El bot puede
        # enviar una zona como dato de interfaz, pero nunca imponer una zona
        # distinta a la que contiene realmente la dirección.
        zona = db.session.get(ZonaEntrega, geo.get("zona_id")) if geo.get("zona_id") else None
        es_entrega_epicentro = True
        tiempo_estimado = 30
        if zona is not None and not zona.activo:
            zona = None
        if zona is None and zona_id:
            candidata = db.session.get(ZonaEntrega, zona_id)
            if candidata and candidata.activo and not any(
                z.tiene_geo for z in ZonaEntrega.query.filter_by(activo=True).all()
            ):
                zona = candidata
        if zona is None:
            zonas_activas = ZonaEntrega.query.filter_by(activo=True)\
                .order_by(ZonaEntrega.orden, ZonaEntrega.nombre).all()
            if geo.get("validacion_desactivada") or not any(z.tiene_geo for z in zonas_activas):
                zona = zonas_activas[0] if zonas_activas else None
        if zona is None:
            return jsonify({
                "ok": False,
                "error": "No pudimos asociar la dirección a una zona de entrega válida.",
            }), 422
        if zona:
            es_entrega_epicentro = bool(zona.es_epicentro)
            tiempo_estimado = zona.tiempo_estimado_min

        # ── Motor de pricing unificado (mismas reglas que web) ──
        cliente = bloquear_cliente_puntos(cliente)
        puntos_cfg = get_puntos_config()
        ratio = puntos_cfg["ratio"]
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
        puntos_ganados = calcular_puntos_ganados(total)
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
        aplicar_snapshot_zona_pedido(pedido, zona, costo_envio)
        db.session.add(pedido)
        db.session.flush()
        registrar_pedido_creado(
            pedido,
            canal="bot",
            detalle="pedido creado por API bot compat",
            metadata={
                "telefono": telefono,
                "zona_id": zona.id if zona else None,
                "zona_nombre": pedido.zona_nombre_snapshot,
                "costo_envio": pedido.costo_envio_aplicado,
            },
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
            flavor_rows = [row for row in item.get("option_rows", []) if row.get("tipo") == "sabor"]
            extra_rows = [row for row in item.get("option_rows", []) if row.get("tipo") != "sabor"]
            if flavor_rows:
                item_metadata["sabores"] = {"opciones": flavor_rows}
            if extra_rows:
                item_metadata["extras"] = {
                    "total_unitario": round(sum(row.get("subtotal", 0) for row in extra_rows), 2),
                    "opciones": extra_rows,
                }
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
        if pedido.confirmacion_estado != "pending":
            encolar_notificaciones_proveedores_pedido(pedido)
            distribuir_pedido(pedido)

        if afiliado_obj:
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


def _operador_bar_por_telefono(telefono_raw):
    """Localiza el bar activo cuyo teléfono operador coincide con `telefono_raw`.

    Tolera formatos `+34...` vs `34...` (compara por dígitos). Solo activo cuando
    la tienda opera en modo `bar_servicio`; en modo `propia` se mantiene el
    menú del bar del bot desactivado (single-tenant).

    Retorna (bar, bar) — devuelve el mismo Proveedor dos veces para conservar la
    firma tupla histórica (operador, bar). El "operador" no es un `User` sino el
    número que respondió; el bar concentra ambas identidades.
    """
    if not telefono_raw:
        return None, None
    modo = (SiteConfig.get("MODO_TIENDA", "propia") or "propia").strip().lower()
    if modo != "bar_servicio":
        return None, None
    digits = solo_digitos(telefono_raw)
    if not digits:
        return None, None
    # Lista pequeña (bares activos); comparación en Python evita depender de
    # normalización SQL (portable a SQLite en tests).
    candidatos = Proveedor.query.filter(
        Proveedor.activo.is_(True),
        Proveedor.telefono.isnot(None),
    ).all()
    coincidencias = [bar for bar in candidatos if solo_digitos(bar.telefono) == digits]
    if len(coincidencias) > 1:
        # Estado inconsistente: dos bares activos comparten el mismo teléfono
        # operador. Cualquier decisión aquí es arbitraria y puede autorizar
        # acciones sobre pedidos del bar equivocado. Fallamos cerrado (sin
        # autorizar) y alertamos para que admin corrija el duplicado.
        current_app.logger.critical(
            "Multiple bares activos comparten telefono operador: ids=%s",
            [b.id for b in coincidencias],
        )
        return None, None
    if coincidencias:
        return coincidencias[0], coincidencias[0]
    return None, None


# Alias local por compat semántica con el handoff a agente humano.
ESTADOS_PEDIDO_ACTIVO_HANDOFF = ESTADOS_ACTIVOS


def _telefonos_usuarios_handoff(query):
    telefonos = []
    vistos = set()
    for usuario in query.order_by(User.id.asc()).all():
        telefono = normalizar_telefono_cliente(usuario.telefono_normalizado or usuario.telefono)
        if telefono and telefono_valido(telefono) and telefono not in vistos:
            vistos.add(telefono)
            telefonos.append("".join(c for c in telefono if c.isdigit()))
    return telefonos


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
    """Determina si el remitente es operador de un bar activo.

    En modo `propia` (single-tenant) el menú bar del bot está desactivado por
    diseño → siempre `es_bar: False`. En `bar_servicio` matchea por teléfono
    contra `Proveedor.telefono` activos.
    """
    telefono = (request.args.get("telefono") or "").strip()
    bar, _ = _operador_bar_por_telefono(telefono)
    if not bar:
        return jsonify({"ok": True, "es_bar": False, "bar": None})
    return jsonify({
        "ok": True,
        "es_bar": True,
        "bar": {
            "id": bar.id,
            "nombre": bar.nombre,
            "modelo_acuerdo": getattr(bar, "modelo_acuerdo", None),
        },
    })


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

    pedido = (
        Order.query.filter_by(id=pedido_id).with_for_update().first()
    )
    if pedido is None:
        return jsonify({"ok": False, "error": "Pedido no encontrado"}), 404
    if pedido.estado == "cancelado":
        return jsonify({"ok": False, "error": "El pedido fue cancelado"}), 409
    if pedido.confirmacion_estado == "pending":
        return jsonify({
            "ok": False,
            "error": "El cliente todavía no confirmó su primer pedido por WhatsApp",
            "code": "CONFIRMACION_PENDIENTE",
        }), 409
    if pedido.estado not in ESTADOS_EN_PREPARACION + ("listo",):
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
    repartidor_asignado = None
    if (not pedido.proveedores_pendientes
            and es_pedido_solo_bar(pedido)
            and pedido.estado in ESTADOS_EN_PREPARACION):
        pedido.estado = "listo"
        try:
            repartidor_asignado = distribuir_repartidor(pedido)
        except Exception:
            current_app.logger.exception(
                "bar_marcar_preparado: distribuir_repartidor falló pedido=%s bar=%s",
                pedido.id, bar.id,
            )
        if not repartidor_asignado:
            current_app.logger.warning(
                "bar_marcar_preparado: pedido %s avanzó a listo SIN repartidor "
                "(nadie disponible). Quedará en cola de espera.",
                pedido.numero_pedido,
            )
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
    except _HTTPExc:
        raise
    except Exception:
        current_app.logger.exception("estado_pedido: fallo pedido=%s", pedido_id)
        return jsonify({"ok": False, "error": "No se pudo consultar el pedido"}), 500


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
            },
        )
        db.session.commit()
        return jsonify({
            "ok": True,
            "pedido": pedido.numero_pedido,
            "mensaje": "Incidencia registrada — el equipo responsable la recibirá.",
        })
    except _HTTPExc:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        current_app.logger.exception("reportar_incidencia: fallo pedido=%s", pedido_id)
        return jsonify({"ok": False, "error": "No se pudo registrar la incidencia"}), 500


@api_bot_bp.route("/pedido/estado")
@bot_required
def estado_pedido_buscar():
    """Busca estado de pedido. Las consultas cliente siempre requieren teléfono."""
    try:
        pedido_id = request.args.get("pedido_id", type=int)
        numero = (request.args.get("numero") or request.args.get("numero_pedido") or "").strip()
        telefono = normalizar_telefono_cliente(request.args.get("telefono"))
        if (pedido_id or numero) and not telefono_valido(telefono):
            return jsonify({
                "ok": False,
                "error": "Indica el teléfono del cliente para consultar ese pedido",
                "code": "TELEFONO_REQUERIDO",
            }), 400

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
            # El teléfono puede pertenecer a una cuenta operativa que también
            # compra. buscar_cliente_por_telefono ya resuelve esa identidad
            # dual; volver a exigir rol='cliente' hacía invisible su pedido.
            cliente, telefono = _cliente_por_telefono(telefono)
            if not cliente:
                return jsonify({"ok": False, "error": "Cliente no encontrado"}), 404
            query = query.filter(Order.cliente_id == cliente.id)

        pedido = query.first()
        if not pedido:
            return jsonify({"ok": False, "error": "Pedido no encontrado"}), 404
        return jsonify({"ok": True, "pedido": _pedido_bot_payload(pedido)})
    except Exception:
        current_app.logger.exception("estado_pedido_buscar: fallo de consulta")
        return jsonify({"ok": False, "error": "No se pudo consultar el pedido"}), 500


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


@api_bot_bp.route("/confirmacion/responder", methods=["POST"])
@bot_required
def responder_confirmacion_pedido():
    """El bot delega la resolución de la respuesta del cliente en Oxidian.

    Body:
      { "telefono": "+34...", "respuesta": "si" | "no" }

    El bot detecta el reply pero NO conoce qué pedido está en confirmación —
    Oxidian es el único que sabe. Aquí buscamos el pedido `pending` más
    reciente del cliente y aplicamos:
      - "si" / "confirmo" / "confirmar" → confirmed.
      - "no" / "cancelar" / "cancelo"    → cancelacion vía flujo estándar.

    Devuelve un dict con:
      - `accion`: `"confirmado"` | `"cancelado"` | `"sin_pendiente"` | `"respuesta_invalida"`
      - `numero` (opcional): número de pedido afectado.
      - `mensaje`: texto listo para reenviar al cliente.

    Diseñado para ser idempotente: si el cliente responde SI dos veces,
    la segunda devuelve `sin_pendiente` con un mensaje amable.
    """
    from services import marcar_pedido_confirmado, cancelar_pedido_operativo

    data = request.get_json(silent=True) or {}
    cliente, telefono = _cliente_por_telefono(data.get("telefono"))
    if not cliente or not telefono_valido(telefono):
        return jsonify({"ok": False, "error": "Cliente no encontrado"}), 404

    raw = str(data.get("respuesta") or "").strip().lower()
    palabras_si = {"si", "sí", "s", "ok", "vale", "confirmo", "confirmar", "confirmado"}
    palabras_no = {"no", "n", "cancelo", "cancelar", "cancelado", "anular"}
    if raw in palabras_si:
        accion = "confirmar"
    elif raw in palabras_no:
        accion = "cancelar"
    else:
        return jsonify({
            "ok": True,
            "accion": "respuesta_invalida",
            "mensaje": (
                "No entendí tu respuesta. Contesta *SI* para confirmar el pedido "
                "o *NO* para cancelarlo."
            ),
        })

    pedido = (
        Order.query
        .filter_by(cliente_id=cliente.id, confirmacion_estado="pending")
        # Un pedido pendiente de verificación nunca debería estar armando. Si
        # hay un dato legacy inconsistente, no lo confirmamos silenciosamente:
        # debe revisarlo un agente antes de seguir procesándolo.
        .filter(Order.estado == "pendiente")
        .order_by(Order.creado_en.desc())
        .with_for_update()
        .first()
    )
    if not pedido:
        return jsonify({
            "ok": True,
            "accion": "sin_pendiente",
            "mensaje": (
                "No tienes ningún pedido pendiente de confirmación ahora mismo. "
                "Si necesitas algo escribe *MENU*."
            ),
        })

    if accion == "confirmar":
        try:
            marcar_pedido_confirmado(pedido)
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "responder_confirmacion: confirmar falló pedido=%s", pedido.id
            )
            return jsonify({"ok": False, "error": "No se pudo confirmar el pedido"}), 500
        return jsonify({
            "ok": True,
            "accion": "confirmado",
            "numero": pedido.numero_pedido,
            "mensaje": (
                f"✅ *Pedido {pedido.numero_pedido} confirmado.*\n\n"
                "El equipo ya puede empezar a prepararlo. Te aviso cuando esté listo."
            ),
        })

    # accion == "cancelar"
    if pedido.estado != "pendiente":
        return jsonify({
            "ok": True,
            "accion": "sin_pendiente",
            "mensaje": (
                f"El pedido *{pedido.numero_pedido}* ya entró en preparación. "
                "Escribe *AGENTE* si necesitas ayuda."
            ),
        })
    try:
        cancelar_pedido_operativo(
            pedido,
            canal="chatbot",
            detalle="cliente respondió NO a la verificación pasiva",
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "responder_confirmacion: cancelar falló pedido=%s", pedido.id
        )
        return jsonify({"ok": False, "error": "No se pudo cancelar el pedido"}), 500
    return jsonify({
        "ok": True,
        "accion": "cancelado",
        "numero": pedido.numero_pedido,
        "mensaje": (
            f"❌ *Pedido {pedido.numero_pedido} cancelado.*\n\n"
            "Si fue un error escríbenos, lo resolvemos rápido."
        ),
    })


@api_bot_bp.route("/pedido/<int:pedido_id>/confirmar", methods=["POST"])
@bot_required
def confirmar_pedido_cliente(pedido_id):
    """El bot llama este endpoint cuando el cliente responde SI a la
    verificación pasiva antifraude enviada al crear pedidos de riesgo.

    Idempotente: pedido ya `confirmed` devuelve 200 con `ya_confirmado=True`.
    Pedido sin verificación pendiente devuelve 409 explicativo. Cliente
    no dueño del pedido devuelve 404 para no filtrar existencia.
    """
    from services import marcar_pedido_confirmado

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
    if pedido.confirmacion_estado == "confirmed":
        return jsonify({
            "ok": True,
            "ya_confirmado": True,
            "numero": pedido.numero_pedido,
        })
    if pedido.confirmacion_estado != "pending":
        return jsonify({
            "ok": False,
            "error": "Este pedido no está pendiente de confirmación.",
            "estado_confirmacion": pedido.confirmacion_estado,
        }), 409

    try:
        marcar_pedido_confirmado(pedido)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "confirmar_pedido_cliente falló pedido=%s", pedido_id
        )
        return jsonify({"ok": False, "error": "No se pudo confirmar el pedido"}), 500

    return jsonify({
        "ok": True,
        "numero": pedido.numero_pedido,
        "mensaje": f"Pedido {pedido.numero_pedido} confirmado. Empezaremos a prepararlo.",
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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


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
        current_app.logger.exception("No se pudo encolar el broadcast")
        return jsonify({"ok": False, "error": "No se pudo encolar el envío"}), 500


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
                    "personalizaciones":     (option_groups := product_option_catalog_payload(p)),
                    "requiere_sabor":        any(
                        group["tipo"] == "sabor" and group["min"] > 0
                        for group in option_groups
                    ),
                    "sabores": [
                        option
                        for group in option_groups
                        if group["tipo"] == "sabor"
                        for option in group["opciones"]
                    ],
                }
                for p in visibles
            ]
        })
    except Exception as e:
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


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

        try:
            calificacion = int(calificacion)
        except (TypeError, ValueError):
            calificacion = 0
        if not (1 <= calificacion <= 5):
            return jsonify({"ok": False, "error": "calificacion debe ser 1-5"}), 400
        if not telefono_valido(normalizar_telefono_cliente(telefono)):
            return jsonify({"ok": False, "error": "telefono requerido"}), 400

        pedido = get_or_404(Order, pedido_id)

        # Verificar que el pedido fue entregado
        if pedido.estado != "entregado":
            return jsonify({"ok": False, "error": "Solo se pueden reseñar pedidos entregados"}), 400

        # El teléfono es obligatorio: el ID del pedido por sí solo nunca
        # autoriza a crear o modificar una reseña de otro cliente.
        from services import buscar_cliente_por_telefono
        cliente_match, _ = buscar_cliente_por_telefono(telefono)
        if not cliente_match or pedido.cliente_id != cliente_match.id:
            return jsonify({"ok": False, "error": "Este pedido no pertenece al cliente"}), 403
        pedido.resena_calificacion = calificacion
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
                calificacion = calificacion,
                comentario   = comentario,
                aprobada     = False,  # pendiente de moderación del admin
            )
            db.session.add(nueva_review)
        elif review_existente:
            review_existente.calificacion = calificacion
            review_existente.comentario   = comentario
            review_existente.aprobada     = False  # re-moderar si cambia

        db.session.commit()
        current_app.logger.info(f"Reseña WhatsApp guardada: pedido={pedido_id} rating={calificacion}")
        return jsonify({"ok": True, "pedido_id": pedido_id, "calificacion": calificacion})
    except _HTTPExc:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        current_app.logger.exception("api_bot.guardar_resena: fallo pedido=%s", pedido_id)
        return jsonify({"ok": False, "error": "No se pudo guardar la reseña"}), 500


# ─── HISTORIAL PEDIDOS CLIENTE ───────────────

@api_bot_bp.route("/pedidos")
@bot_required
@(limiter.limit("30 per minute", key_func=lambda: (request.args.get("telefono") or "anon"))
  if limiter is not None else (lambda f: f))
def pedidos_cliente():
    try:
        cliente, _telefono = _cliente_por_telefono(request.args.get("telefono", ""))
        limit = max(1, min(request.args.get("limit", 5, type=int) or 5, 50))
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
    except Exception:
        current_app.logger.exception("pedidos_cliente: fallo de consulta")
        return jsonify({"ok": False, "error": "No se pudieron consultar los pedidos"}), 500


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
        metodo = resultado.get("metodo_cobertura")
        return jsonify({
            "ok": bool(resultado.get("ok")),
            "cobertura": resultado,
            "validacion_activa": _config_bool("VALIDAR_RADIO_ENTREGA", "1"),
            "bloqueo_no_verificada": _config_bool("BLOQUEAR_DIRECCION_NO_VERIFICADA", "1"),
            # Compatibilidad con clientes antiguos. Las interfaces nuevas solo
            # enseñan el radio si fue el método realmente utilizado.
            "radio_km": SiteConfig.get("RADIO_ENTREGA_KM", "5"),
            "metodo_cobertura": metodo,
            "ciudad": SiteConfig.get("CIUDAD_NEGOCIO", ""),
        })
    except Exception as e:
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


# ─── FLUJO DE MENÚ DEL BOT (script paso a paso) ──────────────────────────────

@api_bot_bp.route("/asistente")
@bot_required
def asistente_bot():
    """Contrato compacto para el bot: opciones, endpoints y textos base."""
    try:
        nombre = SiteConfig.get("NOMBRE_NEGOCIO", "Mi tienda")
        tienda_url = get_public_store_url(request.url_root)
        telefono = SiteConfig.get("TELEFONO_NEGOCIO", "")
        features = get_store_features()
        menu = [
            {"key": "1", "label": "Ver menu y combos en la web", "endpoint": "GET /api/bot/catalogo/completo"},
            {"key": "2", "label": "Estado de pedido", "endpoint": "GET /api/bot/pedido/estado"},
        ]
        if features["puntos"]:
            menu.append({"key": "3", "label": "Puntos", "endpoint": "GET /api/bot/puntos?telefono="})
        if features["delivery"]:
            menu.append({"key": "4", "label": "Cobertura delivery", "endpoint": "GET /api/bot/cobertura?direccion="})
        menu.extend([
            {"key": "5", "label": "Abrir tienda online", "endpoint": None, "url": tienda_url},
            {"key": "6", "label": "Horarios y contacto", "endpoint": "GET /api/bot/negocio"},
            {"key": "7", "label": "Hablar con agente", "action": "handoff"},
        ])
        return jsonify({
            "ok": True,
            "negocio": {
                "nombre": nombre,
                "telefono": telefono,
                "tienda_url": tienda_url,
                "delivery_enabled": features["delivery"],
                "pickup_enabled": features["recogida"],
                "points_enabled": features["puntos"],
                "order_create_enabled": False,
                "horario": {
                    "apertura": SiteConfig.get("HORARIO_APERTURA", "09:00"),
                    "cierre": SiteConfig.get("HORARIO_CIERRE", "22:30"),
                },
            },
            "menu": menu,
            "reglas": {
                "identificar_cliente": "Usa telefono como identificador principal.",
                "estado_pedido": "Consulta por numero_pedido y telefono cuando el cliente lo tenga; si no, por telefono para traer el ultimo pedido.",
                "pedido": "No crees pedidos por chatbot. Toda compra se finaliza directamente en tienda_url.",
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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


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
        tipo_tienda = (SiteConfig.get("TIPO_TIENDA", "comida") or "comida").lower()
        features = get_store_features()
        es_comida = (tipo_tienda == "comida")
        catalogo_label = "menú" if es_comida else "catálogo"
        catalogo_emoji = "🍽️" if es_comida else "🛍️"
        preparando_emoji = "👨‍🍳" if es_comida else "📦"
        puntos_on = bool(features.get("puntos"))
        delivery_on = bool(features.get("delivery"))

        menu_opciones = [
            {"key": "1", "emoji": catalogo_emoji, "label": f"Ver el {catalogo_label}", "action": "menu_catalogo"},
            {"key": "2", "emoji": "🔥", "label": "Promociones activas", "action": "promociones"},
            {"key": "3", "emoji": "🎟️", "label": "Mis cupones", "action": "cupones"},
        ]
        if puntos_on:
            menu_opciones.append({"key": "4", "emoji": "⭐", "label": "Consultar mis puntos", "action": "puntos_consulta"})
        if delivery_on:
            menu_opciones.append({"key": "5", "emoji": "📍", "label": "Ver cobertura de entrega", "action": "cobertura"})
        menu_opciones.extend([
            {"key": "6", "emoji": "🛒", "label": "Abrir tienda online", "action": "abrir_tienda"},
            {"key": "7", "emoji": "📦", "label": "Estado de mi pedido", "action": "estado_pedido"},
            {"key": "8", "emoji": "👨‍💼", "label": "Hablar con un agente", "action": "agente"},
        ])
        flujo_puntos = {
            "habilitado": puntos_on,
            "paso_1": {
                "mensaje": (
                    "⭐ *Sistema de puntos*\n\n¿Cuál es tu número de WhatsApp?\n"
                    "_(es tu identificación; no necesitas registrarte)_"
                    if puntos_on
                    else "El club de puntos no está disponible ahora mismo."
                ),
                "accion": "pedir_telefono" if puntos_on else "volver_menu",
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
        }
        # El mensaje operativo real viene de ``mensaje_estado_pedido`` y solo
        # muestra un saldo positivo cuando la consulta/canje está habilitada.
        # Esta plantilla de ayuda se mantiene neutra para que ningún consumidor
        # secundario pueda interpolar accidentalmente "0 puntos".
        entregado_msg = "🎉 ¡Pedido *{num}* entregado! Gracias parce 💛"
        flujo_puntos_instrucciones = [
            "1. El módulo de puntos está apagado; no ofrecer consulta ni canje."
        ]
        if puntos_on:
            flujo_puntos_instrucciones = [
                "1. Usar el teléfono del propio chat como identidad",
                "2. GET /api/bot/puntos?telefono=X → informar saldo",
                "3. GET /api/bot/puntos/productos-canjeables?telefono=X → informar opciones",
                "4. Enviar al cliente a la tienda web para realizar el pedido y aplicar el canje",
            ]

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
            "camisa": "👕", "camiseta": "👕", "polo": "👕",
            "pantalon": "👖", "vaquero": "👖", "jean": "👖",
            "zapato": "👟", "calzado": "👟", "zapatilla": "👟",
            "accesor": "🎒", "bolso": "👜", "mochila": "🎒",
            "chaqueta": "🧥", "abrigo": "🧥",
        }
        for i, cat in enumerate(categorias, 1):
            emoji = catalogo_emoji
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
        cats_menu.append({"key": str(len(cats_menu) + 1), "emoji": "📋", "label": f"Ver todo el {catalogo_label}", "categoria_id": None})

        return jsonify({
            "ok": True,
            "negocio": nombre,
            "horario": f"{horario_ap} – {horario_ci}",
            "telefono_agente": telefono_negocio,

            # ── MENÚ PRINCIPAL ──
            "menu_principal": {
                "bienvenida": f"¡Hola parce! 🥟 Bienvenido a *{nombre}*\n\n¿En qué te ayudo hoy?",
                "opciones": menu_opciones,
            },

            # ── MENÚ CATÁLOGO (por categoría) ──
            "menu_catalogo": {
                "pregunta": ("¿Qué se te antoja hoy? 🤤\nElige una categoría:" if es_comida
                             else "¿Qué buscas hoy? 🛍️\nElige una categoría:"),
                "categorias": cats_menu,
                "nota": "Escribe el número o nombre de la categoría"
            },

            # ── FLUJO DE PUNTOS ──
            "flujo_puntos": flujo_puntos,

            # ── FLUJO DE PEDIDO ──
            "flujo_pedido": {
                "paso_1": {
                    "mensaje": "🛒 *Tienda online*\n\nLas compras se realizan únicamente en la web para validar stock, combos, horarios, módulos activos y pago.",
                    "accion": "abrir_tienda",
                },
                "paso_2": {
                    "mensaje": "¡Hola *{nombre}*! 👋\n\nAbre la tienda web aquí:\n🌐 {tienda_url}\n\nPor WhatsApp puedo ayudarte con estado, horario, información general o pasarte con una persona.",
                    "accion": "abrir_tienda",
                },
            },

            # ── MENSAJES DE ESTADO ──
            "mensajes_estado": {
                "pendiente":  "✅ Tu pedido *{num}* fue recibido. Total: €{total}. ¡Ya lo estamos preparando!",
                "armando":    f"{preparando_emoji} Estamos preparando tu pedido *{{num}}*. En breve sale.",
                "listo":      "📦 Tu pedido *{num}* está listo y pronto sale a entregarse.",
                "en_ruta":    "🚀 Tu pedido *{num}* está en camino. Cuando el repartidor llegue te enviaremos el código de entrega.",
                "entregado":  entregado_msg,
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
                "flujo_puntos": flujo_puntos_instrucciones,
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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


# ─── PUNTOS: PRODUCTOS CANJEABLES ────────────────────────────────────────────

@api_bot_bp.route("/puntos/productos-canjeables")
@bot_required
def productos_canjeables():
    """
    Devuelve los productos que el cliente puede canjear con sus puntos actuales.
    Requiere telefono como query param.
    """
    try:
        if not get_store_features().get("puntos", True):
            return jsonify({
                "ok": False,
                "error": "El club de puntos no está habilitado.",
                "code": "FEATURE_DISABLED",
                "productos_canjeables": [],
            }), 403
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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


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

        if not producto_id:
            return jsonify({"ok": False, "error": "producto_id es requerido para iniciar un canje"}), 400
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
            "producto": {"id": producto.id, "nombre": producto.nombre, "puntos": producto.puntos_para_canje},
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("No se pudo solicitar OTP de puntos")
        return jsonify({"ok": False, "error": "No se pudo iniciar la verificación de puntos"}), 500


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

        if not producto_id:
            return jsonify({"ok": False, "error": "producto_id es requerido para verificar un canje"}), 400
        try:
            producto = db.session.get(Product, int(producto_id))
        except (ValueError, TypeError):
            producto = None
        if not producto or not producto.canje_directo_disponible():
            return jsonify({"ok": False, "error": "Producto no válido para canje"}), 400
        if producto.puntos_para_canje > cliente.puntos:
            return jsonify({
                "ok": False,
                "error": f"Puntos insuficientes. Necesitas {producto.puntos_para_canje}"
            }), 400

        from loyalty_service import bloquear_cliente_puntos
        cliente = bloquear_cliente_puntos(cliente)
        if not cliente.verificar_cod_puntos(codigo):
            db.session.commit()  # persiste incremento de intentos fallidos
            return jsonify({"ok": False, "error": "Código incorrecto o expirado. Solicita uno nuevo."}), 400

        # OTP válido: commit inmediato para invalidarlo y no permitir reutilización
        db.session.commit()

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
        current_app.logger.exception("No se pudo verificar OTP de puntos")
        return jsonify({"ok": False, "error": "No se pudo verificar el código de puntos"}), 500


# ─── PUNTOS MEJORADO: CON PRODUCTOS CANJEABLES ───────────────────────────────

@api_bot_bp.route("/puntos/saldo")
@bot_required
def puntos_saldo_completo():
    """
    Versión extendida de /puntos con info de canje y productos disponibles.
    Reemplaza a /puntos para el bot cuando necesita info completa.
    """
    try:
        if not get_store_features().get("puntos", True):
            return jsonify({
                "ok": False,
                "error": "El club de puntos no está habilitado.",
                "code": "FEATURE_DISABLED",
                "mensaje_bot": "El club de puntos no está disponible ahora mismo.",
            }), 403
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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


# ─── INFO DEL NEGOCIO ─────────────────────────────────────────────────────────

@api_bot_bp.route("/negocio")
@bot_required
def info_negocio():
    """Info pública del negocio para el bot, incluyendo estado de apertura en tiempo real."""
    try:
        tienda_url = get_public_store_url(request.url_root)
        apertura   = SiteConfig.get("HORARIO_APERTURA", "09:00")
        cierre     = SiteConfig.get("HORARIO_CIERRE", "22:30")
        forzada    = str(SiteConfig.get("TIENDA_FORZAR_CERRADA", "0")).strip().lower() in {"1", "true", "yes", "on"}
        forzada_ab = str(SiteConfig.get("TIENDA_FORZAR_ABIERTA", "0")).strip().lower() in {"1", "true", "yes", "on"}
        ahora_str  = datetime.now().strftime("%H:%M")
        is_open    = tienda_abierta_en_horario(apertura, cierre, ahora=ahora_str, forzada_cerrada=forzada, forzada_abierta=forzada_ab)
        features = get_store_features()
        metodos_pago = []
        if _config_bool("EFECTIVO_HABILITADO", "1"):
            metodos_pago.append("efectivo")
        if _config_bool("BIZUM_HABILITADO", "1"):
            metodos_pago.append("Bizum")
        capacidades = ["consultas", "estado del pedido"]
        if features["puntos"]:
            capacidades.append("puntos")
        if features["delivery"]:
            capacidades.append("cobertura")
        mensaje_cierre = (SiteConfig.get("TIENDA_MENSAJE_CIERRE", "") or "").strip()
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
            "mensaje_cierre": (
                mensaje_cierre or f"Cerrado. Horario: {apertura}–{cierre}"
            ) if not is_open else "",
            "metodos_pago": metodos_pago,
            "delivery_enabled": features["delivery"],
            "pickup_enabled": features["recogida"],
            "points_enabled": features["puntos"],
            "order_create_enabled": False,
            "capacidades": capacidades,
            "tienda_url": tienda_url,
            "mensaje_pedido": (
                f"🛒 Para comprar, abre la tienda online:\n{tienda_url}\n\n"
                f"Por WhatsApp puedo ayudarte con {', '.join(capacidades)} o pasarte con una persona."
            ) if tienda_url else (
                "🛒 La tienda online no esta configurada ahora mismo. "
                "Por WhatsApp puedo ayudarte con consultas o pasarte con una persona."
            )
        })
    except Exception as e:
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


@api_bot_bp.route("/tienda/status")
@bot_required
def tienda_status():
    """Respuesta rápida: ¿está abierta la tienda ahora mismo?"""
    try:
        apertura = SiteConfig.get("HORARIO_APERTURA", "09:00")
        cierre   = SiteConfig.get("HORARIO_CIERRE", "22:30")
        forzada    = str(SiteConfig.get("TIENDA_FORZAR_CERRADA", "0")).strip().lower() in {"1", "true", "yes", "on"}
        forzada_ab = str(SiteConfig.get("TIENDA_FORZAR_ABIERTA", "0")).strip().lower() in {"1", "true", "yes", "on"}
        ahora    = datetime.now().strftime("%H:%M")
        is_open  = tienda_abierta_en_horario(apertura, cierre, ahora=ahora, forzada_cerrada=forzada, forzada_abierta=forzada_ab)
        mensaje_cierre = (SiteConfig.get("TIENDA_MENSAJE_CIERRE", "") or "").strip()
        return jsonify({
            "ok": True,
            "is_open": is_open,
            "hora_actual": ahora,
            "horario": f"{apertura} – {cierre}",
            "mensaje": "Abierto ahora" if is_open else (
                mensaje_cierre or f"Cerrado. Horario: {apertura}–{cierre}"
            ),
        })
    except Exception as e:
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


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
        # Fallback portable (SQLite + Postgres): traemos clientes por prefijo
        # aproximado y filtramos dígitos en Python. Volumen pequeño (matches
        # por sufijo de 9 dígitos), sin impacto de rendimiento perceptible.
        try:
            candidatos = User.query.filter(
                User.rol == "cliente",
                User.telefono.isnot(None),
            ).all()
            for u in candidatos:
                if re.sub(r"\D", "", u.telefono or "") == telefono:
                    cliente = u
                    break
        except Exception:
            pass
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


# Traducción de capabilities-legacy del bot → acciones canónicas (permissions.ACTIONS).
# La política (super_only / admin_read / feature:X) vive en `permissions._POLICY`,
# fuente única de verdad compartida con la web. Aquí solo mapeamos vocabulario.
from permissions import ACTIONS as _ACT, Actor as _Actor, allow as _allow

_BOT_CAPABILITY_TO_ACTION = {
    "store_read":   _ACT.STORE_READ,
    "store":        _ACT.STORE_READ,   # alias legacy
    "store_write":  _ACT.STORE_WRITE,
    "modo_tienda":  _ACT.STORE_MODE_TOGGLE,
    "modulos":      _ACT.STORE_MODULES_TOGGLE,
    "config_write": _ACT.CONFIG_WRITE,
    "products":     _ACT.CATALOG_WRITE,
    "stock":        _ACT.STOCK_WRITE,
    "points":       _ACT.POINTS_WRITE,
    "marketing":    _ACT.MARKETING_WRITE,
    "whatsapp":     _ACT.WHATSAPP_SEND,
    "handoff":      _ACT.WHATSAPP_SEND,
    "security":     _ACT.WHATSAPP_SEND,
    "ai":           _ACT.REPORTS_READ,
}


def _env_privileged_digits():
    """Set de dígitos puros de OWNER_NUMBER + SUPERADMINS.

    Es un WHITELIST (defense-in-depth): el teléfono admin debe estar aquí
    para hablar con el bot. Pero NO otorga rol por sí solo — la BD es la
    fuente de verdad del rol. Ver `_resolver_actor_admin_bot`.
    """
    return {
        re.sub(r"\D", "", raw)
        for raw in [
            os.environ.get("OWNER_NUMBER", ""),
            *os.environ.get("SUPERADMINS", "").split(","),
        ]
        if raw
    }


def _resolver_actor_admin_bot(telefono_raw):
    """Resuelve el actor admin del bot desde un teléfono, cruzando DB y env.

    Política (defense-in-depth, sin bypass silencioso):
      1. Normaliza el teléfono a dígitos puros.
      2. Busca `User` activo con rol ∈ {admin, super_admin} y teléfono coincidente.
         La BD es la fuente autoritativa del rol.
      3. Si además el teléfono está en `OWNER_NUMBER`/`SUPERADMINS` env, marca
         el actor como `privileged_by_env=True` (segundo factor implícito).
      4. Si el env tiene el número pero no hay usuario en BD → log de auditoría
         y deny. Nunca se acepta env como fuente única del rol.

    Retorna `Actor` si autoriza, `None` si no.
    """
    digits = re.sub(r"\D", "", telefono_raw or "")
    if not digits:
        return None
    env_whitelist = _env_privileged_digits()

    # Búsqueda de usuario admin/super_admin por teléfono normalizado.
    # Lista pequeña; comparación en Python (portable SQLite/Postgres).
    candidatos = User.query.filter(
        User.activo == True,  # noqa
        User.rol.in_(["admin", "super_admin"]),
        User.telefono_normalizado.isnot(None),
    ).all()
    user = None
    for u in candidatos:
        if re.sub(r"\D", "", u.telefono_normalizado or "") == digits:
            user = u
            break

    if user is None:
        # Env autoriza pero no hay usuario en BD → audit + deny.
        if digits in env_whitelist:
            try:
                current_app.logger.warning(
                    "bot admin: teléfono env-privilegiado sin User(super_admin) "
                    "en BD (digits=***%s). Deny by policy — crea el usuario o "
                    "quita el número del env.",
                    digits[-3:] if len(digits) >= 3 else "?",
                )
            except Exception:
                pass
        return None

    # Usuario existe. Env acompaña como segundo factor (defense-in-depth).
    return _Actor(
        rol=user.rol,
        user_id=user.id,
        privileged_by_env=(digits in env_whitelist),
    )


def _bot_admin_actor_allowed(data, capability):
    """Autoriza una acción del bot admin.

    - Identifica al actor cruzando teléfono + BD + env.
    - Delega la política a `permissions.allow` (fuente única).
    - Deny by default para capability desconocida.
    """
    telefono = normalizar_telefono_cliente((data or {}).get("actor_telefono"))
    if not telefono_valido(telefono):
        return False
    action = _BOT_CAPABILITY_TO_ACTION.get(capability)
    if action is None:
        return False
    actor = _resolver_actor_admin_bot(telefono)
    if actor is None:
        return False
    return _allow(actor, action)


def _bot_admin_request_payload():
    if request.method in {"GET", "HEAD"}:
        return request.args.to_dict(flat=True)
    return request.get_json(silent=True) or {}


def _bot_admin_request_allowed(capability):
    return _bot_admin_actor_allowed(_bot_admin_request_payload(), capability)


def _bot_actor_forbidden(capability: str | None = None):
    """403 estándar para acciones denegadas al actor admin.

    Diferencia entre "requiere super_admin" (política `super_only`) y
    "admin sin la feature" para que el cliente pueda mostrar mensaje útil.
    """
    from permissions import is_super_only
    action = _BOT_CAPABILITY_TO_ACTION.get(capability or "") if capability else None
    if action and is_super_only(action):
        return jsonify({
            "ok": False,
            "code": "SUPERADMIN_REQUIRED",
            "error": "Esta acción requiere super_admin.",
        }), 403
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
        "zona": pedido.zona_nombre_aplicada,
        "creado_en": creado.isoformat() if creado else None,
    }


@api_bot_bp.route("/admin/tienda", methods=["GET", "POST"])
@bot_required
def bot_admin_tienda():
    """Consulta o cambia apertura forzada de tienda desde WhatsApp admin."""
    try:
        if request.method == "GET":
            if not _bot_admin_request_allowed("store"):
                return _bot_actor_forbidden()
            cerrada  = str(SiteConfig.get("TIENDA_FORZAR_CERRADA", "0")).strip().lower() in {"1", "true", "yes", "on"}
            abierta  = str(SiteConfig.get("TIENDA_FORZAR_ABIERTA", "0")).strip().lower() in {"1", "true", "yes", "on"}
            apertura = SiteConfig.get("HORARIO_APERTURA", "09:00")
            cierre_h = SiteConfig.get("HORARIO_CIERRE", "22:30")
            ahora_s  = datetime.now().strftime("%H:%M")
            is_open  = tienda_abierta_en_horario(
                apertura, cierre_h, ahora=ahora_s,
                forzada_cerrada=cerrada, forzada_abierta=abierta,
            )
            return jsonify({
                "ok": True,
                "forzar_cerrada": cerrada,
                "forzar_abierta": abierta,
                "estado": "cerrada" if not is_open else "abierta",
                "dentro_horario": tienda_abierta_en_horario(apertura, cierre_h, ahora=ahora_s),
                "mensaje_cierre": SiteConfig.get("TIENDA_MENSAJE_CIERRE", ""),
            })

        data = request.get_json(silent=True) or {}
        if not _bot_admin_actor_allowed(data, "store_write"):
            return _bot_actor_forbidden("store_write")
        if "forzar_cerrada" not in data:
            return jsonify({"ok": False, "error": "forzar_cerrada requerido"}), 400
        cerrada = _json_bool(data.get("forzar_cerrada"))
        mensaje = str(data.get("mensaje_cierre") or "").strip()[:240]

        # Los dos flags son mutuamente excluyentes en el runtime. Al pedir
        # "cerrar" se activa FORZAR_CERRADA y se limpia FORZAR_ABIERTA. Al
        # pedir "abrir" fuera de horario se activa FORZAR_ABIERTA para que
        # los pedidos se acepten; dentro de horario basta con limpiar los
        # dos overrides (el horario ya autoriza). Antes: sin FORZAR_ABIERTA,
        # el admin veía "OK, abierta" en el bot pero el checkout seguía
        # rechazando pedidos por horario — silencio operativo.
        if cerrada:
            SiteConfig.set("TIENDA_FORZAR_CERRADA", "1",
                           descripcion="Cierre temporal controlado por bot admin")
            SiteConfig.set("TIENDA_FORZAR_ABIERTA", "0",
                           descripcion="Reset por comando de cierre desde bot")
        else:
            apertura = SiteConfig.get("HORARIO_APERTURA", "09:00")
            cierre_h = SiteConfig.get("HORARIO_CIERRE", "22:30")
            ahora_str = datetime.now().strftime("%H:%M")
            dentro_horario = tienda_abierta_en_horario(
                apertura, cierre_h, ahora=ahora_str,
                forzada_cerrada=False, forzada_abierta=False,
            )
            SiteConfig.set("TIENDA_FORZAR_CERRADA", "0",
                           descripcion="Reset por comando de apertura desde bot")
            SiteConfig.set(
                "TIENDA_FORZAR_ABIERTA",
                "0" if dentro_horario else "1",
                descripcion=(
                    "Auto-limpio: dentro de horario" if dentro_horario
                    else "Apertura forzada fuera de horario por bot admin"
                ),
            )
        SiteConfig.set("TIENDA_MENSAJE_CIERRE", mensaje,
                       descripcion="Mensaje de cierre temporal")
        db.session.commit()
        return jsonify({
            "ok": True,
            "forzar_cerrada": cerrada,
            "forzar_abierta": str(SiteConfig.get("TIENDA_FORZAR_ABIERTA", "0")) == "1",
            "estado": "cerrada" if cerrada else "abierta",
            "mensaje_cierre": mensaje,
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


@api_bot_bp.route("/admin/resumen-hoy")
@bot_required
def bot_admin_resumen_hoy():
    """Resumen operativo del día: pedidos, ventas, activos, productos sin stock."""
    if not _bot_admin_request_allowed("store"):
        return _bot_actor_forbidden()
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
            Order.estado.in_(ESTADOS_ACTIVOS)
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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


# ─── Endpoints exclusivos super_admin desde el bot ──────────────
# El bot ya autentica al remitente como super_admin antes de invocarlos
# (isSuperAdminJid). El bot_required guarda la llamada bot→oxidian con la
# clave X-Bot-Key. Ambas capas son necesarias — sin cualquiera, 403.

@api_bot_bp.route("/admin/modo-tienda/toggle", methods=["POST"])
@bot_required
def bot_admin_toggle_modo_tienda():
    """Alterna entre modo 'propia' y 'bar_servicio' desde el bot."""
    if not _bot_admin_request_allowed("modo_tienda"):
        return _bot_actor_forbidden("modo_tienda")
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
    if not _bot_admin_actor_allowed(data, "modulos"):
        return _bot_actor_forbidden("modulos")
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
    try:
        data = request.get_json(silent=True) or {}
        if not _bot_admin_actor_allowed(data, "store_write"):
            return _bot_actor_forbidden("store_write")
        if "cerrada" not in data and "forzar_cerrada" not in data:
            return jsonify({"ok": False, "error": "cerrada requerido"}), 400
        cerrada = _json_bool(data.get("cerrada", data.get("forzar_cerrada")))
        mensaje = str(data.get("mensaje_cierre") or "").strip()[:240]
        SiteConfig.set("TIENDA_FORZAR_CERRADA", "1" if cerrada else "0",
                       descripcion="Forzar cierre desde bot admin")
        SiteConfig.set("TIENDA_MENSAJE_CIERRE", mensaje,
                       descripcion="Mensaje de cierre temporal")
        db.session.commit()
        return jsonify({
            "ok": True,
            "cerrada": cerrada,
            "forzar_cerrada": cerrada,
            "estado": "cerrada" if cerrada else "abierta",
            "mensaje_cierre": mensaje,
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


@api_bot_bp.route("/admin/salud")
@bot_required
def bot_admin_salud():
    """Snapshot rápido de salud del sistema para el super_admin."""
    if not _bot_admin_request_allowed("store"):
        return _bot_actor_forbidden()
    from datetime import date
    try:
        pedidos_hoy = Order.query.filter(
            db.func.date(Order.creado_en) == date.today()
        ).count()
        pedidos_pend = Order.query.filter(
            Order.estado.in_(ESTADOS_EN_PREPARACION)
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
    if not _bot_admin_request_allowed("store"):
        return _bot_actor_forbidden()
    estados_activos = ESTADOS_ACTIVOS
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
    if not _bot_admin_request_allowed("store"):
        return _bot_actor_forbidden()
    try:
        now = _utcnow()
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
            Order.estado.in_(ESTADOS_EN_PREPARACION),
            Order.preparador_id.is_(None),
        ).order_by(Order.creado_en.asc()).limit(8).all()
        sin_repartidor = Order.query.filter(
            Order.estado == "listo",
            Order.repartidor_id.is_(None),
            Order.tipo_entrega_cliente == "delivery",
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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


@api_bot_bp.route("/admin/productos/buscar")
@bot_required
def bot_admin_buscar_productos():
    try:
        if not _bot_admin_request_allowed("products"):
            return _bot_actor_forbidden()
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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


@api_bot_bp.route("/admin/clientes/buscar")
@bot_required
def bot_admin_buscar_cliente():
    try:
        if not _bot_admin_request_allowed("points"):
            return _bot_actor_forbidden()
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
                "puntos": int(cliente.puntos or 0),
                "activo": bool(cliente.activo),
            },
        })
    except Exception as e:
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


@api_bot_bp.route("/admin/clientes/<int:cliente_id>/puntos/historial")
@bot_required
def bot_admin_historial_puntos(cliente_id):
    try:
        if not _bot_admin_request_allowed("points"):
            return _bot_actor_forbidden()
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
        current_app.logger.exception("api_bot 500")
        return jsonify({"ok": False, "error": "Error interno del servidor"}), 500


# ─── IA para admin/super_admin vía WhatsApp ─────────────────────────────
# El bot llama a este endpoint cuando un super_admin/admin envía `!ia <pregunta>`.
# Reutiliza la misma capa de análisis del panel web (agregados + guardrails).

def _telefono_admin_autorizado(telefono):
    """Devuelve el teléfono normalizado si pertenece a un `User` activo con rol
    admin/super_admin. Cruza con el whitelist env (SUPERADMINS/OWNER_NUMBER)
    como defense-in-depth, PERO nunca acepta env sin usuario en BD.

    Si el env tiene un teléfono sin usuario correspondiente → warning y deny,
    para evitar bypass silencioso si el env se filtra o queda desincronizado.
    """
    tn = normalizar_telefono_cliente(telefono)
    if not telefono_valido(tn):
        return None
    tn_digits = re.sub(r"\D", "", tn)

    # Whitelist runtime (SiteConfig o env) — solo para trazabilidad.
    whitelist_digits = set()
    for clave in ("SUPERADMINS", "OWNER_NUMBER"):
        raw = (SiteConfig.get(clave, current_app.config.get(clave, "")) or "")
        for chunk in raw.replace(";", ",").split(","):
            d = re.sub(r"\D", "", chunk or "")
            if d:
                whitelist_digits.add(d)

    # Búsqueda autoritativa en BD.
    user = None
    for u in User.query.filter(
        User.telefono_normalizado.isnot(None),
        User.rol.in_(["admin", "super_admin"]),
    ).all():
        if re.sub(r"\D", "", u.telefono_normalizado or "") == tn_digits:
            user = u
            break
    if user and user.activo:
        return tn

    # Env-only sin usuario en BD → warning + deny.
    if tn_digits in whitelist_digits:
        try:
            current_app.logger.warning(
                "ai admin: teléfono env-privilegiado sin User(admin/super_admin) "
                "en BD (digits=***%s). Deny by policy.",
                tn_digits[-3:] if len(tn_digits) >= 3 else "?",
            )
        except Exception:
            pass
    return None


@api_bot_bp.route("/ai/admin-consulta", methods=["POST"])
@bot_required
def ai_admin_consulta():
    """Consulta IA desde WhatsApp para admin/super_admin.

    Body JSON:
      { "telefono": "34...", "pregunta": "top productos..." }

    Reutiliza _llamar_ia_analisis + _resumen_negocio_para_ia del panel web.
    """
    from routes.admin import _resumen_negocio_para_ia, _llamar_ia_analisis

    payload = request.get_json(silent=True) or {}
    telefono = (payload.get("telefono") or "").strip()
    pregunta = (payload.get("pregunta") or "").strip()

    tn = _telefono_admin_autorizado(telefono)
    if not tn or not _bot_admin_actor_allowed({"actor_telefono": tn}, "ai"):
        return jsonify({"ok": False, "error": "No autorizado"}), 403
    if len(pregunta) < 5:
        return jsonify({"ok": False, "error": "Escribe una pregunta más específica."})
    if len(pregunta) > 500:
        return jsonify({"ok": False, "error": "Pregunta demasiado larga (máx 500)."})

    # Rate limit por teléfono: máx N consultas/hora (configurable). Evita que
    # un admin comprometido queme tokens del proveedor IA en un bucle.
    try:
        limite_hora = int(SiteConfig.get("IA_ADMIN_LIMITE_HORA", "30") or "30")
    except (TypeError, ValueError):
        limite_hora = 30
    ventana = _utcnow() - timedelta(hours=1)
    recientes = AuditLog.query.filter(
        AuditLog.accion == "ia_consulta_whatsapp",
        AuditLog.creado_en >= ventana,
        AuditLog.detalle.isnot(None),
        AuditLog.ip == request.remote_addr,
    ).count()
    if recientes >= limite_hora:
        return jsonify({"ok": False, "error": f"Has alcanzado el límite de {limite_hora} consultas/hora. Intenta más tarde."})

    contexto = _resumen_negocio_para_ia()
    respuesta, error = _llamar_ia_analisis(pregunta, contexto)
    if error:
        return jsonify({"ok": False, "error": error})

    # Auditoría (registra teléfono normalizado, no la pregunta completa)
    try:
        user = User.query.filter_by(telefono_normalizado=tn).first()
        AuditLog.registrar(
            (user.id if user else None), "ia_consulta_whatsapp",
            "analisis", detalle=pregunta[:180],
            ip=request.remote_addr,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()

    # Contexto expandido: nueva clave 'ventas' con subranges 7/30/90d.
    ventas_30 = (contexto.get("ventas") or {}).get("ultimos_30_dias") or {}
    return jsonify({"ok": True, "respuesta": respuesta, "contexto_resumen": {
        "pedidos_30d": ventas_30.get("pedidos", 0),
        "facturacion_30d": ventas_30.get("facturacion_eur", 0),
    }})


# ─── Comandos admin ampliados (todos requieren teléfono autorizado) ─────

@api_bot_bp.route("/config", methods=["GET"])
@bot_required
def bot_config_ver():
    """Devuelve un subconjunto de SiteConfig. Filtra por prefijo o claves
    específicas. Requiere actor admin y NO devuelve secretos."""
    if not _bot_admin_request_allowed("store"):
        return _bot_actor_forbidden()
    SENSIBLES = {"BOT_AI_API_KEY", "BOT_API_KEY", "SECRET_KEY", "BOT_PANEL_KEY"}
    prefijo = (request.args.get("prefijo") or "").strip().upper()
    claves_arg = (request.args.get("claves") or "").strip()
    q = SiteConfig.query
    if claves_arg:
        wanted = [c.strip() for c in claves_arg.split(",") if c.strip()]
        q = q.filter(SiteConfig.clave.in_(wanted))
    elif prefijo:
        q = q.filter(SiteConfig.clave.like(f"{prefijo}%"))
    out = {}
    for row in q.order_by(SiteConfig.clave).limit(50).all():
        if row.clave in SENSIBLES:
            continue
        out[row.clave] = row.valor or ""
    return jsonify({"ok": True, "config": out})


@api_bot_bp.route("/config/set", methods=["POST"])  # super_admin only via config_write
@bot_required
def bot_config_set():
    """Cambia una SiteConfig. Bloquea claves sensibles y requiere actor
    admin verificado (defense-in-depth: si el X-Bot-Key se filtra, esto
    aún exige que el teléfono admin sea válido)."""
    if not _bot_admin_request_allowed("config_write"):
        return _bot_actor_forbidden("config_write")
    from store_config import LOCKED_CONFIG_KEYS
    from routes.superadmin import _validar_config_value

    BLOQUEADAS = {"BOT_AI_API_KEY", "BOT_API_KEY", "SECRET_KEY", "BOT_PANEL_KEY",
                  "SEED_PASSWORD", "OXIDIAN_KEY"}
    payload = request.get_json(silent=True) or {}
    clave = (payload.get("clave") or "").strip().upper()
    valor = str(payload.get("valor") or "").strip()
    if not clave or len(clave) > 60:
        return jsonify({"ok": False, "error": "Clave requerida (<60 chars)"})
    if clave in BLOQUEADAS or clave.endswith("_API_KEY") or "PASSWORD" in clave:
        return jsonify({"ok": False, "error": f"Clave protegida: {clave}"}), 403
    actor_telefono = _bot_admin_request_payload().get("actor_telefono")
    actor_norm = normalizar_telefono_cliente(actor_telefono)
    actor_user = User.query.filter_by(telefono_normalizado=actor_norm, activo=True).first() if actor_norm else None
    # Fuente autoritativa: BD. El env (SUPERADMINS/OWNER_NUMBER) es whitelist
    # de defensa, no otorga rol. Ver `_resolver_actor_admin_bot` para el
    # patrón general del bot.
    actor_superadmin = bool(actor_user and actor_user.rol == "super_admin")
    if clave in LOCKED_CONFIG_KEYS and not actor_superadmin:
        return jsonify({
            "ok": False,
            "code": "SUPERADMIN_REQUIRED",
            "error": f"Solo el super admin puede cambiar {clave}.",
        }), 403
    ok, clave, valor, error = _validar_config_value(clave, valor)
    if not ok:
        return jsonify({"ok": False, "error": error}), 400
    try:
        SiteConfig.set(clave, valor)
        AuditLog.registrar(None, "config_set_whatsapp", "site_config",
                           detalle=f"{clave}={valor[:80]}",
                           ip=request.remote_addr)
        db.session.commit()
        from services import refrescar_bot_si_claves_relevantes
        refrescar_bot_si_claves_relevantes([clave])
        return jsonify({"ok": True, "clave": clave, "valor": valor})
    except Exception as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 500


@api_bot_bp.route("/admin/buscar-producto", methods=["GET"])
@bot_required
def bot_buscar_producto():
    """Busca productos por nombre (LIKE) para el bot admin."""
    if not _bot_admin_request_allowed("products"):
        return _bot_actor_forbidden()
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"ok": False, "error": "Query mínimo 2 chars"})
    from sqlalchemy import func
    resultados = (
        Product.query
        .filter(func.lower(Product.nombre).like(f"%{q.lower()}%"))
        .order_by(Product.activo.desc(), Product.nombre.asc())
        .limit(15)
        .all()
    )
    out = []
    for p in resultados:
        stock_total = None
        try:
            stock_total = int(p.stock_total or 0)
        except Exception:
            stock_total = None
        out.append({
            "id": p.id,
            "nombre": p.nombre,
            "precio": float(p.precio or 0),
            "activo": bool(p.activo),
            "stock": stock_total,
            "categoria": p.categoria.nombre if p.categoria else None,
            "vertical": p.vertical or "ambos",
        })
    return jsonify({"ok": True, "productos": out})


@api_bot_bp.route("/admin/producto/toggle", methods=["POST"])
@bot_required
def bot_producto_toggle():
    """Activa/desactiva un producto rápido desde WhatsApp admin."""
    if not _bot_admin_request_allowed("products"):
        return _bot_actor_forbidden()
    payload = request.get_json(silent=True) or {}
    pid = payload.get("producto_id")
    activo = bool(payload.get("activo", True))
    try:
        pid = int(pid) if pid is not None else None
    except (TypeError, ValueError):
        pid = None
    if not pid:
        return jsonify({"ok": False, "error": "producto_id requerido"})
    p = db.session.get(Product, pid)
    if not p:
        return jsonify({"ok": False, "error": f"Producto #{pid} no existe"}), 404
    p.activo = activo
    try:
        AuditLog.registrar(None, "producto_toggle_whatsapp", "product",
                           detalle=f"#{pid}→{'activo' if activo else 'inactivo'}",
                           ip=request.remote_addr)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "No se pudo guardar"}), 500
    return jsonify({"ok": True, "id": pid, "activo": activo, "nombre": p.nombre})


@api_bot_bp.route("/admin/diagnostico", methods=["GET"])
@bot_required
def bot_diagnostico():
    """Snapshot rápido del estado del sistema: stock, finanzas, features
    y flujos. Diseñado para el comando `!diag` del admin en WhatsApp.
    Sin PII, todo agregados."""
    if not _bot_admin_request_allowed("store"):
        return _bot_actor_forbidden()
    from sqlalchemy import func
    hoy = date.today()
    hace_7d = hoy - timedelta(days=7)

    # Catálogo
    total_prod = Product.query.filter_by(activo=True).count()
    total_combo = Product.query.filter_by(activo=True, es_combo=True).count()
    con_stock = db.session.query(Product.id).filter(
        Product.activo == True,  # noqa
        Product.tipo_entrega == "inmediato",
        Product.es_combo == False,  # noqa
    ).count()
    sin_stock = 0
    for p in Product.query.filter_by(activo=True, es_combo=False, tipo_entrega="inmediato").all():
        try:
            if int(p.stock_total or 0) <= 0:
                sin_stock += 1
        except Exception:
            pass

    # Finanzas 7d
    pedidos_7d = Order.query.filter(Order.creado_en >= hace_7d).count()
    entregados_7d = Order.query.filter(
        Order.creado_en >= hace_7d, Order.estado == "entregado"
    ).count()
    from models import Caja as _Caja
    ingresos_7d = float(db.session.query(func.coalesce(func.sum(_Caja.monto), 0))
                        .filter(_Caja.tipo == "ingreso", _Caja.fecha >= hace_7d).scalar() or 0)
    egresos_7d = float(db.session.query(func.coalesce(func.sum(_Caja.monto), 0))
                       .filter(_Caja.tipo == "egreso", _Caja.fecha >= hace_7d).scalar() or 0)

    # Features runtime
    from store_config import get_store_features
    features = get_store_features()

    # Pedidos "atascados" — pendientes con >30 min
    from datetime import datetime as _dt
    hace_30min = _utcnow() - timedelta(minutes=30)
    atascados = Order.query.filter(
        Order.estado.in_(ESTADOS_EN_PREPARACION),
        Order.creado_en < hace_30min,
    ).count()

    return jsonify({
        "ok": True,
        "catalogo": {
            "productos_activos": total_prod,
            "combos_activos": total_combo,
            "productos_sin_stock": sin_stock,
        },
        "finanzas_7d": {
            "pedidos": pedidos_7d,
            "entregados": entregados_7d,
            "ingresos_eur": round(ingresos_7d, 2),
            "egresos_eur": round(egresos_7d, 2),
            "resultado_eur": round(ingresos_7d - egresos_7d, 2),
        },
        "operativa": {
            "pedidos_atascados_>30min": atascados,
        },
        "features": features,
    })


# ─── Comandos AVANZADOS del bot (solo modo bar_servicio) ─────────────────

def _requiere_bar_servicio():
    """Guard runtime: 403 si MODO_TIENDA != bar_servicio."""
    modo = (SiteConfig.get("MODO_TIENDA", "propia") or "propia").strip().lower()
    if modo != "bar_servicio":
        return jsonify({
            "ok": False,
            "error": "Comando disponible solo en modo bar_servicio",
        }), 403
    return None


@api_bot_bp.route("/admin/producto/precio", methods=["POST"])
@bot_required
def bot_producto_precio():
    if not _bot_admin_request_allowed("products"):
        return _bot_actor_forbidden()
    guard = _requiere_bar_servicio()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    pid = payload.get("producto_id")
    precio = payload.get("precio")
    try:
        pid = int(pid); precio = float(precio)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "producto_id y precio numéricos"}), 400
    if precio < 0 or precio > 10000:
        return jsonify({"ok": False, "error": "Precio fuera de rango [0, 10000]"})
    p = db.session.get(Product, pid)
    if not p:
        return jsonify({"ok": False, "error": f"Producto #{pid} no existe"}), 404
    from decimal import Decimal
    p.precio = Decimal(str(precio))
    try:
        AuditLog.registrar(None, "precio_whatsapp", "product",
                           detalle=f"#{pid} → €{precio}",
                           ip=request.remote_addr)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "No se pudo guardar"}), 500
    return jsonify({"ok": True, "id": pid, "precio": precio, "nombre": p.nombre})


@api_bot_bp.route("/admin/producto/stock", methods=["POST"])
@bot_required
def bot_producto_stock():
    if not _bot_admin_request_allowed("stock"):
        return _bot_actor_forbidden()
    guard = _requiere_bar_servicio()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    pid = payload.get("producto_id")
    op = (payload.get("operacion") or "=").strip()
    try:
        pid = int(pid); cantidad = int(payload.get("cantidad", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "producto_id y cantidad enteros"}), 400
    if op not in ("+", "-", "="):
        return jsonify({"ok": False, "error": "operacion debe ser +, - o ="})
    p = db.session.get(Product, pid)
    if not p:
        return jsonify({"ok": False, "error": f"Producto #{pid} no existe"}), 404

    from models import Stock
    from datetime import date as _d, timedelta as _td
    # Trabajamos con un lote único "ajuste bot" con caducidad lejana.
    lote = Stock.query.filter_by(producto_id=pid, lote="__bot_ajuste").first()
    antes = int(lote.cantidad) if lote else 0
    if op == "=":
        nuevo = max(0, cantidad)
    elif op == "+":
        nuevo = antes + cantidad
    else:
        nuevo = max(0, antes - cantidad)
    if lote is None:
        lote = Stock(
            producto_id=pid, cantidad=nuevo,
            lote="__bot_ajuste",
            fecha_caducidad=_d.today() + _td(days=365 * 3),
        )
        db.session.add(lote)
    else:
        lote.cantidad = nuevo
    try:
        AuditLog.registrar(None, "stock_whatsapp", "stock",
                           detalle=f"#{pid} {antes}→{nuevo}",
                           ip=request.remote_addr)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "No se pudo guardar"}), 500
    return jsonify({"ok": True, "id": pid, "antes": antes, "nuevo": nuevo})


@api_bot_bp.route("/admin/producto/crear", methods=["POST"])
@bot_required
def bot_producto_crear():
    if not _bot_admin_request_allowed("products"):
        return _bot_actor_forbidden()
    guard = _requiere_bar_servicio()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    nombre = (payload.get("nombre") or "").strip()
    cat_nombre = (payload.get("categoria") or "").strip() or None
    try:
        precio = float(payload.get("precio") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "precio inválido"}), 400
    if len(nombre) < 2 or len(nombre) > 120:
        return jsonify({"ok": False, "error": "Nombre entre 2 y 120 chars"}), 400
    if precio <= 0 or precio > 10000:
        return jsonify({"ok": False, "error": "Precio fuera de rango"}), 400

    from decimal import Decimal
    cat = None
    if cat_nombre:
        cat = Categoria.query.filter(
            db.func.lower(Categoria.nombre) == cat_nombre.lower()
        ).first()
        if not cat:
            cat = Categoria(nombre=cat_nombre, activo=True)
            db.session.add(cat)
            db.session.flush()

    tt = (SiteConfig.get("TIPO_TIENDA", "comida") or "comida").lower()
    p = Product(
        nombre=nombre,
        precio=Decimal(str(precio)),
        activo=True,
        categoria_id=cat.id if cat else None,
        canal_preparacion="cocina" if tt == "comida" else "almacen",
        tipo_entrega="inmediato",
        modalidad_entrega="ambas",
        vertical=tt,
    )
    db.session.add(p)
    try:
        db.session.flush()
        AuditLog.registrar(None, "producto_crear_whatsapp", "product",
                           detalle=f"'{nombre}' €{precio}",
                           ip=request.remote_addr)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": f"No se pudo crear: {exc}"}), 500
    return jsonify({
        "ok": True, "id": p.id, "nombre": p.nombre,
        "precio": float(p.precio), "categoria": cat.nombre if cat else None,
    })


@api_bot_bp.route("/admin/pedidos", methods=["GET"])
@bot_required
def bot_admin_pedidos():
    if not _bot_admin_request_allowed("store"):
        return _bot_actor_forbidden()
    numero = (request.args.get("numero") or "").strip().lstrip("#")
    estados_raw = (request.args.get("estados") or "pendiente,armando,listo").strip()
    limit = min(50, max(1, request.args.get("limit", 10, type=int)))
    estados = [e.strip() for e in estados_raw.split(",") if e.strip()]
    q = Order.query
    if numero:
        q = q.filter(db.or_(Order.numero_pedido == numero, Order.numero_pedido == f"#{numero}"))
    else:
        q = q.filter(Order.estado.in_(estados))
    q = q.order_by(Order.creado_en.desc()).limit(limit)
    out = []
    ahora = _utcnow()
    for p in q.all():
        creado = p.creado_en
        mins = int((ahora - creado).total_seconds() // 60) if creado else 0
        if mins < 60:
            hace = f"{mins} min"
        elif mins < 1440:
            hace = f"{mins // 60}h {mins % 60}min"
        else:
            hace = f"{mins // 1440}d"
        out.append({
            "numero": p.numero_pedido,
            "estado": p.estado,
            "total": float(p.total or 0),
            "creado_hace": hace,
            "metodo_pago": p.metodo_pago or "",
        })
    return jsonify({"ok": True, "pedidos": out})


# ─── Endpoints admin adicionales ────────────────────────────────────

@api_bot_bp.route("/admin/aviso-pedido", methods=["POST"])
@bot_required
def bot_admin_aviso_pedido():
    """Envía un mensaje libre al cliente de un pedido específico.
    Reusa el mismo canal WhatsApp que el bot usa para notificaciones."""
    if not _bot_admin_request_allowed("whatsapp"):
        return _bot_actor_forbidden()
    payload = request.get_json(silent=True) or {}
    numero = (payload.get("numero_pedido") or "").strip()
    mensaje = (payload.get("mensaje") or "").strip()
    if not numero:
        return jsonify({"ok": False, "error": "numero_pedido requerido"})
    if len(mensaje) < 3 or len(mensaje) > 800:
        return jsonify({"ok": False, "error": "Mensaje entre 3 y 800 chars"})
    p = Order.query.filter(
        db.or_(Order.numero_pedido == numero, Order.numero_pedido == f"#{numero}")
    ).order_by(Order.creado_en.desc()).first()
    if not p or not p.cliente or not p.cliente.telefono:
        return jsonify({"ok": False, "error": f"Pedido {numero} sin cliente/teléfono"}), 404
    from services import enviar_whatsapp_generico
    ok = enviar_whatsapp_generico(
        p.cliente.telefono, mensaje,
        evento="admin_aviso", pedido_id=p.id,
    )
    if ok:
        AuditLog.registrar(None, "aviso_pedido_whatsapp", "order",
                           p.id, detalle=mensaje[:150],
                           ip=request.remote_addr)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
    tel = p.cliente.telefono or ""
    tel_masked = f"…{tel[-3:]}" if len(tel) >= 3 else "?"
    return jsonify({"ok": ok, "telefono_masked": tel_masked})


@api_bot_bp.route("/admin/cupon/crear", methods=["POST"])
@bot_required
def bot_admin_cupon_crear():
    """Crea un cupón porcentual express desde WhatsApp."""
    if not _bot_admin_request_allowed("marketing"):
        return _bot_actor_forbidden()
    payload = request.get_json(silent=True) or {}
    codigo = (payload.get("codigo") or "").strip().upper()
    try:
        pct = int(payload.get("descuento_pct") or 0)
    except (TypeError, ValueError):
        pct = 0
    import re
    if not re.fullmatch(r"[A-Z0-9_-]{2,20}", codigo):
        return jsonify({"ok": False, "error": "Código inválido (2-20 alfanuméricos)"})
    if pct < 1 or pct > 90:
        return jsonify({"ok": False, "error": "Descuento entre 1 y 90%"})
    if Coupon.query.filter_by(codigo=codigo).first():
        return jsonify({"ok": False, "error": f"Ya existe el cupón {codigo}"})
    from datetime import timedelta
    from decimal import Decimal
    c = Coupon(
        codigo=codigo, descuento_pct=Decimal(str(pct)),
        activo=True,
        fecha_inicio=date.today(),
        fecha_fin=date.today() + timedelta(days=30),
        usos_maximos=None,
    )
    db.session.add(c)
    try:
        AuditLog.registrar(None, "cupon_crear_whatsapp", "coupon",
                           detalle=f"{codigo} {pct}%",
                           ip=request.remote_addr)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": f"No se pudo crear: {exc}"}), 500
    return jsonify({"ok": True, "codigo": codigo, "descuento_pct": pct})


@api_bot_bp.route("/admin/top-productos", methods=["GET"])
@bot_required
def bot_admin_top_productos():
    """Top productos vendidos en los últimos N días."""
    if not _bot_admin_request_allowed("store"):
        return _bot_actor_forbidden()
    try:
        dias = max(1, min(365, int(request.args.get("dias", 30))))
    except (TypeError, ValueError):
        dias = 30
    desde = date.today() - timedelta(days=dias)
    from sqlalchemy import func
    q = (
        db.session.query(
            Product.id.label("id"),
            Product.nombre.label("nombre"),
            func.sum(OrderItem.cantidad).label("unidades"),
            func.sum(OrderItem.subtotal).label("total"),
        )
        .join(OrderItem, OrderItem.producto_id == Product.id)
        .join(Order, Order.id == OrderItem.pedido_id)
        .filter(Order.creado_en >= desde)
        .group_by(Product.id, Product.nombre)
        .order_by(func.sum(OrderItem.cantidad).desc())
        .limit(10)
    )
    return jsonify({
        "ok": True, "dias": dias,
        "top": [
            {"id": row.id, "nombre": row.nombre,
             "unidades": int(row.unidades or 0),
             "total": float(row.total or 0)}
            for row in q.all()
        ],
    })


@api_bot_bp.route("/admin/stock-bajo", methods=["GET"])
@bot_required
def bot_admin_stock_bajo():
    """Productos activos con stock <= umbral (default 10)."""
    if not _bot_admin_request_allowed("stock"):
        return _bot_actor_forbidden()
    try:
        umbral = max(0, min(500, int(request.args.get("umbral", 10))))
    except (TypeError, ValueError):
        umbral = 10
    bajos = []
    for p in Product.query.filter(Product.activo == True, Product.es_combo == False).all():  # noqa
        try:
            s = int(p.stock_total or 0)
        except Exception:
            s = 0
        if s <= umbral:
            bajos.append({"id": p.id, "nombre": p.nombre, "stock": s})
    bajos.sort(key=lambda x: x["stock"])
    return jsonify({"ok": True, "umbral": umbral, "productos": bajos[:50]})
