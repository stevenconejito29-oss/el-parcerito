import unittest
from decimal import Decimal

from flask import Flask

from extensions import db
from models import Order, SiteConfig, User, utcnow
from services import calcular_pl, distribuir_repartidor, estado_cola


class FeatureWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-only",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.customer = User(
            nombre="Cliente",
            email="customer@test.invalid",
            telefono="+34610000000",
            rol="cliente",
            activo=True,
        )
        self.customer.set_password("password")
        db.session.add(self.customer)
        db.session.flush()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _repartidor(self, index):
        user = User(
            nombre=f"Repartidor {index}",
            email=f"rep{index}@test.invalid",
            rol="repartidor",
            activo=True,
            en_linea=True,
            last_seen=utcnow(),
        )
        user.set_password("password")
        db.session.add(user)
        db.session.flush()
        return user

    def _pedido(self, numero, estado="listo", repartidor_id=None):
        order = Order(
            numero_pedido=numero,
            cliente_id=self.customer.id,
            estado=estado,
            subtotal=Decimal("10.00"),
            total=Decimal("10.00"),
            tipo_entrega_cliente="delivery",
            repartidor_id=repartidor_id,
        )
        db.session.add(order)
        db.session.flush()
        return order

    def test_multiple_drivers_are_balanced_by_active_load(self):
        SiteConfig.set("FEATURE_DELIVERY", "1")
        first = self._repartidor(1)
        second = self._repartidor(2)
        self._pedido("#OCUPADO", estado="en_ruta", repartidor_id=first.id)
        target = self._pedido("#NUEVO")
        db.session.flush()

        assigned = distribuir_repartidor(target)

        self.assertEqual(assigned.id, second.id)
        self.assertEqual(target.repartidor_id, second.id)

    def test_delivery_off_blocks_assignment_and_removes_role_from_queue(self):
        SiteConfig.set("FEATURE_DELIVERY", "0")
        driver = self._repartidor(1)
        target = self._pedido("#SIN-DELIVERY")
        db.session.flush()

        self.assertIsNone(distribuir_repartidor(target))
        self.assertIsNone(target.repartidor_id)
        self.assertNotIn("repartidor", estado_cola())
        self.assertIsNotNone(driver.id)

    def test_queue_does_not_expose_legacy_staff_role(self):
        staff = User(
            nombre="Staff legado",
            email="staff@test.invalid",
            rol="staff",
            activo=True,
            en_linea=True,
            last_seen=utcnow(),
        )
        staff.set_password("password")
        db.session.add(staff)
        db.session.commit()

        self.assertNotIn("staff", estado_cola())

    def test_service_commission_is_subtracted_from_pl_result(self):
        today = utcnow().date()
        order = Order(
            numero_pedido="#PL-SERVICE",
            cliente_id=self.customer.id,
            estado="entregado",
            origen="online",
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
            service_commission_amount=Decimal("12.00"),
            merchant_net_amount=Decimal("88.00"),
            entregado_en=utcnow(),
        )
        db.session.add(order)
        db.session.commit()

        pl = calcular_pl(today, today)

        self.assertEqual(pl["ingresos_netos"], 100.0)
        self.assertEqual(pl["service_commission"], 12.0)
        self.assertEqual(pl["resultado"], 88.0)


if __name__ == "__main__":
    unittest.main()
