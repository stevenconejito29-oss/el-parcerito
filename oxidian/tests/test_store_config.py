import unittest
from unittest.mock import patch

from store_config import get_store_profile


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


if __name__ == "__main__":
    unittest.main()
