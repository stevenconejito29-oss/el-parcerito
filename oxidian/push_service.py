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
import os
import uuid
from typing import Optional
from urllib.parse import urlsplit

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


def _prepare_vapid_private_key(private_key: str):
    """Convierte PEM almacenado en BD al objeto esperado por pywebpush.

    `pywebpush` interpreta cualquier string que no sea una ruta como Base64
    DER. Pasarle el contenido PEM directamente provoca
    ``Could not deserialize key data`` aunque la clave sea válida.
    Se conserva compatibilidad con claves raw/Base64 existentes.
    """
    value = (private_key or "").strip()
    if "-----BEGIN" not in value:
        return value
    from py_vapid import Vapid
    return Vapid.from_pem(value.encode("ascii"))


def _vapid_subject() -> str | None:
    """Contacto VAPID aceptado también por Apple Push Notification service."""
    from store_config import get_store_value

    email = (get_store_value("EMAIL_CONTACTO") or "").strip()
    if "@" in email:
        domain = email.rsplit("@", 1)[-1].strip().lower().rstrip(".")
        if domain and not domain.endswith((".invalid", ".localhost", ".local", ".test")):
            return f"mailto:{email}"

    public_url = (
        get_store_value("OXIDIAN_PUBLIC_URL")
        or os.environ.get("OXIDIAN_PUBLIC_URL")
        or get_store_value("TIENDA_URL")
        or ""
    ).strip()
    parsed = urlsplit(public_url)
    if parsed.scheme == "https" and parsed.hostname:
        return f"https://{parsed.netloc}"
    return None


def vapid_configuration_error() -> str | None:
    """Devuelve un diagnóstico sin exponer material criptográfico."""
    public_key, private_key = _get_vapid_keys()
    if not public_key or not private_key:
        return "vapid_no_configurado"
    if not _vapid_subject():
        return "vapid_subject_invalido"
    try:
        import base64
        import hmac
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid

        prepared = _prepare_vapid_private_key(private_key)
        vapid = prepared if hasattr(prepared, "sign") else Vapid.from_string(prepared)
        derived_raw = vapid.public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
        derived_public = base64.urlsafe_b64encode(derived_raw).rstrip(b"=").decode("ascii")
        if not hmac.compare_digest(derived_public, public_key.strip().rstrip("=")):
            return "vapid_claves_no_corresponden"
        vapid.sign({
            "sub": "mailto:diagnostico@example.invalid",
            "aud": "https://push.example.invalid",
            "exp": int(__import__("time").time()) + 300,
        })
        return None
    except Exception:
        logger.exception("La clave privada VAPID no se puede cargar")
        return "vapid_clave_invalida"


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
            vapid_private_key=_prepare_vapid_private_key(priv_key),
            vapid_claims=vapid_claims,
        )
        return True, False, None
    except WebPushException as e:
        code = e.response.status_code if e.response is not None else 0
        response_reason = ""
        if e.response is not None:
            try:
                response_body = e.response.json()
                response_reason = str(response_body.get("reason") or "")[:80]
            except (TypeError, ValueError):
                response_reason = (e.response.text or "").strip()[:80]
        if code in (404, 410):
            return False, True, f"push_expired:{code}"
        logger.warning(
            "WebPush error %s para endpoint %s reason=%s: %s",
            code, sub_row.endpoint[:40], response_reason or "-", e,
        )
        suffix = f":{response_reason}" if response_reason else ""
        return False, False, f"webpush_error:{code or 'unknown'}{suffix}"
    except Exception as e:
        logger.warning("Error de red enviando push: %s", e)
        return False, False, str(e)


def _send_one(sub_row, payload: dict, vapid_claims: dict, priv_key: str) -> bool:
    """Compatibilidad: True si enviada o si la suscripcion debe descartarse."""
    ok, expired, _ = _send_one_result(sub_row, payload, vapid_claims, priv_key)
    return ok or expired


def _dispatch(subscriptions, payload: dict, *, evento: str = "web_push",
              push_broadcast_id: int | None = None,
              commit: bool = True) -> int:
    """Encola push persistente para que lo procese el worker de outbox."""
    from extensions import db
    from models import NotificationOutbox

    total = 0
    for sub in subscriptions:
        job_payload = {
            "subscription_id": sub.id,
            "payload": payload,
        }
        db.session.add(NotificationOutbox(
            canal="push",
            evento=evento,
            destinatario=str(sub.id),
            payload_json=json.dumps(job_payload, ensure_ascii=False, default=str),
            user_id=sub.user_id,
            push_broadcast_id=push_broadcast_id,
            max_intentos=3,
        ))
        total += 1
        # Mantiene acotado el unit-of-work cuando una tienda acumula miles de
        # dispositivos, pero conserva una única transacción atómica.
        if total % 500 == 0:
            db.session.flush()
    if commit:
        db.session.commit()
    return total


PUSH_BROADCAST_AUDIENCES = frozenset({"all", "customers", "staff"})


def _normalise_broadcast_url(value: str) -> str:
    """Acepta únicamente destinos internos para evitar redirecciones desde push."""
    target = (value or "/").strip()
    parsed = urlsplit(target)
    if (
        not target.startswith("/")
        or target.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or len(target) > 500
    ):
        raise ValueError("El destino debe ser una ruta interna de la aplicación.")
    return target


def queue_push_broadcast(*, creator_id: int, idempotency_key: str,
                         title: str, body: str, url: str = "/",
                         audience: str = "all"):
    """Crea una campaña y sus trabajos Web Push en una transacción.

    Devuelve ``(campaña, creada)``. Repetir la misma clave no vuelve a encolar
    dispositivos, incluso si el navegador reenvía el formulario.
    """
    from extensions import db
    from models import PushBroadcast, PushSubscription
    from sqlalchemy.exc import IntegrityError

    try:
        key = str(uuid.UUID(str(idempotency_key)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("La solicitud de envío ha caducado; recarga la página.")

    clean_title = (title or "").strip()
    clean_body = (body or "").strip()
    clean_audience = (audience or "all").strip().lower()
    if not 3 <= len(clean_title) <= 80:
        raise ValueError("El título debe tener entre 3 y 80 caracteres.")
    # El Service Worker limita el cuerpo a 180: validar igual aquí evita que el
    # operador previsualice un texto distinto del que finalmente recibe Android.
    if not 3 <= len(clean_body) <= 180:
        raise ValueError("El mensaje debe tener entre 3 y 180 caracteres.")
    if clean_audience not in PUSH_BROADCAST_AUDIENCES:
        raise ValueError("La audiencia seleccionada no es válida.")
    clean_url = _normalise_broadcast_url(url)

    existing = PushBroadcast.query.filter_by(idempotency_key=key).first()
    if existing:
        return existing, False

    query = PushSubscription.query.filter(PushSubscription.activo.is_(True))
    if clean_audience == "customers":
        query = query.filter(PushSubscription.rol == "cliente")
    elif clean_audience == "staff":
        query = query.filter(PushSubscription.rol != "cliente")

    campaign = PushBroadcast(
        idempotency_key=key,
        titulo=clean_title,
        cuerpo=clean_body,
        url=clean_url,
        audiencia=clean_audience,
        creado_por_id=creator_id,
    )
    db.session.add(campaign)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return PushBroadcast.query.filter_by(idempotency_key=key).one(), False

    payload = _build_payload(
        clean_title,
        clean_body,
        clean_url,
        tag=f"broadcast-{campaign.id}",
    )
    campaign.destinatarios = _dispatch(
        query.yield_per(500),
        payload,
        evento="pwa_broadcast",
        push_broadcast_id=campaign.id,
        commit=False,
    )
    db.session.commit()
    return campaign, True


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

    subject = _vapid_subject()
    if not subject:
        return False, "vapid_subject_invalido"
    ok, expired, error = _send_one_result(
        sub,
        push_payload,
        vapid_claims={"sub": subject},
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
    try:
        from flask import current_app
        asset_v = current_app.config.get("ASSET_VERSION", "52")
    except RuntimeError:
        asset_v = "52"
    from store_config import get_store_value
    return {
        "title": title,
        "body": body,
        "url": url,
        "icon": icon or get_store_value("APP_ICON_URL") or f"/static/pwa-icon-192.png?v={asset_v}",
        # Android transforma `badge` en una silueta monocroma; usar el favicon
        # a color producía un cuadrado ilegible en la barra de estado.
        "badge": badge or f"/static/pwa-badge-96.png?v={asset_v}",
        "tag": tag,
        "requireInteraction": bool(require_interaction),
        "timestamp": int(__import__("time").time() * 1000),
        "badgeCount": 1,
        "actions": [{"action": "open", "title": "Ver ahora"}],
    }
