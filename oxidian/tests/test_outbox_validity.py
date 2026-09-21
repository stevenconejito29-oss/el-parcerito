import unittest
from datetime import timedelta
from unittest.mock import patch
from flask import Flask
from extensions import db
from models import NotificationOutbox, Order, User, utcnow
from services import (_registrar_notificacion, procesar_notificaciones_pendientes,
                      purgar_registros_antiguos)


class OutboxValidityTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite://')
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.user = User(nombre='QA', email='outbox@test.invalid', telefono='+34610000001', rol='cliente', activo=True)
        self.user.set_password('test-only-password')
        db.session.add(self.user)
        db.session.flush()
        self.order = Order(numero_pedido='OUT-1', cliente_id=self.user.id, subtotal=10, total=10,
                           estado='pendiente', confirmacion_estado='pending', tipo_entrega_cliente='delivery')
        db.session.add(self.order)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def enqueue(self, event='order_confirmation', **payload):
        row = _registrar_notificacion('whatsapp', event, self.user.telefono,
            {'telefono':self.user.telefono, 'mensaje':'QA', **payload},
            pedido_id=self.order.id, user_id=self.user.id)
        db.session.commit()
        return row

    @patch('services._send_whatsapp_message', return_value=True)
    def test_valid_confirmation_sends_once(self, send):
        job = self.enqueue()
        result = procesar_notificaciones_pendientes()
        self.assertEqual(result['enviadas'], 1)
        self.assertEqual(job.estado, 'sent')
        procesar_notificaciones_pendientes()
        send.assert_called_once()

    @patch('services._send_whatsapp_message', return_value=True)
    def test_confirmed_or_cancelled_order_never_receives_old_confirmation(self, send):
        for state in ('confirmed', 'cancelled'):
            self.order.estado = 'pendiente'
            self.order.confirmacion_estado = 'pending'
            db.session.commit()
            job = self.enqueue()
            if state == 'confirmed':
                self.order.confirmacion_estado = 'confirmed'
            else:
                self.order.estado = 'cancelado'
            db.session.commit()
            self.assertEqual(procesar_notificaciones_pendientes()['saltadas'], 1)
            self.assertEqual(job.estado, 'failed')
            self.assertTrue(job.ultimo_error.startswith('discarded:'))
        send.assert_not_called()

    @patch('services._send_whatsapp_message', return_value=False)
    def test_retry_revalidates_order_after_first_transport_failure(self, send):
        job = self.enqueue()
        procesar_notificaciones_pendientes()
        self.assertEqual(job.intentos, 1)
        self.order.estado = 'cancelado'
        job.siguiente_intento_en = utcnow() - timedelta(seconds=1)
        db.session.commit()
        self.assertEqual(procesar_notificaciones_pendientes()['saltadas'], 1)
        send.assert_called_once()

    def test_duplicate_pending_requests_reuse_same_notification(self):
        first = self.enqueue()
        second = self.enqueue()
        self.assertEqual(first.id, second.id)
        self.assertEqual(NotificationOutbox.query.count(), 1)

    @patch('services._send_whatsapp_message', return_value=True)
    def test_delivery_code_only_sends_while_current_and_in_route(self, send):
        self.order.estado = 'en_ruta'
        self.order.codigo_confirmacion = '123456'
        db.session.commit()
        self.enqueue('delivery_code', delivery_code='123456')
        self.assertEqual(procesar_notificaciones_pendientes()['enviadas'], 1)
        self.enqueue('delivery_code', delivery_code='123456')
        self.order.estado = 'entregado'
        db.session.commit()
        self.assertEqual(procesar_notificaciones_pendientes()['saltadas'], 1)
        send.assert_called_once()

    @patch('services._send_whatsapp_message', return_value=True)
    def test_old_otp_is_discarded_when_replaced(self, send):
        self.user.cod_puntos = '123456'
        self.user.cod_puntos_expira = utcnow() + timedelta(minutes=10)
        db.session.commit()
        self.enqueue('points_otp')
        self.user.cod_puntos = '654321'
        self.user.cod_puntos_expira = utcnow() + timedelta(minutes=10)
        db.session.commit()
        self.enqueue('points_otp')
        result = procesar_notificaciones_pendientes()
        self.assertEqual(result['saltadas'], 1)
        self.assertEqual(result['enviadas'], 1)
        send.assert_called_once()

    @patch('services._send_whatsapp_message', return_value=True)
    def test_expired_otp_and_changed_recipient_are_discarded(self, send):
        self.user.cod_puntos = '123456'
        self.user.cod_puntos_expira = utcnow() - timedelta(minutes=1)
        db.session.commit()
        self.enqueue('points_otp')
        self.enqueue()
        self.user.telefono = '+34610000002'
        db.session.commit()
        self.assertEqual(procesar_notificaciones_pendientes()['saltadas'], 2)
        send.assert_not_called()

    def test_retention_removes_failed_without_send_date_but_keeps_pending(self):
        old = self.enqueue()
        old.estado = 'failed'
        old.creado_en = utcnow() - timedelta(days=40)
        db.session.commit()
        old_id = old.id
        pending = self.enqueue()
        pending.creado_en = utcnow() - timedelta(days=40)
        db.session.commit()
        pending_id = pending.id
        self.assertEqual(purgar_registros_antiguos()['notification_outbox'], 1)
        self.assertIsNone(db.session.get(NotificationOutbox, old_id))
        self.assertIsNotNone(db.session.get(NotificationOutbox, pending_id))

    @patch('services._send_whatsapp_message', return_value=True)
    def test_slot_arrival_uses_allowed_code_outbox_once(self, send):
        from delivery_slots_service import notificar_en_la_puerta
        self.order.estado = 'en_ruta'
        db.session.commit()
        job = notificar_en_la_puerta(self.order)
        db.session.commit()
        self.assertEqual(job.evento, 'delivery_code')
        self.assertTrue(self.order.codigo_confirmacion)
        self.assertIsNone(notificar_en_la_puerta(self.order))
        self.assertEqual(procesar_notificaciones_pendientes()['enviadas'], 1)
        procesar_notificaciones_pendientes()
        send.assert_called_once()

    @patch('services._send_whatsapp_message', return_value=True)
    def test_slot_arrival_cancelled_before_worker_is_discarded(self, send):
        from delivery_slots_service import notificar_en_la_puerta
        self.order.estado = 'en_ruta'
        db.session.commit()
        notificar_en_la_puerta(self.order)
        db.session.commit()
        self.order.estado = 'cancelado'
        db.session.commit()
        self.assertEqual(procesar_notificaciones_pendientes()['saltadas'], 1)
        send.assert_not_called()

    @patch('push_service.notify_user')
    def test_departure_is_idempotent_and_never_queues_whatsapp(self, notify):
        from delivery_slots_service import notificar_en_camino
        self.order.estado = 'en_ruta'
        self.order.customer_device_hash = 'qa-device'
        db.session.commit()
        self.assertEqual(notificar_en_camino(self.order)[1], 'push_web')
        db.session.commit()
        self.assertEqual(notificar_en_camino(self.order)[1], 'ya_notificado')
        notify.assert_called_once()
        self.assertFalse(notify.call_args.kwargs['commit'])
        self.assertEqual(NotificationOutbox.query.count(), 0)
