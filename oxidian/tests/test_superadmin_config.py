import unittest
from unittest.mock import patch

from flask import Flask
from flask_login import LoginManager, UserMixin
from werkzeug.datastructures import MultiDict

from routes.superadmin import (
    _config_section_submission,
    _validar_config_value,
    superadmin_bp,
)


class _SuperAdmin(UserMixin):
    id = 1
    rol = "super_admin"


class SuperadminConfigTest(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config.update(SECRET_KEY="test-only", TESTING=True)
        login_manager = LoginManager(app)
        login_manager.user_loader(lambda _user_id: _SuperAdmin())
        app.register_blueprint(superadmin_bp, url_prefix="/superadmin")
        self.app = app
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = "1"
            session["_fresh"] = True

    def test_section_parser_only_accepts_declared_present_fields(self):
        form = MultiDict([
            ("section", "tienda-colores"),
            ("config_key", "COLOR_PRIMARIO"),
            ("COLOR_PRIMARIO", "#ce1126"),
        ])

        section, changes, errors = _config_section_submission(form)

        self.assertEqual(section, "tienda-colores")
        self.assertEqual(changes, [("COLOR_PRIMARIO", "#CE1126")])
        self.assertEqual(errors, [])

        foreign_form = MultiDict([
            ("section", "tienda-colores"),
            ("config_key", "TELEFONO_NEGOCIO"),
            ("TELEFONO_NEGOCIO", "+34 600 000 000"),
        ])
        _, changes, errors = _config_section_submission(foreign_form)
        self.assertEqual(changes, [])
        self.assertIn("no pertenece", errors[0])

    @patch("routes.superadmin.AuditLog.registrar")
    @patch("routes.superadmin.db.session.commit")
    @patch("routes.superadmin.SiteConfig.set")
    @patch("routes.superadmin.SiteConfig.get")
    def test_posting_one_field_does_not_rewrite_other_settings(
        self, config_get, config_set, commit, _audit
    ):
        config_get.side_effect = lambda key, default="": {
            "COLOR_PRIMARIO": "#000000",
            "COLOR_SECUNDARIO": "#111111",
            "COLOR_ACENTO": "#222222",
        }.get(key, default)

        response = self.client.post(
            "/superadmin/config/guardar-seccion",
            data=MultiDict([
                ("section", "tienda-colores"),
                ("config_key", "COLOR_PRIMARIO"),
                ("COLOR_PRIMARIO", "#abcdef"),
            ]),
        )

        self.assertEqual(response.status_code, 302)
        config_set.assert_called_once_with(
            "COLOR_PRIMARIO",
            "#ABCDEF",
            user_id=1,
            descripcion=None,
        )
        commit.assert_called_once()

    @patch("routes.superadmin.AuditLog.registrar")
    @patch("routes.superadmin.db.session.commit")
    @patch("routes.superadmin.SiteConfig.set")
    @patch("routes.superadmin.SiteConfig.get")
    def test_payment_toggle_uses_persisted_sibling_value(
        self, config_get, config_set, commit, _audit
    ):
        config_get.side_effect = lambda key, default="": {
            "EFECTIVO_HABILITADO": "1",
            "BIZUM_HABILITADO": "0",
        }.get(key, default)

        response = self.client.post(
            "/superadmin/config/guardar-seccion",
            data=MultiDict([
                ("section", "operacion-pagos"),
                ("config_key", "EFECTIVO_HABILITADO"),
                ("EFECTIVO_HABILITADO", "0"),
            ]),
        )

        self.assertEqual(response.status_code, 302)
        config_set.assert_not_called()
        commit.assert_not_called()

    @patch("routes.superadmin.AuditLog.registrar")
    @patch("routes.superadmin.db.session.commit")
    @patch("routes.superadmin.SiteConfig.set")
    @patch("routes.superadmin.SiteConfig.get", return_value=None)
    def test_explicit_default_is_created_in_site_config(
        self, _config_get, config_set, commit, _audit
    ):
        response = self.client.post(
            "/superadmin/config/guardar-seccion",
            data=MultiDict([
                ("section", "puntos"),
                ("config_key", "PUNTOS_POR_EURO"),
                ("PUNTOS_POR_EURO", "1"),
            ]),
        )

        self.assertEqual(response.status_code, 302)
        config_set.assert_called_once_with(
            "PUNTOS_POR_EURO",
            "1",
            user_id=1,
            descripcion="Puntos que gana el cliente por cada euro gastado",
        )
        commit.assert_called_once()

    def test_phone_time_color_url_and_toggle_validation(self):
        valid_cases = [
            ("TELEFONO_NEGOCIO", "+34 600 000 000", "34600000000"),
            ("HORARIO_APERTURA", "09:30", "09:30"),
            ("COLOR_PRIMARIO", "#ce1126", "#CE1126"),
            ("COLOR_CABECERA_FONDO", "#123abc", "#123ABC"),
            ("UI_HEADER_CART_ACTION", "Abrir compra", "Abrir compra"),
            ("HERO_IMAGE_URL", "https://cdn.example.com/hero.jpg", "https://cdn.example.com/hero.jpg"),
            ("BIZUM_HABILITADO", "0", "0"),
        ]
        for key, raw, expected in valid_cases:
            with self.subTest(key=key):
                ok, normalized_key, value, error = _validar_config_value(key, raw)
                self.assertTrue(ok)
                self.assertEqual(normalized_key, key)
                self.assertEqual(value, expected)
                self.assertIsNone(error)

        invalid_cases = [
            ("TELEFONO_NEGOCIO", "llama al 600000000"),
            ("HORARIO_CIERRE", "24:00"),
            ("COLOR_ACENTO", "#12345"),
            ("COLOR_TEXTO", "blue"),
            ("UI_HEADER_CART_ACTION", ""),
            ("LOGO_URL", "javascript:alert(1)"),
            ("EFECTIVO_HABILITADO", "yes"),
        ]
        for key, raw in invalid_cases:
            with self.subTest(key=key):
                ok, _, _, error = _validar_config_value(key, raw)
                self.assertFalse(ok)
                self.assertTrue(error)


if __name__ == "__main__":
    unittest.main()
