import unittest
from decimal import Decimal
from unittest.mock import patch

from flask import Flask, session
from werkzeug.datastructures import MultiDict

from extensions import db
from models import (
    ExtraCatalogItem,
    Product,
    ProductExtraGroup,
    ProductExtraOption,
    SiteConfig,
    Stock,
    User,
    ZonaEntrega,
)
from routes.admin import _sync_catalog_extras
from routes.admin import _parsear_campos_producto
from routes.public import (
    _build_items_from_carrito,
    _cart_compatibility,
    _parse_product_extras,
    _product_extras_payload,
    public_bp,
)
from routes.api_bot import api_bot_bp


class ProductExtrasWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-only",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            CART_MAX_QTY=20,
            SKIP_DELIVERY_VALIDATION=True,
            BOT_API_KEY="test-bot-key",
        )
        db.init_app(self.app)
        self.app.register_blueprint(public_bp)
        self.app.register_blueprint(api_bot_bp, url_prefix="/api/bot")
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

    def test_check_address_blocks_all_branches_when_delivery_is_disabled(self):
        SiteConfig.set("FEATURE_DELIVERY", "0")
        db.session.commit()

        by_coordinates = self.client.post("/api/check-address", json={"lat": 37.39, "lng": -5.98})
        by_text = self.client.post("/api/check-address", json={"direccion": "Calle Sierpes 1, Sevilla"})

        self.assertEqual(by_coordinates.status_code, 403)
        self.assertEqual(by_text.status_code, 403)

    def test_chatbot_coverage_uses_the_same_zone_resolver(self):
        zone = ZonaEntrega(
            nombre="Centro bot QA", precio_envio=Decimal("2.75"), activo=True,
            centro_lat=37.4736, centro_lng=-5.6438, radio_km=2,
        )
        db.session.add(zone)
        SiteConfig.set("VALIDAR_RADIO_ENTREGA", "1")
        db.session.commit()

        with patch("services.geocodificar_direccion", return_value=(37.474, -5.644)):
            response = self.client.get(
                "/api/bot/cobertura?direccion=Calle%20Real%201%2C%20Carmona",
                headers={"X-Bot-Key": "test-bot-key"},
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["cobertura"]["zona_id"], zone.id)
        self.assertEqual(payload["cobertura"]["zona_nombre"], "Centro bot QA")
        self.assertEqual(payload["metodo_cobertura"], "radio")

    def test_cart_does_not_offer_delivery_without_an_active_zone(self):
        SiteConfig.set("FEATURE_DELIVERY", "1")
        SiteConfig.set("FEATURE_RECOGIDA", "0")
        db.session.commit()

        compatibility = _cart_compatibility(
            [self.product], check_zone_availability=True,
        )

        self.assertFalse(compatibility["ok"])
        self.assertNotIn("delivery", compatibility["fulfillment_options"])
        self.assertEqual(
            compatibility["issues"][0]["code"],
            "delivery_no_active_zones",
        )

    def test_points_code_verification_does_not_enumerate_customers(self):
        customer = User(
            nombre="Cliente puntos",
            email="puntos@test.invalid",
            telefono="+34610009999",
            rol="cliente",
            activo=True,
            puntos=100,
        )
        customer.set_password("test-only-password")
        customer.generar_cod_puntos()
        db.session.add(customer)
        db.session.commit()

        unknown = self.client.post(
            "/puntos/verificar-codigo",
            json={"telefono": "+34619999999", "codigo": "000000"},
        ).get_json()
        wrong = self.client.post(
            "/puntos/verificar-codigo",
            json={"telefono": customer.telefono, "codigo": "000000"},
        ).get_json()

        self.assertFalse(unknown["ok"])
        self.assertFalse(wrong["ok"])
        self.assertEqual(unknown["msg"], wrong["msg"])
        self.assertNotIn("Cliente no encontrado", unknown["msg"])

    def test_consultar_saldo_bot_no_genera_ni_expone_otp(self):
        customer = User(
            nombre="Cliente saldo",
            email="saldo@test.invalid",
            telefono="+34610008888",
            rol="cliente",
            activo=True,
            puntos=250,
        )
        customer.set_password("test-only-password")
        db.session.add(customer)
        db.session.commit()

        response = self.client.get(
            "/api/bot/puntos?telefono=34610008888",
            headers={"X-Bot-Key": "test-bot-key"},
        )
        payload = response.get_json()
        db.session.refresh(customer)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["puntos"], 250)
        self.assertNotIn("codigo_verificacion", payload)
        self.assertIsNone(customer.cod_puntos)

    def test_retail_product_import_defaults_to_warehouse_channel(self):
        SiteConfig.set("TIPO_TIENDA", "producto")
        db.session.commit()

        fields, error = _parsear_campos_producto(MultiDict({
            "nombre": "Camiseta sin canal explícito",
            "precio": "19.90",
            "modalidad_entrega": "ambas",
            "tipo_entrega": "inmediato",
        }))

        self.assertIsNone(error)
        self.assertEqual(fields["canal_preparacion"], "almacen")


if __name__ == "__main__":
    unittest.main()
