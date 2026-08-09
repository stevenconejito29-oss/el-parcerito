import unittest
from unittest.mock import Mock, patch

from flask import Flask

from commercial_insights_service import (
    answer_commercial_question,
    build_commercial_diagnostic,
)
from extensions import db
from models import Categoria, Product, SiteConfig
from routes.admin import _llamar_ia_analisis, _resumen_negocio_para_ia


class CommercialInsightsTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="commercial-insights-test",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context(); self.ctx.push()
        db.create_all()
        SiteConfig.set("AI_TARGET_MARGIN_PCT", "35")
        food = Categoria(nombre="Platos", activo=True)
        drinks = Categoria(nombre="Bebidas", activo=True)
        db.session.add_all([food, drinks]); db.session.flush()
        db.session.add_all([
            Product(nombre="Arepa", categoria_id=food.id, precio=8, precio_costo=6, activo=True),
            Product(nombre="Lulada", categoria_id=drinks.id, precio=4, precio_costo=1, activo=True),
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def test_detects_low_margin_and_builds_safe_combo_without_sales(self):
        diagnostic = build_commercial_diagnostic()
        self.assertEqual(diagnostic["products_analyzed"], 2)
        self.assertEqual(diagnostic["price_alerts"][0]["name"], "Arepa")
        self.assertEqual(len(diagnostic["combo_candidates"]), 1)
        self.assertFalse(diagnostic["has_demand_evidence"])
        self.assertIsNone(diagnostic["coupon"])
        self.assertEqual(diagnostic["campaign_candidates"][0]["name"], "Prueba de combo para elevar ticket")

    def test_local_answer_is_actionable_and_never_claims_to_apply_changes(self):
        answer = answer_commercial_question("Sugiere combo, precio y cupón")
        self.assertIn("Arepa", answer)
        self.assertIn("Lulada", answer)
        self.assertIn("margen", answer.lower())
        self.assertIn("no se aplicó ningún cambio", answer.lower())
        self.assertIn("evita inventar un umbral", answer.lower())

    @patch("requests.post")
    def test_claude_uses_messages_api_and_real_catalog_context(self, post):
        SiteConfig.set("COMMERCIAL_AI_ENABLED", "1")
        SiteConfig.set("COMMERCIAL_AI_PROVIDER", "anthropic")
        SiteConfig.set("COMMERCIAL_AI_MODEL", "claude-test")
        SiteConfig.set("COMMERCIAL_AI_API_KEY", "sk-ant-test")
        db.session.commit()
        upstream = Mock(status_code=200)
        upstream.json.return_value = {
            "content": [{"type": "text", "text": "Propuesta comprobada"}]
        }
        post.return_value = upstream

        context = _resumen_negocio_para_ia()
        answer, error = _llamar_ia_analisis("Sugiere un combo", context)

        self.assertIsNone(error)
        self.assertEqual(answer, "Propuesta comprobada")
        self.assertEqual(post.call_args.args[0], "https://api.anthropic.com/v1/messages")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["system"].startswith("Eres un analista"), True)
        self.assertIn('"nombre": "Arepa"', sent["messages"][0]["content"])
        self.assertIn('"coste_eur": 6.0', sent["messages"][0]["content"])

    @patch("requests.post")
    def test_groq_is_internal_commercial_enrichment(self, post):
        SiteConfig.set("COMMERCIAL_AI_ENABLED", "1")
        SiteConfig.set("COMMERCIAL_AI_PROVIDER", "groq")
        SiteConfig.set("COMMERCIAL_AI_MODEL", "model-enabled-in-account")
        SiteConfig.set("COMMERCIAL_AI_API_KEY", "gsk-test")
        db.session.commit()
        upstream = Mock(status_code=200)
        upstream.json.return_value = {"choices": [{"message": {"content": "Memo financiero"}}]}
        post.return_value = upstream

        answer, error = _llamar_ia_analisis("Cómo mejorar margen", _resumen_negocio_para_ia())

        self.assertIsNone(error)
        self.assertEqual(answer, "Memo financiero")
        self.assertEqual(post.call_args.args[0], "https://api.groq.com/openai/v1/chat/completions")
        system = post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertIn("memo para dirección", system)
        self.assertIn("no texto publicitario para clientes", system)


if __name__ == "__main__":
    unittest.main()
