import json
import unittest
from decimal import Decimal

from flask import Flask

from extensions import db
from models import Order, OrderItem, Product, Proveedor, User
from routes.api_bot import api_bot_bp


class BotHandoffDestinationTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            BOT_API_KEY="test-bot-key",
        )
        db.init_app(self.app)
        self.app.register_blueprint(api_bot_bp, url_prefix="/api/bot")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

        self.customer = User(
            nombre="Cliente",
            email="cliente@test.invalid",
            telefono="+34610000001",
            rol="cliente",
            activo=True,
        )
        self.superadmin = User(
            nombre="Global",
            email="global@test.invalid",
            telefono="+34600000001",
            rol="super_admin",
            activo=True,
        )
        self.provider = Proveedor(
            nombre="Proveedor",
            telefono="+34999999999",
            activo=True,
        )
        self.customer.set_password("test-only-password")
        self.superadmin.set_password("test-only-password")
        db.session.add_all([self.customer, self.superadmin, self.provider])
        db.session.flush()
        self.operator = User(
            nombre="Operador",
            email="operador@test.invalid",
            telefono="+34600000002",
            rol="proveedor",
            proveedor_id=self.provider.id,
            activo=True,
        )
        self.operator.set_password("test-only-password")
        self.product = Product(
            nombre="Producto",
            precio=Decimal("10.00"),
            activo=True,
        )
        db.session.add_all([self.operator, self.product])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _get(self, phone="+34610000001", authenticated=True):
        headers = {"X-Bot-Key": "test-bot-key"} if authenticated else {}
        return self.client.get(
            f"/api/bot/handoff/destination?telefono={phone}",
            headers=headers,
        )

    def _order(self, provider_id):
        order = Order(
            numero_pedido=f"#10{Order.query.count() + 1}",
            cliente_id=self.customer.id,
            estado="armando",
            subtotal=Decimal("10.00"),
            total=Decimal("10.00"),
        )
        db.session.add(order)
        db.session.flush()
        snapshot = {
            "producto": {
                "origen_operativo_key": (
                    f"proveedor:{provider_id}" if provider_id else "propio"
                ),
                "proveedor_despachador_id": provider_id,
            }
        }
        item = OrderItem(
            pedido_id=order.id,
            producto_id=self.product.id,
            cantidad=1,
            precio_unit=Decimal("10.00"),
            subtotal=Decimal("10.00"),
            metadata_json=json.dumps(snapshot),
        )
        db.session.add(item)
        db.session.commit()
        return order

    def test_requires_bot_authentication(self):
        self.assertEqual(self._get(authenticated=False).status_code, 401)

    def test_provider_order_uses_linked_user_phone_not_public_contact(self):
        order = self._order(self.provider.id)

        payload = self._get().get_json()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["scope"], f"provider:{self.provider.id}")
        self.assertEqual(payload["order_id"], order.id)
        self.assertEqual(payload["agents"], ["34600000002"])
        self.assertNotIn("34999999999", payload["agents"])

    def test_own_order_uses_global_superadmins(self):
        self._order(None)

        payload = self._get().get_json()

        self.assertEqual(payload["scope"], "global")
        self.assertIsNone(payload["provider_id"])
        self.assertEqual(payload["agents"], ["34600000001"])

    def test_provider_without_active_operator_falls_back_global(self):
        self.operator.activo = False
        db.session.commit()
        self._order(self.provider.id)

        payload = self._get().get_json()

        self.assertEqual(payload["scope"], "global")
        self.assertEqual(payload["agents"], ["34600000001"])


if __name__ == "__main__":
    unittest.main()
