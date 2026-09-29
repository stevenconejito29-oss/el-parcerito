"""Asesor IA comercial con conversación persistente.

Módulo portable y aislado. Reutiliza:
- ``_resumen_negocio_para_ia()`` y ``_llamar_ia_analisis()`` de ``routes.admin``
  para el snapshot y el proveedor externo (Anthropic / OpenAI / Groq).
- ``commercial_insights_service`` para el fallback local sin credenciales.

Añade dos capas nuevas sobre esa infra:
1. **Persistencia de conversación** vía ``AiAdvisorConversation`` y
   ``AiAdvisorMessage`` — permite que la IA responda con contexto del hilo
   completo y no solo de la última pregunta.
2. **Quick-actions preconfiguradas** para las 5 áreas de mayor impacto:
   finanzas, combos, promociones, cupones y campañas.

Contrato:
- Todo lo que llega al modelo es agregado, sin PII.
- El histórico se acorta automáticamente para no exceder ~6k tokens de contexto.
- El fallback local NUNCA falla: si el proveedor externo devuelve error, el
  módulo garantiza una respuesta razonable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from models import (
    AiAdvisorConversation,
    AiAdvisorMessage,
    SiteConfig,
    db,
)


# ─────────────────────────────────────────────────────────────────────
# Quick actions — prompts curados para las 5 áreas de más impacto
# ─────────────────────────────────────────────────────────────────────

QUICK_ACTIONS = {
    "finanzas": {
        "titulo": "📊 Análisis financiero",
        "descripcion": "Ingresos vs egresos, márgenes y ROI reciente",
        "categoria": "finanzas",
        "prompt": (
            "Hazme un análisis financiero completo del negocio con los datos "
            "actuales del contexto:\n"
            "1. Ventas por rango (7/30/90 días) y tendencia.\n"
            "2. Ticket medio y su evolución.\n"
            "3. Tasa de cancelación y su impacto en la caja.\n"
            "4. Margen bruto estimado a partir del coste de los productos "
            "más vendidos.\n"
            "5. ROI aproximado si se hicieran las siguientes acciones "
            "durante 30 días: (a) subir stock del top 5; (b) crear combo con "
            "los 3 productos más rentables; (c) cupón del 10 % para reactivar "
            "clientes inactivos.\n"
            "Cierra con las 3 métricas críticas a vigilar la próxima semana."
        ),
    },
    "combos": {
        "titulo": "🎁 Diseñar combos",
        "descripcion": "Combinaciones con margen sano según el catálogo real",
        "categoria": "combos",
        "prompt": (
            "Propón 3 combos concretos usando los productos activos del "
            "catálogo del contexto. Para cada combo especifica:\n"
            "- Nombre corto y comercial.\n"
            "- Productos exactos con cantidades.\n"
            "- Suma individual (según los precios del contexto) y precio "
            "sugerido del combo.\n"
            "- Ahorro absoluto y porcentual para el cliente.\n"
            "- Margen bruto estimado.\n"
            "- Justificación (por qué esos productos: complementarios, top "
            "ventas, oportunidad de rotar stock, etc.).\n"
            "Regla dura: precio del combo nunca por debajo del coste total."
        ),
    },
    "promociones": {
        "titulo": "🎯 Plan de promociones",
        "descripcion": "Ofertas rentables con métricas de éxito",
        "categoria": "promociones",
        "prompt": (
            "Diseña un plan de 3 promociones para las próximas 4 semanas "
            "basado en los datos del contexto. Para cada una:\n"
            "- Objetivo comercial (aumentar ticket medio / reactivar clientes "
            "/ mover stock parado / captar nuevos).\n"
            "- Segmento objetivo (clientes activos, dormidos, nuevos).\n"
            "- Mecánica exacta (2×1 sobre X, envío gratis sobre Y, "
            "descuento por franja horaria valle, etc.).\n"
            "- Duración y ventana horaria.\n"
            "- Métrica de éxito y umbral para pararla.\n"
            "- Riesgo esperado y cómo mitigarlo."
        ),
    },
    "cupones": {
        "titulo": "🎟️ Ideas de cupones",
        "descripcion": "Códigos calibrados por margen y objetivo",
        "categoria": "cupones",
        "prompt": (
            "Propón 4 cupones concretos con código sugerido, tipo "
            "(porcentaje / importe fijo / envío gratis), monto mínimo de "
            "pedido, límite global y por cliente, y ventana de fechas. "
            "Explica en cada caso el objetivo (captación / reactivación / "
            "reseña / cumpleaños) y estima el impacto en margen si el 10 % "
            "de los pedidos lo usa."
        ),
    },
    "campanas": {
        "titulo": "📣 Campañas de marketing",
        "descripcion": "Comunicación por WhatsApp / PWA para pico de ventas",
        "categoria": "campanas",
        "prompt": (
            "Propón 2 campañas de marketing accionables para las próximas "
            "2 semanas usando los canales disponibles (WhatsApp del bot, "
            "push PWA, banner en el storefront). Para cada campaña:\n"
            "- Hipótesis de por qué funcionará (soportada por datos del "
            "contexto: días fuertes, categorías con margen, clientes "
            "dormidos, etc.).\n"
            "- Copy para WhatsApp (< 260 caracteres, sin emojis ambiguos).\n"
            "- Copy para push (< 90 caracteres).\n"
            "- Segmento y horario de envío.\n"
            "- KPI de éxito y cómo medirlo desde el propio panel."
        ),
    },
}


# ─────────────────────────────────────────────────────────────────────
# Snapshot del negocio (delegado a la infra existente)
# ─────────────────────────────────────────────────────────────────────

def build_snapshot() -> dict:
    """Devuelve el snapshot agregado que se inyecta como contexto al modelo.

    Dos capas:

    1. **Datos base** — ``_resumen_negocio_para_ia`` (catálogo, ventas 7/30/90d,
       top por volumen, fidelidad, zonas, cupones).
    2. **Insights derivados** — ``business_intelligence.compute_insights`` con
       comparativas semana-vs-semana / mes-vs-mes, ranking por CONTRIBUCIÓN
       MARGINAL (no solo volumen), heatmap horarios+día de semana,
       pares cross-sell, salud de clientes (activos/dormidos/perdidos/nuevos),
       mix de métodos de pago y anomalías legibles.

    Con esta capa el modelo cita hechos concretos ("ventas cayeron 22% esta
    semana, principalmente el miércoles") en lugar de generalidades.
    """
    from routes.admin import _resumen_negocio_para_ia
    base = _resumen_negocio_para_ia()
    try:
        from ai_services.business_intelligence import compute_insights
        base["insights"] = compute_insights()
    except Exception as exc:
        base["insights"] = {"error": f"insights_no_disponibles: {exc}"}
    return base


# ─────────────────────────────────────────────────────────────────────
# Conversación
# ─────────────────────────────────────────────────────────────────────

MAX_HISTORY_MESSAGES = 20
"""Nº máximo de mensajes previos que se envían al modelo (control de tokens)."""


def crear_conversacion(owner_id: int, titulo: str, categoria: str = "general") -> AiAdvisorConversation:
    """Abre un hilo nuevo del asesor."""
    conv = AiAdvisorConversation(
        owner_id=owner_id,
        titulo=(titulo or "Nueva conversación").strip()[:160],
        categoria=categoria if categoria in _CATEGORIAS_VALIDAS else "general",
    )
    db.session.add(conv)
    db.session.flush()
    return conv


def listar_conversaciones(owner_id: int, incluir_archivadas: bool = False) -> list[AiAdvisorConversation]:
    q = AiAdvisorConversation.query.filter_by(owner_id=owner_id)
    if not incluir_archivadas:
        q = q.filter_by(archivada=False)
    return q.order_by(AiAdvisorConversation.updated_at.desc()).all()


def archivar_conversacion(conv: AiAdvisorConversation) -> None:
    conv.archivada = True
    db.session.add(conv)


def _historial_para_modelo(conv: AiAdvisorConversation) -> list[dict]:
    """Últimos N mensajes ordenados por id, sin metadatos internos."""
    mensajes = (
        conv.mensajes
        .order_by(AiAdvisorMessage.id.desc())
        .limit(MAX_HISTORY_MESSAGES)
        .all()
    )
    mensajes.reverse()
    return [
        {"role": m.role, "content": m.contenido}
        for m in mensajes
        if m.role in ("user", "assistant") and m.contenido
    ]


def _tokens_estimados(texto: str) -> int:
    """Heurística barata (~4 chars = 1 token) sin tiktoken."""
    return max(1, len(texto or "") // 4)


def _guardar_mensaje(conv: AiAdvisorConversation, role: str, contenido: str, fuente: str | None = None) -> AiAdvisorMessage:
    msg = AiAdvisorMessage(
        conversacion_id=conv.id,
        role=role,
        contenido=(contenido or "").strip(),
        fuente=fuente,
        tokens_estimados=_tokens_estimados(contenido),
    )
    db.session.add(msg)
    # Toca updated_at del hilo para ordenar la lista lateral
    conv.updated_at = _utcnow()
    return msg


def _utcnow():
    from datetime import datetime
    return datetime.utcnow()


# ─────────────────────────────────────────────────────────────────────
# Preguntar al asesor
# ─────────────────────────────────────────────────────────────────────

_CATEGORIAS_VALIDAS = {"general", "finanzas", "combos", "promociones", "cupones", "campanas"}


@dataclass
class RespuestaAsesor:
    """Resultado envuelto para la UI."""
    texto: str
    fuente: str                    # 'external' | 'local'
    error: str | None = None
    conversacion_id: int | None = None
    mensaje_id: int | None = None


def preguntar(
    conv: AiAdvisorConversation,
    pregunta: str,
    quick_action: str | None = None,
) -> RespuestaAsesor:
    """Envía un mensaje del usuario y devuelve la respuesta del asesor.

    - Persiste el mensaje del usuario y la respuesta del asistente.
    - Reenvía el histórico reciente al modelo (últimos MAX_HISTORY_MESSAGES).
    - Si el proveedor externo falla, cae al análisis local sin romper.
    """
    pregunta = (pregunta or "").strip()
    if not pregunta:
        return RespuestaAsesor(texto="", fuente="local", error="pregunta_vacia")

    _guardar_mensaje(
        conv,
        role="user",
        contenido=pregunta,
        fuente=("quick_action" if quick_action else None),
    )

    contexto = build_snapshot()
    historial = _historial_para_modelo(conv)

    # Delega al llamador externo existente para no duplicar el prompt system.
    from routes.admin import _llamar_ia_analisis
    from commercial_insights_service import answer_commercial_question, build_commercial_diagnostic

    # El proveedor externo espera pregunta + contexto. Le pasamos como
    # "pregunta" la última del usuario, prefijada con un resumen del hilo
    # cuando hay histórico previo (más de 1 turno). Así no forzamos un cambio
    # de firma en _llamar_ia_analisis y mantenemos compatibilidad total.
    if len(historial) > 2:
        prefijo_hilo = _resumir_historial(historial[:-1])
        pregunta_efectiva = f"{prefijo_hilo}\n\nMi pregunta actual:\n{pregunta}"
    else:
        pregunta_efectiva = pregunta

    respuesta_ext, error_ext = _llamar_ia_analisis(pregunta_efectiva, contexto)

    if respuesta_ext:
        fuente = "external"
        texto = respuesta_ext
        error_visible = None
    else:
        # Fallback local — siempre devuelve algo.
        diagnostico = build_commercial_diagnostic()
        texto = answer_commercial_question(pregunta, diagnostico)
        fuente = "local"
        error_visible = None if error_ext == "external_not_configured" else error_ext

    msg = _guardar_mensaje(conv, role="assistant", contenido=texto, fuente=fuente)
    return RespuestaAsesor(
        texto=texto,
        fuente=fuente,
        error=error_visible,
        conversacion_id=conv.id,
        mensaje_id=msg.id,
    )


def _resumir_historial(mensajes: Iterable[dict]) -> str:
    """Resumen compacto de los últimos turnos para preservar contexto sin
    inflar el prompt. Formato legible: 'Usuario: … / Asesor: …'."""
    lineas = []
    for m in mensajes:
        rol_es = "Usuario" if m["role"] == "user" else "Asesor"
        contenido = (m["content"] or "").strip().replace("\n", " ")
        if len(contenido) > 320:
            contenido = contenido[:317] + "…"
        lineas.append(f"{rol_es}: {contenido}")
    cuerpo = "\n".join(lineas[-8:])  # máximo 8 líneas de contexto previo
    return f"Contexto de la conversación previa:\n{cuerpo}"


# ─────────────────────────────────────────────────────────────────────
# Estado del proveedor (para pintar en la UI)
# ─────────────────────────────────────────────────────────────────────

def estado_proveedor() -> dict:
    """Info sobre la configuración del proveedor externo.

    Se usa en la UI para explicar al super admin cuándo la respuesta es
    del modelo externo y cuándo del fallback local.
    """
    provider = (SiteConfig.get("COMMERCIAL_AI_PROVIDER", "") or "").strip().lower()
    return {
        "enabled": str(SiteConfig.get("COMMERCIAL_AI_ENABLED", "0") or "0").strip().lower()
                   in {"1", "true", "yes", "on"},
        "provider": provider,
        "provider_label": {
            "anthropic": "Anthropic Claude",
            "openai": "OpenAI",
            "groq": "Groq",
            "": "Sin proveedor externo",
        }.get(provider, provider or "—"),
        "model": SiteConfig.get("COMMERCIAL_AI_MODEL", "") or "",
        "key_set": bool(SiteConfig.get("COMMERCIAL_AI_API_KEY", "")),
    }
