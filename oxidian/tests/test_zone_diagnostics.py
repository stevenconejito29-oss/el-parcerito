"""Diagnóstico + probador de zonas de entrega."""
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g

from extensions import db, login_manager
from models import SiteConfig, User, ZonaEntrega
from routes.superadmin import superadmin_bp, _diagnostico_zonas_payload


class ZoneDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__,
            template_folder=str(root / "templates"),
            static_folder=str(root / "static"),
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="zone-diag",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SESSION_PROTECTION=None,
        )
        db.init_app(self.app)
        login_manager.init_app(self.app)
        self.app.register_blueprint(superadmin_bp, url_prefix="/superadmin")

        @login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        # FEATURE_DELIVERY debe estar ON para no bloquear el módulo.
        SiteConfig.set("FEATURE_DELIVERY", "1")
        self.super = User(nombre="S", email="s@t.invalid", rol="super_admin")
        self.super.set_password("x")
        db.session.add(self.super)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        g.pop("_login_user", None)
        with self.client.session_transaction() as s:
            s.clear()
            s["_user_id"] = str(self.super.id)
            s["_fresh"] = True

    def _by(self, checks, clave):
        for c in checks:
            if c["clave"] == clave:
                return c
        self.fail(f"Check {clave} no encontrado")

    def test_diagnostico_marca_todo_en_danger_por_defecto(self):
        # Sin zonas, sin CENTRO_LAT/LON, sin legacy toggle → varias fallas.
        with self.app.test_request_context():
            payload = _diagnostico_zonas_payload()
        # El diagnostico agrega checks aunque no haya legacy activo.
        self.assertEqual(payload["resumen_severidad"], "danger")
        self.assertEqual(self._by(payload["checks"], "hay_zonas")["severidad"], "danger")
        self.assertEqual(self._by(payload["checks"], "geo_negocio")["severidad"], "danger")

    def test_diagnostico_marca_legacy_como_danger_si_activo(self):
        SiteConfig.set("ALLOW_LEGACY_ZONE_FALLBACK", "1")
        with self.app.test_request_context():
            payload = _diagnostico_zonas_payload()
        self.assertEqual(
            self._by(payload["checks"], "legacy_fallback")["severidad"], "danger",
        )

    def test_diagnostico_ok_cuando_todo_esta_configurado(self):
        SiteConfig.set("ALLOW_LEGACY_ZONE_FALLBACK", "0")
        SiteConfig.set("BLOQUEAR_DIRECCION_NO_VERIFICADA", "1")
        SiteConfig.set("CENTRO_LAT", "37.4736")
        SiteConfig.set("CENTRO_LON", "-5.6438")
        SiteConfig.set("RADIO_ENTREGA_KM", "5")
        z = ZonaEntrega(
            nombre="Centro", precio_envio=Decimal("2"),
            tiempo_estimado_min=25, es_epicentro=True, activo=True,
            centro_lat=37.4736, centro_lng=-5.6438, radio_km=3.0,
        )
        db.session.add(z)
        db.session.commit()
        with self.app.test_request_context():
            payload = _diagnostico_zonas_payload()
        self.assertEqual(payload["resumen_severidad"], "ok")

    def test_probar_direccion_sin_geocode_muestra_error(self):
        self._login()
        # Mockeamos geocodificar_direccion para que devuelva None
        # (simulando calle no encontrada).
        with patch("services.geocodificar_direccion", return_value=None):
            captured = {}

            def fake_render(template, **ctx):
                captured["template"] = template
                captured.update(ctx)
                return "OK"

            with patch("routes.superadmin.render_template", side_effect=fake_render):
                r = self.client.post("/superadmin/zonas/probar", data={
                    "direccion": "Calle No Existe 999",
                })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(captured["template"], "superadmin/zona_probar.html")
        self.assertFalse(captured["resultado"]["ok"])
        self.assertIsNone(captured["resultado"]["coords"])

    def test_probar_direccion_con_zona_matching(self):
        z = ZonaEntrega(
            nombre="Centro", precio_envio=Decimal("2"),
            tiempo_estimado_min=25, es_epicentro=True, activo=True,
            centro_lat=37.4736, centro_lng=-5.6438, radio_km=3.0,
        )
        db.session.add(z)
        db.session.commit()
        self._login()
        with patch("services.geocodificar_direccion",
                   return_value=(37.4740, -5.6440)):
            captured = {}

            def fake_render(template, **ctx):
                captured.update(ctx)
                return "OK"

            with patch("routes.superadmin.render_template", side_effect=fake_render):
                self.client.post("/superadmin/zonas/probar", data={
                    "direccion": "Calle San Pedro 15, Carmona",
                })
        self.assertTrue(captured["resultado"]["ok"])
        self.assertEqual(captured["resultado"]["zona"].nombre, "Centro")

    def test_probar_direccion_fuera_de_zona(self):
        z = ZonaEntrega(
            nombre="Centro", precio_envio=Decimal("2"),
            tiempo_estimado_min=25, es_epicentro=True, activo=True,
            centro_lat=37.4736, centro_lng=-5.6438, radio_km=1.0,  # pequeño
        )
        db.session.add(z)
        db.session.commit()
        self._login()
        # Coordenadas MUY lejos (Madrid)
        with patch("services.geocodificar_direccion",
                   return_value=(40.4168, -3.7038)):
            captured = {}

            def fake_render(template, **ctx):
                captured.update(ctx)
                return "OK"

            with patch("routes.superadmin.render_template", side_effect=fake_render):
                self.client.post("/superadmin/zonas/probar", data={
                    "direccion": "Puerta del Sol, Madrid",
                })
        self.assertFalse(captured["resultado"]["ok"])
        self.assertIsNotNone(captured["resultado"]["coords"])
        self.assertIsNone(captured["resultado"]["zona"])


if __name__ == "__main__":
    unittest.main()
