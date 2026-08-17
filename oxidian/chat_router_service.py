"""Enrutador de mensajes del chatbot — 100% determinista, sin IA.

Contrato: el bot Node envía texto libre; este servicio devuelve una respuesta
precargada (o fallback). Sin llamadas a modelos, sin scoring probabilístico.
Match por keywords normalizadas + ranking simple por número de coincidencias.

Fuente de verdad:
- Contenido de respuestas → `KnowledgeEntry` (editable en caliente por super_admin).
- Datos volátiles (horario, teléfono, dirección, web) → `SiteConfig`, sustituidos
  al vuelo en placeholders `{{clave}}` para no duplicar información.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional

from models import KnowledgeEntry
from store_config import get_store_value


AUDIENCIAS_VALIDAS = ("cliente", "admin", "super_admin", "todos")


def normalize(text: Optional[str]) -> str:
    """Lowercase, sin tildes, sin puntuación, colapsa espacios."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def tokenize(text: str) -> List[str]:
    return [t for t in normalize(text).split(" ") if t]


def _placeholder_context() -> dict:
    """Valores actuales de SiteConfig disponibles como `{{clave}}`.

    Se resuelve por request (no cache global) para reflejar cambios del super_admin
    sin reinicio. Devuelve strings vacíos si el super_admin no configuró el valor.
    """
    from schedule_service import configured_schedule_context
    schedule = configured_schedule_context()
    apertura = get_store_value("HORARIO_APERTURA", "") or ""
    cierre = get_store_value("HORARIO_CIERRE", "") or ""
    horario = schedule["weekly"]
    return {
        "horario": horario,
        "horario_apertura": apertura,
        "horario_cierre": cierre,
        "horario_hoy": schedule["today"],
        "proxima_apertura": schedule["next_opening"] or "",
        "telefono": get_store_value("TELEFONO_NEGOCIO", "") or "nuestro WhatsApp",
        "direccion": get_store_value("DIRECCION_NEGOCIO", "") or "consulta en la web",
        "nombre_negocio": get_store_value("NOMBRE_NEGOCIO", "") or "",
        "web_url": get_store_value("OXIDIAN_PUBLIC_URL", "") or get_store_value("TIENDA_URL", "") or "",
    }


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_][a-z_0-9]*)\s*\}\}")


def render_response(template: str, context: Optional[dict] = None) -> str:
    """Sustituye `{{clave}}` con valores de SiteConfig. No falla si falta la clave."""
    ctx = context if context is not None else _placeholder_context()
    def _sub(m):
        return str(ctx.get(m.group(1), "")).strip()
    return _PLACEHOLDER_RE.sub(_sub, template or "").strip()


def _audience_filter(audiencia: str) -> Iterable[str]:
    """Devuelve las audiencias que el usuario puede consumir.

    Un `super_admin` puede leer respuestas dirigidas a super_admin, admin, cliente
    y `todos`. Un `admin` puede leer admin+todos+cliente. `cliente` solo cliente+todos.
    Cualquier otro valor cae a cliente para no exponer info sensible.
    """
    a = (audiencia or "cliente").strip().lower()
    if a == "super_admin":
        return ("super_admin", "admin", "cliente", "todos")
    if a == "admin":
        return ("admin", "cliente", "todos")
    return ("cliente", "todos")


def _score(entry_keywords: List[str], entry_pregunta: str, tokens: set,
           normalized_text: str = "") -> int:
    """Score simple: nº de keywords que aparecen en los tokens del mensaje.

    La pregunta también aporta (1 punto por token en común). Devuelve 0 si no
    hay ninguna coincidencia — así el fallback distingue entre "match débil" y
    "sin match".
    """
    if not tokens:
        return 0
    kw_normalized = {normalize(k) for k in entry_keywords if k}
    score = sum(1 for k in kw_normalized if k and " " not in k and k in tokens)
    # keywords multi-palabra: match por substring del texto normalizado.
    joined = normalized_text or " ".join(tokens)
    score += sum(3 for k in kw_normalized if " " in k and k in joined)
    # Bonus por tokens de la pregunta canonical.
    q_tokens = set(tokenize(entry_pregunta))
    score += len(q_tokens & tokens)
    return score


def match_query(text: str, audiencia: str = "cliente", limit: int = 3) -> List[dict]:
    """Devuelve las mejores respuestas para `text` según la audiencia.

    Sin match → lista vacía. El caller decide el fallback (bot muestra
    menú/consejo). Cada dict incluye `id`, `categoria`, `pregunta`,
    `respuesta` (ya con placeholders resueltos), `score`.
    """
    normalized_text = normalize(text)
    tokens = set(tokenize(normalized_text))
    if not tokens:
        return []
    audiences = tuple(_audience_filter(audiencia))
    q = KnowledgeEntry.query.filter(
        KnowledgeEntry.activo.is_(True),
        KnowledgeEntry.audiencia.in_(audiences),
    ).all()
    ctx = _placeholder_context()
    ranked = []
    for e in q:
        s = _score(e.keyword_list(), e.pregunta, tokens, normalized_text)
        if s <= 0:
            continue
        ranked.append((s, e))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].orden, pair[1].id))
    out = []
    for score, e in ranked[:max(1, int(limit))]:
        out.append({
            "id": e.id,
            "categoria": e.categoria,
            "pregunta": e.pregunta,
            "respuesta": render_response(e.respuesta, ctx),
            "score": score,
        })
    return out


def menu_for_audience(audiencia: str = "cliente") -> List[dict]:
    """Categorías activas agrupando entradas — util para menús contextuales."""
    audiences = tuple(_audience_filter(audiencia))
    entries = KnowledgeEntry.query.filter(
        KnowledgeEntry.activo.is_(True),
        KnowledgeEntry.audiencia.in_(audiences),
    ).order_by(KnowledgeEntry.orden, KnowledgeEntry.categoria, KnowledgeEntry.id).all()
    ctx = _placeholder_context()
    grouped: dict = {}
    for e in entries:
        cat = e.categoria or "general"
        grouped.setdefault(cat, []).append({
            "id": e.id,
            "pregunta": e.pregunta,
            "respuesta": render_response(e.respuesta, ctx),
        })
    return [{"categoria": cat, "entradas": items} for cat, items in grouped.items()]


def fallback_message(audiencia: str = "cliente") -> str:
    """Mensaje cuando no hay match — apunta al menú disponible."""
    ctx = _placeholder_context()
    web = ctx.get("web_url") or ""
    base = (
        "🤔 No encontré esa información. Escribe *MENU* para ver las opciones "
        "disponibles o *AGENTE* para hablar con una persona."
    )
    if audiencia == "cliente" and web:
        base += f"\n🛒 Para pedir: {web}"
    return base


__all__ = [
    "normalize",
    "tokenize",
    "render_response",
    "match_query",
    "menu_for_audience",
    "fallback_message",
    "AUDIENCIAS_VALIDAS",
]
