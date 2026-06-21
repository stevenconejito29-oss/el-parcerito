import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, session

from routes.public import (
    _descontar_stock_en_origen,
    _metadata_item_con_origen,
    _normalizar_origen,
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
        self.assertEqual(_normalizar_origen("proveedor:007"), "proveedor:7")
        self.assertIsNone(_normalizar_origen("proveedor:x"))
        self.assertIsNone(_normalizar_origen("otro:7"))

    def test_product_availability_uses_requested_origin_without_substitution(self):
        product = OriginAwareProduct("proveedor:7")

        self.assertTrue(_producto_disponible_en_origen(product, "proveedor:7", 2))
        self.assertFalse(_producto_disponible_en_origen(product, "propio", 2))
        self.assertIn(("disponible", "proveedor:7", 2), product.calls)

    def test_legacy_catalog_helper_never_replaces_or_collapses_products(self):
        products = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        selected = _variantes_catalogo_unificadas(
            products,
            origen_preferido="proveedor:7",
        )
        self.assertEqual([product.id for product in selected], [1, 2])

    def test_cart_origin_survives_items_and_is_cleared_with_empty_cart(self):
        with self.app.test_request_context("/"):
            _set_carrito_origen("proveedor:7")
            _save_carrito({"12": 1})
            self.assertEqual(session["carrito_origen"], "proveedor:7")

            session["cart_puntos"] = {"origen": "proveedor:7"}
            session["cart_producto_canje_id"] = 9
            _save_carrito({})

            self.assertNotIn("carrito_origen", session)
            self.assertNotIn("cart_puntos", session)
            self.assertNotIn("cart_producto_canje_id", session)

    def test_changing_origin_clears_reward_state(self):
        with self.app.test_request_context("/"):
            session["carrito_origen"] = "propio"
            session["cart_puntos"] = {"origen": "propio", "verificado": True}
            session["cart_producto_canje_id"] = 9

            _set_carrito_origen("proveedor:7")

            self.assertEqual(session["carrito_origen"], "proveedor:7")
            self.assertNotIn("cart_puntos", session)
            self.assertNotIn("cart_producto_canje_id", session)

    def test_public_blueprint_exposes_one_menu_per_bar(self):
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
                    "proveedor:7",
                )

        self.assertEqual(data["producto"]["origen_operativo_key"], "proveedor:7")
        self.assertEqual(data["producto"]["proveedor_despachador_id"], 7)

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

        _descontar_stock_en_origen(Combo(), "proveedor:7", 2, [10, 11])
        self.assertEqual(calls, [("proveedor:7", 2, [10, 11])])


if __name__ == "__main__":
    unittest.main()
