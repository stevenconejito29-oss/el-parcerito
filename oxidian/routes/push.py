"""
Rutas Web Push — suscripción, baja y clave pública VAPID.

  GET  /api/push/vapid-key     → devuelve la clave pública VAPID
  POST /api/push/subscribe     → registra/actualiza una suscripción
  POST /api/push/unsubscribe   → elimina una suscripción
  POST /api/push/test          → envía notificación de prueba (solo admin)
"""
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from extensions import db
from models import PushSubscription, utcnow

push_bp = Blueprint("push", __name__)


@push_bp.route("/vapid-key")
def vapid_key():
    """Clave pública VAPID para que el frontend suscriba al usuario."""
    from push_service import get_vapid_public_key
    key = get_vapid_public_key()
    if not key:
        return jsonify({"ok": False, "error": "VAPID no configurado"}), 503
    return jsonify({"ok": True, "public_key": key})


@push_bp.route("/subscribe", methods=["POST"])
@login_required
def subscribe():
    """Registra o actualiza la suscripción push del usuario actual."""
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint", "").strip()
    p256dh   = (data.get("keys") or {}).get("p256dh", "").strip()
    auth_key = (data.get("keys") or {}).get("auth", "").strip()

    if not endpoint or not p256dh or not auth_key:
        return jsonify({"ok": False, "error": "Suscripción incompleta"}), 400

    ua = request.headers.get("User-Agent", "")[:300]
    rol = current_user.rol if current_user.is_authenticated else None

    # Upsert: si el endpoint ya existe, actualizar keys y user
    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub:
        sub.user_id  = current_user.id
        sub.p256dh   = p256dh
        sub.auth     = auth_key
        sub.rol      = rol
        sub.activo   = True
        sub.ultimo_uso = utcnow()
    else:
        sub = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth_key,
            rol=rol,
            user_agent=ua,
        )
        db.session.add(sub)

    db.session.commit()
    return jsonify({"ok": True})


@push_bp.route("/unsubscribe", methods=["POST"])
@login_required
def unsubscribe():
    """Elimina la suscripción del endpoint enviado."""
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint", "").strip()
    if endpoint:
        PushSubscription.query.filter_by(
            endpoint=endpoint, user_id=current_user.id
        ).delete()
        db.session.commit()
    return jsonify({"ok": True})


@push_bp.route("/test", methods=["POST"])
@login_required
def test_push():
    """Envía una notificación de prueba al usuario actual (solo admin/super_admin)."""
    if current_user.rol not in ("admin", "super_admin"):
        return jsonify({"ok": False, "error": "Sin permiso"}), 403
    from push_service import notify_user
    notify_user(
        current_user.id,
        title="🔔 Notificaciones activas",
        body="Las notificaciones push están funcionando correctamente.",
        url="/admin/dashboard",
    )
    return jsonify({"ok": True, "msg": "Notificación de prueba enviada"})
