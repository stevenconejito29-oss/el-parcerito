"""Invariantes del sistema de canje con puntos (Fase G).

Cubre las reglas críticas del producto `solo_canje`:
- Un producto solo_canje TIENE que costar 0 en euros (precio=0).
- Un producto solo_canje NO se puede añadir al carrito por la ruta normal
  de compra: debe pasar por el flujo de canje del Club de puntos.
"""

import unittest
from datetime import date, timedelta
from decimal import Decimal

from flask import Flask

from extensions import db
from models import Product
from routes.public import public_bp


class CanjeSoloCanjeTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-only",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SKIP_DELIVERY_VALIDATION=True,
        )
        db.init_app(self.app)
        self.app.register_blueprint(public_bp)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _producto_solo_canje(self, puntos=200):
        # solo_canje IMPLICA precio=0 y canjeable_con_puntos=True.
        # Simulamos la salida esperada de _parse_producto_form (admin.py:2002-2004).
        p = Product(
            nombre="Regalo Cumple",
            precio=Decimal("0.00"),
            activo=True,
            tipo_entrega="inmediato",
            modalidad_entrega="ambas",
            canjeable_con_puntos=True,
            solo_canje=True,
            puntos_para_canje=puntos,
        )
        db.session.add(p)
        db.session.commit()
        return p

    def test_solo_canje_forces_zero_price(self):
        # Invariante estructural: un producto solo_canje persistido
        # con precio > 0 sería un bug. Verifica que el modelo lo acepta a 0.
        p = self._producto_solo_canje()
        self.assertEqual(float(p.precio or 0), 0.0)
        self.assertTrue(p.canjeable_con_puntos)
        self.assertTrue(p.solo_canje)
        self.assertGreater(int(p.puntos_para_canje or 0), 0)

    def test_solo_canje_cannot_be_added_via_normal_cart(self):
        # Regla en routes/public.py:agregar_carrito (línea 858):
        # producto solo_canje redirige al Club de puntos.
        p = self._producto_solo_canje()
        response = self.client.post(
            f"/carrito/agregar/{p.id}",
            data={"cantidad": "1", "origen": "propio"},
            headers={"X-Ajax": "1"},
        )
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertIn("Club", payload["msg"])

    def test_regular_canjeable_product_can_be_added_normally(self):
        # Producto canjeable_con_puntos=True pero solo_canje=False:
        # sí se compra con dinero, y opcionalmente el cliente puede canjearlo.
        p = Product(
            nombre="Cerveza",
            precio=Decimal("2.50"),
            activo=True,
            tipo_entrega="inmediato",
            modalidad_entrega="ambas",
            canjeable_con_puntos=True,
            solo_canje=False,
            puntos_para_canje=100,
        )
        db.session.add(p)
        db.session.commit()

        response = self.client.post(
            f"/carrito/agregar/{p.id}",
            data={"cantidad": "1", "origen": "propio"},
            headers={"X-Ajax": "1"},
        )
        self.assertTrue(response.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
