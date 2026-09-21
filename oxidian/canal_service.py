"""Canal service — decide el canal optimo para notificar al cliente.

Objetivo: proteger la instancia de WhatsApp de banes de Meta al enviar
unicamente por WA los eventos que Meta considera service messages
(codigos/OTP/handoff) o mensajes dentro de la ventana de 24h desde el
ultimo inbound del cliente. El resto se enruta a push (VAPID) y/o al
chat web integrado.

Diseno defensivo:
  - API publica unica: `elegir_canal(cliente, evento, *, contexto)`.
  - Reglas evaluadas en orden explicito y documentado; devuelve un
    `ChannelDecision` que incluye la razon (auditoria en outbox).
  - Escape hatch por config: `notif_gate_activo=0` -> siempre 'wa' (legacy).
  - Override JSON por evento en `notif_canales_por_evento` para producto
    (sin redeploy).
  - Constantes: `TRANSACTIONAL_ALWAYS_WA` no se pueden vetar. Un test
    dedicado verifica que un evento critico jamas cae a canal=='none'.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from models import User  # noqa: F401

logger = logging.getLogger(__name__)


# -- Constantes de politica ------------------------------------------------

# Eventos que Meta considera transaccionales (service messages) o que el
# cliente espera imperativamente por WhatsApp. Estos bypassan el gate.
# Mantener sincronizado con services.WHATSAPP_TRANSACTIONAL_PURPOSES.
TRANSACTIONAL_ALWAYS_WA = frozenset({
    "order_confirmation",   # confirmacion al hacer un pedido
    "delivery_code",        # codigo de entrega al llegar el rider
    "points_otp",           # OTP para gestionar puntos
    "canje_codigo",         # codigo de canje de recompensa
    "web_chat_handoff",     # aviso al cliente de que un humano tomo su chat web
})

# Defaults por evento cuando NO hay override en SiteConfig.notif_canales_por_evento.
# Se lista que canales SON ACEPTABLES para cada evento; canal_service
# elegira el primero disponible en el cliente.
# Valores: 'wa', 'push', 'web'. Un evento sin entrada usa 'push,web,wa'.
DEFAULT_CANALES_POR_EVENTO: dict[str, tuple[str, ...]] = {
    "delivery_en_camino": ("push", "web", "wa"),
    "delivery_en_puerta": ("push", "web", "wa"),
    "order_status_change": ("push", "web"),
    "review_request": ("push", "web"),
}


CANAL_WA = "wa"
CANAL_PUSH = "push"
CANAL_WEB = "web"
CANAL_PUSH_WEB = "push+web"
CANAL_NONE = "none"


@dataclass
class ChannelDecision:
    canal: str
    razon: str
    metadata: dict = field(default_factory=dict)

    def is_wa(self) -> bool:
        return self.canal == CANAL_WA

    def is_none(self) -> bool:
        return self.canal == CANAL_NONE


# -- Helpers privados ------------------------------------------------------

def _get_config():
    from models import SiteConfig
    return SiteConfig


def _bool_flag(value, default_true: bool = True) -> bool:
    if value is None:
        return default_true
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on", "si", "sí"}:
        return True
    if s in {"0", "false", "no", "off", ""}:
        return False
    return default_true


def _push_eligible(cliente) -> bool:
    """True si el cliente tiene al menos una suscripcion push activa."""
    if cliente is None or not getattr(cliente, "id", None):
        return False
    try:
        from models import PushSubscription
        return (
            PushSubscription.query
            .filter_by(user_id=cliente.id, activo=True)
            .first()
            is not None
        )
    except Exception:
        logger.exception("canal_service: fallo consultando push_eligible")
        return False


def _web_chat_activo(cliente, ventana_min: int = 30) -> bool:
    """True si el cliente tiene una conversacion web reciente.

    Consideramos activa cualquier conversacion (bot/agente) cuya
    last_activity_at este dentro de la ventana. Un cliente que abrio el
    chat integrado hace <30 min recibira el mensaje in-app; si ademas
    tiene push, notify_user despierta el aviso incluso con la pestana
    cerrada.
    """
    if cliente is None or not getattr(cliente, "id", None):
        return False
    try:
        from models import WebChatConversation, utcnow
        umbral = utcnow() - timedelta(minutes=max(1, int(ventana_min)))
        return (
            WebChatConversation.query
            .filter(
                WebChatConversation.customer_id == cliente.id,
                WebChatConversation.status.in_(["bot", "waiting_agent", "active_agent"]),
                WebChatConversation.last_activity_at >= umbral,
            )
            .first()
            is not None
        )
    except Exception:
        logger.exception("canal_service: fallo consultando web_chat_activo")
        return False


def _dentro_ventana_wa(cliente, ventana_horas: int) -> bool:
    """True si el cliente envio un inbound WA hace <= ventana_horas.

    Robusto ante ts naive (BD) o tz-aware (test/otras fuentes): normaliza
    ambos a naive UTC antes de restar.
    """
    if cliente is None:
        return False
    ts = getattr(cliente, "last_wa_inbound_at", None)
    if ts is None:
        return False
    try:
        from datetime import datetime, timezone as _tz
        if ts.tzinfo is not None:
            ts_naive = ts.astimezone(_tz.utc).replace(tzinfo=None)
        else:
            ts_naive = ts
        now_naive = datetime.now(_tz.utc).replace(tzinfo=None)
        return (now_naive - ts_naive) <= timedelta(hours=max(1, int(ventana_horas)))
    except Exception:
        return False


def _canales_permitidos_para_evento(evento: str) -> tuple[str, ...]:
    """Lee override JSON de SiteConfig o cae al default por evento."""
    SiteConfig = _get_config()
    raw = (SiteConfig.get("notif_canales_por_evento", "") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and evento in data:
                lst = data[evento]
                if isinstance(lst, list) and all(isinstance(x, str) for x in lst):
                    return tuple(x.strip().lower() for x in lst if x.strip())
        except json.JSONDecodeError:
            logger.warning("canal_service: notif_canales_por_evento no es JSON valido")
    if evento in DEFAULT_CANALES_POR_EVENTO:
        return DEFAULT_CANALES_POR_EVENTO[evento]
    return ("push", "web", "wa")


def _ventana_wa_horas() -> int:
    SiteConfig = _get_config()
    raw = SiteConfig.get("notif_ventana_wa_horas", "24") or "24"
    try:
        return max(1, int(str(raw).strip()))
    except (TypeError, ValueError):
        return 24


def _gate_activo() -> bool:
    SiteConfig = _get_config()
    return _bool_flag(SiteConfig.get("notif_gate_activo", "1"), default_true=True)


# -- API publica -----------------------------------------------------------

def elegir_canal(cliente, evento: str, *, contexto: Optional[dict] = None) -> ChannelDecision:
    """Decide el canal optimo para notificar a un cliente sobre un evento.

    Reglas (en orden estricto):
      1. Evento en TRANSACTIONAL_ALWAYS_WA -> 'wa' (bypass total).
      2. Toggle `notif_gate_activo=0` -> 'wa' (escape hatch legacy).
      3. Sin cliente identificable -> 'wa' (fallback historico).
      4. Restringimos por override/default `notif_canales_por_evento`.
      5. Si 'push' permitido y push activo, y 'web' permitido y web
         activo -> 'push+web'.
      6. Si 'push' permitido y push activo -> 'push'.
      7. Si 'web' permitido y web activo -> 'web'.
      8. Si 'wa' permitido y dentro de ventana WA (24h) -> 'wa'.
      9. Fallback duro -> 'none' (se registra skip para metricas).
    """
    evento_norm = str(evento or "").strip()
    contexto = contexto or {}

    # 1) transaccional siempre WA
    if evento_norm in TRANSACTIONAL_ALWAYS_WA:
        return ChannelDecision(CANAL_WA, "transactional_always_wa")

    # 2) escape hatch legacy
    if not _gate_activo():
        return ChannelDecision(CANAL_WA, "gate_desactivado")

    # 3) sin cliente
    if cliente is None or not getattr(cliente, "id", None):
        return ChannelDecision(CANAL_WA, "cliente_desconocido")

    permitidos = set(_canales_permitidos_para_evento(evento_norm))
    push_ok = CANAL_PUSH in permitidos and _push_eligible(cliente)
    web_ok = CANAL_WEB in permitidos and _web_chat_activo(cliente)
    wa_permitido = CANAL_WA in permitidos
    dentro_wa = wa_permitido and _dentro_ventana_wa(cliente, _ventana_wa_horas())

    meta = {
        "push_ok": push_ok,
        "web_ok": web_ok,
        "dentro_ventana_wa": dentro_wa,
        "permitidos": sorted(permitidos),
    }

    # 5) push + web
    if push_ok and web_ok:
        return ChannelDecision(CANAL_PUSH_WEB, "push_eligible+web_activo", meta)
    # 6) push
    if push_ok:
        return ChannelDecision(CANAL_PUSH, "push_eligible", meta)
    # 7) web
    if web_ok:
        return ChannelDecision(CANAL_WEB, "web_activo_sin_push", meta)
    # 8) ventana WA
    if dentro_wa:
        return ChannelDecision(CANAL_WA, "fallback_ventana_wa", meta)
    # 9) skip
    logger.warning(
        "canal_service: evento=%s cliente_id=%s canal=none razon=sin_canal_disponible",
        evento_norm, getattr(cliente, "id", None),
    )
    return ChannelDecision(CANAL_NONE, "sin_canal_disponible", meta)


def registrar_skip(cliente, evento: str, decision: ChannelDecision,
                   pedido_id: int | None = None) -> None:
    """Persiste un NotificationOutbox status='skipped' para metricas.

    No pierde silenciosamente la intencion de notificar: queda auditable
    en la tabla junto con el resto de decisiones y puede replayearse por
    otro trigger. `estado='skipped'` sigue siendo un string en outbox.
    """
    try:
        from extensions import db
        from models import NotificationOutbox
        job = NotificationOutbox(
            canal=CANAL_NONE,
            evento=evento,
            destinatario=str(getattr(cliente, "telefono", "") or "") or "unknown",
            payload_json=json.dumps({
                "razon": decision.razon,
                "metadata": decision.metadata,
            }, ensure_ascii=False),
            estado="skipped",
            pedido_id=pedido_id,
            user_id=getattr(cliente, "id", None),
            max_intentos=1,
        )
        db.session.add(job)
    except Exception:
        logger.exception("canal_service: no se pudo registrar skip")


def enviar_por_canal(cliente, decision: ChannelDecision, *,
                     evento: str,
                     titulo: str,
                     mensaje: str,
                     url: str = "/",
                     pedido_id: int | None = None) -> tuple[bool, str]:
    """Ejecuta el envio segun la decision de canal.

    Devuelve (ok, canal_efectivo). Para canal='wa' el llamador debe
    encolar por outbox estandar (encolar_whatsapp_generico) porque las
    plantillas WA son especificas del evento.
    """
    canal = decision.canal
    device_hash = None
    if canal in (CANAL_PUSH, CANAL_PUSH_WEB, CANAL_WEB) and getattr(cliente, "rol", "cliente") == "cliente":
        from extensions import db
        from models import Order
        order = db.session.get(Order, pedido_id) if pedido_id else None
        if not order or order.cliente_id != cliente.id or not order.customer_device_hash:
            return False, CANAL_NONE
        device_hash = order.customer_device_hash
    if canal in (CANAL_PUSH, CANAL_PUSH_WEB):
        try:
            from push_service import notify_user
            notify_user(cliente.id, titulo, mensaje, url=url, tag=evento, device_hash=device_hash)
            logger.info(
                "canal_service: push enviado evento=%s cliente_id=%s",
                evento, cliente.id,
            )
        except Exception:
            logger.exception("canal_service: fallo push")
            return False, canal
    if canal in (CANAL_WEB, CANAL_PUSH_WEB):
        try:
            from extensions import db  # noqa: F401
            from models import WebChatConversation, utcnow
            from web_chat_service import add_message
            conv = (
                WebChatConversation.query
                .filter(
                    WebChatConversation.customer_id == cliente.id,
                    WebChatConversation.device_hash == device_hash,
                    WebChatConversation.status.in_(["bot", "waiting_agent", "active_agent"]),
                )
                .order_by(WebChatConversation.last_activity_at.desc())
                .first()
            )
            if conv is not None:
                add_message(conv, "system", mensaje)
                conv.last_activity_at = utcnow()
                logger.info(
                    "canal_service: web_chat enviado evento=%s cliente_id=%s conv_id=%s",
                    evento, cliente.id, conv.id,
                )
        except Exception:
            logger.exception("canal_service: fallo web_chat")
    if canal == CANAL_NONE:
        registrar_skip(cliente, evento, decision, pedido_id=pedido_id)
        return False, CANAL_NONE
    if canal == CANAL_WA:
        return True, CANAL_WA
    return True, canal
