import json
import unittest
from decimal import Decimal

from flask import Flask

from extensions import db
from models import (
    Caja, ComboItem, Order, OrderItem, Product, Proveedor,
    ProveedorProducto, User, utcnow,
)
from services import calcular_pl
from business_time import business_today


class FinancialControlTest(unittest.TestCase):
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
            nombre="Cliente finanzas",
            email="cliente-control@test.invalid",
            rol="cliente",
            activo=True,
        )
        self.customer.set_password("test")
        db.session.add(self.customer)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _order(self, number, subtotal, discount, total, delivery=0):
        order = Order(
            numero_pedido=number,
            cliente_id=self.customer.id,
            estado="entregado",
            origen="online",
            subtotal=Decimal(str(subtotal)),
            descuento=Decimal(str(discount)),
            total=Decimal(str(total)),
            costo_envio_snapshot=Decimal(str(delivery)),
            entregado_en=utcnow(),
        )
        db.session.add(order)
        db.session.flush()
        return order

    def test_separates_product_margin_delivery_and_cash_inventory_purchase(self):
        product = Product(
            nombre="Producto rentable",
            precio=Decimal("10.00"),
            precio_costo=Decimal("4.00"),
            activo=True,
        )
        db.session.add(product)
        db.session.flush()
        order = self._order("FIN-CONTROL-1", 20, 2, 21, delivery=3)
        db.session.add(OrderItem(
            pedido_id=order.id,
            producto_id=product.id,
            cantidad=2,
            precio_unit=Decimal("10.00"),
            subtotal=Decimal("20.00"),
            metadata_json=json.dumps({"producto": {
                "id": product.id,
                "nombre": product.nombre,
                "precio_costo": 4.0,
                "categoria_nombre": "Dulces",
            }}),
        ))
        db.session.add_all([
            Caja(tipo="ingreso", categoria="venta_online", monto=Decimal("21.00"), pedido_id=order.id),
            Caja(tipo="egreso", categoria="compra_insumos", monto=Decimal("50.00"), concepto="Reposición"),
        ])
        db.session.commit()

        report = calcular_pl(business_today(), business_today())

        self.assertEqual(report["ventas_productos_netas"], 18.0)
        self.assertEqual(report["ingreso_envios"], 3.0)
        self.assertEqual(report["cogs"], 8.0)
        self.assertEqual(report["margen_bruto"], 10.0)
        self.assertEqual(report["resultado"], 13.0)
        self.assertEqual(report["compras_inventario"], 50.0)
        self.assertEqual(report["gastos_caja"], 0.0)
        self.assertEqual(report["saldo_caja"], -29.0)
        self.assertFalse(report["resultado_provisional"])
        row = report["productos_rentabilidad"][0]
        self.assertEqual(row["ganancia"], 10.0)
        self.assertEqual(row["margen_pct"], 55.6)

    def test_combo_cost_comes_from_frozen_components(self):
        component = Product(
            nombre="Componente",
            precio=Decimal("3.00"),
            precio_costo=Decimal("2.00"),
            activo=True,
        )
        combo = Product(
            nombre="Combo rentable",
            precio=Decimal("12.00"),
            combo_precio_base=Decimal("12.00"),
            es_combo=True,
            tipo_producto="combo",
            activo=True,
        )
        db.session.add_all([component, combo])
        db.session.flush()
        db.session.add(ComboItem(
            combo_id=combo.id,
            producto_id=component.id,
            cantidad=2,
            activo=True,
        ))
        order = self._order("FIN-CONTROL-2", 24, 0, 24)
        db.session.add(OrderItem(
            pedido_id=order.id,
            producto_id=combo.id,
            cantidad=2,
            precio_unit=Decimal("12.00"),
            subtotal=Decimal("24.00"),
            metadata_json=json.dumps({
                "producto": {"id": combo.id, "nombre": combo.nombre, "es_combo": True},
                "combo": {
                    "componentes": [{
                        "producto_id": component.id,
                        "nombre": component.nombre,
                        "cantidad": 2,
                        "precio_costo_congelado": 2.0,
                    }],
                    "selecciones": [],
                },
            }),
        ))
        db.session.commit()

        report = calcular_pl(business_today(), business_today())

        self.assertEqual(report["cogs"], 8.0)
        self.assertEqual(report["productos_rentabilidad"][0]["ganancia"], 16.0)
        self.assertFalse(report["resultado_provisional"])

    def test_missing_cost_never_looks_like_confirmed_profit(self):
        product = Product(
            nombre="Sin coste",
            precio=Decimal("9.00"),
            precio_costo=None,
            activo=True,
        )
        db.session.add(product)
        db.session.flush()
        order = self._order("FIN-CONTROL-3", 9, 0, 9)
        db.session.add(OrderItem(
            pedido_id=order.id,
            producto_id=product.id,
            cantidad=1,
            precio_unit=Decimal("9.00"),
            subtotal=Decimal("9.00"),
            metadata_json=json.dumps({"producto": {
                "id": product.id,
                "nombre": product.nombre,
                "precio_costo": None,
            }}),
        ))
        db.session.commit()

        report = calcular_pl(business_today(), business_today())

        self.assertTrue(report["resultado_provisional"])
        self.assertEqual(report["cobertura_costes_pct"], 0.0)
        self.assertTrue(report["productos_rentabilidad"][0]["coste_incompleto"])

    def test_partner_product_recognizes_only_store_commission_as_margin(self):
        partner = Proveedor(
            nombre="Socio financiero",
            activo=True,
            modelo_acuerdo="socio_porcentaje",
            comision_pct=20,
        )
        product = Product(
            nombre="Producto socio",
            precio=Decimal("10.00"),
            precio_costo=None,
            activo=True,
            proveedor_despachador=partner,
            stock_mostrar_en_web=True,
        )
        db.session.add_all([partner, product])
        db.session.flush()
        db.session.add(ProveedorProducto(
            proveedor_id=partner.id,
            producto_id=product.id,
            stock=10,
            activo=True,
        ))
        order = self._order("FIN-SOCIO-1", 20, 0, 20)
        db.session.add(OrderItem(
            pedido_id=order.id,
            producto_id=product.id,
            cantidad=2,
            precio_unit=Decimal("10.00"),
            subtotal=Decimal("20.00"),
            metadata_json=json.dumps({"producto": {
                "id": product.id,
                "nombre": product.nombre,
                "proveedor_despachador_id": partner.id,
                "proveedor_modelo_acuerdo": "socio_porcentaje",
                "proveedor_comision_pct": 20,
            }}),
        ))
        db.session.commit()

        report = calcular_pl(business_today(), business_today())

        self.assertEqual(report["cogs"], 16.0)
        self.assertEqual(report["margen_bruto"], 4.0)
        self.assertFalse(report["resultado_provisional"])
        self.assertEqual(report["productos_sin_coste"], [])


if __name__ == "__main__":
    unittest.main()
