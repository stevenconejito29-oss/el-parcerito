"""Tests del helper `consultar_estado_bot`.

Cubre:
- Best-effort ante red caída (timeout, 5xx, JSON inválido).
- Clasificación de salud según connected / errores_24h / handoffs.
- Guardrail cuando no hay BOT_API_URL / BOT_PANEL_KEY configurados.
"""
import unittest
from unittest.mock import patch, MagicMock

from flask import Flask

from extensions import db
from models import SiteConfig


class BotHealthTest(unittest.TestCase):
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
        SiteConfig.set("BOT_API_URL", "http://chat-test:3000", descripcion="test")
        SiteConfig.set("BOT_PANEL_KEY", "test-key", descripcion="test")
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk_response(self, status=200, json_body=None):
        m = MagicMock()
        m.ok = status < 400
        m.status_code = status
        m.content = b'{"ok": true}'
        m.json.return_value = json_body or {}
        return m

    # ── Sin configuración ────────────────────────────────────────────

    def test_devuelve_unknown_sin_bot_url(self):
        from services import consultar_estado_bot
        # Vaciamos la config
        SiteConfig.set("BOT_API_URL", "", descripcion="test")
        db.session.commit()
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("BOT_API_URL", None)
            r = consultar_estado_bot(timeout=1)
        self.assertEqual(r["salud"], "unknown")

    def test_devuelve_unknown_sin_panel_key(self):
        from services import consultar_estado_bot
        SiteConfig.set("BOT_PANEL_KEY", "", descripcion="test")
        SiteConfig.set("BOT_API_KEY", "", descripcion="test")
        db.session.commit()
        r = consultar_estado_bot(timeout=1)
        self.assertEqual(r["salud"], "unknown")

    # ── Red caída ────────────────────────────────────────────────────

    def test_devuelve_down_ante_timeout(self):
        from services import consultar_estado_bot
        with patch("services.requests.get", side_effect=Exception("timeout")):
            r = consultar_estado_bot(timeout=1)
        self.assertEqual(r["salud"], "down")
        self.assertFalse(r["connected"])

    def test_devuelve_down_ante_http_5xx(self):
        from services import consultar_estado_bot
        with patch("services.requests.get", return_value=self._mk_response(status=502)):
            r = consultar_estado_bot(timeout=1)
        self.assertEqual(r["salud"], "down")

    # ── Clasificación salud ──────────────────────────────────────────

    def test_up_cuando_connected_y_pocos_errores(self):
        from services import consultar_estado_bot
        body = {
            "ok": True,
            "connected": True,
            "evolution_state": "open",
            "errores_24h": 3,
            "sessions": {"client": 12, "admin": 2},
            "handoffs": {"pending": 1, "undelivered_messages": 0},
        }
        with patch("services.requests.get", return_value=self._mk_response(200, body)):
            r = consultar_estado_bot(timeout=1)
        self.assertEqual(r["salud"], "up")
        self.assertTrue(r["connected"])
        self.assertEqual(r["evolution_state"], "open")

    def test_degraded_cuando_whatsapp_desconectado(self):
        from services import consultar_estado_bot
        body = {
            "ok": True,
            "connected": False,
            "evolution_state": "close",
            "errores_24h": 1,
            "sessions": {"client": 0},
            "handoffs": {"pending": 0, "undelivered_messages": 0},
        }
        with patch("services.requests.get", return_value=self._mk_response(200, body)):
            r = consultar_estado_bot(timeout=1)
        self.assertEqual(r["salud"], "degraded")
        self.assertFalse(r["connected"])

    def test_degraded_cuando_muchos_errores_24h(self):
        from services import consultar_estado_bot
        body = {
            "ok": True,
            "connected": True,
            "evolution_state": "open",
            "errores_24h": 500,
            "sessions": {"client": 0},
            "handoffs": {"pending": 0, "undelivered_messages": 0},
        }
        with patch("services.requests.get", return_value=self._mk_response(200, body)):
            r = consultar_estado_bot(timeout=1)
        self.assertEqual(r["salud"], "degraded")

    def test_degraded_cuando_muchos_handoffs_sin_entregar(self):
        from services import consultar_estado_bot
        body = {
            "ok": True,
            "connected": True,
            "evolution_state": "open",
            "errores_24h": 5,
            "sessions": {"client": 3},
            "handoffs": {"pending": 0, "undelivered_messages": 80},
        }
        with patch("services.requests.get", return_value=self._mk_response(200, body)):
            r = consultar_estado_bot(timeout=1)
        self.assertEqual(r["salud"], "degraded")

    def test_incluye_latency_ms_cuando_hay_respuesta(self):
        from services import consultar_estado_bot
        body = {
            "ok": True,
            "connected": True,
            "evolution_state": "open",
            "errores_24h": 0,
            "sessions": {"client": 0},
            "handoffs": {"pending": 0, "undelivered_messages": 0},
        }
        with patch("services.requests.get", return_value=self._mk_response(200, body)):
            r = consultar_estado_bot(timeout=1)
        self.assertIsInstance(r["latency_ms"], int)
        self.assertGreaterEqual(r["latency_ms"], 0)


if __name__ == "__main__":
    unittest.main()
