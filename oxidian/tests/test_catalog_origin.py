import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, session

from routes.public import (
    _cart_compatibility,
    _descontar_stock_en_origen,
    _metadata_item_con_origen,
    _normalizar_origen,
    _order_group,
    _order_group_label,
    _fulfillment_unavailable_reasons,
    _fulfillment_options,
    _product_fulfillment_badge,
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
    vertical = "comida"  # default; los tests que necesiten otro lo sobrescriben.

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

    def test_expired_scheduled_product_is_not_available(self):
        product = OriginAwareProduct("propio")
        product.tipo_entrega = "programado"
        product.modalidad_entrega = "ambas"
        product.vertical = "comida"
        product.fecha_llegada = date.today() - timedelta(days=1)

        with patch("routes.public.get_store_features", return_value={
            "delivery": True,
            "recogida": True,
            "pedidos_programados": True,
            "puntos": True,
        }):
            self.assertFalse(_producto_disponible_en_origen(product, "propio", 1))

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

    def test_fulfillment_badges_are_customer_facing(self):
        self.assertEqual(
            _product_fulfillment_badge(SimpleNamespace(modalidad_entrega="delivery"))["label"],
            "Envío a domicilio",
        )
        self.assertEqual(
            _product_fulfillment_badge(SimpleNamespace(modalidad_entrega="recogida"))["label"],
            "Recoger en local",
        )
        self.assertEqual(
            _product_fulfillment_badge(SimpleNamespace(modalidad_entrega="ambas"))["label"],
            "Llevar o recoger",
        )

    def test_fulfillment_unavailable_reasons_list_blocking_products(self):
        delivery_only = SimpleNamespace(nombre="Arepa delivery", modalidad_entrega="delivery")
        pickup_only = SimpleNamespace(nombre="Postre recogida", modalidad_entrega="recogida")

        with patch("routes.public.get_store_features", return_value={"delivery": True, "recogida": True}):
            reasons = _fulfillment_unavailable_reasons([delivery_only, pickup_only])

        self.assertIn("delivery", reasons)
        self.assertIn("recogida", reasons)
        self.assertEqual(reasons["delivery"]["products"], [pickup_only])
        self.assertEqual(reasons["recogida"]["products"], [delivery_only])

    def test_cart_compatibility_explains_mixed_fulfillment_modes(self):
        delivery_only = SimpleNamespace(
            nombre="Arepa delivery",
            modalidad_entrega="delivery",
            tipo_entrega="inmediato",
            grupo_pedido=None,
            vertical="comida",
        )
        pickup_only = SimpleNamespace(
            nombre="Postre recogida",
            modalidad_entrega="recogida",
            tipo_entrega="inmediato",
            grupo_pedido=None,
            vertical="comida",
        )

        with patch("routes.public.get_store_features", return_value={
            "delivery": True,
            "recogida": True,
            "pedidos_programados": True,
            "puntos": True,
        }):
            result = _cart_compatibility([delivery_only, pickup_only])

        self.assertFalse(result["ok"])
        self.assertEqual(result["issues"][0]["code"], "fulfillment_conflict")
        self.assertIn("solo con envío a domicilio", result["message"])
        self.assertIn("solo para recoger", result["message"])

    def test_cart_compatibility_respects_disabled_modules_and_programados(self):
        product = SimpleNamespace(
            nombre="Bandeja programada",
            modalidad_entrega="ambas",
            tipo_entrega="programado",
            grupo_pedido=None,
            vertical="comida",
        )

        with patch("routes.public.get_store_features", return_value={
            "delivery": False,
            "recogida": False,
            "pedidos_programados": False,
            "puntos": True,
        }):
            result = _cart_compatibility([product])

        codes = {issue["code"] for issue in result["issues"]}
        self.assertIn("programados_disabled", codes)
        self.assertIn("fulfillment_modules_disabled", codes)

    def test_cart_compatibility_reports_expired_scheduled_products(self):
        product = SimpleNamespace(
            nombre="Lechona sábado pasado",
            modalidad_entrega="ambas",
            tipo_entrega="programado",
            fecha_llegada=date.today() - timedelta(days=1),
            grupo_pedido=None,
            vertical="comida",
        )

        with patch("routes.public.get_store_features", return_value={
            "delivery": True,
            "recogida": True,
            "pedidos_programados": True,
            "puntos": True,
        }):
            result = _cart_compatibility([product])

        self.assertFalse(result["ok"])
        self.assertEqual(result["issues"][0]["code"], "programados_expired")
        self.assertIn("fecha programada", result["message"].lower())

    def test_cart_compatibility_blocks_products_from_other_vertical(self):
        product = SimpleNamespace(
            nombre="Camiseta",
            modalidad_entrega="ambas",
            tipo_entrega="inmediato",
            grupo_pedido=None,
            vertical="producto",
        )

        with patch("routes.public.get_store_features", return_value={
            "delivery": True,
            "recogida": True,
            "pedidos_programados": True,
            "puntos": True,
        }), patch("routes.public.SiteConfig.get", return_value="comida"):
            result = _cart_compatibility([product])

        self.assertFalse(result["ok"])
        self.assertEqual(result["issues"][0]["code"], "vertical")

    def test_order_groups_are_configurable_and_case_insensitive(self):
        general = SimpleNamespace(grupo_pedido=None)
        cold_a = SimpleNamespace(grupo_pedido="Cadena de frío")
        cold_b = SimpleNamespace(grupo_pedido="  CADENA   DE FRÍO ")
        hot = SimpleNamespace(grupo_pedido="Entrega caliente")

        self.assertEqual(_order_group(general), "__general__")
        self.assertEqual(_order_group(cold_a), _order_group(cold_b))
        self.assertNotEqual(_order_group(cold_a), _order_group(hot))
        self.assertEqual(_order_group_label(cold_b), "CADENA DE FRÍO")


if __name__ == "__main__":
    unittest.main()
