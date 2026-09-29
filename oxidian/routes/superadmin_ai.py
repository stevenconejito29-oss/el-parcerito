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
from services import ai_advisor


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
    else:
        prompt = (request.form.get("mensaje") or "").strip()

    if not prompt:
        if request.headers.get("Accept") == "application/json":
            return jsonify({"ok": False, "error": "mensaje_vacio"}), 400
        flash("Escribí tu pregunta o elegí una acción rápida.", "warning")
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
