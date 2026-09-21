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


def _push_user():
    from models import User
    if current_user.is_authenticated:
        return current_user if current_user.activo else None
    user = db.session.get(User, session.get("push_cliente_id")) if session.get("push_cliente_id") else None
    return user if user and user.activo and user.rol == "cliente" else None


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

    user = _push_user()
    user_id = user.id if user else None
    from device_identity import browser_device_hash
    device_hash = browser_device_hash()
    active_devices = 0
    this_device_active = False
    if user_id:
        active_devices = PushSubscription.query.filter_by(user_id=user_id, activo=True).count()
        endpoint = (request.args.get("endpoint") or "").strip()
        if endpoint:
            this_device_active = PushSubscription.query.filter_by(
                user_id=user_id, endpoint=endpoint, activo=True, device_hash=device_hash,
            ).first() is not None
    return jsonify({
        "ok": True,
        "configured": not vapid_configuration_error(),
        "eligible": bool(user_id),
        "active_devices": active_devices,
        "this_device_active": this_device_active,
    })


@push_bp.route("/subscribe", methods=["POST"])
def subscribe():
    """Registra o actualiza la suscripción push del usuario actual."""
    from device_identity import browser_device_hash
    from order_access import visitor_order_tokens
    from models import Order
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("keys"), dict):
        return jsonify({"ok": False, "error": "Suscripción inválida"}), 400
    values = [data.get("endpoint"), data["keys"].get("p256dh"), data["keys"].get("auth")]
    if any(not isinstance(value,str) or not value.strip() for value in values):
        return jsonify({"ok": False, "error": "Suscripción incompleta"}), 400
    endpoint,p256dh,auth_key = (value.strip() for value in values)
    try:
        error = _validate_subscription(endpoint,p256dh,auth_key)
    except ValueError:
        error = "Endpoint push inválido"
    if error:
        return jsonify({"ok": False, "error": error}), 400
    user = _push_user()
    if not user:
        return jsonify({"ok": False, "error": "Completa un pedido antes de activar avisos"}), 403
    device_hash = browser_device_hash(create=True)
    values = dict(user_id=user.id, device_hash=device_hash, endpoint=endpoint,
                  p256dh=p256dh, auth=auth_key, rol=user.rol, activo=True,
                  user_agent=request.headers.get("User-Agent", "")[:300], ultimo_uso=utcnow())
    # Una sola operación con la clave única del proveedor; dos pestañas no
    # pueden crear registros duplicados ni provocar un 500 por la carrera.
    if db.engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    statement=insert(PushSubscription).values(**values)
    db.session.execute(statement.on_conflict_do_update(index_elements=["endpoint"],set_={k:v for k,v in values.items() if k != "endpoint"}))
    # Los pedidos anteriores solo se vinculan si ESTA sesión conserva su
    # autorización. Nunca se deduce el dispositivo por teléfono o user_id.
    ids=list(visitor_order_tokens())
    if ids:
        Order.query.filter(Order.id.in_(ids),Order.cliente_id==user.id,Order.customer_device_hash.is_(None)).update(
            {Order.customer_device_hash:device_hash},synchronize_session=False)
    db.session.commit()
    return jsonify({"ok": True, "this_device_active": True})


@push_bp.route("/unsubscribe", methods=["POST"])
def unsubscribe():
    """Elimina la suscripción del endpoint enviado."""
    data = request.get_json(silent=True)
    if not isinstance(data,dict) or not isinstance(data.get("endpoint"),str):
        return jsonify({"ok":False,"error":"Suscripción inválida"}),400
    endpoint = data["endpoint"].strip()
    user = _push_user()
    user_id = user.id if user else None
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
