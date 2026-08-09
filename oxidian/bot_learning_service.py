"""Auto-aprendizaje pasivo del chatbot.

Objetivo: cada vez que el LLM (aiSmartReply) responde a una pregunta que
el sistema determinista NO reconoció, registrar una "señal" que un
admin puede revisar en /admin/bot/aprendizaje para promover a FAQ
canned o keyword permanente. Con el tiempo el bot depende MENOS del
LLM (ahorra tokens y latencia) para las preguntas realmente frecuentes
de sus clientes.

Diseño conservador de v1:
    - Agrupamiento por hash EXACTO del mensaje normalizado (no fuzzy).
      "cambiar dirección" y "la dirección cambia" caen al mismo hash
      porque las palabras se ordenan alfabéticamente antes del hash.
    - Aprobación MANUAL — nada se aplica al vocabulario automáticamente
      en v1. El admin ve la lista y decide.
    - Fire-and-forget desde el bot: si falla, no rompe la conversación
      del cliente. Cualquier error se loguea y se descarta.
    - RGPD: teléfono guardado como hash-MAC con OXIDIAN_KEY. Solo el
      último cliente que hizo la pregunta — no un historial. Retención
      configurable (default 90 días).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import unicodedata

from datetime import datetime, timedelta

from flask import current_app


# ── Normalización de mensajes ──────────────────────────────────────────

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")
# Stopwords conservadoras — quitarlas ayuda a que "cuánto cuesta el envío"
# y "cuanto cuesta envio" caigan al mismo hash. Lista corta y neutra: sin
# artículos, preposiciones cortas ni conectores. NO removemos palabras
# semánticamente útiles como "no", "sin", "para".
_STOPWORDS_ES = frozenset({
    "a", "al", "de", "del", "el", "en", "es", "la", "las", "lo",
    "los", "me", "mi", "mis", "por", "que", "se", "su", "sus", "te",
    "tu", "tus", "un", "una", "unos", "unas", "y", "o", "u",
})


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def normalizar_mensaje(mensaje: str) -> str:
    """Devuelve la forma normalizada del mensaje del cliente para
    agrupamiento. Reglas conservadoras:

    1. Minúsculas + sin acentos.
    2. Sin puntuación.
    3. Espacios múltiples colapsados.
    4. Stopwords cortas ES eliminadas (artículos y pronombres).
    5. Palabras ordenadas alfabéticamente (para que "cambiar dirección"
       y "la dirección cambia" agrupen igual).
    6. Recortado a 400 chars (defensa contra payloads gigantes).
    """
    if not mensaje:
        return ""
    text = _strip_accents(str(mensaje).lower())
    text = _PUNCT_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    palabras = [w for w in text.split() if w and w not in _STOPWORDS_ES]
    palabras.sort()
    resultado = " ".join(palabras)
    return resultado[:400]


def hash_mensaje(mensaje_norm: str) -> str:
    """SHA-256 truncado a 24 chars — suficiente para evitar colisiones
    en el volumen esperado (miles de patrones distintos) sin ocupar
    demasiado en el índice único."""
    if not mensaje_norm:
        return ""
    return hashlib.sha256(mensaje_norm.encode("utf-8")).hexdigest()[:24]


def hash_telefono(telefono: str) -> str:
    """HMAC-SHA256 del teléfono con OXIDIAN_KEY, truncado a 32 chars.
    Mismo patrón que ``whatsapp_role_profiles`` — agregable pero no
    reversible sin la clave del server. Si falta la key, cae a ``""``
    y no persiste (mejor sin dato que dato débil)."""
    if not telefono:
        return ""
    key = (os.environ.get("OXIDIAN_KEY") or "").strip()
    if not key:
        try:
            from models import SiteConfig
            key = (SiteConfig.get("OXIDIAN_KEY", "") or "").strip()
        except Exception:
            key = ""
    if not key:
        return ""
    telefono_clean = "".join(c for c in str(telefono) if c.isdigit())
    if not telefono_clean:
        return ""
    return hmac.new(
        key.encode("utf-8"), telefono_clean.encode("utf-8"), hashlib.sha256,
    ).hexdigest()[:32]


# ── Registro de señales ────────────────────────────────────────────────

def _config_enabled() -> bool:
    try:
        from models import SiteConfig
        raw = SiteConfig.get("BOT_LEARNING_ENABLED", "1") or "1"
    except Exception:
        return True
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def registrar_signal(
    mensaje: str,
    action_llm: str | None = None,
    reply_snippet: str | None = None,
    telefono: str | None = None,
    intent_matched: bool = False,
) -> bool:
    """Idempotente por hash del mensaje normalizado.

    Devuelve True si la señal se persistió (nueva o incremento del
    contador), False si se descartó (input inválido, aprendizaje
    desactivado, o error controlado). Nunca lanza excepciones —
    debe poder llamarse fire-and-forget.
    """
    if not _config_enabled():
        return False
    if not mensaje or not str(mensaje).strip():
        return False
    if len(str(mensaje)) < 3:
        return False

    from extensions import db
    from models import BotLearningSignal, utcnow

    try:
        mensaje_str = str(mensaje)[:400]
        mensaje_norm = normalizar_mensaje(mensaje_str)
        if not mensaje_norm or len(mensaje_norm) < 2:
            return False
        h = hash_mensaje(mensaje_norm)
        if not h:
            return False
        action = (action_llm or "")[:40] or None
        reply = (reply_snippet or "")[:400] or None
        tel_h = hash_telefono(telefono or "")
        now = utcnow()

        # Upsert idempotente por hash. Con índice único en mensaje_hash
        # el segundo intento con misma clave falla y volvemos a leer +
        # actualizar. Evitamos INSERT..ON CONFLICT (no portable SQLite/PG).
        existing = BotLearningSignal.query.filter_by(mensaje_hash=h).first()
        if existing is None:
            row = BotLearningSignal(
                mensaje_hash=h,
                mensaje_norm=mensaje_norm,
                mensaje_ejemplo=mensaje_str,
                action_llm=action,
                reply_snippet=reply,
                count=1,
                telefono_hash=tel_h or None,
                intent_matched_before=bool(intent_matched),
                first_seen_at=now,
                last_seen_at=now,
            )
            db.session.add(row)
        else:
            existing.count = (existing.count or 0) + 1
            existing.last_seen_at = now
            if action:
                existing.action_llm = action
            if reply:
                existing.reply_snippet = reply
            if tel_h:
                existing.telefono_hash = tel_h
            if intent_matched:
                existing.intent_matched_before = True
        db.session.commit()
        return True
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        try:
            current_app.logger.info(
                "bot_learning_signal: descartada por error",
                exc_info=True,
            )
        except Exception:
            pass
        return False


# ── Purga oportunista ──────────────────────────────────────────────────

def _config_retention_days() -> int:
    try:
        from models import SiteConfig
        raw = int(SiteConfig.get("BOT_LEARNING_RETENTION_DAYS", 90) or 90)
    except Exception:
        return 90
    return max(7, min(365, raw))


def prune_stale(min_count: int = 2) -> int:
    """Borra señales con count < min_count Y sin applied Y más viejas
    que retention_days. No toca las aplicadas ni las de alto count
    (patrones valiosos aunque sean viejos)."""
    from extensions import db
    from models import BotLearningSignal

    retention = _config_retention_days()
    cutoff = datetime.utcnow() - timedelta(days=retention)
    try:
        deleted = (
            BotLearningSignal.query
            .filter(BotLearningSignal.applied.is_(False))
            .filter(BotLearningSignal.count < min_count)
            .filter(BotLearningSignal.last_seen_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.session.commit()
        return int(deleted or 0)
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return 0
