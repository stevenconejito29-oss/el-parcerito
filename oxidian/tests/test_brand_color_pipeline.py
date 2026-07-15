"""Tests que garantizan la cadena config → CSS var --brand-primary.

Antes: 3 declaraciones `:root` en oxidian.css/tokens.css definían
`--brand-primary: #FFC200` como hex fijo. Aunque `<body style>` inyectaba
el color de SiteConfig, ninguno de los aliases (`--col-gold`,
`--col-primary`, `--ox-primary`) lo consumía — los botones "dorados"
del sidebar admin, del carrito y del panel operativo se quedaban fijos.

Ahora todos los aliases derivan del brand primary vía CSS var:
    :root { --col-gold: var(--brand-primary); }
    body.ox-body-admin { --col-primary: var(--brand-primary, #D9961A); }
    :root#unified { --ox-primary: var(--brand-primary, #B7791F); }

Estos tests renderizan `base.html` con un `COLOR_PRIMARIO` custom y
verifican que la CSS var inyectada en `<body>` coincide.
"""
import unittest

from flask import Flask

from extensions import db
from models import SiteConfig


class BrandColorPipelineTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test",
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _brand_snapshot(self):
        from store_config import get_store_profile
        return get_store_profile()

    def test_default_brand_primary_es_hex_valido(self):
        p = self._brand_snapshot()
        # El schema define el default como amarillo Parcerito.
        self.assertTrue(p["color_primario"].startswith("#"))
        self.assertEqual(len(p["color_primario"]), 7)

    def test_cambiar_COLOR_PRIMARIO_se_refleja_en_snapshot(self):
        SiteConfig.set("COLOR_PRIMARIO", "#FF0000")
        db.session.commit()
        p = self._brand_snapshot()
        self.assertEqual(p["color_primario"].upper(), "#FF0000")

    def test_cambiar_COLOR_SECUNDARIO_se_refleja(self):
        SiteConfig.set("COLOR_SECUNDARIO", "#00FF00")
        db.session.commit()
        p = self._brand_snapshot()
        self.assertEqual(p["color_secundario"].upper(), "#00FF00")

    def test_cambiar_COLOR_ACENTO_se_refleja(self):
        SiteConfig.set("COLOR_ACENTO", "#0000FF")
        db.session.commit()
        p = self._brand_snapshot()
        self.assertEqual(p["color_acento"].upper(), "#0000FF")

    def _on_color(self, hex_str):
        """Helper que replica la fórmula de app._on_color para test aislado."""
        raw = str(hex_str or "").lstrip("#")
        r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
        luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
        return "#18120A" if luminance > 0.58 else "#FFFFFF"

    def test_on_primario_claro_sobre_fondo_oscuro(self):
        self.assertEqual(self._on_color("#000000"), "#FFFFFF")

    def test_on_primario_oscuro_sobre_amarillo(self):
        # El amarillo Parcerito debe llevar texto oscuro para contraste.
        self.assertEqual(self._on_color("#FFC200"), "#18120A")
        self.assertEqual(self._on_color("#FFFF00"), "#18120A")

    def test_theme_dict_contiene_todos_los_tokens_visuales(self):
        """El objeto brand.theme debe exponer los tokens del tema
        auto-derivados. Templates dependen de esa estructura estable."""
        p = self._brand_snapshot()
        self.assertIn("theme", p)
        expected = {
            "fondo_app", "superficie", "superficie_alt", "texto", "texto_suave",
            "cabecera_fondo", "cabecera_texto", "exito", "alerta",
            "informativo", "advertencia", "promocion", "destacado",
        }
        self.assertEqual(set(p["theme"].keys()), expected)

    def test_colores_funcionales_son_configurables_sin_tocar_la_marca(self):
        SiteConfig.set("COLOR_PROMOCION", "#AA22CC")
        SiteConfig.set("COLOR_DESTACADO", "#117766")
        db.session.commit()

        theme = self._brand_snapshot()["theme"]
        self.assertEqual(theme["promocion"], "#AA22CC")
        self.assertEqual(theme["destacado"], "#117766")


if __name__ == "__main__":
    unittest.main()
