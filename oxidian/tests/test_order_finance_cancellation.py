import unittest

from flask import Flask

from extensions import db
from models import Caja, Order, User
from services import cancelar_pedido_operativo, registrar_ingreso_pedido


class OrderFinanceCancellationTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite://")
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        customer = User(nombre="Cliente", email="finance@test.invalid", rol="cliente", activo=True)
        customer.set_password("test")
        db.session.add(customer)
        db.session.commit()
        self.customer_id = customer.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _order(self, number, paid):
        order = Order(
            numero_pedido=number, cliente_id=self.customer_id, origen="online",
            subtotal=20, total=20, estado="pendiente", metodo_pago="bizum",
            pago_confirmado=paid,
        )
        db.session.add(order)
        db.session.commit()
        return order

    def test_cancel_paid_order_creates_exact_refund(self):
        order = self._order("FIN-PAID", True)
        registrar_ingreso_pedido(order)
        db.session.commit()
        cancelar_pedido_operativo(order, canal="test", detalle="prueba")
        db.session.commit()
        refund = Caja.query.filter_by(pedido_id=order.id, tipo="egreso", categoria="devolucion").one()
        self.assertEqual(float(refund.monto), 20.0)

    def test_cancel_unpaid_order_does_not_invent_expense(self):
        order = self._order("FIN-UNPAID", False)
        cancelar_pedido_operativo(order, canal="test", detalle="sin cobro")
        db.session.commit()
        self.assertEqual(Caja.query.filter_by(pedido_id=order.id, tipo="egreso").count(), 0)


if __name__ == "__main__":
    unittest.main()
