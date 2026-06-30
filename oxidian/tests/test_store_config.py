import unittest
from unittest.mock import patch

from store_config import get_service_commission, get_store_profile


class StoreConfigTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
