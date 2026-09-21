"""Aislamiento de dispositivos sin transporte real ni datos de producción."""
import hashlib
import time
import unittest
from unittest.mock import patch
from flask import Flask
from flask_login import LoginManager
from extensions import db
from models import Order, PushSubscription, User
from routes.push import push_bp
from device_identity import browser_device_hash


class DeviceNotificationTest(unittest.TestCase):
    def setUp(self):
        self.app=Flask(__name__)
        self.app.config.update(TESTING=True,SECRET_KEY='test-only',SQLALCHEMY_DATABASE_URI='sqlite://',SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        login=LoginManager(self.app)
        login.user_loader(lambda uid:db.session.get(User,int(uid)))
        self.app.register_blueprint(push_bp,url_prefix='/api/push')
        self.ctx=self.app.app_context();self.ctx.push();db.create_all()
        user=User(nombre='Cliente',email='device@test.invalid',rol='cliente',activo=True)
        user.set_password('test-only');db.session.add(user);db.session.flush();self.uid=user.id
        self.order=Order(numero_pedido='DEVICE-1',cliente_id=user.id,total=10,subtotal=10,estado='listo',tipo_entrega_cliente='recogida',confirmacion_estado='confirmed')
        db.session.add(self.order);db.session.commit()
        self.a=self.browser('a');self.b=self.browser('b')

    def tearDown(self):
        db.session.remove();db.drop_all();self.ctx.pop()

    def browser(self,key):
        client=self.app.test_client()
        with client.session_transaction() as s:
            s['push_cliente_id']=self.uid;s['_push_device_key']=key*40
        return client

    def subscribe(self,client,suffix):
        return client.post('/api/push/subscribe',json={'endpoint':f'https://push.example.invalid/{suffix}','keys':{'p256dh':'valid-key','auth':'valid-auth'}})

    def test_only_authorized_session_binds_legacy_order_and_retries_do_not_duplicate(self):
        self.assertEqual(self.subscribe(self.b,'b').status_code,200)
        db.session.refresh(self.order);self.assertIsNone(self.order.customer_device_hash)
        with self.a.session_transaction() as s:
            s['guest_order_tokens']={str(self.order.id):{'token':'private-test-token','exp':time.time()+3600}}
        for _ in range(2):self.assertEqual(self.subscribe(self.a,'a').status_code,200)
        self.assertEqual(PushSubscription.query.count(),2)
        db.session.refresh(self.order)
        self.assertEqual(self.order.customer_device_hash,hashlib.sha256(('a'*40).encode()).hexdigest())
        from push_service import notify_order_state
        with patch('push_service._dispatch') as dispatch:
            notify_order_state(self.order)
        self.assertEqual([s.endpoint for s in dispatch.call_args.args[0]],['https://push.example.invalid/a'])

    def test_no_device_no_customer_broadcast(self):
        from push_service import notify_order_state
        self.subscribe(self.a,'a');self.subscribe(self.b,'b')
        with patch('push_service._dispatch') as dispatch:notify_order_state(self.order)
        dispatch.assert_not_called()

    def test_queued_push_discarded_if_device_or_role_changes(self):
        from push_service import send_push_outbox_payload
        self.subscribe(self.a,'a')
        sub=PushSubscription.query.one()
        payload={'subscription_id':sub.id,'expected_user_id':self.uid,'expected_device_hash':sub.device_hash,'expected_role':'cliente','payload':{'title':'Privado'}}
        for change in ('device','role'):
            with self.subTest(change=change):
                sub.device_hash='different' if change=='device' else payload['expected_device_hash']
                sub.usuario.rol='cocina' if change=='role' else 'cliente';db.session.commit()
                with patch('push_service._send_one_result') as send:
                    self.assertEqual(send_push_outbox_payload(payload),(True,None))
                send.assert_not_called()

    def test_logout_without_endpoint_only_deactivates_this_browser(self):
        from routes.auth import _desactivar_push_del_dispositivo
        self.subscribe(self.a,'a');self.subscribe(self.b,'b')
        with self.app.test_request_context('/'):
            from flask import session
            session['_push_device_key']='a'*40
            _desactivar_push_del_dispositivo(self.uid,'');db.session.commit()
        db.session.expire_all()
        self.assertFalse(PushSubscription.query.filter_by(endpoint='https://push.example.invalid/a').one().activo)
        self.assertTrue(PushSubscription.query.filter_by(endpoint='https://push.example.invalid/b').one().activo)

    def test_guest_session_cannot_subscribe_staff_or_inactive_customer(self):
        user=db.session.get(User,self.uid)
        user.rol='super_admin';db.session.commit()
        self.assertEqual(self.subscribe(self.a,'a').status_code,403)
        user.rol='cliente';user.activo=False;db.session.commit()
        self.assertEqual(self.subscribe(self.a,'a').status_code,403)
        self.assertEqual(PushSubscription.query.count(),0)

    def test_malformed_json_rejected(self):
        for payload in ([],{'keys':[]},{'keys':{'p256dh':4,'auth':True},'endpoint':'x'}):
            self.assertEqual(self.a.post('/api/push/subscribe',json=payload).status_code,400)
