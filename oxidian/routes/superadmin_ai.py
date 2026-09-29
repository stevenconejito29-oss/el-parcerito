"""Rutas del asesor IA comercial (chat interno para super admin / admin).

Blueprint aislado montado en /superadmin/ai. Depende únicamente de:
- ``services.ai_advisor`` (lógica portable)
- ``models`` (persistencia)
- El decorador de autorización del propio ``routes.admin`` para no duplicar
  la política de acceso.
"""
from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from models import AiAdvisorConversation, AuditLog, db
from ai_services import ai_advisor


superadmin_ai_bp = Blueprint("superadmin_ai", __name__)


def _authorised() -> bool:
    """Solo admin y super_admin acceden al asesor comercial."""
    return getattr(current_user, "rol", None) in ("admin", "super_admin")


def _get_conv_o_404(conv_id: int) -> AiAdvisorConversation:
    conv = AiAdvisorConversation.query.get(conv_id)
    if not conv:
        abort(404)
    # Aislamiento por owner: cada admin ve solo sus propios hilos.
    if conv.owner_id != current_user.id and current_user.rol != "super_admin":
        abort(403)
    return conv


# ─────────────────────────────────────────────────────────────────
# Vista principal
# ─────────────────────────────────────────────────────────────────

@superadmin_ai_bp.route("")
@superadmin_ai_bp.route("/")
@login_required
def index():
    if not _authorised():
        abort(403)
    conversaciones = ai_advisor.listar_conversaciones(current_user.id)
    conv_actual = None
    conv_id = request.args.get("conv", type=int)
    if conv_id:
        conv_actual = _get_conv_o_404(conv_id)
    elif conversaciones:
        conv_actual = conversaciones[0]

    mensajes = []
    if conv_actual:
        mensajes = conv_actual.mensajes.order_by("id").all()

    return render_template(
        "superadmin/ai_asesor.html",
        conversaciones=conversaciones,
        conv_actual=conv_actual,
        mensajes=mensajes,
        quick_actions=ai_advisor.QUICK_ACTIONS,
        estado_ia=ai_advisor.estado_proveedor(),
    )


# ─────────────────────────────────────────────────────────────────
# Crear nueva conversación
# ─────────────────────────────────────────────────────────────────

@superadmin_ai_bp.route("/nueva", methods=["POST"])
@login_required
def nueva():
    if not _authorised():
        abort(403)
    categoria = (request.form.get("categoria") or "general").strip().lower()
    titulo = (request.form.get("titulo") or "").strip() or "Nueva conversación"
    conv = ai_advisor.crear_conversacion(current_user.id, titulo, categoria)
    db.session.commit()
    return redirect(url_for("superadmin_ai.index", conv=conv.id))


# ─────────────────────────────────────────────────────────────────
# Enviar mensaje (formulario + AJAX)
# ─────────────────────────────────────────────────────────────────

@superadmin_ai_bp.route("/<int:conv_id>/mensaje", methods=["POST"])
@login_required
def enviar_mensaje(conv_id: int):
    if not _authorised():
        abort(403)
    conv = _get_conv_o_404(conv_id)

    quick_action = (request.form.get("quick_action") or "").strip() or None
    if quick_action and quick_action in ai_advisor.QUICK_ACTIONS:
        prompt = ai_advisor.QUICK_ACTIONS[quick_action]["prompt"]
        # Ajusta la categoría del hilo si el usuario dispara una quick_action
        # y el hilo todavía es "general" (así se agrupa mejor en la lista).
        if conv.categoria == "general":
            conv.categoria = ai_advisor.QUICK_ACTIONS[quick_action]["categoria"]
            db.session.add(conv)
            db.session.flush()  # persistir cambio de categoría antes de continuar
    else:
        prompt = (request.form.get("mensaje") or "").strip()

    if not prompt:
        if request.headers.get("Accept") == "application/json":
            return jsonify({"ok": False, "error": "mensaje_vacio"}), 400
        flash("Escribí tu pregunta o elegí una acción rápida.", "warning")
        return redirect(url_for("superadmin_ai.index", conv=conv.id))

    # Rate limit simple: máx 10 preguntas/minuto por usuario para no saturar el
    # proveedor externo. Se cuenta sobre AiAdvisorMessage con role=user.
    from datetime import datetime as _dt, timedelta
    from models import AiAdvisorMessage
    ventana = _dt.utcnow() - timedelta(minutes=1)
    recientes = (
        db.session.query(AiAdvisorMessage)
        .join(AiAdvisorConversation, AiAdvisorMessage.conversacion_id == AiAdvisorConversation.id)
        .filter(
            AiAdvisorConversation.owner_id == current_user.id,
            AiAdvisorMessage.role == "user",
            AiAdvisorMessage.created_at >= ventana,
        )
        .count()
    )
    if recientes >= 10:
        if request.headers.get("Accept") == "application/json":
            return jsonify({"ok": False, "error": "rate_limit"}), 429
        flash("Vas muy rápido — máximo 10 preguntas por minuto. Esperá un momento.", "warning")
        return redirect(url_for("superadmin_ai.index", conv=conv.id))

    respuesta = ai_advisor.preguntar(conv, prompt, quick_action=quick_action)

    # Auditoría (sin pregunta completa para no llenar la tabla)
    AuditLog.registrar(
        current_user.id,
        "ia_asesor_consulta",
        recurso="ai_advisor",
        recurso_id=conv.id,
        detalle=(quick_action or prompt[:80]),
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    if request.headers.get("Accept") == "application/json":
        return jsonify({
            "ok": True,
            "respuesta": respuesta.texto,
            "fuente": respuesta.fuente,
            "error": respuesta.error,
            "mensaje_id": respuesta.mensaje_id,
        })
    return redirect(url_for("superadmin_ai.index", conv=conv.id))


# ─────────────────────────────────────────────────────────────────
# Archivar / eliminar conversación
# ─────────────────────────────────────────────────────────────────

@superadmin_ai_bp.route("/<int:conv_id>/archivar", methods=["POST"])
@login_required
def archivar(conv_id: int):
    if not _authorised():
        abort(403)
    conv = _get_conv_o_404(conv_id)
    ai_advisor.archivar_conversacion(conv)
    db.session.commit()
    flash("Conversación archivada.", "info")
    return redirect(url_for("superadmin_ai.index"))


# ─────────────────────────────────────────────────────────────────
# Ver el snapshot que ve la IA (JSON)
# ─────────────────────────────────────────────────────────────────

@superadmin_ai_bp.route("/snapshot")
@login_required
def snapshot_json():
    if not _authorised():
        abort(403)
    return jsonify(ai_advisor.build_snapshot())


# ─────────────────────────────────────────────────────────────────
# Configuración rápida del proveedor externo desde el propio asesor
# ─────────────────────────────────────────────────────────────────

# Modelos por defecto recomendados por proveedor (para pintar en el form).
DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "anthropic": "claude-3-5-sonnet-latest",
    "openai": "gpt-4o-mini",
}


API_KEY_PATTERNS = {
    "groq":      (r"^gsk_[A-Za-z0-9]{20,}$",     "Las keys de Groq empiezan con 'gsk_' seguidas de 20+ caracteres."),
    "anthropic": (r"^sk-ant-[A-Za-z0-9_\-]{20,}$", "Las keys de Anthropic empiezan con 'sk-ant-'."),
    "openai":    (r"^sk-[A-Za-z0-9_\-]{20,}$",   "Las keys de OpenAI empiezan con 'sk-'."),
}


def _validar_api_key(provider: str, api_key: str) -> str | None:
    """Verifica que la key luce como una del proveedor. Devuelve mensaje de
    error o None si es OK."""
    import re
    if not api_key or len(api_key) < 20:
        return "La API key parece demasiado corta. Copiala completa desde el portal del proveedor."
    pat = API_KEY_PATTERNS.get(provider)
    if pat and not re.fullmatch(pat[0], api_key):
        return f"El formato de la key no coincide con {provider}. {pat[1]}"
    return None


def _ping_proveedor(provider: str, api_key: str, model: str) -> tuple[bool, str]:
    """Llama al proveedor con un mensaje mínimo. Devuelve (ok, detalle)."""
    import requests as _req
    try:
        if provider == "anthropic":
            r = _req.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model, "max_tokens": 8,
                      "messages": [{"role": "user", "content": "ping"}]},
                timeout=10,
            )
        else:
            endpoint = ("https://api.openai.com/v1/chat/completions" if provider == "openai"
                        else "https://api.groq.com/openai/v1/chat/completions")
            r = _req.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "max_tokens": 8,
                      "messages": [{"role": "user", "content": "ping"}]},
                timeout=10,
            )
        if r.status_code == 200:
            return True, "ok"
        # Errores comunes → mensaje claro
        try:
            detail = r.json()
            msg = detail.get("error", {}).get("message") or str(detail)[:200]
        except Exception:
            msg = r.text[:200]
        if r.status_code in (401, 403):
            return False, f"API key rechazada por {provider} (HTTP {r.status_code}): {msg}"
        if r.status_code == 404:
            return False, f"Modelo '{model}' no encontrado en {provider}. Revisá el nombre exacto."
        if r.status_code == 429:
            return False, f"Cuota agotada en {provider}. Esperá o revisá tu plan."
        return False, f"HTTP {r.status_code}: {msg}"
    except _req.Timeout:
        return False, "Timeout al conectar con el proveedor."
    except Exception as exc:
        return False, f"Error de red: {exc}"


@superadmin_ai_bp.route("/configurar", methods=["POST"])
@login_required
def configurar_proveedor():
    """Guarda proveedor + modelo + API key con VALIDACIÓN Y PING.

    Solo super_admin — cambia claves soberanas.
    Si el usuario activa 'enabled', hacemos un ping real. Si falla:
    guardamos las claves pero forzamos enabled=0 y explicamos por qué.
    Así nunca queda una config marcada como activa que en realidad no funciona.
    """
    if getattr(current_user, "rol", None) != "super_admin":
        abort(403)
    from models import SiteConfig
    provider = (request.form.get("provider") or "").strip().lower()
    model = (request.form.get("model") or "").strip()
    api_key = (request.form.get("api_key") or "").strip()
    quiere_activar = bool(request.form.get("enabled"))

    if provider not in {"anthropic", "openai", "groq"}:
        flash("Proveedor no válido.", "danger")
        return redirect(url_for("superadmin_ai.index"))

    if not model:
        model = DEFAULT_MODELS.get(provider, "")

    # Si viene una key nueva, la validamos por formato.
    key_actual = SiteConfig.get("COMMERCIAL_AI_API_KEY", "") or ""
    if api_key:
        err = _validar_api_key(provider, api_key)
        if err:
            flash(err, "danger")
            return redirect(url_for("superadmin_ai.index"))
    else:
        # Sin key nueva: reutilizamos la existente pero también la validamos.
        api_key = key_actual
        if quiere_activar and _validar_api_key(provider, api_key):
            flash(
                "Para activar la IA externa necesitás una API key válida. "
                "Pegala en el campo 'API Key' y guardá otra vez.",
                "danger",
            )
            return redirect(url_for("superadmin_ai.index"))

    # Ping en vivo antes de marcar activo. Sin ping si no quieren activar
    # (permite guardar credenciales para probar después).
    enabled_final = "0"
    ping_msg = None
    if quiere_activar:
        ok, detalle = _ping_proveedor(provider, api_key, model)
        if ok:
            enabled_final = "1"
            ping_msg = f"✅ Conexión OK con {provider}/{model}."
        else:
            enabled_final = "0"  # nunca marcar activo con ping fallido
            ping_msg = f"⚠️ Guardé las credenciales pero NO activé la IA externa: {detalle}"

    SiteConfig.set("COMMERCIAL_AI_PROVIDER", provider, user_id=current_user.id, descripcion="asesor IA")
    SiteConfig.set("COMMERCIAL_AI_MODEL", model, user_id=current_user.id, descripcion="asesor IA")
    if api_key:
        SiteConfig.set("COMMERCIAL_AI_API_KEY", api_key, user_id=current_user.id, descripcion="asesor IA")
    SiteConfig.set("COMMERCIAL_AI_ENABLED", enabled_final, user_id=current_user.id, descripcion="asesor IA")

    try:
        db.session.commit()
        if ping_msg:
            flash(ping_msg, "success" if enabled_final == "1" else "warning")
        else:
            flash("Credenciales guardadas. Activá el toggle y guardá para probar la conexión.", "info")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error guardando configuración: {exc}", "danger")
    return redirect(url_for("superadmin_ai.index"))


@superadmin_ai_bp.route("/test-conexion", methods=["POST"])
@login_required
def test_conexion():
    """Ejecuta un ping al proveedor y devuelve resultado en JSON."""
    if not _authorised():
        abort(403)
    from routes.admin import _llamar_ia_analisis
    respuesta, error = _llamar_ia_analisis(
        "Responde solamente 'ok' para confirmar la conexión.",
        {"prueba": True},
    )
    if respuesta:
        return jsonify({"ok": True, "respuesta_muestra": (respuesta or "")[:200]})
    return jsonify({"ok": False, "error": error or "sin_respuesta"}), 400
