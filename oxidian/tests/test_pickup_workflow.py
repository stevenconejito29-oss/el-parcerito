import unittest
from unittest.mock import patch
from flask import Flask
from extensions import db, login_manager
from models import User, Order, OrderEvent, Caja, PointsLog, NotificationOutbox
from services import completar_recogida, distribuir_repartidor
from routes.preparador import preparador_bp
from order_presentation import order_presentation


class PickupWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='pickup-tests', SQLALCHEMY_DATABASE_URI='sqlite://', WTF_CSRF_ENABLED=False, SESSION_PROTECTION=None)
        db.init_app(self.app); login_manager.init_app(self.app)
        login_manager.user_loader(lambda uid: db.session.get(User,int(uid)))
        self.app.register_blueprint(preparador_bp,url_prefix='/preparador')
        self.ctx=self.app.app_context(); self.ctx.push(); db.create_all()
        self.users={}
        for role in ('cliente','cocina','repartidor','admin'):
            user=User(nombre=role,email=role+'@pickup.invalid',rol=role,activo=True)
            user.set_password('test-only'); db.session.add(user); db.session.flush(); self.users[role]=user
        self.order=Order(numero_pedido='PICK-1',cliente_id=self.users['cliente'].id,preparador_id=self.users['cocina'].id,
                         estado='armando',confirmacion_estado='confirmed',tipo_entrega_cliente='recogida',
                         subtotal=12,total=12,metodo_pago='efectivo')
        db.session.add(self.order); db.session.commit()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def test_ready_pickup_stays_in_business_without_code_or_rider(self):
        self.order.avanzar_estado(); db.session.flush()
        self.assertEqual(self.order.estado,'listo')
        self.assertIsNone(distribuir_repartidor(self.order))
        self.assertIsNone(self.order.repartidor_id)
        self.assertFalse(self.order.codigo_confirmacion)
        view=order_presentation(self.order)
        self.assertTrue(view['pickup_ready']); self.assertEqual(view['stage'],3)
        self.assertNotIn('repartidor',view['description'])

    def test_pickup_cannot_skip_counter_via_state_machine(self):
        self.order.estado = 'listo'
        db.session.commit()
        with self.assertRaises(ValueError):
            self.order.avanzar_estado()
        self.assertEqual(self.order.estado, 'listo')
        from services import avanzar_estado_pedido
        with self.assertRaises(ValueError):
            avanzar_estado_pedido(self.order, actor_id=self.users['cocina'].id, canal='test')
        self.assertEqual(self.order.estado, 'listo')

    def test_collection_requires_ready_order_and_explicit_receipt(self):
        for state,confirmed in [('armando',True),('cancelado',True),('listo',False)]:
            self.order.estado=state; db.session.commit()
            with self.assertRaises(ValueError):
                completar_recogida(self.order,self.users['cocina'].id,cobro_recibido=confirmed)
            self.assertFalse(self.order.pago_confirmado)
            self.assertEqual(Caja.query.count(),0)

    def test_collection_posts_cash_and_points_exactly_once_without_delivery(self):
        self.order.estado='listo'; db.session.commit()
        self.assertTrue(completar_recogida(self.order,self.users['cocina'].id,cobro_recibido=True))
        db.session.commit()
        self.assertFalse(completar_recogida(self.order,self.users['cocina'].id,cobro_recibido=True))
        db.session.commit()
        self.assertEqual(self.order.estado,'entregado'); self.assertTrue(self.order.pago_confirmado)
        self.assertIsNone(self.order.en_ruta_en); self.assertFalse(self.order.codigo_confirmacion)
        self.assertEqual(Caja.query.filter_by(pedido_id=self.order.id,tipo='ingreso').count(),1)
        self.assertEqual(PointsLog.query.filter_by(pedido_id=self.order.id,tipo='ganado').count(),1)
        self.assertEqual(OrderEvent.query.filter_by(pedido_id=self.order.id,tipo='recogida_entregada').count(),1)
        self.assertEqual(NotificationOutbox.query.count(),0)
        self.assertEqual(order_presentation(self.order)['status_label'],'Recogido')

    def test_rider_cannot_close_collection_and_delivery_cannot_use_counter(self):
        self.order.estado='listo'; db.session.commit()
        with self.assertRaises(ValueError):completar_recogida(self.order,self.users['repartidor'].id,cobro_recibido=True)
        self.order.tipo_entrega_cliente='delivery'; db.session.commit()
        with self.assertRaises(ValueError):completar_recogida(self.order,self.users['admin'].id,cobro_recibido=True)

    def test_bizum_needs_received_reference_and_card_remains_pay_on_collection(self):
        self.order.estado='listo';self.order.metodo_pago='bizum';db.session.commit()
        with self.assertRaises(ValueError):completar_recogida(self.order,self.users['cocina'].id,cobro_recibido=True)
        self.assertTrue(completar_recogida(self.order,self.users['cocina'].id,cobro_recibido=True,referencia='QA123'))
        db.session.rollback()
        self.order.metodo_pago='tarjeta';db.session.commit()
        self.assertFalse(self.order.pago_confirmado)
        self.assertTrue(completar_recogida(self.order,self.users['cocina'].id,cobro_recibido=True))

    @patch('push_service.notify_order_state')
    def test_kitchen_handoff_endpoint_checks_receipt_and_is_idempotent(self,notify):
        self.order.estado='listo';db.session.commit()
        client=self.app.test_client()
        with client.session_transaction() as s:s.update(_user_id=str(self.users['cocina'].id),_fresh=True)
        url=f'/preparador/pedidos/{self.order.id}/recoger'
        client.post(url,data={});self.assertEqual(self.order.estado,'listo')
        client.post(url,data={'cobro_recibido':'1'});self.assertEqual(self.order.estado,'entregado')
        client.post(url,data={'cobro_recibido':'1'});notify.assert_called_once()
        self.assertEqual(Caja.query.count(),1)
