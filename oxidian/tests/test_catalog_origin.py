import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, session

from routes.public import (
    _descontar_stock_en_origen,
    _metadata_item_con_origen,
    _normalizar_origen,
    _fulfillment_options,
    _product_fulfillment_modes,
    _producto_disponible_en_origen,
    _save_carrito,
    _set_carrito_origen,
    _variantes_catalogo_unificadas,
    public_bp,
)


class OriginAwareProduct:
    activo = True
    visible_ahora = True
    es_combo = False

    def __init__(self, allowed_origin):
        self.allowed_origin = allowed_origin
        self.calls = []

    def pertenece_a_origen(self, origen):
        self.calls.append(("pertenece", origen))
        return origen == self.allowed_origin

    def disponible_para_venta_en_origen(self, origen, cantidad=1):
        self.calls.append(("disponible", origen, cantidad))
        return origen == self.allowed_origin and cantidad <= 3


class CatalogOriginTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test-only"

    def test_normalizes_only_supported_origin_keys(self):
        self.assertEqual(_normalizar_origen(" PROPIO "), "propio")
        self.assertIsNone(_normalizar_origen("proveedor:007"))
        self.assertIsNone(_normalizar_origen("proveedor:x"))
        self.assertIsNone(_normalizar_origen("otro:7"))

    def test_product_availability_uses_requested_origin_without_substitution(self):
        product = OriginAwareProduct("propio")

        self.assertTrue(_producto_disponible_en_origen(product, "propio", 2))
        self.assertFalse(_producto_disponible_en_origen(product, "proveedor:7", 2))
        self.assertIn(("disponible", "propio", 2), product.calls)

    def test_legacy_catalog_helper_never_replaces_or_collapses_products(self):
        products = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        selected = _variantes_catalogo_unificadas(
            products,
            origen_preferido="propio",
        )
        self.assertEqual([product.id for product in selected], [1, 2])

    def test_cart_origin_survives_items_and_is_cleared_with_empty_cart(self):
        with self.app.test_request_context("/"):
            _set_carrito_origen("propio")
            _save_carrito({"12": 1})
            self.assertEqual(session["carrito_origen"], "propio")

            session["cart_puntos"] = {"origen": "propio"}
            session["cart_producto_canje_id"] = 9
            _save_carrito({})

            self.assertNotIn("carrito_origen", session)
            self.assertNotIn("cart_puntos", session)
            self.assertNotIn("cart_producto_canje_id", session)

    def test_invalid_origin_clears_reward_state(self):
        with self.app.test_request_context("/"):
            session["carrito_origen"] = "propio"
            session["cart_puntos"] = {"origen": "propio", "verificado": True}
            session["cart_producto_canje_id"] = 9

            _set_carrito_origen(None)

            self.assertNotIn("carrito_origen", session)
            self.assertNotIn("cart_puntos", session)
            self.assertNotIn("cart_producto_canje_id", session)

    def test_public_blueprint_keeps_legacy_bar_route_as_redirect(self):
        self.app.register_blueprint(public_bp)
        rules = {rule.rule for rule in self.app.url_map.iter_rules()}
        self.assertIn("/bar/<int:proveedor_id>", rules)

    def test_metadata_fallback_freezes_requested_origin(self):
        with self.app.test_request_context("/"):
            with patch(
                "routes.public.metadata_item_pedido",
                return_value={"producto": {"id": 4}},
            ):
                data = _metadata_item_con_origen(
                    SimpleNamespace(id=4),
                    {"flujo": "web"},
                    "propio",
                )

        self.assertEqual(data["producto"]["origen_operativo_key"], "propio")
        self.assertIsNone(data["producto"]["proveedor_despachador_id"])

    def test_combo_stock_discount_keeps_origin_and_selection(self):
        calls = []

        class Combo:
            es_combo = True

            def descontar_stock_en_origen(
                self,
                origen,
                cantidad,
                seleccion_item_ids=None,
            ):
                calls.append((origen, cantidad, seleccion_item_ids))

        _descontar_stock_en_origen(Combo(), "propio", 2, [10, 11])
        self.assertEqual(calls, [("propio", 2, [10, 11])])

    def test_product_fulfillment_modes_are_explicit(self):
        self.assertEqual(_product_fulfillment_modes(SimpleNamespace(modalidad_entrega="delivery")), {"delivery"})
        self.assertEqual(_product_fulfillment_modes(SimpleNamespace(modalidad_entrega="recogida")), {"recogida"})
        self.assertEqual(_product_fulfillment_modes(SimpleNamespace(modalidad_entrega="ambas")), {"delivery", "recogida"})

    def test_cart_uses_intersection_of_store_and_product_modes(self):
        products = [SimpleNamespace(modalidad_entrega="ambas"), SimpleNamespace(modalidad_entrega="delivery")]
        with patch("routes.public.get_store_features", return_value={"delivery": True, "recogida": True}):
            self.assertEqual(_fulfillment_options(products), ["delivery"])
            incompatible = products + [SimpleNamespace(modalidad_entrega="recogida")]
            self.assertEqual(_fulfillment_options(incompatible), [])

        with patch("routes.public.get_store_features", return_value={"delivery": False, "recogida": True}):
            self.assertEqual(_fulfillment_options([SimpleNamespace(modalidad_entrega="ambas")]), ["recogida"])


if __name__ == "__main__":
    unittest.main()
