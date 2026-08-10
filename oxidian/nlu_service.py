"""NLU con Groq para el chatbot — mapea texto libre a respuestas canónicas.

**Contrato crítico**: Groq NUNCA responde en crudo al cliente. Solo interpreta
el mensaje y devuelve JSON estructurado con:
  - `match_id` de un `KnowledgeEntry` existente que responde mejor la pregunta.
  - `suggested_keywords` para enriquecer la matcher determinista de ese entry.
  - `suggested_new_entry` cuando ninguna entry cubre el tema, para revisión
    manual del admin (se crea `activo=False`).

El bot siempre entrega al cliente el TEXTO de la `KnowledgeEntry` (canónico,
editable por super_admin, con placeholders resueltos), o el fallback estándar
si Groq no encontró match con suficiente confianza.

Diseño:
  - Confianza mínima 0.75 para aceptar `match_id` de Groq.
  - Auto-aplica `suggested_keywords` al matched entry (dedup, máx 30 keywords).
  - `suggested_new_entry` se persiste como `activo=False, categoria='autogenerada'`
    — nunca se muestra al cliente hasta que un admin la active.
  - Fire-and-forget: cualquier excepción devuelve `None` sin propagar.
  - Registra `BotLearningSignal` con el resultado (matched o no) para el panel
    de aprendizaje.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from flask import current_app


NLU_MODEL_DEFAULT = "llama-3.1-70b-versatile"
NLU_CONFIDENCE_MIN = 0.75
NLU_MAX_KEYWORDS_PER_ENTRY = 30
NLU_MAX_ENTRIES_IN_PROMPT = 60  # top-N por orden/id — evita prompts gigantes
NLU_TIMEOUT_SECONDS = 12


def is_enabled() -> bool:
    """El NLU se habilita con `BOT_NLU_ENABLED=1` en SiteConfig. Es un flag
    SEPARADO de `_client_generative_ai_enabled` (que sigue bloqueando el chat
    libre). Aquí Groq solo mapea a respuestas de la BD."""
    try:
        from models import SiteConfig
        raw = SiteConfig.get("BOT_NLU_ENABLED", "0") or "0"
    except Exception:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _get_groq_credentials() -> tuple[str, str, str]:
    """Devuelve (api_key, modelo, provider). Vacío si no configurado."""
    try:
        from models import SiteConfig
        api_key = (SiteConfig.get("BOT_AI_API_KEY", "") or "").strip()
        modelo = (SiteConfig.get("BOT_AI_MODEL", "") or "").strip() or NLU_MODEL_DEFAULT
        provider = (SiteConfig.get("BOT_AI_PROVIDER", "groq") or "groq").strip().lower()
    except Exception:
        return "", NLU_MODEL_DEFAULT, "groq"
    return api_key, modelo, provider


def _candidate_entries(limit: int = NLU_MAX_ENTRIES_IN_PROMPT) -> list[dict]:
    """Top-N entries activas para cliente — se pasan a Groq como catálogo."""
    from models import KnowledgeEntry
    rows = (
        KnowledgeEntry.query
        .filter(KnowledgeEntry.activo.is_(True))
        .filter(KnowledgeEntry.audiencia.in_(("cliente", "todos")))
        .order_by(KnowledgeEntry.orden, KnowledgeEntry.id)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": e.id,
            "categoria": e.categoria or "general",
            "pregunta": e.pregunta,
            "keywords": e.keyword_list(),
        }
        for e in rows
    ]


def _build_system_prompt(entries: list[dict]) -> str:
    catalogo = json.dumps(entries, ensure_ascii=False)
    return (
        "Eres un enrutador NLU para un chatbot de WhatsApp en español. "
        "Tu ÚNICA tarea es mapear el mensaje del cliente a UNA respuesta "
        "del catálogo que te doy, o proponer una nueva si ninguna aplica. "
        "NUNCA respondes en prosa libre al cliente — solo devuelves JSON.\n\n"
        "Catálogo de respuestas disponibles (JSON):\n"
        f"{catalogo}\n\n"
        "Reglas:\n"
        "1. Si el mensaje del cliente encaja claramente con alguna `pregunta` "
        "del catálogo, devuelve su `id` en `match_id` con `confidence` alto "
        "(0.75-1.0).\n"
        "2. Si el encaje es dudoso, devuelve `match_id: null` y `confidence` bajo.\n"
        "3. En `suggested_keywords` sugiere 2-6 palabras/frases del mensaje del "
        "cliente que ayuden al matcher determinista futuro (minúsculas, sin "
        "puntuación, útiles como keywords — no palabras vacías).\n"
        "4. Si NINGUNA entry cubre el tema y crees que amerita respuesta canónica, "
        "propón `suggested_new_entry` con {pregunta, respuesta, keywords, categoria}. "
        "Si no vale la pena, deja `suggested_new_entry: null`.\n"
        "5. Respuestas propuestas deben ser genéricas, útiles, en español "
        "neutro. No inventes datos concretos (precios, horarios, direcciones) "
        "— para eso el admin usa placeholders {{horario}} {{telefono}}.\n\n"
        "Formato de salida OBLIGATORIO (JSON, sin markdown, sin comentarios):\n"
        '{"match_id": <int>|null, "confidence": <float>, '
        '"suggested_keywords": [<string>...], '
        '"suggested_new_entry": {"pregunta": <string>, "respuesta": <string>, '
        '"keywords": [<string>...], "categoria": <string>}|null}'
    )


def _call_groq(api_key: str, modelo: str, provider: str, mensaje: str, entries: list[dict]) -> Optional[dict]:
    import requests
    endpoint = (
        "https://api.openai.com/v1/chat/completions"
        if provider == "openai"
        else "https://api.groq.com/openai/v1/chat/completions"
    )
    system_prompt = _build_system_prompt(entries)
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
                    {"role": "user", "content": mensaje[:600]},
                ],
                "temperature": 0.1,
                "max_tokens": 400,
                "response_format": {"type": "json_object"},
            },
            timeout=NLU_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        current_app.logger.info("nlu_service: request fail: %s", exc)
        return None
    if resp.status_code != 200:
        current_app.logger.info("nlu_service: proveedor %s", resp.status_code)
        return None
    try:
        data = resp.json()
        texto = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        parsed = json.loads(texto)
    except Exception:
        current_app.logger.info("nlu_service: JSON parse fail", exc_info=True)
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _clean_keyword(raw: str) -> str:
    """Normaliza una keyword propuesta: minúsculas, sin espacios extremos,
    máx 40 chars. Rechaza vacías o solo puntuación."""
    if not raw:
        return ""
    k = str(raw).strip().lower()[:40]
    # Rechaza si solo puntuación/espacios
    if not any(c.isalnum() for c in k):
        return ""
    return k


def _apply_suggested_keywords(entry_id: int, suggested: list) -> int:
    """Auto-aplica keywords sugeridas al KnowledgeEntry matched. Dedup y
    límite. Devuelve cuántas keywords se añadieron netas."""
    if not entry_id or not isinstance(suggested, list) or not suggested:
        return 0
    from extensions import db
    from models import KnowledgeEntry
    entry = KnowledgeEntry.query.get(entry_id)
    if not entry:
        return 0
    existing = {k.lower() for k in entry.keyword_list()}
    added = []
    for raw in suggested:
        k = _clean_keyword(raw)
        if not k or k in existing:
            continue
        existing.add(k)
        added.append(k)
        if len(existing) >= NLU_MAX_KEYWORDS_PER_ENTRY:
            break
    if not added:
        return 0
    combined = entry.keyword_list() + added
    entry.keywords = ", ".join(combined[:NLU_MAX_KEYWORDS_PER_ENTRY])
    try:
        db.session.commit()
        return len(added)
    except Exception:
        db.session.rollback()
        current_app.logger.info("nlu_service: no pude persistir keywords", exc_info=True)
        return 0


def _persist_new_entry_pending(proposed: dict) -> Optional[int]:
    """Crea KnowledgeEntry `activo=False, categoria='autogenerada'` con la
    propuesta de Groq. Nunca se muestra al cliente hasta que admin la active.
    Devuelve el id creado o None."""
    if not isinstance(proposed, dict):
        return None
    pregunta = str(proposed.get("pregunta") or "").strip()[:200]
    respuesta = str(proposed.get("respuesta") or "").strip()
    if not pregunta or not respuesta:
        return None
    keywords_raw = proposed.get("keywords") or []
    if not isinstance(keywords_raw, list):
        keywords_raw = []
    keywords_clean = []
    seen = set()
    for raw in keywords_raw:
        k = _clean_keyword(raw)
        if k and k not in seen:
            seen.add(k)
            keywords_clean.append(k)
        if len(keywords_clean) >= NLU_MAX_KEYWORDS_PER_ENTRY:
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
            activo=False,  # pending admin approval — nunca al cliente
            orden=999,
            es_seed=False,
        )
        db.session.add(entry)
        db.session.commit()
        return int(entry.id)
    except Exception:
        db.session.rollback()
        current_app.logger.info("nlu_service: no pude crear entry pendiente", exc_info=True)
        return None


def resolve(mensaje: str, telefono: Optional[str] = None) -> Optional[dict]:
    """Punto de entrada. Devuelve un dict listo para responder al cliente,
    o None si el NLU no está habilitado / falló.

    Estructura del dict de éxito:
        {
          "action": "canned" | "fallback",
          "respuesta": <str>,          # solo si action=canned
          "entry_id": <int>,           # solo si action=canned
          "confidence": <float>,
          "keywords_added": <int>,     # cuántas se añadieron al entry matched
          "new_pending_id": <int>|None # id del entry autogenerado pendiente
        }
    """
    if not is_enabled():
        return None
    if not mensaje or not str(mensaje).strip():
        return None
    api_key, modelo, provider = _get_groq_credentials()
    if not api_key:
        return None

    entries = _candidate_entries()
    if not entries:
        return None

    parsed = _call_groq(api_key, modelo, provider, str(mensaje), entries)
    if not parsed:
        return None

    try:
        confidence = float(parsed.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    match_id_raw = parsed.get("match_id")
    try:
        match_id = int(match_id_raw) if match_id_raw is not None else None
    except (TypeError, ValueError):
        match_id = None
    suggested_keywords = parsed.get("suggested_keywords") or []
    suggested_new = parsed.get("suggested_new_entry")

    # ── Camino 1: match con confianza suficiente ──────────────────
    if match_id and confidence >= NLU_CONFIDENCE_MIN:
        # Verificar que el entry existe y está activo
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

    # ── Camino 2: sin match — persistir propuesta pendiente si existe ──
    new_pending_id = _persist_new_entry_pending(suggested_new) if suggested_new else None
    _register_learning(mensaje, telefono, matched=False, entry_id=None, pending_id=new_pending_id)
    return {
        "action": "fallback",
        "confidence": confidence,
        "keywords_added": 0,
        "new_pending_id": new_pending_id,
    }


def _register_learning(mensaje: str, telefono: Optional[str], matched: bool,
                       entry_id: Optional[int] = None, pending_id: Optional[int] = None):
    """Registra señal de aprendizaje para el panel admin. Fire-and-forget."""
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
        logging.getLogger(__name__).info("nlu_service: learning register fail", exc_info=True)


__all__ = ["resolve", "is_enabled", "NLU_CONFIDENCE_MIN", "NLU_MODEL_DEFAULT"]
