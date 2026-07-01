import json
import unittest
from decimal import Decimal

from flask import Flask

from extensions import db
from models import BotAiMessage, BotAiUsage, Order, OrderItem, Product, User
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
        self.customer.set_password("test-only-password")
        self.superadmin.set_password("test-only-password")
        db.session.add_all([self.customer, self.superadmin])
        db.session.flush()
        self.product = Product(
            nombre="Producto",
            precio=Decimal("10.00"),
            activo=True,
        )
        db.session.add(self.product)
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

    def test_ai_usage_and_memory_keep_phone_out_of_storage(self):
        headers = {"X-Bot-Key": "test-bot-key"}
        phone = "+34610000001"
        preflight = self.client.post(
            "/api/bot/ai/usage",
            json={"telefono": phone, "tokens_in": 0, "tokens_out": 0},
            headers=headers,
        )
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(BotAiUsage.query.count(), 0)

        recorded = self.client.post(
            "/api/bot/ai/usage",
            json={"telefono": phone, "tokens_in": 12, "tokens_out": 8},
            headers=headers,
        )
        self.assertEqual(recorded.status_code, 200)
        usage = BotAiUsage.query.one()
        self.assertNotEqual(usage.telefono_hash, phone)
        self.assertNotIn("610000001", usage.telefono_hash)

        saved = self.client.post(
            "/api/bot/ai/memory",
            json={"telefono": phone, "rol": "user", "contenido": "¿Cuál es el horario?"},
            headers=headers,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertNotEqual(BotAiMessage.query.one().telefono_hash, phone)
        memory = self.client.get(
            f"/api/bot/ai/memory?telefono={phone}", headers=headers
        ).get_json()
        self.assertEqual(memory["messages"][0]["role"], "user")

    def test_ai_context_hides_points_when_module_is_disabled(self):
        from models import SiteConfig

        self.customer.puntos = 120
        SiteConfig.set("FEATURE_PUNTOS", "0")
        db.session.commit()
        response = self.client.get(
            "/api/bot/ai/cliente-context?telefono=+34610000001",
            headers={"X-Bot-Key": "test-bot-key"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()["cliente"]["puntos"])

    def test_legacy_provider_metadata_still_uses_global_superadmins(self):
        order = self._order(17)

        payload = self._get().get_json()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["scope"], "global")
        self.assertIsNone(payload["provider_id"])
        self.assertEqual(payload["order_id"], order.id)
        self.assertEqual(payload["agents"], ["34600000001"])

    def test_own_order_uses_global_superadmins(self):
        self._order(None)

        payload = self._get().get_json()

        self.assertEqual(payload["scope"], "global")
        self.assertIsNone(payload["provider_id"])
        self.assertEqual(payload["agents"], ["34600000001"])

if __name__ == "__main__":
    unittest.main()
