import unittest
from decimal import Decimal

from flask import Flask, session
from werkzeug.datastructures import MultiDict

from extensions import db
from models import (
    ExtraCatalogItem,
    Product,
    ProductExtraGroup,
    ProductExtraOption,
    Stock,
    ZonaEntrega,
)
from routes.admin import _sync_catalog_extras
from routes.public import (
    _build_items_from_carrito,
    _parse_product_extras,
    _product_extras_payload,
    public_bp,
)


class ProductExtrasWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-only",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            CART_MAX_QTY=20,
        )
        db.init_app(self.app)
        self.app.register_blueprint(public_bp)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

        self.product = Product(
            nombre="Hamburguesa extras QA",
            precio=Decimal("5.00"),
            activo=True,
            tipo_entrega="inmediato",
            modalidad_entrega="ambas",
            canal_preparacion="cocina",
        )
        db.session.add(self.product)
        db.session.flush()
        db.session.add(Stock(producto_id=self.product.id, cantidad=20))
        self.group = ProductExtraGroup(
            producto_id=self.product.id,
            nombre="Ingredientes",
            min_selecciones=0,
            max_selecciones=3,
        )
        db.session.add(self.group)
        db.session.flush()
        self.cheese = ProductExtraOption(
            grupo_id=self.group.id, nombre="Queso", precio=Decimal("1.50"), max_cantidad=2
        )
        self.sauce = ProductExtraOption(
            grupo_id=self.group.id, nombre="Salsa", precio=Decimal("0.00"), max_cantidad=1
        )
        db.session.add_all([self.cheese, self.sauce])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_extra_quantities_are_validated_and_priced_per_product_unit(self):
        selected, error = _parse_product_extras(
            self.product,
            MultiDict({f"extra_qty_{self.cheese.id}": "2", f"extra_qty_{self.sauce.id}": "1"}),
        )
        self.assertIsNone(error)
        rows, extra_total = _product_extras_payload(self.product, selected)
        self.assertEqual(extra_total, 3.0)
        self.assertEqual({row["nombre"] for row in rows}, {"Queso", "Salsa"})

        with self.app.test_request_context():
            session["carrito"] = {str(self.product.id): 2}
            session["carrito_origen"] = "propio"
            session["extras_selecciones"] = {str(self.product.id): selected}
            items, subtotal = _build_items_from_carrito(session["carrito"])
        self.assertEqual(items[0]["precio_unit"], 8.0)
        self.assertEqual(items[0]["subtotal"], 16.0)
        self.assertEqual(subtotal, 16.0)

    def test_group_and_option_limits_reject_invalid_quantities(self):
        _, error = _parse_product_extras(
            self.product,
            MultiDict({f"extra_qty_{self.cheese.id}": "3"}),
        )
        self.assertIn("Cantidad inválida", error)

        self.group.min_selecciones = 1
        self.group.max_selecciones = 1
        db.session.commit()
        _, error = _parse_product_extras(self.product, MultiDict())
        self.assertIn("al menos 1", error)
        self.product.canjeable_con_puntos = True
        self.product.puntos_para_canje = 500
        db.session.commit()
        self.assertFalse(self.product.canje_directo_disponible())

    def test_same_product_cannot_silently_replace_a_saved_configuration(self):
        first = self.client.post(
            f"/carrito/agregar/{self.product.id}",
            data={"cantidad": "1", "origen": "propio", f"extra_qty_{self.cheese.id}": "1"},
            headers={"X-Ajax": "1"},
        )
        self.assertTrue(first.get_json()["ok"])
        second = self.client.post(
            f"/carrito/agregar/{self.product.id}",
            data={"cantidad": "1", "origen": "propio", f"extra_qty_{self.sauce.id}": "1"},
            headers={"X-Ajax": "1"},
        )
        self.assertFalse(second.get_json()["ok"])
        self.assertIn("otros extras", second.get_json()["msg"])

    def test_catalog_selection_is_reused_by_products_and_keeps_snapshot_fields(self):
        bacon = ExtraCatalogItem(
            nombre="Bacon crujiente", precio=Decimal("2.25"), max_cantidad=2, activo=True
        )
        db.session.add(bacon)
        db.session.commit()
        form = MultiDict([
            ("extras_catalog_present", "1"),
            ("extra_catalog_ids", str(bacon.id)),
            ("extras_max_selecciones", "2"),
        ])
        self.assertIsNone(_sync_catalog_extras(self.product, form))
        db.session.commit()

        linked = ProductExtraOption.query.filter_by(catalog_item_id=bacon.id).one()
        self.assertEqual(linked.grupo.producto_id, self.product.id)
        self.assertEqual(linked.nombre, "Bacon crujiente")
        self.assertEqual(float(linked.precio), 2.25)
        self.assertEqual(linked.grupo.max_selecciones, 2)

    def test_browser_coordinates_resolve_delivery_zone_without_trusting_an_address(self):
        db.session.add(ZonaEntrega(
            nombre="Centro QA", precio_envio=Decimal("2.50"), activo=True,
            centro_lat=37.3891, centro_lng=-5.9845, radio_km=5,
        ))
        db.session.commit()
        inside = self.client.post("/api/check-address", json={"lat": 37.39, "lng": -5.98})
        self.assertEqual(inside.status_code, 200)
        self.assertTrue(inside.get_json()["ok"])
        self.assertEqual(inside.get_json()["zona"]["nombre"], "Centro QA")
        outside = self.client.post("/api/check-address", json={"lat": 40.4168, "lng": -3.7038})
        self.assertFalse(outside.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
