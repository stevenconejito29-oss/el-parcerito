import unittest
from unittest.mock import patch
from flask import Flask
from sqlalchemy import update
from extensions import db, login_manager
from models import User, Order, OrderEvent
from routes.preparador import preparador_bp
from routes.web_chat import web_chat_bp
from web_chat_service import cancel_visitor_order


class CancellationWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.app=Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='test-only', SQLALCHEMY_DATABASE_URI='sqlite://', SESSION_PROTECTION=None, WTF_CSRF_ENABLED=False)
        db.init_app(self.app)
        login_manager.init_app(self.app)
        login_manager.user_loader(lambda uid: db.session.get(User,int(uid)))
        self.app.register_blueprint(preparador_bp, url_prefix='/preparador')
        self.app.register_blueprint(web_chat_bp, url_prefix='/api/web-chat')
        self.app.add_url_rule('/', endpoint='public.index', view_func=lambda:'ok')
        self.app.add_url_rule('/pedido/<int:pedido_id>/confirmado', endpoint='public.pedido_confirmado', view_func=lambda pedido_id:'ok')
        self.ctx=self.app.app_context(); self.ctx.push(); db.create_all()
        self.user=User(nombre='Cocina QA', email='cancel@test.invalid', rol='cocina', activo=True)
        self.user.set_password('test-only-password')
        db.session.add(self.user); db.session.flush()
        self.order=Order(numero_pedido='CANCEL-QA', cliente_id=self.user.id, estado='pendiente', subtotal=10,total=10,metodo_pago='efectivo',preparador_id=self.user.id)
        db.session.add(self.order); db.session.commit()
        self.client=self.app.test_client()
        with self.client.session_transaction() as session:
            session['_user_id']=str(self.user.id); session['_fresh']=True

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    @patch('push_service.notify_order_state')
    def test_kitchen_cancels_delivery_and_pickup_with_reason_and_one_event(self, notify):
        for kind in ('delivery', 'recogida'):
            self.order.tipo_entrega_cliente=kind
            self.order.estado='armando'
            db.session.commit()
            count=OrderEvent.query.count()
            response=self.client.post(f'/preparador/pedidos/{self.order.id}/cancelar',data={'motivo':'Sin existencias', 'confirmar':'1'})
            self.assertEqual(response.status_code,302)
            self.assertEqual(self.order.estado,'cancelado')
            self.assertEqual(OrderEvent.query.count(),count+1)
            self.client.post(f'/preparador/pedidos/{self.order.id}/cancelar',data={'motivo':'Sin existencias','confirmar':'1'})
            self.assertEqual(OrderEvent.query.count(),count+1)
        self.assertEqual(notify.call_count,2)

    def test_kitchen_rejects_missing_confirmation_paid_or_finalized(self):
        path=f'/preparador/pedidos/{self.order.id}/cancelar'
        self.client.post(path,data={'motivo':'Sin existencias'})
        self.assertEqual(self.order.estado,'pendiente')
        self.order.pago_confirmado=True; db.session.commit()
        self.client.post(path,data={'motivo':'Sin existencias','confirmar':'1'})
        self.assertEqual(self.order.estado,'pendiente')
        self.order.pago_confirmado=False; self.order.estado='listo'; db.session.commit()
        self.client.post(path,data={'motivo':'Sin existencias','confirmar':'1'})
        self.assertEqual(self.order.estado,'listo')

    def test_kitchen_cannot_cancel_another_workers_order(self):
        other=User(nombre='Otro',email='other@test.invalid',rol='cocina',activo=True)
        other.set_password('test-only-password'); db.session.add(other); db.session.flush()
        self.order.preparador_id=other.id; db.session.commit()
        response=self.client.post(f'/preparador/pedidos/{self.order.id}/cancelar',data={'motivo':'Sin existencias','confirmar':'1'})
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.order.estado,'pendiente')

    def test_client_cannot_cancel_another_browser_order(self):
        with self.app.test_request_context('/'), patch('web_chat_service.visitor_orders',return_value=[]):
            self.assertFalse(cancel_visitor_order(self.order.id)[0])
        self.assertEqual(self.order.estado,'pendiente')

    @patch('push_service.notify_order_state')
    def test_preparation_starts_without_advance_payment_for_all_methods(self, notify):
        from models import Caja
        for kind in ('delivery', 'recogida'):
            for method in ('efectivo', 'bizum', 'tarjeta'):
                self.order.estado='pendiente'; self.order.confirmacion_estado='confirmed'
                self.order.tipo_entrega_cliente=kind; self.order.metodo_pago=method
                self.order.pago_confirmado=False
                db.session.commit()
                response=self.client.post(f'/preparador/pedidos/{self.order.id}/empezar')
                self.assertEqual(response.status_code,302)
                self.assertEqual(self.order.estado,'armando', (kind,method))
                self.assertFalse(self.order.pago_confirmado)
                self.assertEqual(Caja.query.count(),0)

    def test_client_cancels_own_pending_delivery_and_pickup_from_http_chat(self):
        for kind in ('delivery', 'recogida'):
            self.order.estado='pendiente'; self.order.tipo_entrega_cliente=kind
            db.session.commit()
            with self.client.session_transaction() as session:
                session['guest_order_tokens']={str(self.order.id): {'token':'owned-test-token'}}
            response=self.client.post(f'/api/web-chat/orders/{self.order.id}/cancel',json={'confirm':True})
            self.assertEqual(response.status_code,200, response.get_json())
            self.assertTrue(response.get_json()['cancelled'])
            self.assertEqual(self.order.estado,'cancelado')

    def test_client_reloads_state_after_kitchen_has_started(self):
        self.assertEqual(self.order.estado,'pendiente')
        db.session.execute(update(Order).where(Order.id==self.order.id).values(estado='armando').execution_options(synchronize_session=False))
        self.assertEqual(self.order.estado,'pendiente')
        with self.app.test_request_context('/'), patch('web_chat_service.visitor_orders',return_value=[{'id':self.order.id}]):
            self.assertFalse(cancel_visitor_order(self.order.id)[0])
        self.assertEqual(self.order.estado,'armando')

    def test_client_paid_card_requires_refund_review(self):
        self.order.metodo_pago='tarjeta'; self.order.pago_confirmado=True; db.session.commit()
        with self.app.test_request_context('/'), patch('web_chat_service.visitor_orders',return_value=[{'id':self.order.id}]):
            ok,message=cancel_visitor_order(self.order.id)
        self.assertFalse(ok); self.assertIn('devolución',message)

    def test_chat_write_failure_rolls_back_order_cancellation(self):
        with patch('web_chat_service.visitor_orders',return_value=[{'id':self.order.id}]), patch('routes.web_chat.conversation_for_visitor',return_value=object()), patch('routes.web_chat.add_message',side_effect=RuntimeError('test storage failure')):
            response=self.client.post(f'/api/web-chat/orders/{self.order.id}/cancel',json={'confirm':True})
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.order.estado,'pendiente')
        self.assertEqual(OrderEvent.query.count(),0)
