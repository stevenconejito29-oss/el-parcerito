"""Dominio del chat web: conocimiento determinista y handoff transaccional."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import unicodedata
import uuid
import requests
from rapidfuzz import fuzz

from flask import current_app, session, url_for

from extensions import db
from models import KnowledgeEntry, Order, SiteConfig, User, WebChatConversation, WebChatMessage, utcnow
from services import encolar_whatsapp_generico
from store_config import get_store_features

VALID_STATUSES = {"bot", "waiting_agent", "active_agent", "closed"}
MAX_MESSAGE = 1200

INTENT_GUIDANCE = {
    "pedido": (
        "Para proteger tus datos, abre el seguimiento desde la pantalla final "
        "de tu pedido. Allí puedes consultar su estado. Si este navegador reconoce "
        "un pedido pendiente que aún admite cancelación, verás aquí debajo el botón "
        "«Cancelar»; en los demás casos pide atención humana para que el equipo revise el pago."
    ),
}

_STOPWORDS = {"que", "como", "cual", "donde", "cuando", "para", "por", "con", "una", "uno", "unos", "unas", "los", "las", "del", "este", "esta", "esto", "funciona", "funcionan", "quiero", "quisiera", "puedo", "pueden", "tienen"}
_INTENT_TERMS = {
    "human": {"agente", "asesor", "humano", "persona", "operador"},
    "cancel": {"cancelar", "cancelo", "anular", "anulo"},
    "tracking": {"estado", "seguimiento", "tracking", "repartidor", "demora", "tarda", "llega"},
    "delivery": {"delivery", "envio", "envios", "domicilio", "domicilios", "cobertura", "zona", "reparto"},
    "loyalty": {"cafecito", "cafecitos", "punto", "puntos", "grano", "granitos", "canje", "canjear"},
    "hours": {"horario", "horarios", "abren", "abrir", "cierran", "cerrar", "abierto", "cerrado"},
    "payments": {"pago", "pagos", "pagar", "efectivo", "bizum", "tarjeta", "paypal"},
    "location": {"direccion", "ubicacion", "ubicados", "mapa", "llegar", "local"},
    "catalog": {"menu", "carta", "catalogo", "producto", "productos", "combo", "combos", "comprar", "pedir"},
    "tutorial": {"tutorial", "paso", "pasos", "usar", "uso", "comprar"},
    "greeting": {"hola", "buenas", "buenos", "hey", "saludos"},
    "thanks": {"gracias", "perfecto", "genial", "vale"},
    "notifications": {"notificacion", "notificaciones", "avisos", "alertas", "instalar", "pwa", "app"},
    "privacy": {"privacidad", "datos", "seguridad", "cuenta"},
    "allergens": {"alergia", "alergias", "alergeno", "alergenos", "ingrediente", "ingredientes"},
    "pickup": {"recoger", "recogida", "retiro", "local", "buscarlo"},
    "coupons": {"cupon", "cupones", "descuento", "descuentos", "promocion", "promociones", "oferta"},
    "changes": {"cambiar", "cambio", "modificar", "editar", "direccion", "nota", "sabor", "tamano"},
    "availability": {"disponible", "disponibilidad", "agotado", "stock", "queda", "quedan"},
    "receipt": {"ticket", "recibo", "factura", "comprobante", "numero"},
    "reorder": {"repetir", "recomprar", "pedirlo", "anterior", "ultima"},
}

_INTENT_PHRASES = {
    "human": ("hablar con alguien", "servicio al cliente", "atencion al cliente"),
    "cancel": ("ya no lo quiero", "cancelar pedido", "anular pedido"),
    "tracking": ("donde esta mi pedido", "donde va mi pedido", "donde esta el repartidor", "cuanto falta"),
    "delivery": ("hacen envios", "llega a mi casa", "zona de entrega", "cuanto cuesta el envio"),
    "payments": ("como se paga", "puedo pagar", "pago al recibir", "pago contra entrega"),
    "tutorial": ("como hago un pedido", "como usar la pagina", "como funciona la pagina"),
    "pickup": ("recoger en tienda", "recoger en el local", "pasar a buscar"),
    "coupons": ("codigo promocional", "aplicar un cupon", "tienen ofertas"),
    "changes": ("cambiar mi pedido", "cambiar la direccion", "agregar una nota"),
    "availability": ("hay disponibilidad", "queda disponible", "esta agotado"),
    "receipt": ("ticket de compra", "numero de pedido", "comprobante del pedido"),
    "reorder": ("repetir mi ultimo pedido", "comprar lo mismo", "ultima compra"),
}

_INTENT_ORDER = (
    "human", "cancel", "tracking", "payments", "delivery", "pickup", "loyalty",
    "hours", "notifications", "privacy", "allergens", "coupons", "changes",
    "availability", "receipt", "reorder", "tutorial", "catalog", "location", "greeting", "thanks",
)


def _normalise(value: str) -> str:
    value = "".join(c for c in unicodedata.normalize("NFD", value or "") if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", value.lower()).split())


def _visitor_hash(create: bool = True) -> str | None:
    token = session.get("web_chat_token")
    if not token and create:
        token = secrets.token_urlsafe(32)
        session["web_chat_token"] = token
    return hashlib.sha256(str(token).encode()).hexdigest() if token else None


def conversation_for_visitor(create: bool = True) -> WebChatConversation | None:
    token_hash = _visitor_hash(create=create)
    if not token_hash:
        return None
    row = WebChatConversation.query.filter_by(visitor_token_hash=token_hash).first()
    session_customer_id = session.get("push_cliente_id")
    if row:
        # El vínculo solo puede provenir de la identidad establecida por el
        # checkout. En un dispositivo compartido, un cliente distinto recibe
        # una conversación nueva: nunca heredamos historial ni destinatario.
        if session_customer_id and row.customer_id and row.customer_id != int(session_customer_id):
            token = secrets.token_urlsafe(32)
            session["web_chat_token"] = token
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            row = WebChatConversation(
                public_id=str(uuid.uuid4()), visitor_token_hash=token_hash,
                customer_id=int(session_customer_id),
            )
            db.session.add(row); db.session.flush()
            add_message(row, "bot", welcome_message()); db.session.commit()
            return row
        if session_customer_id and not row.customer_id:
            row.customer_id = int(session_customer_id)
            db.session.commit()
        return row
    if not create:
        return row
    row = WebChatConversation(
        public_id=str(uuid.uuid4()), visitor_token_hash=token_hash,
        customer_id=int(session_customer_id) if session_customer_id else None,
    )
    db.session.add(row)
    db.session.flush()
    add_message(row, "bot", welcome_message())
    db.session.commit()
    return row


def welcome_message() -> str:
    name = SiteConfig.get("NOMBRE_NEGOCIO", "nuestra tienda") or "nuestra tienda"
    features = get_store_features()
    topics = ["productos", "horarios", "pagos", "pedidos"]
    if features.get("delivery"):
        topics.append("envíos")
    if features.get("puntos"):
        topics.append("cafecitos")
    return f"¡Hola! Soy el asistente de {name}. Puedo ayudarte con {', '.join(topics)}. ¿Qué necesitas?"


def add_message(conversation, sender: str, body: str, *, agent_id=None, nonce=None):
    # Conserva párrafos y listas, pero normaliza espacios y líneas vacías.
    lines = [" ".join(line.split()) for line in str(body or "").replace("\x00", "").splitlines()]
    clean = "\n".join(lines).strip()
    clean = re.sub(r"\n{3,}", "\n\n", clean)[:MAX_MESSAGE]
    if not clean:
        raise ValueError("empty_message")
    row = WebChatMessage(
        conversation_id=conversation.id, sender=sender, body=clean,
        agent_id=agent_id, client_nonce=(str(nonce)[:64] if nonce else None),
    )
    conversation.last_activity_at = utcnow()
    db.session.add(row)
    db.session.flush()
    return row


def _replace_placeholders(answer: str) -> str:
    public_url = current_app.config.get("PUBLIC_BASE_URL") or url_for("public.index", _external=True)
    values = {
        "nombre": SiteConfig.get("NOMBRE_NEGOCIO", "la tienda"),
        "negocio": SiteConfig.get("NOMBRE_NEGOCIO", "la tienda"),
        "telefono": SiteConfig.get("TELEFONO_NEGOCIO", ""),
        "direccion": SiteConfig.get("DIRECCION_NEGOCIO", ""),
        "horario": _business_hours_text(),
        "web_url": public_url,
        "tienda_url": public_url,
    }
    rendered = str(answer or "")
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", str(value or ""))
    return rendered


def _business_hours_text() -> str:
    try:
        from schedule_service import configured_schedule_context
        context = configured_schedule_context()
        return context.get("today") or context.get("weekly") or "Consulta el horario visible en el menú"
    except Exception:
        return SiteConfig.get("HORARIO_NEGOCIO", "Consulta el horario visible en el menú")


def knowledge_answer(question: str) -> str | None:
    query = _normalise(question)
    if not query:
        return None
    qwords = set(query.split())
    meaningful = {word for word in qwords if len(word) >= 3 and word not in _STOPWORDS}
    best = (0, None)
    entries = KnowledgeEntry.query.filter(
        KnowledgeEntry.activo.is_(True),
        KnowledgeEntry.audiencia.in_(("cliente", "todos")),
    ).order_by(KnowledgeEntry.orden.desc(), KnowledgeEntry.id.asc()).all()
    for entry in entries:
        prompt = _normalise(entry.pregunta)
        score = 100 if query == prompt else 0
        # RapidFuzz aporta tolerancia a faltas, orden de palabras y frases
        # naturales sin enviar texto del cliente a un proveedor externo.
        score = max(score, round(fuzz.WRatio(query, prompt) * .42))
        if prompt and (prompt in query or query in prompt):
            score = max(score, 35)
        prompt_words = {word for word in prompt.split() if len(word) >= 3}
        overlap = meaningful & prompt_words
        if overlap:
            score += round(18 * len(overlap) / max(1, len(meaningful)))
        for keyword in entry.keyword_list():
            kw = _normalise(keyword)
            if not kw:
                continue
            kw_words = {word for word in kw.split() if word not in _STOPWORDS}
            if " " in kw and kw_words and kw in query:
                score += 30
            elif kw in qwords and len(kw) >= 4:
                score += 9
        if score > best[0]:
            best = (score, entry)
    return _replace_placeholders(best[1].respuesta) if best[0] >= 31 and best[1] else None


def visitor_orders() -> list[dict]:
    """Pedidos que este mismo navegador puede consultar o cancelar."""
    slots = session.get("guest_order_tokens", {})
    ids = [int(key) for key in slots if str(key).isdigit()]
    if not ids:
        return []
    # El chat es una bandeja operativa, no un historial: cancelados y
    # finalizados desaparecen para no confundir al cliente con acciones viejas.
    rows = Order.query.filter(
        Order.id.in_(ids),
        Order.estado.in_(("pendiente", "armando", "listo", "en_ruta")),
    ).order_by(Order.creado_en.desc()).limit(10).all()
    result = []
    for order in rows:
        slot = slots.get(str(order.id))
        token = str(slot.get("token") if isinstance(slot, dict) else slot or "")
        if not token:
            continue
        result.append({
            "id": order.id,
            "number": order.numero_pedido,
            "status": order.estado,
            "status_label": {
                "pendiente": "Recibido", "armando": "En preparación",
                "listo": "Listo", "en_ruta": "En reparto",
                "entregado": "Entregado",
                "cancelado": "Cancelado",
            }.get(order.estado, str(order.estado or "En proceso").replace("_", " ").title()),
            "tracking_url": url_for(
                "public.pedido_confirmado", pedido_id=order.id, token=token,
            ),
            "cancelable": order.estado == "pendiente" and not (
                order.metodo_pago == "bizum" and order.pago_confirmado
            ),
        })
    return result


def cancel_visitor_order(order_id: int) -> tuple[bool, str]:
    """Cancelación transaccional autorizada por la sesión, con bloqueo de fila."""
    allowed = {row["id"]: row for row in visitor_orders()}
    if order_id not in allowed:
        return False, "No pudimos verificar ese pedido en este dispositivo."
    order = Order.query.filter_by(id=order_id).with_for_update().first()
    if not order or order.estado != "pendiente":
        return False, "Ese pedido ya no admite cancelación automática."
    if order.metodo_pago == "bizum" and order.pago_confirmado:
        return False, "El pago ya fue confirmado; solicita atención humana para revisar la devolución."
    from services import cancelar_pedido_operativo
    cancelar_pedido_operativo(
        order, actor_id=order.cliente_id, canal="chat_web",
        detalle="cancelación confirmada desde el chat web",
    )
    db.session.commit()
    return True, f"El pedido {order.numero_pedido} quedó cancelado correctamente."


def reorder_visitor_order(order_id: int):
    """Reconstruye el carrito solo desde un pedido autorizado por esta sesión."""
    if order_id not in {row["id"] for row in visitor_orders()}:
        return False, "No pudimos verificar ese pedido en este dispositivo.", None
    order = db.session.get(Order, order_id)
    if not order:
        return False, "Ese pedido ya no está disponible.", None
    from routes.public import _reconstruir_carrito_desde_pedido
    response = _reconstruir_carrito_desde_pedido(order)
    return True, f"Añadimos los productos disponibles de {order.numero_pedido}.", response.location


def _classify_intent(question: str) -> str | None:
    query = _normalise(question)
    words = set(query.split())
    if not words:
        return None
    scores: dict[str, int] = {}
    for intent in _INTENT_ORDER:
        score = 0
        for phrase in _INTENT_PHRASES.get(intent, ()):
            if phrase in query:
                score = max(score, 100)
            elif len(query) >= 8 and fuzz.partial_ratio(query, phrase) >= 88:
                score = max(score, 78)
        terms = _INTENT_TERMS[intent]
        exact = words & terms
        score += 32 * len(exact)
        # Corrige errores frecuentes ("envioo", "notificasiones", "repartidorr")
        # sin convertir palabras cortas o ambiguas en intenciones falsas.
        for word in words - exact:
            if len(word) < 5:
                continue
            if any(fuzz.ratio(word, term) >= 82 for term in terms if len(term) >= 5):
                score += 22
        if score:
            scores[intent] = score
    if not scores:
        return None
    # El orden estable resuelve empates en favor de acciones críticas.
    return max(_INTENT_ORDER, key=lambda name: (scores.get(name, 0), -_INTENT_ORDER.index(name))) if max(scores.values()) >= 22 else None


def _intent_answer(intent: str) -> str | None:
    features = get_store_features()
    public_url = current_app.config.get("PUBLIC_BASE_URL") or url_for("public.index", _external=True)
    if intent in {"cancel", "tracking"}:
        return INTENT_GUIDANCE["pedido"]
    if intent == "human":
        return "Pulsa «Hablar con alguien» debajo del chat. Un agente continuará la conversación aquí mismo."
    if intent == "delivery":
        return ("Sí tenemos delivery. La cobertura, el coste y el tiempo se calculan con tu dirección en el carrito antes de confirmar; así siempre ves información actualizada." if features.get("delivery") else "El delivery no está disponible en este momento. Revisa en el carrito las modalidades activas.")
    if intent == "loyalty":
        return ("Con tus compras entregadas acumulas cafecitos. En la sección Cafecitos puedes consultar las recompensas y ver cuáles puedes canjear; la verificación final se realiza de forma segura al confirmar." if features.get("puntos") else "El programa de cafecitos no está activo en este momento.")
    if intent == "hours":
        return f"El horario configurado para hoy es: {_business_hours_text()}."
    if intent == "payments":
        methods = []
        if features.get("efectivo"): methods.append("efectivo")
        if features.get("bizum"): methods.append("Bizum")
        if features.get("tarjeta"): methods.append("tarjeta con datáfono")
        available = ", ".join(methods) if methods else "la opción indicada en el carrito"
        return (
            f"El pago es siempre contra entrega o al recoger: actualmente puedes elegir {available}. "
            "No pagas por adelantado ni escribes datos bancarios en la página o en el chat. "
            "Selecciona tu preferencia en la canasta y paga cuando tengas el pedido delante."
        )
    if intent == "location":
        return f"Estamos en {SiteConfig.get('DIRECCION_NEGOCIO', '') or 'la dirección mostrada en la información de la tienda'}. Puedes consultar cobertura y entrega sin salir de la app."
    if intent == "notifications":
        return "Instala la app para recibir el estado de tu pedido y respuestas del equipo. En iPhone: Compartir → Añadir a pantalla de inicio. Tus avisos son privados y solo corresponden a este dispositivo."
    if intent == "privacy":
        return "Protegemos cada conversación y pedido por dispositivo y sesión. No compartas contraseñas, PIN, códigos ni datos bancarios en el chat. Puedes revisar privacidad y condiciones desde el pie del menú."
    if intent == "allergens":
        return "Revisa la descripción y opciones de cada producto. Si tienes una alergia o intolerancia, no confirmes basándote solo en el chat: pulsa «Hablar con una persona» para que el equipo verifique ingredientes y posible contaminación cruzada."
    if intent == "pickup":
        return ("Puedes elegir Recogida en la canasta. La página te mostrará el horario y omite la tarifa de delivery; espera el aviso de pedido listo antes de ir al local." if features.get("recogida") else "La recogida en el local no está activa ahora. La canasta mostrará únicamente las modalidades disponibles.")
    if intent == "coupons":
        return "Si tienes un cupón, escríbelo en la canasta antes de continuar. La página valida vigencia, requisitos y límite de uso, y muestra el descuento en el total antes de confirmar. El chat nunca inventa ni activa códigos."
    if intent == "changes":
        return "Antes de confirmar puedes volver a la canasta y cambiar productos, cantidades, opciones, dirección o notas. Después de confirmar, no envíes datos por el chat: pide atención humana para que el equipo compruebe si cocina aún permite el cambio."
    if intent == "availability":
        return f"El Menú muestra únicamente productos y opciones disponibles en este momento. Abre el producto para revisar tamaños, sabores, extras y precio actual: {public_url}"
    if intent == "receipt":
        return "Al terminar recibes una ficha con número de pedido, productos, cantidades, entrega, forma de pago y total. Ese número identifica tu compra; desde «Ver estado» puedes volver a consultar la ficha en este dispositivo."
    if intent == "reorder":
        return "Si este dispositivo reconoce una compra anterior entregada, aparecerá debajo «Repetir compra». Añadiremos a la canasta solo los productos que sigan disponibles; sabores, extras o variantes se vuelven a elegir para evitar errores de precio."
    if intent == "catalog":
        return f"Puedes ver productos, precios, disponibilidad y combos actualizados en el Menú: {public_url}"
    if intent == "tutorial":
        delivery_step = "Elige delivery o recogida" if features.get("delivery") else "Elige la modalidad disponible"
        return (
            "Para comprar: 1) abre Menú y elige un producto; 2) selecciona tamaño, "
            f"sabor o extras; 3) revisa la canasta; 4) {delivery_step}; "
            "5) elige el pago y confirma. El total siempre se muestra antes de enviar."
        )
    if intent == "greeting":
        return f"¡Hola! Soy el asistente de {SiteConfig.get('NOMBRE_NEGOCIO', 'la tienda') or 'la tienda'}. Pregúntame por productos, horario, pagos, delivery, cafecitos o tu pedido."
    if intent == "thanks":
        return "¡Con gusto! Si necesitas algo más, aquí estoy."
    return None


def bot_reply(question: str) -> tuple[str, str]:
    intent = _classify_intent(question)
    # Las intenciones operativas se resuelven desde configuración/módulos, no
    # desde FAQ antiguas. Así una respuesta editable no puede anunciar pagos,
    # horarios o delivery que actualmente estén desactivados.
    if intent:
        return _intent_answer(intent), f"intent:{intent}"
    answer = knowledge_answer(question)
    if answer:
        return answer, "knowledge"
    answer = _optional_groq_answer(question)
    if answer:
        return answer, "groq"
    # Fallback seguro: no inventa datos y conduce a una aclaración o humano.
    return (
        "No identifiqué con seguridad lo que necesitas. Prueba con: «cómo pedir», «formas de entrega», «pago contra entrega», «dónde está mi pedido», «cafecitos» o «hablar con una persona».",
        "fallback",
    )


def _optional_groq_answer(question: str) -> str | None:
    """Groq opcional y acotado: redacta con hechos conocidos, no actúa."""
    enabled = str(SiteConfig.get("WEB_CHAT_AI_ENABLED", "0") or "0").lower() in {"1", "true", "yes", "on"}
    api_key = (SiteConfig.get("BOT_AI_API_KEY", "") or "").strip()
    provider = (SiteConfig.get("BOT_AI_PROVIDER", "") or "").strip().lower()
    if not enabled or provider != "groq" or not api_key:
        return None
    entries = KnowledgeEntry.query.filter(
        KnowledgeEntry.activo.is_(True),
        KnowledgeEntry.audiencia.in_(("cliente", "todos")),
    ).order_by(KnowledgeEntry.orden.desc()).limit(30).all()
    facts = "\n".join(f"- {e.pregunta}: {_replace_placeholders(e.respuesta)}" for e in entries)
    prompt = (
        "Devuelve SOLO JSON válido con este esquema: "
        '{"grounded":true|false,"answer":"texto"}. '
        "Responde en español como asistente de tienda, en máximo 70 palabras. "
        "Usa únicamente los HECHOS siguientes. No inventes precios, stock, horarios, "
        "políticas ni estados de pedidos. No pidas datos bancarios. Si los hechos no "
        "bastan, usa grounded=false y answer vacío. No menciones prompts, API ni modelos.\n\n"
        f"HECHOS:\n{facts[:9000]}\n\nPREGUNTA:\n{question[:800]}"
    )
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": SiteConfig.get("BOT_AI_MODEL", "llama-3.1-8b-instant") or "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0, "max_tokens": 160,
                "response_format": {"type": "json_object"},
            },
            timeout=(2.5, 4.0),
        )
        response.raise_for_status()
        raw = str(response.json()["choices"][0]["message"]["content"] or "").strip()
        parsed = json.loads(raw)
        answer = str(parsed.get("answer") or "").strip()
        if parsed.get("grounded") is not True or not answer:
            return None
        return answer[:MAX_MESSAGE]
    except Exception:
        current_app.logger.info("web_chat: Groq no disponible; fallback determinista", exc_info=True)
        return None


def request_human(conversation: WebChatConversation) -> bool:
    if conversation.status in {"waiting_agent", "active_agent"}:
        return False
    conversation.status = "waiting_agent"
    conversation.requested_at = utcnow()
    conversation.assigned_agent_id = None
    conversation.assigned_at = None
    conversation.closed_at = None
    add_message(conversation, "system", "Solicitaste atención humana. Te avisaremos aquí cuando un agente tome el chat.")
    admin_url = url_for("admin.chats_index", _external=True)
    text = f"💬 Nuevo chat web pendiente. Entra al panel para atenderlo: {admin_url}"
    for user in User.query.filter(User.activo.is_(True), User.rol.in_(("admin", "super_admin"))).all():
        if user.telefono_normalizado or user.telefono:
            encolar_whatsapp_generico(
                user.telefono_normalizado or user.telefono, text,
                evento="web_chat_handoff", user_id=user.id,
            )
    # Push y WhatsApp son avisos redundantes dirigidos exclusivamente al
    # equipo. El cliente continúa siempre dentro del chat web.
    from push_service import notify_roles
    notify_roles(
        ["admin", "super_admin"], "💬 Chat web pendiente",
        "Un cliente solicita atención. Abre la bandeja para responder.",
        url="/admin/chats", tag=f"web-chat-{conversation.public_id}",
        require_interaction=True,
    )
    db.session.commit()
    return True


def resume_bot(conversation: WebChatConversation):
    conversation.status = "bot"
    conversation.assigned_agent_id = None
    conversation.assigned_at = None
    conversation.closed_at = None
    add_message(conversation, "system", "Volviste al asistente automático.")
    add_message(conversation, "bot", "Estoy de vuelta. ¿En qué más te ayudo?")
    db.session.commit()


def serialise_message(row):
    return {
        "id": row.id, "sender": row.sender, "body": row.body,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
    }


def serialise_conversation(row):
    return {
        "id": row.public_id, "status": row.status,
        # No exponemos nombre, teléfono ni id interno. El frontend solo necesita
        # saber si la sesión de checkout quedó vinculada para explicar avisos.
        "customer_recognised": bool(row.customer_id),
        "assigned_agent": row.assigned_agent.nombre if row.assigned_agent else None,
        "last_activity_at": row.last_activity_at.isoformat() + "Z" if row.last_activity_at else None,
    }
