import hashlib
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from flask import Flask
from extensions import db, login_manager
from models import User, SiteConfig, CustomerAccessGrant, PushSubscription, NotificationOutbox, PointsLog, AuditLog
from routes.public import _combo_display_items
from routes.push import push_bp
from loyalty_service import ajustar_saldo_cliente


class FlowRefinementsTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='qa', SQLALCHEMY_DATABASE_URI='sqlite://', WTF_CSRF_ENABLED=False, SESSION_PROTECTION=None)
        db.init_app(self.app); login_manager.init_app(self.app)
        login_manager.user_loader(lambda uid: db.session.get(User, int(uid)))
        self.app.register_blueprint(push_bp, url_prefix='/api/push')
        from routes.marketing import marketing_bp
        self.app.register_blueprint(marketing_bp, url_prefix='/marketing')
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.owner = User(nombre='Owner', email='owner@test.invalid', password_hash='!', rol='super_admin', activo=True)
        self.customer = User(nombre='Cliente', email='client@test.invalid', password_hash='!', rol='cliente', activo=True, puntos=20, telefono='+34600000000')
        db.session.add_all([self.owner, self.customer]); db.session.flush()
        self.device_key = 'qa-browser-device-key-0123456789'
        self.device_hash = hashlib.sha256(self.device_key.encode()).hexdigest()
        self.grant = CustomerAccessGrant(user_id=self.customer.id, approved_by=self.owner.id, activo=True, device_hash=self.device_hash)
        db.session.add(self.grant); SiteConfig.set('ACCESO_CLIENTES_REGISTRADOS','1'); db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s['_push_device_key'] = self.device_key
            s['customer_access'] = {'id':self.customer.id, 'phone':self.customer.telefono, 'at':time.time(), 'version':0}

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def test_cart_keeps_selected_quantity_and_explicit_presentation(self):
        item = SimpleNamespace(id=9, cantidad=1, es_seleccionable=True, grupo=None, grupo_seleccion='Bebidas')
        metadata = {'combo':{'selecciones':[{'opciones':[{'combo_item_id':9, 'cantidad':3, 'presentacion':{'label':'Grande'}}]}]}}
        rows = _combo_display_items([item], metadata)
        self.assertEqual(rows[0]['cantidad'],3)
        self.assertEqual(rows[0]['presentation_cliente']['label'],'Grande')
        metadata['combo']['selecciones'][0]['opciones'][0].update(presentacion={}, presentation_cliente={'label':'Obsoleto'})
        self.assertEqual(_combo_display_items([item], metadata)[0]['presentation_cliente'], {})

    def test_adjustments_are_separate_audited_and_cannot_overdraw(self):
        ajustar_saldo_cliente(self.customer.id, -5, 'Corrección autorizada', actor=self.owner); db.session.commit()
        self.assertEqual(self.customer.puntos,15)
        self.assertEqual(PointsLog.query.one().tipo,'ajuste')
        self.assertEqual(AuditLog.query.count(),1)
        with self.assertRaises(ValueError): ajustar_saldo_cliente(self.customer.id,-16,'No permitido',actor=self.owner)
        self.assertEqual(self.customer.puntos,15)
        with self.assertRaises(ValueError): ajustar_saldo_cliente(self.owner.id,5,'No es cliente',actor=self.owner)
        with self.assertRaises(ValueError): ajustar_saldo_cliente(self.customer.id,5,'',actor=self.owner)
        with self.assertRaises(ValueError): ajustar_saldo_cliente(self.customer.id,5,'Sin permiso',actor=self.customer)

    @patch('push_service.vapid_configuration_error', return_value=None)
    def test_verified_customer_can_subscribe_before_first_order_and_test_own_device(self, _):
        payload = {'endpoint':'https://push.example.test/qa','keys':{'p256dh':'test_key','auth':'test_auth'}}
        response = self.client.post('/api/push/subscribe',json=payload)
        self.assertEqual(response.status_code,200)
        self.assertEqual(PushSubscription.query.one().device_hash,self.device_hash)
        self.assertEqual(self.client.post('/api/push/self-test',json={'endpoint':payload['endpoint']}).status_code,200)
        self.assertEqual(NotificationOutbox.query.count(),1)
        self.assertEqual(self.client.post('/api/push/self-test',json={'endpoint':'https://push.example.test/other'}).status_code,409)
        self.assertEqual(NotificationOutbox.query.count(),1)
        self.grant.activo=False; db.session.commit()
        with self.client.session_transaction() as s: s['push_cliente_id']=self.customer.id
        self.assertEqual(self.client.post('/api/push/subscribe',json=payload).status_code,403)

    def test_adjustment_form_retries_apply_only_once(self):
        import uuid
        with self.client.session_transaction() as s:
            s['_user_id']=str(self.owner.id); s['_fresh']=True
        payload={'cliente_id':str(self.customer.id), 'cantidad':'5', 'descripcion':'Corrección', 'adjustment_key':str(uuid.uuid4())}
        for _ in range(2):
            self.assertEqual(self.client.post('/marketing/puntos/ajustar',data=payload).status_code,302)
        self.assertEqual(db.session.get(User,self.customer.id).puntos,25)
        self.assertEqual(PointsLog.query.count(),1)
