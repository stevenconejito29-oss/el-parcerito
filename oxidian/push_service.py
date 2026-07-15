"""
Servicio de Web Push Notifications para Oxidian.

Flujo:
  1. El frontend suscribe al usuario con la clave pública VAPID.
  2. La suscripción (endpoint + keys) se guarda en PushSubscription.
  3. Desde el backend se llama a send_push(...) con un mensaje JSON.
  4. El service worker muestra la notificación al usuario.

Estrategia de targeting:
  - notify_admin()   → todos los usuarios con rol admin/super_admin
  - notify_user()    → suscripciones de un user_id específico
  - notify_roles()   → todos los usuarios con los roles indicados
"""
from __future__ import annotations
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── VAPID keys (se leen de SiteConfig en tiempo de ejecución) ──────────────

def _get_vapid_keys() -> tuple[str, str]:
    """Devuelve (public_key, private_key) desde SiteConfig."""
    try:
        from models import SiteConfig
        pub  = SiteConfig.get("VAPID_PUBLIC_KEY",  "")
        priv = SiteConfig.get("VAPID_PRIVATE_KEY", "")
        if pub and priv:
            return pub, priv
    except Exception:
        logger.exception("No se pudieron leer claves VAPID desde SiteConfig")
    return "", ""


def get_vapid_public_key() -> str:
    pub, _ = _get_vapid_keys()
    return pub


# ── Envío de una notificación push a una suscripción ──────────────────────

def _send_one_result(sub_row, payload: dict, vapid_claims: dict, priv_key: str) -> tuple[bool, bool, str | None]:
    """Envia push a una suscripcion. Devuelve (ok, expirada, error)."""
    from pywebpush import webpush, WebPushException
    try:
        webpush(
            subscription_info={
                "endpoint": sub_row.endpoint,
                "keys": {"p256dh": sub_row.p256dh, "auth": sub_row.auth},
            },
            data=json.dumps(payload),
            vapid_private_key=priv_key,
            vapid_claims=vapid_claims,
        )
        return True, False, None
    except WebPushException as e:
        code = e.response.status_code if e.response is not None else 0
        if code in (404, 410):
            return False, True, f"push_expired:{code}"
        logger.warning("WebPush error %s para endpoint %s: %s", code, sub_row.endpoint[:40], e)
        return False, False, f"webpush_error:{code or 'unknown'}"
    except Exception as e:
        logger.warning("Error de red enviando push: %s", e)
        return False, False, str(e)


def _send_one(sub_row, payload: dict, vapid_claims: dict, priv_key: str) -> bool:
    """Compatibilidad: True si enviada o si la suscripcion debe descartarse."""
    ok, expired, _ = _send_one_result(sub_row, payload, vapid_claims, priv_key)
    return ok or expired


def _dispatch(subscriptions, payload: dict) -> None:
    """Encola push persistente para que lo procese el worker de outbox."""
    if not subscriptions:
        return
    from extensions import db
    from models import NotificationOutbox

    for sub in subscriptions:
        job_payload = {
            "subscription_id": sub.id,
            "payload": payload,
        }
        db.session.add(NotificationOutbox(
            canal="push",
            evento="web_push",
            destinatario=str(sub.id),
            payload_json=json.dumps(job_payload, ensure_ascii=False, default=str),
            user_id=sub.user_id,
            max_intentos=3,
        ))
    db.session.commit()


def send_push_outbox_payload(payload: dict) -> tuple[bool, str | None]:
    """Procesa un job `canal=push` desde notification_outbox."""
    from extensions import db
    from models import PushSubscription

    subscription_id = payload.get("subscription_id")
    push_payload = payload.get("payload") or {}
    if not subscription_id or not push_payload:
        return False, "push_payload_invalido"

    sub = db.session.get(PushSubscription, int(subscription_id))
    if not sub or not sub.activo:
        return True, None

    pub, priv = _get_vapid_keys()
    if not pub or not priv:
        return False, "vapid_no_configurado"

    from store_config import get_store_value
    email = get_store_value("EMAIL_CONTACTO") or "admin@example.invalid"
    ok, expired, error = _send_one_result(
        sub,
        push_payload,
        vapid_claims={"sub": f"mailto:{email}"},
        priv_key=priv,
    )
    if expired:
        sub.activo = False
        logger.info("Push expirada, marcando inactiva: id=%s", sub.id)
        return True, None
    return ok, error


# ── API pública del servicio ──────────────────────────────────────────────

def notify_roles(roles: list[str], title: str, body: str, url: str = "/",
                 icon: Optional[str] = None, badge: Optional[str] = None,
                 *, tag: Optional[str] = None,
                 require_interaction: bool = False) -> None:
    """Envía notificación push a todos los usuarios con los roles indicados."""
    from models import PushSubscription, User
    subs = PushSubscription.query.filter(
        PushSubscription.activo.is_(True),
        PushSubscription.usuario.has(User.rol.in_(roles)),
    ).all()
    if not subs:
        return
    payload = _build_payload(title, body, url, icon, badge, tag, require_interaction)
    _dispatch(subs, payload)


def notify_user(user_id: int, title: str, body: str, url: str = "/",
                icon: Optional[str] = None, badge: Optional[str] = None,
                *, tag: Optional[str] = None,
                require_interaction: bool = False) -> None:
    """Envía notificación push a todas las suscripciones activas de un usuario."""
    from models import PushSubscription
    subs = PushSubscription.query.filter_by(user_id=user_id, activo=True).all()
    if not subs:
        return
    payload = _build_payload(title, body, url, icon, badge, tag, require_interaction)
    _dispatch(subs, payload)


def notify_new_order(pedido) -> None:
    """Alerta a admins y al rol de preparación correspondiente.

    Antes: solo se avisaba a admin/super_admin. Cocina y preparación se
    enteraban recargando la vista o via SSE, con lag. Ahora el rol
    operativo correcto recibe push directo con URL a su cola.
    """
    num = pedido.numero_pedido
    # Defensa: pedido.origen y pedido.metodo_pago pueden ser None en pedidos
    # legacy o durante la ventana entre creación y elección de método. `dict.get`
    # evalúa `default` eagerly, así que `pedido.origen.capitalize()` como default
    # crasheaba si origen era None. Coalescemos antes.
    _origen = (pedido.origen or "manual")
    origen_label = {"online": "Web", "whatsapp": "WhatsApp", "presencial": "POS"}.get(
        _origen, _origen.capitalize()
    )
    total = f"€{float(pedido.total):.2f}" if pedido.total else ""
    _metodo = (pedido.metodo_pago or "").capitalize() or "sin método"

    # Rol operativo destinatario según tipo. Nunca lanza si `_tipo_pedido`
    # falla — la notificación es best-effort, no debe romper el checkout.
    prep_role: Optional[str] = None
    prep_url: str = "/preparador/pedidos"
    prep_prefix: str = "🔔"
    try:
        from services import _tipo_pedido
        if _tipo_pedido(pedido) == "programado":
            prep_role = "preparacion"
            prep_prefix = "📦 Nuevo encargo"
        else:
            prep_role = "cocina"
            prep_prefix = "🍳 Nuevo pedido"
    except Exception:
        logger.exception("notify_new_order: no se pudo determinar rol operativo")

    notify_roles(
        ["admin", "super_admin"],
        title=f"🔔 Nuevo pedido {origen_label}",
        body=f"#{num} · {total} · {_metodo}",
        url="/admin/pedidos",
        tag=f"nuevo-pedido-{pedido.id}",
        require_interaction=True,
    )
    if prep_role:
        notify_roles(
            [prep_role],
            title=f"{prep_prefix} — {origen_label}",
            body=f"#{num} · {total}",
            url=prep_url,
            tag=f"nuevo-pedido-{pedido.id}",
            require_interaction=True,
        )


def notify_order_state(pedido) -> None:
    """Notifica al cliente del cambio de estado de su pedido."""
    if not pedido.cliente_id:
        return
    from models import SiteConfig
    _tt = (SiteConfig.get("TIPO_TIENDA", "comida") or "comida").lower()
    _es_comida = (_tt == "comida")
    _prep_emoji = "🍳" if _es_comida else "📦"
    _entregado_extra = "¡Buen provecho!" if _es_comida else "¡Que lo disfrutes!"
    msgs = {
        "armando":   (f"{_prep_emoji} Preparando tu pedido", f"#{pedido.numero_pedido} está siendo preparado."),
        "listo":     ("✅ Pedido listo", f"#{pedido.numero_pedido} está listo para salir."),
        "en_ruta":   ("🚴 En camino", f"Tu pedido #{pedido.numero_pedido} viene de camino."),
        "entregado": ("🎉 Pedido entregado", f"#{pedido.numero_pedido} ha sido entregado. {_entregado_extra}"),
        "cancelado": ("❌ Pedido cancelado", f"#{pedido.numero_pedido} ha sido cancelado."),
    }
    entry = msgs.get(pedido.estado)
    if not entry:
        return
    title, body = entry
    notify_user(
        pedido.cliente_id, title, body, url="/",
        tag=f"pedido-{pedido.id}",
    )


def _build_payload(title, body, url, icon=None, badge=None, tag=None,
                   require_interaction=False) -> dict:
    from store_config import get_store_value
    return {
        "title": title,
        "body": body,
        "url": url,
        "icon": icon or get_store_value("APP_ICON_URL") or "/static/pwa-icon-192.png",
        "badge": badge or "/static/favicon-32.png",
        "tag": tag,
        "requireInteraction": bool(require_interaction),
        "timestamp": int(__import__("time").time() * 1000),
    }
