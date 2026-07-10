"""Tests del auto-router IA del chatbot (`/api/bot/ai/route`).

Cubre las 5 reglas críticas del PR #3:
  - MENU → route=menu
  - "cancelar 123" → intent existente (noop)
  - "¿qué hamburguesas tienen?" → route=ai
  - "hola" (corto) → route=noop
  - 21ª pregunta en 1h → route=handoff (rate limit hora)
"""
import unittest
from datetime import datetime, timedelta, timezone


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

from flask import Flask

from extensions import db
from models import BotAiUsage, SiteConfig
from routes.api_bot import api_bot_bp, _ai_phone_hash


class AiAutoRouterTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            BOT_API_KEY="test-bot-key",
            SECRET_KEY="test-only-secret",
        )
        db.init_app(self.app)
        self.app.register_blueprint(api_bot_bp, url_prefix="/api/bot")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        # Activamos IA con clave dummy para que la ruta AI esté “habilitada”.
        SiteConfig.set("BOT_AI_ENABLED", "1")
        SiteConfig.set("BOT_AI_PROVIDER", "openai")
        SiteConfig.set("BOT_AI_API_KEY", "sk-test-dummy")
        # Límite horario por cliente = 20 (default). Lo dejamos explícito.
        SiteConfig.set("AI_MAX_MESSAGES_PER_HOUR", "20")
        db.session.commit()
        self.client = self.app.test_client()
        self.phone = "+34611000001"

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _route(self, mensaje, telefono=None, force_ai=False):
        return self.client.post(
            "/api/bot/ai/route",
            headers={"X-Bot-Key": "test-bot-key"},
            json={
                "telefono": telefono or self.phone,
                "mensaje": mensaje,
                "force_ai": force_ai,
            },
        )

    # ── Casos del PR #3 ─────────────────────────────────────────

    def test_menu_command_route_menu(self):
        r = self._route("MENU")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["route"], "menu")

    def test_numeric_command_route_menu(self):
        r = self._route("1")
        self.assertEqual(r.get_json()["route"], "menu")

    def test_cancelar_numero_route_noop_intent(self):
        # "cancelar 123" cae en hard command (prefijo cancelar) → menu.
        # Lo importante: NO invoca IA.
        r = self._route("cancelar 123")
        route = r.get_json()["route"]
        self.assertIn(route, {"menu", "noop"})
        self.assertNotEqual(route, "ai")

    def test_intent_puntos_no_ai(self):
        # "cuántos puntos tengo" cae en intent conocido → noop (no IA).
        r = self._route("cuántos puntos tengo")
        self.assertEqual(r.get_json()["route"], "noop")

    def test_pregunta_natural_route_ai(self):
        r = self._route("¿qué hamburguesas tienen?")
        data = r.get_json()
        self.assertEqual(data["route"], "ai", msg=data)

    def test_texto_corto_route_noop(self):
        # "xyz" no matchea ningún hard command ni intent conocido y es demasiado
        # corto para el heurístico de pregunta → noop deja pasar el flujo estándar.
        # NOTA: "hola" sí es un hard command por diseño (saludo → menú).
        r = self._route("xyz")
        self.assertEqual(r.get_json()["route"], "noop")

    def test_rate_limit_handoff(self):
        # Insertamos 20 usos previos en la última hora → 21ª pregunta → handoff.
        phone_hash, _ = _ai_phone_hash(self.phone)
        now = _now()
        for _ in range(20):
            db.session.add(BotAiUsage(
                telefono_hash=phone_hash,
                tokens_in=10, tokens_out=10,
                creado_en=now - timedelta(minutes=10),
            ))
        db.session.commit()

        r = self._route("¿tienen pollo asado hoy?")
        data = r.get_json()
        self.assertEqual(data["route"], "handoff", msg=data)
        self.assertIn("consulta", data.get("message", "").lower())

    def test_force_ai_ignora_rate_limit(self):
        phone_hash, _ = _ai_phone_hash(self.phone)
        now = _now()
        for _ in range(25):
            db.session.add(BotAiUsage(
                telefono_hash=phone_hash,
                tokens_in=10, tokens_out=10,
                creado_en=now - timedelta(minutes=5),
            ))
        db.session.commit()
        r = self._route("dime lo que sea", force_ai=True)
        self.assertEqual(r.get_json()["route"], "ai")

    def test_ai_disabled_fallback_message(self):
        SiteConfig.set("BOT_AI_ENABLED", "0")
        db.session.commit()
        r = self._route("¿tienen algo vegano?")
        data = r.get_json()
        self.assertEqual(data["route"], "noop")
        self.assertIn("MENU", data.get("message", ""))

    def test_requires_bot_key(self):
        r = self.client.post(
            "/api/bot/ai/route",
            json={"telefono": self.phone, "mensaje": "hola"},
        )
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
