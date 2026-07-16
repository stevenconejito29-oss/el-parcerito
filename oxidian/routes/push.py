"""
Rutas Web Push — suscripción, baja y clave pública VAPID.

  GET  /api/push/vapid-key     → devuelve la clave pública VAPID
  POST /api/push/subscribe     → registra/actualiza una suscripción
  POST /api/push/unsubscribe   → elimina una suscripción
  POST /api/push/test          → envía notificación de prueba (solo admin)
"""
import ipaddress
import re
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request, session
from flask_login import current_user, login_required
from extensions import db
from models import PushSubscription, utcnow

push_bp = Blueprint("push", __name__)
_WEB_PUSH_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def _validate_subscription(endpoint: str, p256dh: str, auth_key: str) -> str | None:
    """Valida forma y destino sin restringir proveedores Web Push legítimos."""
    if len(endpoint) > 4096 or len(p256dh) > 512 or len(auth_key) > 256:
        return "Suscripción demasiado larga"
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return "El endpoint push debe usar HTTPS"
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        return "Endpoint push no permitido"
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return "Endpoint push no permitido"
    if not _WEB_PUSH_KEY_RE.fullmatch(p256dh) or not _WEB_PUSH_KEY_RE.fullmatch(auth_key):
        return "Claves push inválidas"
    return None


@push_bp.route("/vapid-key")
def vapid_key():
    """Clave pública VAPID para que el frontend suscriba al usuario."""
    from push_service import get_vapid_public_key, vapid_configuration_error
    key = get_vapid_public_key()
    if not key or vapid_configuration_error():
        return jsonify({"ok": False, "error": "VAPID no configurado"}), 503
    return jsonify({"ok": True, "public_key": key})


@push_bp.route("/status")
def status():
    """Diagnóstico seguro: nunca expone endpoints ni claves del dispositivo."""
    from push_service import vapid_configuration_error

    user_id = current_user.id if current_user.is_authenticated else session.get("push_cliente_id")
    active_devices = 0
    if user_id:
        active_devices = PushSubscription.query.filter_by(user_id=user_id, activo=True).count()
    return jsonify({
        "ok": True,
        "configured": not vapid_configuration_error(),
        "eligible": bool(user_id),
        "active_devices": active_devices,
    })


@push_bp.route("/subscribe", methods=["POST"])
def subscribe():
    """Registra o actualiza la suscripción push del usuario actual."""
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint", "").strip()
    p256dh   = (data.get("keys") or {}).get("p256dh", "").strip()
    auth_key = (data.get("keys") or {}).get("auth", "").strip()

    if not endpoint or not p256dh or not auth_key:
        return jsonify({"ok": False, "error": "Suscripción incompleta"}), 400
    validation_error = _validate_subscription(endpoint, p256dh, auth_key)
    if validation_error:
        return jsonify({"ok": False, "error": validation_error}), 400

    user_id = current_user.id if current_user.is_authenticated else session.get("push_cliente_id")
    if not user_id:
        return jsonify({"ok": False, "error": "Completa un pedido antes de activar avisos"}), 403
    ua = request.headers.get("User-Agent", "")[:300]
    rol = current_user.rol if current_user.is_authenticated else "cliente"

    # Upsert: si el endpoint ya existe, actualizar keys y user
    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub:
        sub.user_id  = user_id
        sub.p256dh   = p256dh
        sub.auth     = auth_key
        sub.rol      = rol
        sub.activo   = True
        sub.ultimo_uso = utcnow()
    else:
        sub = PushSubscription(
            user_id=user_id,
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
def unsubscribe():
    """Elimina la suscripción del endpoint enviado."""
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint", "").strip()
    user_id = current_user.id if current_user.is_authenticated else session.get("push_cliente_id")
    if endpoint and user_id:
        PushSubscription.query.filter_by(
            endpoint=endpoint, user_id=user_id
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
