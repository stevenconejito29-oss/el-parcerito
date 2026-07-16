"""Invariantes financieros compartidos por todos los canales de pedido."""
import unittest

from flask import Flask

from extensions import db
from models import Caja, Order, StaffPayment, User, normalizar_metodo_pago
from services import (
    cancelar_pedido_operativo,
    pagos_pendientes_staff,
    registrar_ingreso_pedido,
)


class DeliveryFinanceInvariantsTest(unittest.TestCase):
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

        self.cliente = User(
            nombre="Cliente",
            email="cliente-finanzas@test.invalid",
            telefono="+34600001001",
            rol="cliente",
            activo=True,
        )
        self.empleado = User(
            nombre="Repartidor",
            email="repartidor-finanzas@test.invalid",
            telefono="+34600001002",
            rol="repartidor",
            activo=True,
        )
        self.cliente.set_password("test")
        self.empleado.set_password("test")
        db.session.add_all([self.cliente, self.empleado])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pedido(self, estado="en_ruta", total=25):
        pedido = Order(
            numero_pedido="FIN-0001",
            cliente_id=self.cliente.id,
            subtotal=total,
            total=total,
            estado=estado,
            origen="online",
            metodo_pago="efectivo",
            tipo_entrega_cliente="delivery",
        )
        db.session.add(pedido)
        db.session.commit()
        return pedido

    def test_tarjeta_no_se_reclasifica_como_bizum(self):
        self.assertEqual(normalizar_metodo_pago("tarjeta"), "tarjeta")
        self.assertEqual(normalizar_metodo_pago("transferencia"), "bizum")

    def test_cancelacion_revierte_ingreso_desde_cualquier_canal(self):
        pedido = self._pedido()
        registrar_ingreso_pedido(pedido)
        db.session.commit()

        cancelar_pedido_operativo(
            pedido,
            actor_id=self.empleado.id,
            canal="repartidor_no_entregado",
            detalle="Cliente ausente",
        )
        db.session.commit()

        devoluciones = Caja.query.filter_by(
            pedido_id=pedido.id,
            tipo="egreso",
            categoria="devolucion",
        ).all()
        self.assertEqual(len(devoluciones), 1)
        self.assertEqual(float(devoluciones[0].monto), 25.0)

    def test_descuento_no_infla_obligaciones_de_caja(self):
        db.session.add_all([
            StaffPayment(
                user_id=self.empleado.id,
                tipo="comision",
                monto=12,
                pagado=False,
            ),
            StaffPayment(
                user_id=self.empleado.id,
                tipo="descuento",
                monto=5,
                pagado=False,
            ),
        ])
        db.session.commit()

        self.assertEqual(pagos_pendientes_staff(), 12.0)


if __name__ == "__main__":
    unittest.main()
