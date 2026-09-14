import unittest
from types import SimpleNamespace

from order_presentation import order_presentation


class OrderPresentationTest(unittest.TestCase):
    def order(self, status="pendiente", confirmation="confirmed", delivery="delivery"):
        return SimpleNamespace(estado=status, confirmacion_estado=confirmation, tipo_entrega_cliente=delivery)

    def test_pending_confirmation_does_not_claim_preparation_started(self):
        view = order_presentation(self.order(confirmation="pending"))
        self.assertTrue(view["confirmation_pending"])
        self.assertEqual(view["title"], "Confirma tu pedido")

    def test_ready_pickup_and_delivery_have_distinct_instructions(self):
        pickup = order_presentation(self.order("listo", delivery="recogida"))
        delivery = order_presentation(self.order("listo"))
        self.assertEqual(pickup["title"], "Listo para recoger")
        self.assertNotIn("repart", pickup["description"])
        self.assertEqual(delivery["title"], "Listo para el reparto")

    def test_terminal_states_never_request_confirmation(self):
        for state in ["entregado", "cancelado"]:
            with self.subTest(state=state):
                view = order_presentation(self.order(state, "pending"))
                self.assertFalse(view["confirmation_pending"])
                self.assertNotIn("Confirma", view["title"])

    def test_unknown_state_does_not_invent_progress(self):
        self.assertEqual(order_presentation(self.order("legacy"))["title"], "Estado de tu pedido")

    def test_payment_uses_order_data_and_keeps_unknown_methods_explicit(self):
        for method, label in [("efectivo", "Efectivo"), ("bizum", "Bizum"), ("tarjeta", "Tarjeta"), ("transferencia", "Bizum"), (None, "Método por confirmar")]:
            with self.subTest(method=method):
                order = self.order()
                order.metodo_pago = method
                order.pago_confirmado = True
                view = order_presentation(order)
                self.assertEqual(view["payment_label"], label)
                self.assertEqual(view["payment_status"], "Pago confirmado")
                order.pago_confirmado = False
                self.assertFalse(order_presentation(order)["payment_confirmed"])


class OrderPresentationEndpointTest(unittest.TestCase):
    def test_status_presentation_requires_the_existing_order_token(self):
        from flask import Flask
        from unittest.mock import patch
        from routes.public import public_bp

        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY="tracking-test-only")
        app.register_blueprint(public_bp)
        client = app.test_client()
        order = SimpleNamespace(estado="listo", confirmacion_estado="confirmed", tipo_entrega_cliente="recogida", metodo_pago="tarjeta", pago_confirmado=True)
        with patch("routes.public.get_or_404", return_value=order) as lookup:
            self.assertEqual(client.get('/pedido/1/estado?token=wrong').status_code, 403)
            lookup.assert_not_called()
            with client.session_transaction() as session:
                session['guest_order_tokens'] = {'1': 'tracking-test-token'}
            response = client.get('/pedido/1/estado?token=tracking-test-token')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['presentation'], order_presentation(order))
            self.assertEqual(response.json['status_label'], 'Listo')
