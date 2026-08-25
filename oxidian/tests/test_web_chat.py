import unittest

from flask import Flask

from extensions import db
from models import KnowledgeEntry, Order, User, WebChatConversation, WebChatMessage
from routes.web_chat import web_chat_bp


class WebChatTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="web-chat-test",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            PUBLIC_BASE_URL="https://shop.invalid/",
        )
        db.init_app(self.app)
        self.app.register_blueprint(web_chat_bp, url_prefix="/api/web-chat")
        self.app.add_url_rule("/", endpoint="public.index", view_func=lambda: "ok")
        self.app.add_url_rule("/pedido/<int:pedido_id>/confirmado", endpoint="public.pedido_confirmado", view_func=lambda pedido_id: "ok")
        self.app.add_url_rule("/admin/chats", endpoint="admin.chats_index", view_func=lambda: "ok")
        self.ctx = self.app.app_context(); self.ctx.push()
        db.create_all()
        db.session.add(KnowledgeEntry(
            categoria="horario", pregunta="¿Cuál es el horario?",
            respuesta="Abrimos de martes a domingo.", keywords="horario,abren",
            audiencia="cliente", activo=True,
        ))
        db.session.commit()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def test_known_question_is_answered_and_persisted(self):
        client = self.app.test_client()
        response = client.post("/api/web-chat/messages", json={
            "message": "¿A qué horario abren?", "nonce": "one",
        })
        self.assertEqual(response.status_code, 200)
        bodies = [m["body"] for m in response.get_json()["messages"]]
        self.assertTrue(any("horario configurado" in body.lower() for body in bodies))
        self.assertEqual(WebChatConversation.query.count(), 1)
        self.assertEqual(WebChatMessage.query.filter_by(sender="client").count(), 1)

    def test_nonce_makes_client_send_idempotent(self):
        client = self.app.test_client()
        body = {"message": "horario", "nonce": "same-request"}
        client.post("/api/web-chat/messages", json=body)
        client.post("/api/web-chat/messages", json=body)
        self.assertEqual(WebChatMessage.query.filter_by(sender="client").count(), 1)

    def test_visitors_are_isolated(self):
        first, second = self.app.test_client(), self.app.test_client()
        first.post("/api/web-chat/messages", json={"message": "horario", "nonce": "a"})
        second.post("/api/web-chat/messages", json={"message": "horario", "nonce": "b"})
        self.assertEqual(WebChatConversation.query.count(), 2)

    def test_client_can_return_to_bot_after_handoff(self):
        client = self.app.test_client()
        requested = client.post("/api/web-chat/request-agent", json={}).get_json()
        self.assertEqual(requested["conversation"]["status"], "waiting_agent")
        resumed = client.post("/api/web-chat/resume-bot", json={}).get_json()
        self.assertEqual(resumed["conversation"]["status"], "bot")

    def test_repeated_handoff_is_idempotent(self):
        client = self.app.test_client()
        first = client.post("/api/web-chat/request-agent", json={}).get_json()
        second = client.post("/api/web-chat/request-agent", json={}).get_json()
        self.assertTrue(first["requested"])
        self.assertFalse(second["requested"])
        self.assertEqual(
            WebChatMessage.query.filter_by(sender="system").count(), 1,
        )

    def test_answer_preserves_readable_lines(self):
        entry = KnowledgeEntry.query.first()
        entry.respuesta = "Primera línea.\n• Segunda línea."
        db.session.commit()
        from web_chat_service import add_message
        conversation = WebChatConversation(public_id="line-test", visitor_token_hash="line-test")
        db.session.add(conversation)
        db.session.flush()
        row = add_message(conversation, "bot", "Primera línea.\n• Segunda línea.")
        db.session.commit()
        self.assertEqual(row.body, "Primera línea.\n• Segunda línea.")

    def test_order_intent_is_guided_without_exposing_data(self):
        response = self.app.test_client().post("/api/web-chat/messages", json={
            "message": "quiero cancelar mi pedido", "nonce": "order-intent",
        })
        bodies = [m["body"] for m in response.get_json()["messages"]]
        self.assertTrue(any("proteger tus datos" in body for body in bodies))

    def test_delivery_question_does_not_match_generic_menu(self):
        response = self.app.test_client().post("/api/web-chat/messages", json={
            "message": "¿Cómo funciona el delivery?", "nonce": "delivery",
        })
        bodies = [m["body"] for m in response.get_json()["messages"]]
        self.assertTrue(any("cobertura" in body.lower() for body in bodies))
        self.assertFalse(any("escribe *MENU*" in body for body in bodies))

    def test_loyalty_question_uses_active_feature(self):
        response = self.app.test_client().post("/api/web-chat/messages", json={
            "message": "¿Cómo funcionan los cafecitos?", "nonce": "loyalty",
        })
        bodies = [m["body"] for m in response.get_json()["messages"]]
        self.assertTrue(any("cafecitos" in body.lower() for body in bodies))

    def test_greeting_is_not_confused_with_menu_faq(self):
        response = self.app.test_client().post("/api/web-chat/messages", json={
            "message": "hola buenas", "nonce": "hello",
        })
        bodies = [m["body"] for m in response.get_json()["messages"]]
        self.assertTrue(any("pregúntame" in body.lower() for body in bodies))

    def test_typo_uses_fuzzy_knowledge_retrieval(self):
        response = self.app.test_client().post("/api/web-chat/messages", json={
            "message": "a ke ora abren oy", "nonce": "typo",
        })
        bodies = [m["body"] for m in response.get_json()["messages"]]
        self.assertTrue(any("horario" in body.lower() for body in bodies))

    def test_purchase_tutorial_is_resolved_without_ai(self):
        response = self.app.test_client().post("/api/web-chat/messages", json={
            "message": "explícame paso a paso cómo comprar", "nonce": "tutorial",
        })
        bodies = [m["body"] for m in response.get_json()["messages"]]
        self.assertTrue(any("revisa la canasta" in body.lower() for body in bodies))

    def test_install_and_notifications_are_explained_without_ai(self):
        response = self.app.test_client().post("/api/web-chat/messages", json={
            "message": "¿Cómo instalo la app y activo notificaciones?", "nonce": "notifications",
        })
        bodies = [m["body"] for m in response.get_json()["messages"]]
        self.assertTrue(any("añadir a pantalla de inicio" in body.lower() for body in bodies))

    def test_allergy_question_escalates_safely(self):
        response = self.app.test_client().post("/api/web-chat/messages", json={
            "message": "Tengo alergia, ¿qué ingredientes lleva?", "nonce": "allergens",
        })
        bodies = [m["body"] for m in response.get_json()["messages"]]
        self.assertTrue(any("contaminación cruzada" in body.lower() for body in bodies))

    def test_payment_explains_counter_delivery_without_requesting_data(self):
        response = self.app.test_client().post("/api/web-chat/messages", json={
            "message": "¿Cómo se paga, tengo que poner mi tarjeta?", "nonce": "cash-on-delivery",
        })
        answer = "\n".join(m["body"] for m in response.get_json()["messages"])
        self.assertIn("contra entrega", answer.lower())
        self.assertIn("no pagas por adelantado", answer.lower())
        self.assertIn("ni escribes datos bancarios", answer.lower())

    def test_typo_intent_detects_delivery_tracking(self):
        response = self.app.test_client().post("/api/web-chat/messages", json={
            "message": "dond esta el repartidorr y kuanto falta", "nonce": "tracking-typo",
        })
        answer = "\n".join(m["body"] for m in response.get_json()["messages"])
        self.assertIn("seguimiento", answer.lower())

    def test_pickup_and_coupon_guidance_are_available_without_ai(self):
        client = self.app.test_client()
        pickup = client.post("/api/web-chat/messages", json={
            "message": "¿Puedo pasar a recogerlo en el local?", "nonce": "pickup",
        }).get_json()
        coupon = client.post("/api/web-chat/messages", json={
            "message": "¿Dónde aplico un cupón?", "nonce": "coupon",
        }).get_json()
        self.assertIn("recogida", "\n".join(m["body"] for m in pickup["messages"]).lower())
        self.assertIn("canasta", "\n".join(m["body"] for m in coupon["messages"]).lower())

    def test_delivery_schedule_and_cruce_are_explained_without_ai(self):
        client = self.app.test_client()
        scheduled = client.post("/api/web-chat/messages", json={
            "message": "¿Puedo elegir una hora o franja de entrega?", "nonce": "slot-help",
        }).get_json()
        favor = client.post("/api/web-chat/messages", json={
            "message": "¿Cómo funciona El Cruce para llevar un paquete?", "nonce": "cruce-help",
        }).get_json()
        self.assertIn("franja", "\n".join(m["body"] for m in scheduled["messages"]).lower())
        self.assertIn("punto a", "\n".join(m["body"] for m in favor["messages"]).lower())

    def test_messages_require_json(self):
        response = self.app.test_client().post(
            "/api/web-chat/messages", data="message=hola",
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 415)

    def test_cancel_requires_explicit_confirmation(self):
        response = self.app.test_client().post(
            "/api/web-chat/orders/1/cancel", json={"confirm": False},
        )
        self.assertEqual(response.status_code, 400)

    def test_state_exposes_only_session_order_with_tracking_and_cancel_action(self):
        customer = User(nombre="Cliente pedido", email="order@test.invalid", rol="cliente", activo=True)
        customer.set_password("irrelevant-test-password")
        db.session.add(customer); db.session.flush()
        order = Order(
            numero_pedido="WEB-101", cliente_id=customer.id, estado="pendiente",
            subtotal=12, total=12, metodo_pago="efectivo",
        )
        db.session.add(order); db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as browser:
            browser["guest_order_tokens"] = {str(order.id): {"token": "opaque-order-token"}}
        payload = client.get("/api/web-chat/state").get_json()
        self.assertEqual(len(payload["orders"]), 1)
        self.assertEqual(payload["orders"][0]["status_label"], "Recibido")
        self.assertTrue(payload["orders"][0]["cancelable"])
        self.assertIn("opaque-order-token", payload["orders"][0]["tracking_url"])

    def test_closed_orders_do_not_appear_in_chat(self):
        customer = User(nombre="Cliente cerrado", email="closed@test.invalid", rol="cliente", activo=True)
        customer.set_password("irrelevant-test-password")
        db.session.add(customer); db.session.flush()
        orders = [
            Order(numero_pedido="WEB-CANCEL", cliente_id=customer.id, estado="cancelado", subtotal=8, total=8, metodo_pago="efectivo"),
            Order(numero_pedido="WEB-DONE", cliente_id=customer.id, estado="entregado", subtotal=9, total=9, metodo_pago="efectivo"),
        ]
        db.session.add_all(orders); db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as browser:
            browser["guest_order_tokens"] = {str(row.id): {"token": f"token-{row.id}"} for row in orders}
        payload = client.get("/api/web-chat/state").get_json()
        self.assertEqual(payload["orders"], [])
        self.assertEqual(payload["reorder"]["id"], orders[1].id)

    def test_empty_order_token_never_authorises_tracking_or_reorder(self):
        customer = User(nombre="Cliente sin token", email="empty-token@test.invalid", rol="cliente", activo=True)
        customer.set_password("irrelevant-test-password")
        db.session.add(customer); db.session.flush()
        order = Order(numero_pedido="WEB-EMPTY", cliente_id=customer.id, estado="entregado", subtotal=8, total=8, metodo_pago="efectivo")
        db.session.add(order); db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as browser:
            browser["guest_order_tokens"] = {str(order.id): {"token": ""}}
        payload = client.get("/api/web-chat/state").get_json()
        self.assertEqual(payload["orders"], [])
        self.assertIsNone(payload["reorder"])

    def test_reorder_rejects_order_from_another_browser(self):
        customer = User(nombre="Cliente aislado", email="isolated@test.invalid", rol="cliente", activo=True)
        customer.set_password("irrelevant-test-password")
        db.session.add(customer); db.session.flush()
        order = Order(numero_pedido="WEB-ISO", cliente_id=customer.id, estado="entregado", subtotal=8, total=8, metodo_pago="efectivo")
        db.session.add(order); db.session.commit()
        response = self.app.test_client().post(f"/api/web-chat/orders/{order.id}/reorder", json={})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()["ok"])

    def test_conversation_binds_only_to_checkout_session_customer(self):
        customer = User(nombre="Cliente", email="cliente@test.invalid", rol="cliente", activo=True)
        customer.set_password("irrelevant-test-password")
        db.session.add(customer); db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as browser:
            browser["push_cliente_id"] = customer.id
        client.get("/api/web-chat/state")
        row = WebChatConversation.query.one()
        self.assertEqual(row.customer_id, customer.id)

    def test_anonymous_state_does_not_replace_conversation_customer(self):
        customer = User(nombre="Cliente", email="cliente2@test.invalid", rol="cliente", activo=True)
        customer.set_password("irrelevant-test-password")
        db.session.add(customer); db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as browser:
            browser["push_cliente_id"] = customer.id
        client.get("/api/web-chat/state")
        with client.session_transaction() as browser:
            browser.pop("push_cliente_id")
        client.get("/api/web-chat/state")
        self.assertEqual(WebChatConversation.query.one().customer_id, customer.id)

    def test_shared_device_rotates_conversation_for_a_different_customer(self):
        first = User(nombre="Uno", email="uno@test.invalid", rol="cliente", activo=True)
        second = User(nombre="Dos", email="dos@test.invalid", rol="cliente", activo=True)
        first.set_password("irrelevant-test-password"); second.set_password("irrelevant-test-password")
        db.session.add_all([first, second]); db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as browser: browser["push_cliente_id"] = first.id
        client.post("/api/web-chat/messages", json={"message":"hola", "nonce":"first"})
        with client.session_transaction() as browser: browser["push_cliente_id"] = second.id
        client.get("/api/web-chat/state")
        self.assertEqual(WebChatConversation.query.count(), 2)
        current = WebChatConversation.query.filter_by(customer_id=second.id).one()
        self.assertFalse(current.messages.filter_by(sender="client").count())


if __name__ == "__main__":
    unittest.main()
