"""Regresiones financieras de productos pertenecientes a socios."""
import json
import unittest
from decimal import Decimal
from types import SimpleNamespace

from flask import Flask

from extensions import db
from models import (
    Order, OrderEvent, OrderItem, Product, Proveedor, StaffPayment, User, utcnow,
)
from services import (
    _costo_item_pedido,
    calcular_liquidaciones_proveedores,
    origen_liquidacion_proveedor,
)
from business_time import business_today


class _PartnerItem:
    cantidad = 3
    precio_unit = 10

    @staticmethod
    def get_metadata():
        return {
            "producto": {
                "proveedor_despachador_id": 8,
                "proveedor_modelo_acuerdo": "socio_porcentaje",
                # Comisión que conserva la tienda.
                "proveedor_comision_pct": 20,
            }
        }


class PartnerFinanceTest(unittest.TestCase):
    def test_partner_share_is_liability_not_own_margin(self):
        cost, missing, estimated = _costo_item_pedido(_PartnerItem())
        self.assertEqual(cost, 24.0)
        self.assertFalse(missing)
        self.assertFalse(estimated)

    def test_own_item_still_uses_frozen_cost(self):
        item = SimpleNamespace(
            cantidad=2,
            precio_unit=10,
            get_metadata=lambda: {
                "producto": {"precio_costo": 4},
                "combo": {},
            },
            producto=None,
        )
        cost, missing, estimated = _costo_item_pedido(item)
        self.assertEqual(cost, 8.0)
        self.assertFalse(missing)
        self.assertFalse(estimated)


class PartnerSettlementTest(unittest.TestCase):
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
        self.customer = User(
            nombre="Cliente liquidación",
            email="cliente-liquidacion@test.invalid",
            rol="cliente",
            activo=True,
        )
        self.customer.set_password("test")
        self.partner = Proveedor(
            nombre="Socio congelado",
            activo=True,
            modelo_acuerdo="socio_porcentaje",
            comision_pct=45,  # No debe sustituir el 20% del snapshot.
        )
        db.session.add_all([self.customer, self.partner])
        db.session.flush()
        self.operator = User(
            nombre="Operador socio",
            email="operador-socio@test.invalid",
            rol="socio_producto",
            proveedor_id=self.partner.id,
            activo=True,
        )
        self.operator.set_password("test")
        self.product = Product(
            nombre="Producto del socio",
            precio=Decimal("10"),
            activo=True,
        )
        db.session.add_all([self.operator, self.product])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _order(self, number, *, state, quantity, discount=0):
        gross = Decimal("10") * quantity
        order = Order(
            numero_pedido=number,
            cliente_id=self.customer.id,
            estado=state,
            origen="online",
            subtotal=gross,
            descuento=Decimal(str(discount)),
            total=gross - Decimal(str(discount)),
            entregado_en=utcnow() if state == "entregado" else None,
        )
        db.session.add(order)
        db.session.flush()
        db.session.add(OrderItem(
            pedido_id=order.id,
            producto_id=self.product.id,
            cantidad=quantity,
            precio_unit=Decimal("10"),
            subtotal=gross,
            metadata_json=json.dumps({"producto": {
                "id": self.product.id,
                "nombre": self.product.nombre,
                "proveedor_despachador_id": self.partner.id,
                "proveedor_modelo_acuerdo": "socio_porcentaje",
                "proveedor_comision_pct": 20,
            }}),
        ))
        return order

    def test_uses_delivery_date_snapshot_discount_and_covers_loss(self):
        self._order("SOCIO-ENTREGADO", state="entregado", quantity=2, discount=2)
        lost = self._order("SOCIO-EXTRAVIADO", state="cancelado", quantity=1)
        db.session.flush()
        db.session.add(OrderEvent(
            pedido_id=lost.id,
            tipo="pedido_extraviado",
            creado_en=utcnow(),
        ))
        db.session.add(StaffPayment(
            user_id=self.operator.id,
            tipo="liquidacion_proveedor",
            monto=Decimal("5"),
            periodo_inicio=business_today(),
            periodo_fin=business_today(),
            origen=origen_liquidacion_proveedor(self.partner.id),
        ))
        db.session.add(StaffPayment(
            user_id=self.operator.id,
            tipo="liquidacion_proveedor",
            monto=Decimal("1"),
            concepto=f"Liquidación legacy (proveedor: {self.partner.nombre})",
            periodo_inicio=business_today(),
            periodo_fin=business_today(),
            origen="manual",
        ))
        db.session.commit()

        report = calcular_liquidaciones_proveedores(
            business_today(), business_today(), proveedor_id=self.partner.id
        )
        bucket = report["por_proveedor"][self.partner.id]

        self.assertEqual(report["total_ingresos"], Decimal("18.00"))
        self.assertEqual(bucket["total_entregado"], Decimal("14.40"))
        self.assertEqual(bucket["total_extraviado"], Decimal("8.00"))
        self.assertEqual(bucket["total"], Decimal("22.40"))
        self.assertEqual(bucket["registrado"], Decimal("6.00"))
        self.assertEqual(bucket["pendiente_registrar"], Decimal("16.40"))
        self.assertEqual(bucket["pendiente_pago"], Decimal("22.40"))
        self.assertEqual(report["perdida_extravios"], Decimal("8.00"))


if __name__ == "__main__":
    unittest.main()
