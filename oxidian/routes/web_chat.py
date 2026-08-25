"""API pública del chat web; identidad por token opaco guardado en sesión."""
from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import IntegrityError

from extensions import db, limiter
from models import WebChatMessage
from web_chat_service import (
    MAX_MESSAGE, add_message, bot_reply, conversation_for_visitor,
    cancel_visitor_order, last_reorderable_order, reorder_visitor_order, request_human, resume_bot, serialise_conversation,
    serialise_message, visitor_orders,
)

web_chat_bp = Blueprint("web_chat", __name__)


def _payload(conversation, after=0):
    messages = conversation.messages.filter(WebChatMessage.id > max(0, after)).limit(100).all()
    return {
        "ok": True,
        "conversation": serialise_conversation(conversation),
        "messages": [serialise_message(row) for row in messages],
    }


@web_chat_bp.get("/state")
@limiter.limit("120 per minute") if limiter else (lambda f: f)
def state():
    conversation = conversation_for_visitor()
    try:
        after = int(request.args.get("after", 0) or 0)
    except ValueError:
        after = 0
    return jsonify({
        **_payload(conversation, after), "orders": visitor_orders(),
        "reorder": last_reorderable_order(),
    })


@web_chat_bp.post("/messages")
@limiter.limit("30 per minute") if limiter else (lambda f: f)
def send_message():
    if not request.is_json:
        return jsonify({"ok": False, "error": "Formato no admitido."}), 415
    data = request.get_json(silent=True) or {}
    body = str(data.get("message") or "").strip()
    nonce = str(data.get("nonce") or "").strip()[:64]
    if not body or len(body) > MAX_MESSAGE:
        return jsonify({"ok": False, "error": "Mensaje inválido."}), 400
    conversation = conversation_for_visitor()
    if nonce:
        existing = WebChatMessage.query.filter_by(
            conversation_id=conversation.id, client_nonce=nonce,
        ).first()
        if existing:
            return jsonify(_payload(conversation, 0))
    if conversation.status == "closed":
        resume_bot(conversation)
    try:
        add_message(conversation, "client", body, nonce=nonce)
        assigned_agent_id = conversation.assigned_agent_id if conversation.status == "active_agent" else None
        learning = None
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
            from bot_learning_service import registrar_signal
            # Aprendizaje aislado: nunca decide ni revierte la conversación.
            registrar_signal(body, action_llm=f"web_{learning[0]}", reply_snippet=learning[1])
    except IntegrityError:
        db.session.rollback()
    return jsonify(_payload(conversation_for_visitor(), 0))


@web_chat_bp.post("/request-agent")
@limiter.limit("5 per hour") if limiter else (lambda f: f)
def request_agent():
    conversation = conversation_for_visitor()
    requested = request_human(conversation)
    return jsonify({**_payload(conversation_for_visitor(), 0), "requested": requested})


@web_chat_bp.post("/resume-bot")
@limiter.limit("10 per hour") if limiter else (lambda f: f)
def return_to_bot():
    conversation = conversation_for_visitor()
    resume_bot(conversation)
    return jsonify(_payload(conversation, 0))


@web_chat_bp.post("/orders/<int:order_id>/cancel")
@limiter.limit("5 per hour") if limiter else (lambda f: f)
def cancel_order(order_id):
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return jsonify({"ok": False, "error": "Debes confirmar la cancelación."}), 400
    ok, message = cancel_visitor_order(order_id)
    conversation = conversation_for_visitor()
    add_message(conversation, "system", message)
    db.session.commit()
    return jsonify({**_payload(conversation, 0), "orders": visitor_orders(), "cancelled": ok}), (200 if ok else 409)


@web_chat_bp.post("/orders/<int:order_id>/reorder")
@limiter.limit("10 per hour") if limiter else (lambda f: f)
def reorder_order(order_id):
    ok, message, redirect_url = reorder_visitor_order(order_id)
    return jsonify({"ok": ok, "message": message, "redirect_url": redirect_url}), (200 if ok else 403)
