"""Tests del push de refresh al bot cuando cambia SiteConfig.

Cubre:
- `store_config.alguna_clave_refresca_bot`: whitelist estricta.
- `services.refrescar_bot_si_claves_relevantes`: dispara `notificar_bot_sync`
  solo si alguna clave está en la whitelist; no lanza aunque la llamada falle.

No cubre la ruta HTTP end-to-end porque los blueprints ya están cubiertos
por el suite existente; aquí verificamos el contrato del helper.
"""
import unittest
from unittest.mock import patch

from flask import Flask

from extensions import db
from store_config import (
    CLAVES_QUE_REFRESCAN_BOT,
    alguna_clave_refresca_bot,
)


class BotConfigRefreshTest(unittest.TestCase):
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

    # ── whitelist ────────────────────────────────────────────────────

    def test_whitelist_incluye_modo_tienda(self):
        self.assertIn("MODO_TIENDA", CLAVES_QUE_REFRESCAN_BOT)

    def test_whitelist_incluye_features(self):
        for k in ("FEATURE_DELIVERY", "FEATURE_RECOGIDA",
                  "FEATURE_PEDIDOS_PROGRAMADOS", "FEATURE_PUNTOS"):
            self.assertIn(k, CLAVES_QUE_REFRESCAN_BOT)

    def test_whitelist_incluye_horario(self):
        for k in ("HORARIO_APERTURA", "HORARIO_CIERRE",
                  "TIENDA_FORZAR_CERRADA"):
            self.assertIn(k, CLAVES_QUE_REFRESCAN_BOT)

    def test_whitelist_incluye_limites_de_flujo(self):
        for key in (
            "BOT_REPORT_RATE_MAX", "BOT_HANDOFF_SLA_WARNING_SEC",
            "BOT_HANDOFF_QUEUE_MAX_SEC", "BOT_HANDOFF_INACTIVITY_SEC",
        ):
            self.assertIn(key, CLAVES_QUE_REFRESCAN_BOT)

    def test_whitelist_no_incluye_defaults_tecnicos(self):
        # Claves de fachada UI o defaults técnicos NO deben pushear al bot
        # cada vez que un admin las toque — sync pasivo de 10 min es suficiente.
        for k in ("COLOR_FONDO_APP", "STOCK_ALERTA_DIAS_DEFAULT",
                  "MAX_PEDIDOS_POR_PREPARADOR"):
            self.assertNotIn(k, CLAVES_QUE_REFRESCAN_BOT)

    # ── alguna_clave_refresca_bot ────────────────────────────────────

    def test_alguna_true_si_incluye_una(self):
        self.assertTrue(alguna_clave_refresca_bot(["NOMBRE_NEGOCIO", "COLOR_FONDO_APP"]))

    def test_alguna_false_si_ninguna(self):
        self.assertFalse(alguna_clave_refresca_bot(["COLOR_FONDO_APP", "UI_HEADER_MODE_BOTH"]))

    def test_alguna_false_para_vacio(self):
        self.assertFalse(alguna_clave_refresca_bot([]))
        self.assertFalse(alguna_clave_refresca_bot(None))

    # ── refrescar_bot_si_claves_relevantes ────────────────────────────

    def test_refrescar_dispara_para_clave_relevante(self):
        from services import refrescar_bot_si_claves_relevantes
        with patch("services.notificar_bot_sync") as mock_sync:
            disparo = refrescar_bot_si_claves_relevantes(["MODO_TIENDA"])
        self.assertTrue(disparo)
        mock_sync.assert_called_once()

    def test_refrescar_no_dispara_para_clave_irrelevante(self):
        from services import refrescar_bot_si_claves_relevantes
        with patch("services.notificar_bot_sync") as mock_sync:
            disparo = refrescar_bot_si_claves_relevantes(["COLOR_FONDO_APP"])
        self.assertFalse(disparo)
        mock_sync.assert_not_called()

    def test_refrescar_dispara_una_vez_por_conjunto(self):
        from services import refrescar_bot_si_claves_relevantes
        with patch("services.notificar_bot_sync") as mock_sync:
            refrescar_bot_si_claves_relevantes([
                "MODO_TIENDA", "FEATURE_DELIVERY", "COLOR_FONDO_APP",
            ])
        # Aunque haya varias claves relevantes, solo se hace UN sync (el
        # bot resincroniza toda su cache — no necesita saber qué claves
        # concretas cambiaron).
        self.assertEqual(mock_sync.call_count, 1)

    def test_refrescar_es_best_effort_ante_excepcion(self):
        from services import refrescar_bot_si_claves_relevantes
        with patch("services.notificar_bot_sync", side_effect=RuntimeError("bot down")):
            # NO debe propagar la excepción — devuelve False silenciosamente.
            disparo = refrescar_bot_si_claves_relevantes(["MODO_TIENDA"])
        self.assertFalse(disparo)


if __name__ == "__main__":
    unittest.main()
