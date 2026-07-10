"""Tests de intentos configurables del código de entrega.

Blindaje: el número máximo se toma de `SiteConfig.DELIVERY_CODE_MAX_INTENTOS`
y los mensajes/props del template usan `intentos_codigo_restantes` +
`codigo_confirmacion_bloqueado`, sin hardcoded 3.
"""
import unittest

from flask import Flask

from extensions import db
from models import Order, SiteConfig, User


class DeliveryIntentosConfigurableTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        cliente = User(
            nombre="Cliente",
            email="c@test.invalid",
            telefono="+34600000010",
            rol="cliente",
            activo=True,
        )
        cliente.set_password("test")
        db.session.add(cliente)
        db.session.commit()
        self.cliente_id = cliente.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk_order(self, intentos=0, codigo="123456"):
        # numero_pedido NOT NULL — usar contador incremental (sqlite en memoria
        # se recrea por test, así que empieza en 1 cada vez).
        self._order_seq = getattr(self, "_order_seq", 0) + 1
        o = Order(
            numero_pedido=f"TEST-INT-{self._order_seq:04d}",
            cliente_id=self.cliente_id,
            total=10,
            subtotal=10,
            estado="en_ruta",
            codigo_confirmacion=codigo,
            intentos_codigo=intentos,
        )
        db.session.add(o)
        db.session.commit()
        return o

    def test_max_intentos_default_3(self):
        o = self._mk_order()
        self.assertEqual(o.max_intentos_entrega, 3)
        self.assertEqual(o.intentos_codigo_restantes, 3)
        self.assertFalse(o.codigo_confirmacion_bloqueado)

    def test_max_intentos_configurable(self):
        SiteConfig.set("DELIVERY_CODE_MAX_INTENTOS", "5", descripcion="test")
        db.session.commit()
        o = self._mk_order(intentos=2)
        self.assertEqual(o.max_intentos_entrega, 5)
        self.assertEqual(o.intentos_codigo_restantes, 3)
        self.assertFalse(o.codigo_confirmacion_bloqueado)

    def test_bloqueado_en_max(self):
        o = self._mk_order(intentos=3)
        self.assertTrue(o.codigo_confirmacion_bloqueado)
        self.assertEqual(o.intentos_codigo_restantes, 0)

    def test_cap_defensivo(self):
        SiteConfig.set("DELIVERY_CODE_MAX_INTENTOS", "999", descripcion="test")
        db.session.commit()
        o = self._mk_order()
        # Cap = 10
        self.assertEqual(o.max_intentos_entrega, 10)

    def test_valor_invalido_fallback_3(self):
        SiteConfig.set("DELIVERY_CODE_MAX_INTENTOS", "abc", descripcion="test")
        db.session.commit()
        o = self._mk_order()
        self.assertEqual(o.max_intentos_entrega, 3)

    def test_mensaje_error_usa_max_configurable(self):
        SiteConfig.set("DELIVERY_CODE_MAX_INTENTOS", "5", descripcion="test")
        db.session.commit()
        o = self._mk_order(intentos=0, codigo="999999")
        ok, msg = o.confirmar_entrega_con_codigo("000000")
        self.assertFalse(ok)
        # Fallo el intento 1 → quedan 4 restantes (no 2 como antes con hardcoded 3)
        self.assertIn("4 intento", msg)


if __name__ == "__main__":
    unittest.main()
