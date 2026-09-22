import unittest
from flask import Flask, session
from device_identity import browser_device_hash, persist_browser_device


class PersistentDeviceIdentityTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='synthetic-test-key')
        self.app.after_request(persist_browser_device)
        self.app.add_url_rule('/device', view_func=lambda: browser_device_hash(create=True))
        self.app.add_url_rule('/known', view_func=lambda: browser_device_hash() or '', endpoint='known')

    def test_expired_session_keeps_same_device_but_not_verified_access(self):
        client = self.app.test_client()
        first = client.get('/device')
        self.assertIn('HttpOnly', first.headers.getlist('Set-Cookie')[0])
        client.delete_cookie('session')
        second = client.get('/device')
        self.assertEqual(first.data, second.data)
        with client.session_transaction() as state:
            self.assertNotIn('customer_access', state)
        self.assertNotEqual(first.data, self.app.test_client().get('/device').data)
        self.assertNotIn('Set-Cookie', second.headers)

    def test_existing_session_migrates_without_rebinding(self):
        client = self.app.test_client()
        with client.session_transaction() as state:
            state['_push_device_key'] = 'legacy-test-device-' * 3
        old = client.get('/known').data
        self.assertEqual(client.get('/device').data, old)
        client.delete_cookie('session')
        self.assertEqual(client.get('/known').data, old)

    def test_tampered_cookie_does_not_restore_identity(self):
        client = self.app.test_client()
        first = client.get('/device').data
        client.delete_cookie('session')
        client.set_cookie('oxidian_device', 'forged-device-token')
        self.assertEqual(client.get('/known').data, b'')
        self.assertNotEqual(client.get('/device').data, first)
