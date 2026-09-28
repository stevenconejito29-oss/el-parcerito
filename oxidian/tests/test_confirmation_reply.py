"""Confirmación explícita del primer pedido; sin transporte externo."""
import unittest
from unittest.mock import patch

from flask import Flask
from extensions import db
from models import Order, User
from routes.api_bot import api_bot_bp


class ConfirmationReplyTest(unittest.TestCase):
    sequence = 0

    def setUp(self):
        ConfirmationReplyTest.sequence += 1
        phone = f'+346001{self.sequence:05d}'
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, BOT_API_KEY='qa-only',
                               SQLALCHEMY_DATABASE_URI='sqlite://',
                               SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.app.register_blueprint(api_bot_bp, url_prefix='/api/bot')
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.customer = User(nombre='QA', email='confirmation@test.invalid', rol='cliente',
                             telefono=phone, telefono_normalizado=phone, activo=True, password_hash='!')
        db.session.add(self.customer); db.session.flush()
        self.order = Order(numero_pedido='QA-CONFIRM', cliente_id=self.customer.id,
                           estado='pendiente', confirmacion_estado='pending', total=10, subtotal=10)
        db.session.add(self.order); db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def reply(self, value):
        return self.client.post('/api/bot/confirmacion/responder',
                                headers={'X-Bot-Key': 'qa-only'},
                                json={'telefono': self.customer.telefono, 'respuesta': value})

    def test_si_confirms_once_without_claiming_payment(self):
        with patch('services.distribuir_pedido') as distribute, patch('services.encolar_notificaciones_proveedores_pedido'):
            self.assertEqual(self.reply(' si ').get_json()['accion'], 'confirmado')
            self.assertEqual(self.reply('SI').get_json()['accion'], 'sin_pendiente')
            distribute.assert_called_once()
        db.session.refresh(self.order)
        self.assertEqual(self.order.confirmacion_estado, 'confirmed')
        self.assertFalse(self.order.pago_confirmado)

    def test_questions_negations_and_conditions_never_mutate_order(self):
        for text in ('¿cómo puedo cancelar?', 'no sé', 'si llega mañana', 'no quiero cancelar', 'si no sé', 'confirmar?'):
            with self.subTest(text=text):
                self.assertEqual(self.reply(text).get_json()['accion'], 'respuesta_invalida')
                db.session.refresh(self.order)
                self.assertEqual(self.order.estado, 'pendiente')
                self.assertEqual(self.order.confirmacion_estado, 'pending')

    def test_no_cancels_pending_order(self):
        with patch('routes.api_bot.enviar_whatsapp_estado'):
            self.assertEqual(self.reply('NO').get_json()['accion'], 'cancelado')
        db.session.refresh(self.order)
        self.assertEqual(self.order.estado, 'cancelado')

    def test_malformed_payload_and_unknown_phone_are_rejected(self):
        for value in ([], True, 1, None):
            self.assertEqual(self.reply(value).status_code, 400)
        response = self.client.post('/api/bot/confirmacion/responder', headers={'X-Bot-Key':'qa-only'},
                                    json={'telefono':'+34600000001','respuesta':'si'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.order.confirmacion_estado, 'pending')
