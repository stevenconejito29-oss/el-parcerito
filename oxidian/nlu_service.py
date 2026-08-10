"""NLU con Groq para el chatbot — mapea texto libre a respuestas canónicas.

**Contrato crítico**: Groq NUNCA responde en crudo al cliente. Solo interpreta
el mensaje y decide qué `KnowledgeEntry` de la BD aplicar (o propone una nueva
para revisión manual). El bot siempre entrega el texto canónico editable por
super_admin, con placeholders resueltos.

## Arquitectura

Dos puntos de entrada, separación de responsabilidades:

  1. `resolve(mensaje, telefono)` — se llama cuando el matcher determinista
     no dio hit. Devuelve una respuesta canónica o fallback. Puede persistir
     una entry nueva `activo=False` para aprobación admin.
  2. `enrich_matched(entry_id, mensaje, telefono)` — se llama cuando SÍ hubo
     match determinista (aunque débil). Solo enriquece keywords del entry
     matched, no crea nuevos. Fire-and-forget desde bot.js.

## Configuración (SiteConfig)

  - `BOT_NLU_ENABLED` — flag maestro (default "0"). Separado del bloqueo del
    chat libre (_client_generative_ai_enabled).
  - `BOT_AI_API_KEY` — key de Groq/OpenAI (compartida con asesor comercial).
  - `BOT_AI_PROVIDER` — "groq" (default) | "openai".
  - `BOT_AI_MODEL` — default "llama-3.1-70b-versatile".
  - `BOT_NLU_CONFIDENCE_MIN` — umbral para aceptar match_id (default 0.75).
  - `BOT_NLU_MAX_ENTRIES_IN_PROMPT` — top-N candidatas al prompt (default 30).
  - `BOT_NLU_MAX_KEYWORDS_PER_ENTRY` — techo por entry (default 30).
  - `BOT_NLU_MAX_NEW_ENTRIES_PER_DAY` — anti-basura autogenerada (default 10).
  - `BOT_NLU_MIN_MESSAGE_LENGTH` — filtro entrada (default 5).

## Garantías

  - Fail-safe: cualquier excepción devuelve None. Nunca rompe la conversación.
  - Rate-limited: cap diario de entries nuevas + cache 30min server-side.
  - Idempotente: dedupe por pregunta normalizada evita entries duplicadas.
  - Observable: logs estructurados con nlu_service.<evento>.
  - Retrocompatible: añadir keywords a un entry existente no rompe el matcher
    determinista (solo lo mejora).
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Optional

from flask import current_app


logger = logging.getLogger(__name__)


# ── Defaults (sobrescribibles vía SiteConfig) ───────────────────────────

NLU_MODEL_DEFAULT = "llama-3.1-70b-versatile"
NLU_CONFIDENCE_MIN_DEFAULT = 0.75
NLU_NEW_ENTRY_CONFIDENCE_MIN = 0.5  # más bajo — es "propuesta", no respuesta
NLU_MAX_KEYWORDS_PER_ENTRY_DEFAULT = 30
NLU_MAX_ENTRIES_IN_PROMPT_DEFAULT = 30
NLU_MAX_ENTRIES_LOAD = 500  # cap defensivo — filtramos en Python
NLU_MAX_NEW_ENTRIES_PER_DAY_DEFAULT = 10
NLU_MIN_MESSAGE_LENGTH_DEFAULT = 5
NLU_TIMEOUT_SECONDS = 12
NLU_PROVIDER_DEFAULT = "groq"


# ── Stopwords ES para tokenización / filtrado de keywords ──────────────

_STOPWORDS_ES = frozenset({
    "a", "al", "de", "del", "el", "en", "es", "la", "las", "lo",
    "los", "me", "mi", "mis", "por", "que", "se", "su", "sus", "te",
    "tu", "tus", "un", "una", "unos", "unas", "y", "o", "u", "con",
    "para", "pero", "si", "sí", "no", "ni", "como", "cuando", "donde",
    "muy", "mas", "más",
})


# ── Utilidades ──────────────────────────────────────────────────────────

def _cfg_str(key: str, default: str) -> str:
    """Lee un valor string de SiteConfig con fallback silencioso."""
    try:
        from models import SiteConfig
        raw = SiteConfig.get(key, default)
        return (raw or default).strip() if raw else default
    except Exception:
        return default


def _cfg_int(key: str, default: int, minimum: int = 1, maximum: int = 10_000) -> int:
    """Lee un entero de SiteConfig, clamped al rango [minimum, maximum]."""
    try:
        from models import SiteConfig
        raw = SiteConfig.get(key, str(default))
        v = int(raw or default)
        return max(minimum, min(maximum, v))
    except Exception:
        return default


def _cfg_float(key: str, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    """Lee un float de SiteConfig, clamped."""
    try:
        from models import SiteConfig
        raw = SiteConfig.get(key, str(default))
        v = float(raw or default)
        return max(minimum, min(maximum, v))
    except Exception:
        return default


def is_enabled() -> bool:
    raw = _cfg_str("BOT_NLU_ENABLED", "0")
    return raw.lower() in {"1", "true", "yes", "on"}


def _get_credentials() -> tuple[str, str, str]:
    """Devuelve (api_key, modelo, provider). api_key vacío si no configurado."""
    api_key = _cfg_str("BOT_AI_API_KEY", "")
    modelo = _cfg_str("BOT_AI_MODEL", NLU_MODEL_DEFAULT) or NLU_MODEL_DEFAULT
    provider = (_cfg_str("BOT_AI_PROVIDER", NLU_PROVIDER_DEFAULT) or NLU_PROVIDER_DEFAULT).lower()
    if provider not in {"groq", "openai"}:
        provider = NLU_PROVIDER_DEFAULT
    return api_key, modelo, provider


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    """Tokens minúsculas sin acentos, sin stopwords, sin palabras <3 chars."""
    if not text:
        return set()
    norm = _strip_accents(str(text).lower())
    tokens = _TOKEN_RE.findall(norm)
    return {t for t in tokens if len(t) >= 3 and t not in _STOPWORDS_ES}


def _clean_keyword(raw) -> str:
    """Normaliza una keyword: minúsculas, trim, max 40 chars. Descarta si
    solo puntuación o si es stopword."""
    if not raw:
        return ""
    k = str(raw).strip().lower()[:40]
    if not any(c.isalnum() for c in k):
        return ""
    # Descartar stopwords sueltas (una sola palabra que sea stopword)
    if " " not in k and k in _STOPWORDS_ES:
        return ""
    return k


# ── Selección de candidatas para el prompt ─────────────────────────────

def _load_candidate_entries(limit_load: int = NLU_MAX_ENTRIES_LOAD) -> list:
    """Carga las entries activas para cliente (cap defensivo)."""
    from models import KnowledgeEntry
    return (
        KnowledgeEntry.query
        .filter(KnowledgeEntry.activo.is_(True))
        .filter(KnowledgeEntry.audiencia.in_(("cliente", "todos")))
        .order_by(KnowledgeEntry.orden, KnowledgeEntry.id)
        .limit(limit_load)
        .all()
    )


def _rank_candidates(entries: list, mensaje: str, top_n: int) -> list[dict]:
    """Rankea candidatas por overlap de tokens con el mensaje. Devuelve los
    top_n para el prompt de Groq. Si el mensaje no tiene tokens útiles,
    devuelve las primeras top_n por orden natural (fallback).

    Score: suma de tokens únicos que aparecen tanto en el mensaje como en
    (pregunta + keywords) del entry, con pequeño boost para keywords
    multi-palabra que aparecen como substring del mensaje.
    """
    mensaje_norm = _strip_accents(str(mensaje or "").lower())
    msg_tokens = _tokenize(mensaje)

    if not msg_tokens:
        # Sin señal léxica útil — pasar primeras top_n en orden natural.
        return [_entry_to_prompt_dict(e) for e in entries[:top_n]]

    scored = []
    for e in entries:
        entry_tokens = _tokenize(e.pregunta or "")
        keywords = e.keyword_list() if hasattr(e, "keyword_list") else []
        for kw in keywords:
            entry_tokens |= _tokenize(kw)

        overlap = len(msg_tokens & entry_tokens)

        # Bonus para keywords multi-palabra que aparecen como substring
        multi_bonus = 0
        for kw in keywords:
            kw_norm = _strip_accents(str(kw).lower().strip())
            if " " in kw_norm and kw_norm and kw_norm in mensaje_norm:
                multi_bonus += 2

        score = overlap + multi_bonus
        if score > 0:
            scored.append((score, e))

    # Fallback: si nada matcheó, incluye igualmente primeras N — permite a
    # Groq juzgar contra el catálogo aunque no haya overlap léxico
    if not scored:
        return [_entry_to_prompt_dict(e) for e in entries[:top_n]]

    scored.sort(key=lambda pair: (-pair[0], pair[1].orden, pair[1].id))
    return [_entry_to_prompt_dict(e) for _, e in scored[:top_n]]


def _entry_to_prompt_dict(entry) -> dict:
    return {
        "id": entry.id,
        "categoria": entry.categoria or "general",
        "pregunta": entry.pregunta,
        "keywords": entry.keyword_list()[:15] if hasattr(entry, "keyword_list") else [],
    }


# ── Prompt engineering ────────────────────────────────────────────────

def _build_system_prompt(entries: list[dict], enrich_mode: bool = False,
                         hint_entry: Optional[dict] = None) -> str:
    """Construye el system prompt para Groq.

    - `enrich_mode=True`: variante para /nlu/enrich — solo interesan keywords
      del `hint_entry`, no proponer nuevas entries.
    - `hint_entry`: si el matcher determinista tenía un candidato, se destaca
      para que Groq priorice validarlo/enriquecerlo antes que otros.
    """
    catalogo = json.dumps(entries, ensure_ascii=False)

    if enrich_mode and hint_entry:
        return (
            "Eres un enriquecedor de keywords para un chatbot de WhatsApp en "
            "español. Se te da UN mensaje del cliente y UNA respuesta canónica "
            f"pre-asignada (id={hint_entry['id']}): {json.dumps(hint_entry, ensure_ascii=False)}\n\n"
            "Tu tarea: sugerir 2-6 keywords o frases cortas (2-4 palabras) que "
            "aparecen en el mensaje del cliente y ayudarían al matcher a "
            "reconocer preguntas SIMILARES en el futuro. Incluye SINÓNIMOS "
            "comunes en español si son útiles (ej: 'envío' → 'domicilio', "
            "'repartir', 'llevar').\n\n"
            "Reglas: minúsculas, sin puntuación, sin stopwords ('el', 'la', "
            "'que', etc.), útiles como keywords. Máx 40 chars cada una.\n\n"
            "Formato de salida OBLIGATORIO (JSON estricto, sin markdown):\n"
            '{"suggested_keywords": [<string>...]}'
        )

    hint_block = ""
    if hint_entry:
        hint_block = (
            f"\nEl matcher determinista sugiere como candidato el id={hint_entry['id']} "
            f"({json.dumps(hint_entry, ensure_ascii=False)}) con confianza baja. "
            "Valida o descarta esta hipótesis.\n"
        )

    return (
        "Eres un enrutador NLU para un chatbot de WhatsApp en español. "
        "Tu ÚNICA tarea es mapear el mensaje del cliente a UNA respuesta "
        "del catálogo que te doy, o proponer una nueva si ninguna aplica. "
        "NUNCA respondes en prosa libre al cliente — solo devuelves JSON.\n\n"
        f"Catálogo de respuestas disponibles (JSON, top {len(entries)} relevantes):\n"
        f"{catalogo}\n"
        f"{hint_block}\n"
        "Reglas:\n"
        "1. Si el mensaje encaja claramente con alguna `pregunta` del catálogo, "
        "devuelve su `id` en `match_id` con `confidence` alto (0.75-1.0).\n"
        "2. Si el encaje es dudoso, `match_id: null` y `confidence` bajo.\n"
        "3. `suggested_keywords`: 2-6 palabras/frases del mensaje del cliente "
        "que ayuden al matcher determinista futuro. INCLUYE sinónimos comunes "
        "en español (ej: mensaje dice 'envío' → sugiere también 'domicilio', "
        "'repartir', 'llevar'). Minúsculas, sin puntuación, sin stopwords, "
        "útiles como keywords. Máx 40 chars cada una.\n"
        "4. Si NINGUNA entry cubre el tema y crees que amerita respuesta "
        "canónica reutilizable, propón `suggested_new_entry`. Si el mensaje es "
        "muy específico de un cliente (nombre, número de pedido, dirección) o "
        "muy vago, deja `suggested_new_entry: null`.\n"
        "5. Respuestas propuestas: genéricas, útiles, español neutro. NO "
        "inventes datos concretos (precios, horarios, direcciones) — para eso "
        "el admin usa placeholders {{horario}} {{telefono}} {{direccion}}.\n\n"
        "Formato de salida OBLIGATORIO (JSON estricto, sin markdown, sin "
        "comentarios):\n"
        '{"match_id": <int>|null, "confidence": <float>, '
        '"suggested_keywords": [<string>...], '
        '"suggested_new_entry": {"pregunta": <string>, "respuesta": <string>, '
        '"keywords": [<string>...], "categoria": <string>}|null}'
    )


# ── Llamada a Groq ────────────────────────────────────────────────────

def _call_provider(api_key: str, modelo: str, provider: str,
                   mensaje: str, system_prompt: str,
                   max_tokens: int = 400) -> Optional[dict]:
    import requests
    endpoint = (
        "https://api.openai.com/v1/chat/completions"
        if provider == "openai"
        else "https://api.groq.com/openai/v1/chat/completions"
    )
    try:
        resp = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": modelo,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": str(mensaje)[:600]},
                ],
                "temperature": 0.1,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            },
            timeout=NLU_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.info("nlu_service.request_fail provider=%s err=%s", provider, exc)
        return None

    if resp.status_code != 200:
        logger.info("nlu_service.provider_error status=%s body=%s",
                    resp.status_code, resp.text[:200])
        return None

    try:
        data = resp.json()
        texto = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        parsed = json.loads(texto)
    except Exception:
        logger.info("nlu_service.parse_fail", exc_info=True)
        return None

    return parsed if isinstance(parsed, dict) else None


# ── Persistencia: enriquecer keywords ─────────────────────────────────

def _apply_suggested_keywords(entry_id: int, suggested: list) -> int:
    """Auto-aplica keywords al entry matched. Dedupe (case-insensitive),
    respeta cap. Devuelve keywords añadidas netas."""
    if not entry_id or not isinstance(suggested, list) or not suggested:
        return 0
    from extensions import db
    from models import KnowledgeEntry

    cap = _cfg_int("BOT_NLU_MAX_KEYWORDS_PER_ENTRY", NLU_MAX_KEYWORDS_PER_ENTRY_DEFAULT, 5, 200)

    entry = KnowledgeEntry.query.get(entry_id)
    if not entry:
        return 0

    existing_norm = {k.lower() for k in entry.keyword_list()}
    added_clean = []
    for raw in suggested:
        k = _clean_keyword(raw)
        if not k or k.lower() in existing_norm:
            continue
        existing_norm.add(k.lower())
        added_clean.append(k)
        if len(existing_norm) >= cap:
            break

    if not added_clean:
        return 0

    combined = entry.keyword_list() + added_clean
    entry.keywords = ", ".join(combined[:cap])
    try:
        db.session.commit()
        logger.info("nlu_service.keywords_added entry=%s count=%s", entry_id, len(added_clean))
        return len(added_clean)
    except Exception:
        db.session.rollback()
        logger.info("nlu_service.keywords_persist_fail entry=%s", entry_id, exc_info=True)
        return 0


# ── Persistencia: crear entry pendiente ───────────────────────────────

def _pregunta_ya_existe(pregunta_norm: str) -> bool:
    """Dedupe: True si ya existe una entry (activa o pendiente) con esa
    pregunta normalizada. Evita que Groq propague duplicados."""
    if not pregunta_norm:
        return True  # trata vacío como "existe" para no crear
    from models import KnowledgeEntry
    try:
        rows = KnowledgeEntry.query.with_entities(KnowledgeEntry.pregunta).all()
    except Exception:
        return False
    for (p,) in rows:
        if _norm_pregunta(p) == pregunta_norm:
            return True
    return False


def _norm_pregunta(text) -> str:
    if not text:
        return ""
    return " ".join(_strip_accents(str(text).lower()).split())


def _new_entries_today() -> int:
    """Cuenta entries autogeneradas creadas hoy (rate limit)."""
    from models import KnowledgeEntry
    inicio = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        return (
            KnowledgeEntry.query
            .filter(KnowledgeEntry.categoria == "autogenerada")
            .filter(KnowledgeEntry.creado_en >= inicio)
            .count()
        )
    except Exception:
        return 0


def _persist_new_entry_pending(proposed: dict, confidence_global: float,
                               mensaje_original: str) -> Optional[int]:
    """Crea KnowledgeEntry `activo=False, categoria='autogenerada'` con
    guardrails:
      - confidence_global >= NLU_NEW_ENTRY_CONFIDENCE_MIN
      - mensaje >= min_length
      - no duplica pregunta existente (activa o pendiente)
      - no supera cap diario
    """
    if not isinstance(proposed, dict):
        return None
    if confidence_global < NLU_NEW_ENTRY_CONFIDENCE_MIN:
        logger.info("nlu_service.new_entry_skip reason=low_confidence conf=%s", confidence_global)
        return None

    min_len = _cfg_int("BOT_NLU_MIN_MESSAGE_LENGTH", NLU_MIN_MESSAGE_LENGTH_DEFAULT, 3, 100)
    if len(str(mensaje_original).strip()) < min_len:
        logger.info("nlu_service.new_entry_skip reason=short_message")
        return None

    pregunta = str(proposed.get("pregunta") or "").strip()[:200]
    respuesta = str(proposed.get("respuesta") or "").strip()
    if not pregunta or not respuesta:
        return None

    pregunta_norm = _norm_pregunta(pregunta)
    if _pregunta_ya_existe(pregunta_norm):
        logger.info("nlu_service.new_entry_skip reason=duplicate pregunta=%s",
                    pregunta_norm[:60])
        return None

    daily_cap = _cfg_int("BOT_NLU_MAX_NEW_ENTRIES_PER_DAY",
                         NLU_MAX_NEW_ENTRIES_PER_DAY_DEFAULT, 1, 500)
    if _new_entries_today() >= daily_cap:
        logger.warning("nlu_service.new_entry_skip reason=daily_cap cap=%s", daily_cap)
        return None

    keywords_raw = proposed.get("keywords") or []
    if not isinstance(keywords_raw, list):
        keywords_raw = []

    cap_keywords = _cfg_int("BOT_NLU_MAX_KEYWORDS_PER_ENTRY",
                            NLU_MAX_KEYWORDS_PER_ENTRY_DEFAULT, 5, 200)
    keywords_clean = []
    seen = set()
    for raw in keywords_raw:
        k = _clean_keyword(raw)
        if k and k.lower() not in seen:
            seen.add(k.lower())
            keywords_clean.append(k)
        if len(keywords_clean) >= cap_keywords:
            break

    from extensions import db
    from models import KnowledgeEntry
    try:
        entry = KnowledgeEntry(
            categoria="autogenerada",
            pregunta=pregunta,
            respuesta=respuesta[:2000],
            keywords=", ".join(keywords_clean),
            audiencia="cliente",
            activo=False,
            orden=999,
            es_seed=False,
        )
        db.session.add(entry)
        db.session.commit()
        logger.info("nlu_service.new_entry_pending id=%s pregunta=%s",
                    entry.id, pregunta[:60])
        return int(entry.id)
    except Exception:
        db.session.rollback()
        logger.info("nlu_service.new_entry_persist_fail", exc_info=True)
        return None


# ── API pública ───────────────────────────────────────────────────────

def resolve(mensaje: str, telefono: Optional[str] = None,
            hint_entry_id: Optional[int] = None) -> Optional[dict]:
    """Punto de entrada principal. Se llama cuando el matcher determinista
    NO dio match satisfactorio.

    Args:
        mensaje: texto del cliente.
        telefono: opcional, para señal de aprendizaje.
        hint_entry_id: opcional, id de un entry que el matcher determinista
            sugirió con score bajo. Se pasa a Groq para que valide/descarte.

    Returns:
        None si NLU deshabilitado, sin key, o falló.
        Dict con estructura:
          {action: "canned", respuesta, entry_id, confidence, keywords_added, new_pending_id}
          {action: "fallback", confidence, keywords_added, new_pending_id}
    """
    if not is_enabled():
        return None
    if not mensaje or not str(mensaje).strip():
        return None

    api_key, modelo, provider = _get_credentials()
    if not api_key:
        logger.info("nlu_service.no_api_key")
        return None

    entries = _load_candidate_entries()
    if not entries:
        logger.info("nlu_service.no_entries_active")
        return None

    top_n = _cfg_int("BOT_NLU_MAX_ENTRIES_IN_PROMPT",
                     NLU_MAX_ENTRIES_IN_PROMPT_DEFAULT, 5, 100)
    candidates = _rank_candidates(entries, mensaje, top_n)

    hint = None
    if hint_entry_id:
        hint_entry = next((e for e in entries if e.id == hint_entry_id), None)
        if hint_entry:
            hint = _entry_to_prompt_dict(hint_entry)

    system_prompt = _build_system_prompt(candidates, enrich_mode=False, hint_entry=hint)
    parsed = _call_provider(api_key, modelo, provider, mensaje, system_prompt)
    if not parsed:
        return None

    confidence = _safe_float(parsed.get("confidence"))
    match_id = _safe_int(parsed.get("match_id"))
    suggested_keywords = parsed.get("suggested_keywords") or []
    suggested_new = parsed.get("suggested_new_entry")

    conf_min = _cfg_float("BOT_NLU_CONFIDENCE_MIN", NLU_CONFIDENCE_MIN_DEFAULT, 0.3, 1.0)

    # ── Camino 1: match con confianza suficiente ──────────────
    if match_id and confidence >= conf_min:
        from models import KnowledgeEntry
        from chat_router_service import render_response
        entry = KnowledgeEntry.query.get(match_id)
        if entry and entry.activo:
            keywords_added = _apply_suggested_keywords(match_id, suggested_keywords)
            respuesta = render_response(entry.respuesta)
            _register_learning(mensaje, telefono, matched=True, entry_id=match_id)
            return {
                "action": "canned",
                "respuesta": respuesta,
                "entry_id": match_id,
                "confidence": confidence,
                "keywords_added": keywords_added,
                "new_pending_id": None,
            }
        else:
            logger.info("nlu_service.match_invalid_entry id=%s", match_id)

    # ── Camino 2: sin match — persistir propuesta si cumple guardrails ──
    new_pending_id = _persist_new_entry_pending(suggested_new, confidence, mensaje) \
        if suggested_new else None
    _register_learning(mensaje, telefono, matched=False,
                       entry_id=None, pending_id=new_pending_id)
    return {
        "action": "fallback",
        "confidence": confidence,
        "keywords_added": 0,
        "new_pending_id": new_pending_id,
    }


def enrich_matched(entry_id: int, mensaje: str,
                   telefono: Optional[str] = None) -> Optional[dict]:
    """Fire-and-forget: enriquece keywords de un entry ya matched por el
    matcher determinista (aunque con score débil). No crea entries nuevas.

    Devuelve None si NLU deshabilitado / falló. Dict {keywords_added} si OK.
    """
    if not is_enabled():
        return None
    if not entry_id or not mensaje or not str(mensaje).strip():
        return None

    api_key, modelo, provider = _get_credentials()
    if not api_key:
        return None

    from models import KnowledgeEntry
    entry = KnowledgeEntry.query.get(entry_id)
    if not entry or not entry.activo:
        return None

    hint = _entry_to_prompt_dict(entry)
    system_prompt = _build_system_prompt([hint], enrich_mode=True, hint_entry=hint)
    parsed = _call_provider(api_key, modelo, provider, mensaje, system_prompt,
                            max_tokens=200)
    if not parsed:
        return None

    suggested = parsed.get("suggested_keywords") or []
    added = _apply_suggested_keywords(entry_id, suggested)

    try:
        from bot_learning_service import registrar_signal
        registrar_signal(
            mensaje=mensaje,
            action_llm=f"nlu_enrich:{entry_id}:+{added}",
            telefono=telefono,
            intent_matched=True,
        )
    except Exception:
        pass

    return {"keywords_added": added, "entry_id": entry_id}


def _register_learning(mensaje: str, telefono: Optional[str], matched: bool,
                       entry_id: Optional[int] = None,
                       pending_id: Optional[int] = None):
    try:
        from bot_learning_service import registrar_signal
        if matched:
            action = f"nlu_match:{entry_id}"
        elif pending_id:
            action = f"nlu_pending:{pending_id}"
        else:
            action = "nlu_nomatch"
        registrar_signal(
            mensaje=mensaje,
            action_llm=action,
            reply_snippet=None,
            telefono=telefono,
            intent_matched=matched,
        )
    except Exception:
        logger.info("nlu_service.learning_register_fail", exc_info=True)


def _safe_float(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "resolve",
    "enrich_matched",
    "is_enabled",
    "NLU_CONFIDENCE_MIN_DEFAULT",
    "NLU_MODEL_DEFAULT",
]
