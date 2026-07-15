"""Tests del validador de config en producción.

Antes: solo validaba presencia de SECRET_KEY, DATABASE_URL, BOT_API_KEY.
Ahora: rechaza defaults débiles ('change-me', 'local-dev', 'example',
'test'…), longitudes cortas y forces HTTPS coherente con cookies secure.

El objetivo: si el fundador arranca prod con `.env.example` sin editar,
la app aborta el boot en lugar de exponer keys de ejemplo al mundo.
"""
import os
import unittest
from unittest.mock import patch

from app import _validar_config_runtime


class _App:
    def __init__(self, **cfg):
        self.config = cfg


class ValidarConfigRuntimeTest(unittest.TestCase):

    def setUp(self):
        # Ambiente base con secrets fuertes y válidos.
        self._base_env = {
            "SECRET_KEY": "x" * 40,
            "DATABASE_URL": "postgresql://u:p@db/oxidian",
            "BOT_API_KEY": "A" * 32,
            "WEBHOOK_SECRET": "B" * 32,
            "EVOLUTION_API_KEY": "C" * 32,
            "BOT_PANEL_KEY": "D" * 32,
        }

    def _run(self, extra_env=None, extra_cfg=None):
        env = {**self._base_env, **(extra_env or {})}
        # clear=True: aislamos del env real del container para que un
        # BOT_PANEL_KEY corto del host no ensucie el test.
        app = _App(**env, **(extra_cfg or {}))
        with patch.dict(os.environ, env, clear=True):
            return _validar_config_runtime(app, "production")

    # ── casos válidos ──
    def test_config_valida_pasa(self):
        self._run()  # no debe lanzar

    def test_dev_environment_no_valida(self):
        # En dev/testing no aplica.
        app = _App(SECRET_KEY="")  # vacío intencional
        _validar_config_runtime(app, "development")

    # ── faltantes ──
    def test_secret_key_faltante_rechaza(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run(extra_env={"SECRET_KEY": ""})
        self.assertIn("no configuradas", str(ctx.exception))

    def test_webhook_secret_faltante_rechaza(self):
        with self.assertRaises(RuntimeError):
            self._run(extra_env={"WEBHOOK_SECRET": ""})

    # ── defaults débiles ──
    def test_bot_api_key_con_change_me_rechaza(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run(extra_env={"BOT_API_KEY": "change-me-real-secret-here"})
        self.assertIn("change-me", str(ctx.exception).lower())

    def test_webhook_secret_local_dev_rechaza(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run(extra_env={"WEBHOOK_SECRET": "local-dev-webhook-secret-yeah"})
        self.assertIn("local-dev", str(ctx.exception).lower())

    def test_evolution_api_key_example_rechaza(self):
        with self.assertRaises(RuntimeError):
            self._run(extra_env={"EVOLUTION_API_KEY": "example-evolution-key-xyz"})

    # ── longitud ──
    def test_secret_key_corta_rechaza(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run(extra_env={"SECRET_KEY": "short"})
        self.assertIn("32", str(ctx.exception))

    def test_bot_api_key_corta_rechaza(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run(extra_env={"BOT_API_KEY": "shorty"})
        self.assertIn("24", str(ctx.exception))

    # ── HTTPS vs cookie secure ──
    def test_https_url_sin_cookie_secure_rechaza(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run(
                extra_env={"OXIDIAN_PUBLIC_URL": "https://mi-tienda.com"},
                extra_cfg={"SESSION_COOKIE_SECURE": False},
            )
        self.assertIn("SESSION_COOKIE_SECURE", str(ctx.exception))

    def test_https_url_con_cookie_secure_pasa(self):
        self._run(
            extra_env={"OXIDIAN_PUBLIC_URL": "https://mi-tienda.com"},
            extra_cfg={"SESSION_COOKIE_SECURE": True},
        )


if __name__ == "__main__":
    unittest.main()
