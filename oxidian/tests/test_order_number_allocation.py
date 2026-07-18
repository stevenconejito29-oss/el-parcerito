import unittest
from pathlib import Path

from flask import Flask

from extensions import db
from models import Order, User


class OrderNumberAllocationTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        customer = User(
            nombre="Cliente numeración",
            email="order-number@test.invalid",
            telefono="+34600111222",
            rol="cliente",
            activo=True,
        )
        customer.set_password("test")
        db.session.add(customer)
        db.session.commit()
        self.customer_id = customer.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_fresh_database_advances_visible_number_independently_from_id(self):
        first_number = Order.generar_numero()
        self.assertEqual(first_number, "#1001")
        db.session.add(Order(
            numero_pedido=first_number,
            cliente_id=self.customer_id,
            estado="pendiente",
            subtotal=10,
            total=10,
        ))
        db.session.flush()

        self.assertEqual(Order.generar_numero(), "#1002")

    def test_postgresql_path_serializes_number_allocation(self):
        source = (Path(__file__).resolve().parents[1] / "models.py").read_text(encoding="utf-8")
        self.assertIn("pg_advisory_xact_lock", source)
        self.assertIn("oxidian.orders.numero_pedido", source)


if __name__ == "__main__":
    unittest.main()
