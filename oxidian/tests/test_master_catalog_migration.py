import os
import unittest
from datetime import date, timedelta

from flask import Flask

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://migration-test:unused@localhost/migration-test",
)

from extensions import db
from models import ComboItem, Product, Proveedor, ProveedorProducto, Stock
from scripts.apply_schema_migrations import _consolidate_simple_catalog_variants


class MasterCatalogMigrationTest(unittest.TestCase):
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

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    @staticmethod
    def _set_catalog_key(product, key):
        product.set_atributos({"catalog_key": key})

    def test_consolidation_prefers_own_master_and_is_idempotent(self):
        provider = Proveedor(nombre="Bar migration", activo=True)
        db.session.add(provider)
        db.session.flush()

        provider_variant = Product(
            nombre="Cola bar",
            precio=2,
            es_combo=False,
            activo=True,
            proveedor_despachador_id=provider.id,
        )
        own_variant = Product(
            nombre="Cola master",
            precio=2,
            es_combo=False,
            activo=False,
        )
        self._set_catalog_key(provider_variant, "cola-330")
        self._set_catalog_key(own_variant, "cola-330")
        combo = Product(nombre="Combo", precio=8, es_combo=True, activo=True)
        db.session.add_all([provider_variant, own_variant, combo])
        db.session.flush()

        db.session.add_all([
            ProveedorProducto(
                proveedor_id=provider.id,
                producto_id=provider_variant.id,
                stock=4,
                activo=True,
            ),
            ProveedorProducto(
                proveedor_id=provider.id,
                producto_id=own_variant.id,
                stock=3,
                activo=True,
            ),
            Stock(
                producto_id=provider_variant.id,
                cantidad=2,
                fecha_caducidad=date.today() + timedelta(days=5),
            ),
            ComboItem(
                combo_id=combo.id,
                producto_id=provider_variant.id,
                cantidad=1,
                activo=True,
                es_seleccionable=False,
            ),
        ])
        db.session.commit()

        _consolidate_simple_catalog_variants()
        db.session.flush()
        _consolidate_simple_catalog_variants()
        db.session.commit()

        mapping = ProveedorProducto.query.one()
        combo_item = ComboItem.query.one()
        stock = Stock.query.one()
        self.assertTrue(own_variant.activo)
        self.assertFalse(provider_variant.activo)
        self.assertIsNone(own_variant.proveedor_despachador_id)
        self.assertIsNone(provider_variant.proveedor_despachador_id)
        self.assertEqual(mapping.producto_id, own_variant.id)
        self.assertEqual(mapping.stock, 7)
        self.assertEqual(combo_item.producto_id, own_variant.id)
        self.assertEqual(stock.producto_id, own_variant.id)


if __name__ == "__main__":
    unittest.main()
