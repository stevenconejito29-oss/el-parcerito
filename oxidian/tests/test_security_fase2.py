"""Tests de los fixes de Seguridad Fase 2.

Cubre:
    1. `/branding` — rechaza requests externos aunque tengan BOT_API_KEY.
    2. `/branding` — expone `phone_hash` (HMAC) además del teléfono.
    3. `_validar_dump_postgres` — solo acepta backups legítimos.
"""
import os
import tempfile
import unittest


class ValidarDumpTest(unittest.TestCase):

    def setUp(self):
        from routes.superadmin import _validar_dump_postgres
        self._validar = _validar_dump_postgres
        self._tmp = tempfile.NamedTemporaryFile(delete=False)
        self._tmp.close()

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def _write(self, data: bytes):
        with open(self._tmp.name, "wb") as fh:
            fh.write(data)

    def test_dump_custom_valido_pasa(self):
        # Un `pg_dump -Fc` real empieza con `PGDMP` seguido de metadata.
        self._write(b"PGDMP" + b"\x00" * 500)
        ok, msg = self._validar(self._tmp.name, ".dump")
        self.assertTrue(ok, msg)

    def test_dump_custom_sin_magic_bytes_rechazado(self):
        self._write(b"\x7fELF\x02\x01" + b"\x00" * 500)  # ELF binary
        ok, msg = self._validar(self._tmp.name, ".dump")
        self.assertFalse(ok)
        self.assertIn("PGDMP", msg)

    def test_dump_vacio_rechazado(self):
        self._write(b"")
        ok, msg = self._validar(self._tmp.name, ".dump")
        self.assertFalse(ok)
        self.assertIn("vacío", msg.lower())

    def test_sql_con_comentario_inicial_pasa(self):
        self._write(b"-- PostgreSQL database dump\nSET client_encoding = 'UTF8';\n")
        ok, msg = self._validar(self._tmp.name, ".sql")
        self.assertTrue(ok, msg)

    def test_sql_con_begin_pasa(self):
        self._write(b"BEGIN;\nCREATE TABLE test (id int);\nCOMMIT;\n")
        ok, msg = self._validar(self._tmp.name, ".sql")
        self.assertTrue(ok, msg)

    def test_sql_texto_arbitrario_rechazado(self):
        self._write(b"This is just some random text without SQL markers.\n")
        ok, msg = self._validar(self._tmp.name, ".sql")
        self.assertFalse(ok)
        self.assertIn("marcadores", msg.lower())

    def test_sql_binario_rechazado(self):
        # Bytes no UTF-8 → rechazo antes de mirar contenido.
        self._write(b"\xff\xfe\x00\x01\x02\x03")
        ok, msg = self._validar(self._tmp.name, ".sql")
        self.assertFalse(ok)
        self.assertIn("utf-8", msg.lower())

    def test_extension_no_soportada_rechazada(self):
        self._write(b"PGDMP" + b"\x00" * 500)
        ok, msg = self._validar(self._tmp.name, ".exe")
        self.assertFalse(ok)


class HmacPhoneTest(unittest.TestCase):

    def _with_key(self, key_value):
        from flask import Flask
        from extensions import db
        app = Flask(__name__)
        app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite://",
                          BOT_API_KEY=key_value)
        db.init_app(app)
        return app

    def test_hmac_phone_determinista_con_misma_key(self):
        from routes.api_bot import _hmac_phone
        app = self._with_key("secret-key-XYZ")
        with app.app_context():
            h1 = _hmac_phone("+34600123456")
            h2 = _hmac_phone("+34600123456")
            self.assertEqual(h1, h2)
            self.assertEqual(len(h1), 32)  # sha256 truncado a 32 hex

    def test_hmac_phone_distinto_con_key_distinta(self):
        from routes.api_bot import _hmac_phone
        app1 = self._with_key("key-A")
        app2 = self._with_key("key-B")
        with app1.app_context():
            h1 = _hmac_phone("+34600123456")
        with app2.app_context():
            h2 = _hmac_phone("+34600123456")
        self.assertNotEqual(h1, h2, "cambiar la key debe invalidar hashes previos")

    def test_hmac_phone_no_revelable_a_texto(self):
        """El hash no debe contener los dígitos del teléfono en claro."""
        from routes.api_bot import _hmac_phone
        app = self._with_key("k")
        with app.app_context():
            h = _hmac_phone("+34600123456")
        self.assertNotIn("600123456", h)
        self.assertNotIn("34600", h)

    def test_mask_phone_oculta_medio(self):
        from routes.api_bot import _mask_phone
        self.assertIn("X", _mask_phone("+34600123456"))
        self.assertEqual(_mask_phone("+34600123456")[-3:], "456")
        self.assertEqual(_mask_phone("+34600123456")[:4], "+346")


class InternalRequestTest(unittest.TestCase):

    def test_localhost_es_interno(self):
        from flask import Flask
        from routes.api_bot import _is_internal_request
        app = Flask(__name__)
        with app.test_request_context(environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            self.assertTrue(_is_internal_request())

    def test_ipv6_loopback_es_interno(self):
        from flask import Flask
        from routes.api_bot import _is_internal_request
        app = Flask(__name__)
        with app.test_request_context(environ_base={"REMOTE_ADDR": "::1"}):
            self.assertTrue(_is_internal_request())

    def test_docker_subnet_es_interno(self):
        from flask import Flask
        from routes.api_bot import _is_internal_request
        app = Flask(__name__)
        with app.test_request_context(environ_base={"REMOTE_ADDR": "172.18.0.5"}):
            self.assertTrue(_is_internal_request())

    def test_ip_publica_es_externo(self):
        from flask import Flask
        from routes.api_bot import _is_internal_request
        app = Flask(__name__)
        with app.test_request_context(environ_base={"REMOTE_ADDR": "8.8.8.8"}):
            self.assertFalse(_is_internal_request())

    def test_sin_remote_addr_es_externo(self):
        from flask import Flask
        from routes.api_bot import _is_internal_request
        app = Flask(__name__)
        with app.test_request_context(environ_base={"REMOTE_ADDR": ""}):
            self.assertFalse(_is_internal_request())


if __name__ == "__main__":
    unittest.main()
