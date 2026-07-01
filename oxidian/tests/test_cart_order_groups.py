import unittest
from datetime import date, timedelta
from decimal import Decimal

from flask import Flask

from extensions import db
from models import Product
from routes.public import public_bp


class CartOrderGroupTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-only",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
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

    def _product(self, name, channel, group=None):
        product = Product(
            nombre=name,
            precio=Decimal("5.00"),
            activo=True,
            canal_preparacion=channel,
            tipo_entrega="programado",
            modalidad_entrega="ambas",
            fecha_llegada=date.today() + timedelta(days=2),
            grupo_pedido=group,
        )
        db.session.add(product)
        db.session.commit()
        return product

    def _add(self, product):
        return self.client.post(
            f"/carrito/agregar/{product.id}",
            data={"cantidad": "1", "origen": "propio"},
            headers={"X-Ajax": "1"},
        )

    def test_kitchen_and_warehouse_can_share_the_general_cart(self):
        meal = self._product("Comida", "cocina")
        drink = self._product("Bebida", "almacen")

        self.assertTrue(self._add(meal).get_json()["ok"])
        self.assertTrue(self._add(drink).get_json()["ok"])

        with self.client.session_transaction() as session:
            self.assertEqual(session["carrito"], {str(meal.id): 1, str(drink.id): 1})

    def test_different_configured_groups_require_separate_orders(self):
        cold = self._product("Postre frío", "almacen", "Cadena de frío")
        hot = self._product("Plato caliente", "cocina", "Entrega caliente")

        self.assertTrue(self._add(cold).get_json()["ok"])
        response = self._add(hot).get_json()

        self.assertFalse(response["ok"])
        self.assertIn("Cadena de frío", response["msg"])
        self.assertIn("Entrega caliente", response["msg"])


if __name__ == "__main__":
    unittest.main()
