"""Tests de helpers de config que reemplazan hardcodes en services.py.

Antes: umbrales de salud del bot, timeout de consulta, backoff de reintentos
y margen del bbox de geocoding vivían como números mágicos en el código.
Ahora se leen de SiteConfig con cap defensivo para poder ajustarlos sin
redeploy.
"""
import unittest

from flask import Flask

from extensions import db
from models import SiteConfig
import services


class ConfigHelpersHardcodeTest(unittest.TestCase):
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

    # ── defaults ──
    def test_defaults_sin_siteconfig(self):
        self.assertEqual(services.bot_health_errors_24h_max(), 200)
        self.assertEqual(services.bot_health_handoffs_undelivered_max(), 50)
        self.assertAlmostEqual(services.bot_status_check_timeout_sec(), 2.5)
        self.assertEqual(services.notification_retry_backoff_max_min(), 60)
        self.assertAlmostEqual(services.geocode_bbox_margin(), 1.5)

    # ── override desde SiteConfig ──
    def test_lee_de_siteconfig(self):
        SiteConfig.set("BOT_HEALTH_ERRORS_24H_MAX", "500")
        SiteConfig.set("BOT_HEALTH_HANDOFFS_UNDELIVERED_MAX", "10")
        SiteConfig.set("BOT_STATUS_CHECK_TIMEOUT_SEC", "1.0")
        SiteConfig.set("NOTIFICATION_RETRY_BACKOFF_MAX_MIN", "120")
        SiteConfig.set("GEOCODE_BBOX_MARGIN", "2.0")
        db.session.commit()
        self.assertEqual(services.bot_health_errors_24h_max(), 500)
        self.assertEqual(services.bot_health_handoffs_undelivered_max(), 10)
        self.assertAlmostEqual(services.bot_status_check_timeout_sec(), 1.0)
        self.assertEqual(services.notification_retry_backoff_max_min(), 120)
        self.assertAlmostEqual(services.geocode_bbox_margin(), 2.0)

    # ── caps defensivos superior ──
    def test_cap_superior_absurdo(self):
        SiteConfig.set("BOT_HEALTH_ERRORS_24H_MAX", "999999")
        SiteConfig.set("BOT_STATUS_CHECK_TIMEOUT_SEC", "9999")
        SiteConfig.set("NOTIFICATION_RETRY_BACKOFF_MAX_MIN", "999999")
        SiteConfig.set("GEOCODE_BBOX_MARGIN", "999")
        db.session.commit()
        self.assertEqual(services.bot_health_errors_24h_max(), 10000)
        self.assertAlmostEqual(services.bot_status_check_timeout_sec(), 30.0)
        self.assertEqual(services.notification_retry_backoff_max_min(), 1440)
        self.assertAlmostEqual(services.geocode_bbox_margin(), 5.0)

    # ── caps defensivos inferior ──
    def test_cap_inferior_absurdo(self):
        SiteConfig.set("BOT_HEALTH_ERRORS_24H_MAX", "0")
        SiteConfig.set("BOT_STATUS_CHECK_TIMEOUT_SEC", "0")
        SiteConfig.set("GEOCODE_BBOX_MARGIN", "0")
        db.session.commit()
        self.assertEqual(services.bot_health_errors_24h_max(), 10)
        self.assertAlmostEqual(services.bot_status_check_timeout_sec(), 0.5)
        self.assertAlmostEqual(services.geocode_bbox_margin(), 1.0)

    # ── fallback ante basura ──
    def test_fallback_ante_string_no_numerico(self):
        SiteConfig.set("BOT_HEALTH_ERRORS_24H_MAX", "no-es-num")
        SiteConfig.set("BOT_STATUS_CHECK_TIMEOUT_SEC", "xyz")
        SiteConfig.set("GEOCODE_BBOX_MARGIN", "abc")
        db.session.commit()
        self.assertEqual(services.bot_health_errors_24h_max(), 200)
        self.assertAlmostEqual(services.bot_status_check_timeout_sec(), 2.5)
        self.assertAlmostEqual(services.geocode_bbox_margin(), 1.5)

    def test_cfg_float_acepta_coma_decimal(self):
        SiteConfig.set("BOT_STATUS_CHECK_TIMEOUT_SEC", "1,25")
        db.session.commit()
        self.assertAlmostEqual(services.bot_status_check_timeout_sec(), 1.25)


if __name__ == "__main__":
    unittest.main()
