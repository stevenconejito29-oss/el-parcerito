import unittest
from datetime import date, timedelta

from flask import Flask

from extensions import db
from models import (
    ComboItem,
    Product,
    Proveedor,
    ProveedorProducto,
    Stock,
    metadata_item_pedido,
)


class InventoryOriginModelTest(unittest.TestCase):
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

        self.provider = Proveedor(nombre="Bar test", activo=True)
        db.session.add(self.provider)
        db.session.flush()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _simple_product(self, name="Cola"):
        product = Product(
            nombre=name,
            precio=2,
            es_combo=False,
            activo=True,
            tipo_entrega="inmediato",
        )
        db.session.add(product)
        db.session.flush()
        return product

    def test_simple_product_separates_own_and_provider_stock(self):
        product = self._simple_product()
        expired = Stock(
            producto_id=product.id,
            cantidad=7,
            fecha_caducidad=date.today() - timedelta(days=1),
        )
        first = Stock(
            producto_id=product.id,
            cantidad=3,
            fecha_caducidad=date.today() + timedelta(days=1),
        )
        second = Stock(
            producto_id=product.id,
            cantidad=5,
            fecha_caducidad=date.today() + timedelta(days=10),
        )
        mapping = ProveedorProducto(
            proveedor_id=self.provider.id,
            producto_id=product.id,
            stock=8,
            activo=True,
        )
        db.session.add_all([expired, first, second, mapping])
        db.session.commit()

        provider_origin = f"proveedor:{self.provider.id}"
        self.assertTrue(product.pertenece_a_origen("propio"))
        self.assertTrue(product.pertenece_a_origen(provider_origin))
        self.assertEqual(product.stock_en_origen("propio"), 8)
        self.assertEqual(product.stock_en_origen(provider_origin), 8)
        self.assertTrue(product.disponible_para_venta_en_origen("propio", 8))

        product.descontar_stock_en_origen("propio", 4)
        product.descontar_stock_en_origen(provider_origin, 2)
        db.session.flush()

        self.assertEqual(expired.cantidad, 7)
        self.assertEqual(first.cantidad, 0)
        self.assertEqual(second.cantidad, 4)
        self.assertEqual(mapping.stock, 6)

    def test_no_origin_keeps_legacy_provider_behavior(self):
        product = self._simple_product("Legacy")
        product.proveedor_despachador_id = self.provider.id
        mapping = ProveedorProducto(
            proveedor_id=self.provider.id,
            producto_id=product.id,
            stock=5,
            activo=True,
        )
        db.session.add(mapping)
        db.session.commit()

        self.assertEqual(product.stock_operativo_total, 5)
        product.descontar_stock(2)
        self.assertEqual(mapping.stock, 3)

    def test_provider_only_product_does_not_belong_to_own_store(self):
        product = self._simple_product("Solo bar")
        db.session.add(ProveedorProducto(
            proveedor_id=self.provider.id,
            producto_id=product.id,
            stock=5,
            activo=True,
        ))
        db.session.commit()

        self.assertFalse(product.pertenece_a_origen("propio"))
        db.session.add(Stock(
            producto_id=product.id,
            cantidad=0,
            fecha_caducidad=date.today() - timedelta(days=1),
        ))
        db.session.commit()
        self.assertTrue(product.pertenece_a_origen("propio"))
        self.assertFalse(product.disponible_para_venta_en_origen("propio"))

    def test_order_snapshot_resolves_provider_from_explicit_origin(self):
        product = self._simple_product("Snapshot")
        self.provider.direccion = "Calle Test 1"
        self.provider.telefono = "+34123456789"
        self.provider.modelo_acuerdo = "stock_propio_bar"
        self.provider.comision_pct = 12.5
        db.session.add(ProveedorProducto(
            proveedor_id=self.provider.id,
            producto_id=product.id,
            stock=4,
            activo=True,
        ))
        db.session.commit()

        origin = f"proveedor:{self.provider.id}"
        snapshot = metadata_item_pedido(
            product,
            origen_operativo=origin,
        )["producto"]

        self.assertEqual(snapshot["origen_operativo_key"], origin)
        self.assertEqual(snapshot["proveedor_despachador_id"], self.provider.id)
        self.assertEqual(snapshot["proveedor_snapshot"], {
            "id": self.provider.id,
            "nombre": "Bar test",
            "direccion": "Calle Test 1",
            "telefono": "+34123456789",
            "modelo": "stock_propio_bar",
            "comision": 12.5,
        })

    def test_provider_combo_uses_master_components_at_provider_origin(self):
        component = self._simple_product("Arepa")
        mapping = ProveedorProducto(
            proveedor_id=self.provider.id,
            producto_id=component.id,
            stock=6,
            activo=True,
        )
        combo = Product(
            nombre="Combo bar",
            precio=10,
            es_combo=True,
            activo=True,
            tipo_entrega="inmediato",
            proveedor_despachador_id=self.provider.id,
        )
        db.session.add_all([mapping, combo])
        db.session.flush()
        db.session.add(ComboItem(
            combo_id=combo.id,
            producto_id=component.id,
            cantidad=2,
            activo=True,
            es_seleccionable=False,
        ))
        db.session.commit()

        origin = f"proveedor:{self.provider.id}"
        self.assertEqual(combo.stock_total_en_origen(origin), 3)
        self.assertEqual(combo.stock_total_en_origen("propio"), 0)
        self.assertTrue(combo.disponible_para_venta_en_origen(origin, 3))

        combo.descontar_stock_en_origen(origin, 2)
        self.assertEqual(mapping.stock, 2)


if __name__ == "__main__":
    unittest.main()
