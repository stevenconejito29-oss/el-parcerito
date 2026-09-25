import time
import unittest
from unittest.mock import patch
from flask import Blueprint, Flask
from extensions import db
from models import SiteConfig, User, CustomerAccessGrant
import hashlib
from customer_access import enforce_customer_access, private_access_headers
from routes.customer_access import customer_access_bp


class CustomerAccessTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite://', WTF_CSRF_ENABLED=False)
        db.init_app(self.app)
        public = Blueprint('public', __name__)
        public.add_url_rule('/', 'index', lambda: 'catalogue')
        public.add_url_rule('/checkout', 'checkout', lambda: 'created', methods=['POST'])
        public.add_url_rule('/legal', 'informacion_legal', lambda: 'legal')
        public.add_url_rule('/api/producto/1/opciones', 'options', lambda: 'options')
        self.app.register_blueprint(public)
        self.app.register_blueprint(customer_access_bp)
        self.app.add_url_rule('/manifest.webmanifest', 'web_manifest', lambda: '{}')
        self.app.add_url_rule('/health', 'health', lambda: 'ok')
        self.app.before_request(enforce_customer_access)
        self.app.after_request(private_access_headers)
        self.ctx = self.app.app_context(); self.ctx.push()
        db.create_all()
        SiteConfig.set('ACCESO_CLIENTES_REGISTRADOS', '1')
        self.customer = User(nombre='Test', email='access@example.invalid', password_hash='!', rol='cliente', activo=True, telefono='+34600000000', telefono_normalizado='+34600000000')
        db.session.add(self.customer); db.session.commit()
        self.owner = User(nombre='Owner', email='owner@example.invalid', password_hash='!', rol='super_admin', activo=True)
        db.session.add(self.owner); db.session.flush()
        self.grant = CustomerAccessGrant(user_id=self.customer.id, approved_by=self.owner.id, activo=True)
        db.session.add(self.grant); db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def authorise(self):
        key = 'test-browser-key-' * 3
        self.grant.device_hash = hashlib.sha256(key.encode()).hexdigest()
        db.session.commit()
        with self.client.session_transaction() as s:
            s['_push_device_key'] = key
            s['customer_access'] = {'id': self.customer.id, 'phone': self.customer.telefono, 'at': time.time(), 'version': self.customer.mfa_session_version or 0}

    def test_blocks_catalog_checkout_public_api_and_manifest(self):
        self.assertEqual(self.client.get('/').status_code, 303)
        self.assertEqual(self.client.post('/checkout').status_code, 303)
        self.assertEqual(self.client.get('/api/producto/1/opciones').status_code, 403)
        self.assertEqual(self.client.get('/manifest.webmanifest').status_code, 403)
        self.assertEqual(self.client.get('/legal').status_code, 200)
        self.assertEqual(self.client.get('/health').status_code, 200)

    def test_enabled_access_never_caches_catalog(self):
        self.authorise()
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_disabled_mode_preserves_public_access(self):
        SiteConfig.set('ACCESO_CLIENTES_REGISTRADOS', '0'); db.session.commit()
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_deactivated_or_changed_phone_revokes_session(self):
        self.authorise(); self.customer.activo = False; db.session.commit()
        self.assertEqual(self.client.get('/').status_code, 303)
        self.customer.activo = True; db.session.commit(); self.authorise()
        self.customer.telefono = '+34600000001'; db.session.commit()
        self.assertEqual(self.client.get('/').status_code, 303)

    def test_otp_consumed_and_replay_rejected(self):
        code = self.customer.generar_cod_puntos(); db.session.commit()
        with self.client.session_transaction() as s:
            s['customer_access_pending'] = {'id': self.customer.id, 'phone': self.customer.telefono, 'at': time.time()}
        response = self.client.post('/acceso', data={'action':'verify', 'codigo':code})
        self.assertEqual(response.location, '/')
        self.assertIsNone(self.customer.cod_puntos)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.client.post('/acceso/salir')
        self.assertEqual(self.client.post('/acceso', data={'action':'verify', 'codigo':code}).location, '/acceso')
        self.assertEqual(self.client.get('/').status_code, 303)

    @patch('routes.customer_access.solicitar_codigo', return_value={'ok':True})
    def test_unknown_and_inactive_phone_do_not_send_or_create(self, send):
        self.customer.activo = False; db.session.commit()
        for phone in ('+34600000000', '+34600000099'):
            response = self.client.post('/acceso', data={'action':'request', 'telefono':phone})
            self.assertEqual(response.status_code, 303)
            with self.client.session_transaction() as s:
                self.assertTrue(s['customer_access_code_step'])
                self.assertNotIn('customer_access_pending', s)
        send.assert_not_called()
        self.assertEqual(User.query.count(), 2)

    def test_expired_session_rejected(self):
        """La sesión de tienda expira a los 30 días (at antiguo → 303)."""
        self.authorise()
        with self.client.session_transaction() as s:
            value = s['customer_access']; value['at'] = 0; s['customer_access'] = value
        self.assertEqual(self.client.get('/').status_code, 303)

    def test_any_role_with_grant_can_shop(self):
        """Regla: cualquier usuario con CustomerAccessGrant activo puede
        comprar, sin importar su rol. Antes bloqueaba a admin/empleados;
        ahora se les permite tener sesión de tienda paralela a su rol."""
        self.authorise()
        # Cambiar rol a admin no debe revocar la sesión de shopping
        self.customer.rol = 'admin'
        db.session.commit()
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_version_change_revokes_old_access_even_when_active(self):
        self.authorise()
        self.customer.mfa_session_version = 1
        db.session.commit()
        self.assertEqual(self.client.get('/').status_code, 303)

    @patch('routes.customer_access.solicitar_codigo', return_value={'ok':False})
    def test_throttled_resend_keeps_existing_challenge(self, send):
        previous = {'id':self.customer.id, 'phone':self.customer.telefono, 'at':time.time()}
        with self.client.session_transaction() as s:
            s['customer_access_pending'] = previous
        self.client.post('/acceso', data={'action':'request', 'telefono':self.customer.telefono})
        with self.client.session_transaction() as s:
            self.assertEqual(s['customer_access_pending'], previous)

    def test_private_setting_is_superadmin_only(self):
        from types import SimpleNamespace
        from store_config import user_puede_modificar_clave
        from routes.superadmin import _validar_config_value, _config_section_submission
        from werkzeug.datastructures import MultiDict
        key = 'ACCESO_CLIENTES_REGISTRADOS'
        self.assertFalse(user_puede_modificar_clave(SimpleNamespace(rol='admin'), key))
        self.assertTrue(user_puede_modificar_clave(SimpleNamespace(rol='super_admin'), key))
        self.assertFalse(_validar_config_value(key, 'invalid')[0])
        _, changes, errors = _config_section_submission(MultiDict({'section':'operacion-horario', 'config_key':key, key:'1'}))
        self.assertEqual(errors, [])
        self.assertIn((key, '1'), changes)

    @patch('routes.customer_access.solicitar_codigo')
    def test_registered_without_superadmin_grant_cannot_request_otp(self, send):
        db.session.delete(self.grant); db.session.commit()
        self.client.post('/acceso', data={'action':'request', 'telefono':self.customer.telefono})
        send.assert_not_called()

    @patch('routes.customer_access.solicitar_codigo')
    def test_other_device_cannot_claim_linked_phone_even_with_otp(self, send):
        self.authorise()
        other = self.app.test_client()
        other.post('/acceso', data={'action':'request', 'telefono':self.customer.telefono})
        send.assert_not_called()
        code = self.customer.generar_cod_puntos(); db.session.commit()
        with other.session_transaction() as s:
            s['customer_access_pending'] = {'id':self.customer.id, 'phone':self.customer.telefono, 'at':time.time()}
        self.assertEqual(other.post('/acceso', data={'action':'verify','codigo':code}).location, '/acceso')
        self.assertEqual(other.get('/').status_code, 303)
        self.assertEqual(self.client.get('/').status_code, 200)

    @patch('routes.customer_access.solicitar_codigo')
    def test_app_signal_never_bypasses_phone_authorisation(self, send):
        SiteConfig.set('ACCESO_REQUIERE_PWA', '1'); db.session.commit()
        self.client.post('/acceso', data={'action':'request', 'telefono':self.customer.telefono})
        send.assert_not_called()
        self.assertEqual(self.client.post('/acceso/app', json={'standalone':False}).status_code, 400)
        self.assertEqual(self.client.post('/acceso/app', json={'standalone':True}).status_code, 200)
        self.assertEqual(self.client.get('/').status_code, 303)
        self.assertEqual(self.client.get('/manifest.webmanifest').status_code, 403)
        db.session.delete(self.grant); db.session.commit()
        self.client.post('/acceso', data={'action':'request', 'telefono':self.customer.telefono})
        send.assert_not_called()

    def test_app_required_also_checks_existing_verified_session(self):
        self.authorise()
        SiteConfig.set('ACCESO_REQUIERE_PWA', '1'); db.session.commit()
        self.assertEqual(self.client.get('/').status_code, 303)
        self.client.post('/acceso/app', json={'standalone':True})
        self.assertEqual(self.client.get('/').status_code, 200)
        self.client.post('/acceso/salir')
        self.assertEqual(self.client.get('/').status_code, 303)

    @patch('store_config.get_store_profile', return_value={'nombre':'QA shop', 'app_icon_url':None})
    def test_install_manifest_does_not_disclose_catalogue(self, profile):
        self.app.config['ASSET_VERSION'] = 'test-assets'
        response=self.client.get('/acceso/manifest.webmanifest')
        self.assertEqual(response.status_code, 200)
        data=response.get_json()
        self.assertEqual(data['id'], '/')
        self.assertEqual(data['start_url'], '/acceso?source=pwa')
        self.assertNotIn('screenshots', data)
        self.assertNotIn('shortcuts', data)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(self.client.get('/manifest.webmanifest').status_code, 403)

    def test_app_requirement_setting_is_superadmin_only(self):
        from types import SimpleNamespace
        from store_config import user_puede_modificar_clave
        from routes.superadmin import _config_section_submission
        from werkzeug.datastructures import MultiDict
        key='ACCESO_REQUIERE_PWA'
        self.assertFalse(user_puede_modificar_clave(SimpleNamespace(rol='admin'), key))
        self.assertTrue(user_puede_modificar_clave(SimpleNamespace(rol='super_admin'), key))
        _, changes, errors = _config_section_submission(MultiDict({'section':'acceso-clientes','config_key':key,key:'1'}))
        self.assertEqual(errors, [])
        self.assertIn((key,'1'), changes)
