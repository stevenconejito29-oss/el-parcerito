import unittest
from unittest.mock import patch

from store_config import (
    get_loyalty_terms,
    get_public_store_url,
    get_service_commission,
    get_store_profile,
)


class StoreConfigTest(unittest.TestCase):
    def test_nonfinite_commission_is_rejected_by_editor_and_legacy_value_is_safe(self):
        from routes.superadmin import _validar_config_value
        for value in ("NaN", "Infinity", "-Infinity"):
            self.assertFalse(_validar_config_value("SERVICE_COMMISSION_PCT", value)[0])
            values = {"MODO_TIENDA": "bar_servicio", "SERVICE_COMMISSION_PCT": value}
            with patch("models.SiteConfig.get", side_effect=lambda key, default="": values.get(key, default)):
                result = get_service_commission("80")
            self.assertEqual(result["amount"], 0)
            self.assertEqual(result["merchant_net"], 80)

    def test_profile_uses_site_config_as_authority(self):
        values = {
            "NOMBRE_NEGOCIO": "Tienda configurable",
            "CIUDAD_NEGOCIO": "Medellín",
            "BIZUM_TELEFONO": "+34111222333",
            "BIZUM_HABILITADO": "0",
            "EFECTIVO_HABILITADO": "1",
        }

        with patch("models.SiteConfig.get", side_effect=lambda key, default="": values.get(key, default)):
            profile = get_store_profile()

        self.assertEqual(profile["nombre"], "Tienda configurable")
        self.assertEqual(profile["ciudad"], "Medellín")
        self.assertEqual(profile["bizum_telefono"], "+34111222333")
        self.assertFalse(profile["bizum_habilitado"])
        self.assertTrue(profile["efectivo_habilitado"])
        self.assertIn("cabecera_fondo", profile["theme"])
        self.assertIn("header_cart_action", profile["ui"])
        self.assertIn("menu_catalog_title", profile["ui"])
        self.assertIn("cart_memory_note", profile["ui"])
        self.assertIn("footer_heritage", profile["ui"])
        self.assertIn("hero_title", profile["ui"])
        self.assertIn("loyalty_unit_plural", profile["ui"])
        self.assertIn("cart_name", profile["ui"])
        self.assertIn("checkout_title", profile["ui"])
        self.assertIn("order_success_title", profile["ui"])

    def test_public_theme_and_copy_are_read_from_site_config(self):
        values = {
            "COLOR_CABECERA_FONDO": "#123456",
            "UI_HEADER_CART_ACTION": "Abrir mi compra",
            "UI_PWA_INAPP_INSTRUCTION": "Abre la tienda en tu navegador principal.",
        }
        with patch("models.SiteConfig.get", side_effect=lambda key, default="": values.get(key, default)):
            profile = get_store_profile()

        self.assertEqual(profile["theme"]["cabecera_fondo"], "#123456")
        self.assertEqual(profile["ui"]["header_cart_action"], "Abrir mi compra")
        self.assertEqual(
            profile["ui"]["pwa_inapp_instruction"],
            "Abre la tienda en tu navegador principal.",
        )

    def test_loyalty_terms_are_customer_facing_and_configurable(self):
        values = {
            "UI_LOYALTY_NAME": "Círculo del Cafetal",
            "UI_LOYALTY_NAV_LABEL": "Círculo",
            "UI_LOYALTY_UNIT": "grano",
            "UI_LOYALTY_UNIT_PLURAL": "granos",
            "UI_LOYALTY_TAGLINE": "Suma granos y canjéa productos.",
            "UI_LOYALTY_ICON": "estrella",
            "UI_LOYALTY_EMOJI": "⭐",
        }
        with patch("models.SiteConfig.get", side_effect=lambda key, default="": values.get(key, default)):
            terms = get_loyalty_terms()

        self.assertEqual(terms["name"], "Círculo del Cafetal")
        self.assertEqual(terms["nav_label"], "Círculo")
        self.assertEqual(terms["singular"], "grano")
        self.assertEqual(terms["plural"], "granos")
        self.assertEqual(terms["tagline"], "Suma granos y canjéa productos.")
        self.assertEqual(terms["icon"], "estrella")
        self.assertEqual(terms["emoji"], "⭐")

    def test_loyalty_icon_falls_back_when_unknown(self):
        from store_config import normalize_loyalty_icon, normalize_loyalty_emoji
        self.assertEqual(normalize_loyalty_icon("inventado"), "grano")
        self.assertEqual(normalize_loyalty_icon("MEDALLA"), "medalla")
        self.assertEqual(normalize_loyalty_emoji(""), "☕")
        self.assertEqual(normalize_loyalty_emoji("🌟🌟🌟🌟🌟🌟🌟🌟🌟"), "🌟🌟🌟🌟🌟🌟🌟🌟")

    def test_service_commission_only_applies_in_service_mode(self):
        values = {
            "MODO_TIENDA": "bar_servicio",
            "SERVICE_COMMISSION_PCT": "12.5",
        }

        with patch("models.SiteConfig.get", side_effect=lambda key, default="": values.get(key, default)):
            fee = get_service_commission("80.00")

        self.assertEqual(str(fee["pct"]), "12.50")
        self.assertEqual(str(fee["amount"]), "10.00")
        self.assertEqual(str(fee["merchant_net"]), "70.00")

    def test_service_commission_zero_for_own_store(self):
        values = {
            "MODO_TIENDA": "propia",
            "SERVICE_COMMISSION_PCT": "50",
        }

        with patch("models.SiteConfig.get", side_effect=lambda key, default="": values.get(key, default)):
            fee = get_service_commission("80.00")

        self.assertEqual(str(fee["pct"]), "0.00")
        self.assertEqual(str(fee["amount"]), "0.00")
        self.assertEqual(str(fee["merchant_net"]), "80.00")

    def test_public_store_url_skips_private_config_on_public_request(self):
        values = {
            "TIENDA_URL": "http://192.168.1.41:5070",
            "OXIDIAN_PUBLIC_URL": "https://elparcerito.com",
        }

        with patch("models.SiteConfig.get", side_effect=lambda key, default="": values.get(key, default)):
            url = get_public_store_url("https://elparcerito.com/")

        self.assertEqual(url, "https://elparcerito.com")

    def test_public_store_url_never_prefers_private_over_public_config(self):
        values = {
            "TIENDA_URL": "http://localhost:5000",
            "OXIDIAN_PUBLIC_URL": "https://elparcerito.com",
        }
        with patch("models.SiteConfig.get", side_effect=lambda key, default="": values.get(key, default)):
            self.assertEqual(get_public_store_url("http://127.0.0.1:5000/"), "https://elparcerito.com")


if __name__ == "__main__":
    unittest.main()
