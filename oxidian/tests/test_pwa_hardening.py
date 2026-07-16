import unittest
import os
from pathlib import Path
from unittest.mock import patch

from routes.push import _validate_subscription
from push_service import _prepare_vapid_private_key, _vapid_subject


ROOT = Path(__file__).resolve().parents[1]


class PushSubscriptionValidationTest(unittest.TestCase):
    def test_accepts_standard_https_push_subscription(self):
        self.assertIsNone(_validate_subscription(
            "https://fcm.googleapis.com/fcm/send/example-token",
            "AbCdEf_0123456789-xyz",
            "auth_key-123",
        ))

    def test_rejects_non_https_and_private_destinations(self):
        for endpoint in (
            "http://push.example.test/subscription",
            "https://localhost/push",
            "https://127.0.0.1/push",
            "https://192.168.1.10/push",
            "https://[::1]/push",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertIsNotNone(_validate_subscription(endpoint, "abc_123", "auth-123"))

    def test_rejects_invalid_or_oversized_keys(self):
        endpoint = "https://push.example.test/subscription"
        self.assertIsNotNone(_validate_subscription(endpoint, "not base64!", "auth-123"))
        self.assertIsNotNone(_validate_subscription(endpoint, "a" * 513, "auth-123"))

    def test_existing_pem_vapid_key_is_loaded_as_a_vapid_object(self):
        from py_vapid import Vapid

        generated = Vapid()
        generated.generate_keys()
        prepared = _prepare_vapid_private_key(generated.private_pem().decode("ascii"))
        self.assertTrue(hasattr(prepared, "sign"))

    def test_raw_vapid_key_remains_compatible(self):
        self.assertEqual(_prepare_vapid_private_key("abc_DEF-123"), "abc_DEF-123")

    def test_vapid_subject_uses_public_https_url_without_real_email(self):
        with patch("store_config.get_store_value", return_value=""), patch.dict(
            os.environ, {"OXIDIAN_PUBLIC_URL": "https://tienda.example.com/app"}, clear=False,
        ):
            self.assertEqual(_vapid_subject(), "https://tienda.example.com")

    def test_vapid_subject_rejects_placeholder_email_and_insecure_url(self):
        def value(key):
            return "admin@example.invalid" if key == "EMAIL_CONTACTO" else ""

        with patch("store_config.get_store_value", side_effect=value), patch.dict(
            os.environ, {"OXIDIAN_PUBLIC_URL": "http://localhost:5000"}, clear=False,
        ):
            self.assertIsNone(_vapid_subject())


class PwaArchitectureContractTest(unittest.TestCase):
    def test_public_and_staff_use_one_shared_manager(self):
        public = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        staff = (ROOT / "templates" / "admin_base.html").read_text(encoding="utf-8")
        for template in (public, staff):
            self.assertEqual(template.count("js/pwa-manager.js"), 1)
            self.assertNotIn("_urlB64ToUint8Array", template)
            self.assertNotIn("navigator.serviceWorker.register('/sw.js'", template)
        self.assertIn('name="ox-pwa-operational"', staff)
        manager = (ROOT / "static" / "js" / "pwa-manager.js").read_text(encoding="utf-8")
        self.assertIn("navigator.wakeLock.request('screen')", manager)

    def test_service_worker_uses_safe_bounded_runtime_cache(self):
        worker = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
        self.assertIn('const CACHE_MEDIA = "ox-media-v52"', worker)
        self.assertIn("trimCache(cache, 80)", worker)
        self.assertIn('type: "OX_PUSH_RECEIVED"', worker)
        self.assertIn('badge  = "/static/pwa-badge-96.png"', worker)
        self.assertNotIn("self.skipWaiting();\n});\n\n// ── ACTIVATE", worker)

    def test_default_brand_asset_is_an_empanada(self):
        icon = (ROOT / "static" / "pwa-icon.svg").read_text(encoding="utf-8")
        self.assertIn("<title id=\"title\">Empanada</title>", icon)
        self.assertTrue((ROOT / "static" / "pwa-icon-512-maskable.png").is_file())
        self.assertTrue((ROOT / "static" / "pwa-badge-96.png").is_file())


if __name__ == "__main__":
    unittest.main()
