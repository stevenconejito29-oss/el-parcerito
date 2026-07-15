"""Tests del atajo "cliente autenticado no necesita OTP para puntos".

Antes: cada vez que el cliente entraba al carrito debía verificar su
número por WhatsApp para usar puntos. Fricción absurda si ya estaba
logueado — su identidad ya está probada.

Ahora: `_auto_verify_puntos_for_authenticated_client` puebla `cart_puntos`
en sesión con `verificado=True` si `current_user.rol == 'cliente'`.
"""
import unittest
from unittest.mock import patch

from flask import Flask

from extensions import db
from models import User
from routes.public import _auto_verify_puntos_for_authenticated_client


class AutoVerifyPuntosTest(unittest.TestCase):
    _seq = 0

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test",
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk_user(self, rol, puntos=0):
        AutoVerifyPuntosTest._seq += 1
        u = User(
            nombre=f"U{self._seq}",
            email=f"u{self._seq}@t.invalid",
            telefono=f"+34600{self._seq:06d}",
            rol=rol,
            activo=True,
            puntos=puntos,
        )
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        return u

    def test_cliente_autenticado_recibe_verificado(self):
        cliente = self._mk_user("cliente", puntos=350)
        with patch("routes.public.current_user", cliente):
            with patch.object(type(cliente), "is_authenticated", True, create=True):
                res = _auto_verify_puntos_for_authenticated_client("propio")
        self.assertIsNotNone(res)
        self.assertTrue(res["verificado"])
        self.assertTrue(res["auto_verified"])
        self.assertEqual(res["cliente_id"], cliente.id)
        self.assertEqual(res["puntos_totales"], 350)
        self.assertEqual(res["origen"], "propio")

    def test_usuario_no_autenticado_devuelve_none(self):
        from flask_login import AnonymousUserMixin
        anon = AnonymousUserMixin()
        with patch("routes.public.current_user", anon):
            res = _auto_verify_puntos_for_authenticated_client("propio")
        self.assertIsNone(res)

    def test_admin_no_recibe_auto_verify(self):
        # Un admin logueado que hace pedido con su número: NO auto-verify —
        # el OTP separa contextos (evita canjes accidentales con puntos
        # del rol operativo).
        admin = self._mk_user("admin", puntos=99)
        with patch("routes.public.current_user", admin):
            with patch.object(type(admin), "is_authenticated", True, create=True):
                res = _auto_verify_puntos_for_authenticated_client("propio")
        self.assertIsNone(res)

    def test_preparacion_no_recibe_auto_verify(self):
        emp = self._mk_user("preparacion", puntos=10)
        with patch("routes.public.current_user", emp):
            with patch.object(type(emp), "is_authenticated", True, create=True):
                res = _auto_verify_puntos_for_authenticated_client("propio")
        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
