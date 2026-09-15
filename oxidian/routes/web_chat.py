"""API pública del chat web; identidad por token opaco guardado en sesión."""
from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import IntegrityError

from extensions import db, limiter
from models import WebChatMessage
from web_chat_service import (
    MAX_MESSAGE, add_message, bot_reply, conversation_for_visitor,
    cancel_visitor_order, last_reorderable_order, reorder_visitor_order, request_human, resume_bot, serialise_conversation,
    serialise_message, visitor_orders, unread_count_for_visitor, mark_conversation_read, redact_chat_credentials,
)

web_chat_bp = Blueprint("web_chat", __name__)


def _payload(conversation, after=None, *, before=None):
    query = conversation.messages.order_by(None)
    if before is not None:
        query = query.filter(WebChatMessage.id < before)
    if after is not None:
        query = query.filter(WebChatMessage.id > max(0, after))
    backwards = after is None
    rows = query.order_by(WebChatMessage.id.desc() if backwards else WebChatMessage.id.asc()).limit(101).all()
    more = len(rows) > 100
    messages = rows[:100]
    if backwards:
        messages.reverse()
    return {
        "ok": True,
        "conversation": serialise_conversation(conversation),
        "messages": [serialise_message(row) for row in messages],
        "has_more": more and not backwards,
        "has_older": more and backwards,
        "orders": visitor_orders(),
        "reorder": last_reorderable_order(),
    }


@web_chat_bp.get("/state")
@limiter.limit("120 per minute") if limiter else (lambda f: f)
def state():
    conversation = conversation_for_visitor()
    try:
        after = int(request.args["after"]) if "after" in request.args else None
        before = int(request.args["before"]) if "before" in request.args else None
    except ValueError:
        return jsonify({"ok": False, "error": "Cursor de conversación inválido."}), 400
    if after is not None and before is not None:
        return jsonify({"ok": False, "error": "Usa una dirección de historial cada vez."}), 400
    return jsonify(_payload(conversation, after, before=before))


@web_chat_bp.post("/messages")
@limiter.limit("30 per minute") if limiter else (lambda f: f)
def send_message():
    if not request.is_json:
        return jsonify({"ok": False, "error": "Formato no admitido."}), 415
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("message"), str):
        return jsonify({"ok": False, "error": "Mensaje inválido."}), 400
    if not isinstance(data.get("nonce", ""), str) or len(data.get("nonce", "")) > 64:
        return jsonify({"ok": False, "error": "Identificador de mensaje inválido."}), 400
    body = data["message"].strip()
    nonce = data.get("nonce", "").strip()
    if "\x00" in body:
        return jsonify({"ok": False, "error": "Mensaje inválido."}), 400
    if not body or len(body) > MAX_MESSAGE:
        return jsonify({"ok": False, "error": "Mensaje inválido."}), 400
    body = redact_chat_credentials(body)
    conversation = conversation_for_visitor()
    if nonce:
        existing = WebChatMessage.query.filter_by(
            conversation_id=conversation.id, client_nonce=nonce,
        ).first()
        if existing:
            return jsonify(_payload(conversation))
    if conversation.status == "closed":
        resume_bot(conversation)
    source = None
    learning = None
    assigned_agent_id = None
    try:
        add_message(conversation, "client", body, nonce=nonce)
        assigned_agent_id = conversation.assigned_agent_id if conversation.status == "active_agent" else None
        if conversation.status == "bot":
            answer, source = bot_reply(body)
            add_message(conversation, "bot", answer)
            if source in {"fallback", "groq"}:
                learning = (source, answer)
        db.session.commit()
        if assigned_agent_id:
            try:
                from push_service import notify_user
                notify_user(
                    assigned_agent_id, "💬 Nuevo mensaje del cliente",
                    body[:120], url=f"/admin/chats/{conversation.public_id}",
                    tag=f"web-chat-{conversation.public_id}", require_interaction=True,
                )
            except Exception:
                # El mensaje ya está confirmado en BD. Push es un canal auxiliar
                # y nunca debe hacer fallar ni duplicar la conversación.
                current_app.logger.exception("No se pudo notificar el mensaje del chat web")
        if learning:
            try:
                from bot_learning_service import registrar_signal
                registrar_signal(body, action_llm=f"web_{learning[0]}", reply_snippet=learning[1])
            except Exception:
                current_app.logger.exception("No se pudo registrar el aprendizaje del chat")
    except IntegrityError:
        db.session.rollback()
        # Solo una colisión de un envío confirmado es un reintento exitoso.
        existing = WebChatMessage.query.filter_by(
            conversation_id=conversation.id, client_nonce=nonce,
        ).first() if nonce else None
        if not existing:
            return jsonify({"ok": False, "error": "No se pudo guardar el mensaje. Inténtalo de nuevo."}), 503
    payload = _payload(conversation_for_visitor())
    payload["offer_human"] = bool(conversation.status == "bot" and source == "intent:human")
    return jsonify(payload)


@web_chat_bp.post("/request-agent")
@limiter.limit("5 per hour") if limiter else (lambda f: f)
def request_agent():
    conversation = conversation_for_visitor()
    requested = request_human(conversation)
    return jsonify({**_payload(conversation_for_visitor()), "requested": requested})


@web_chat_bp.post("/resume-bot")
@limiter.limit("10 per hour") if limiter else (lambda f: f)
def return_to_bot():
    conversation = conversation_for_visitor()
    resume_bot(conversation)
    return jsonify(_payload(conversation))


@web_chat_bp.post("/orders/<int:order_id>/cancel")
@limiter.limit("5 per hour") if limiter else (lambda f: f)
def cancel_order(order_id):
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or data.get("confirm") is not True:
        return jsonify({"ok": False, "error": "Debes confirmar la cancelación."}), 400
    try:
        ok, message = cancel_visitor_order(order_id)
        conversation = conversation_for_visitor()
        add_message(conversation, "system", message)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("No se pudo cancelar desde chat web el pedido %s", order_id)
        return jsonify({"ok": False, "error": "No se pudo completar la cancelación. Revisa el estado del pedido e inténtalo de nuevo."}), 503
    return jsonify({**_payload(conversation), "cancelled": ok}), (200 if ok else 409)


@web_chat_bp.post("/orders/<int:order_id>/reorder")
@limiter.limit("10 per hour") if limiter else (lambda f: f)
def reorder_order(order_id):
    ok, message, redirect_url = reorder_visitor_order(order_id)
    return jsonify({"ok": ok, "message": message, "redirect_url": redirect_url}), (200 if ok else 403)


@web_chat_bp.get("/unread")
@(limiter.limit("120 per minute") if limiter else (lambda f: f))
def unread():
    """Nº mensajes no leídos del staff (sender in agent|system) para el
    visitante actual desde su última visita al /chat. La sesión guarda el
    timestamp de la última lectura (no requiere migración BD).
    """
    try:
        n = unread_count_for_visitor()
    except Exception:
        n = 0
    return jsonify({"ok": True, "count": int(n)})

@web_chat_bp.post("/mark-read")
@(limiter.limit("60 per minute") if limiter else (lambda f: f))
def mark_read():
    """Marca el chat como leído para esta sesión (llamar al abrir /chat)."""
    mark_conversation_read()
    return jsonify({"ok": True})
