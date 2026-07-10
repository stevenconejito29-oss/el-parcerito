"""Tests del filtro nicho estricto: comida y retail son tiendas separadas.

Blindaje ante regresiones: un producto NUNCA aparece en el nicho equivocado.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from extensions import db
from models import SiteConfig
from routes.public import _producto_pertenece_al_vertical


def _prod(vertical):
    """Producto sintético mínimo (sin persistir) — solo evaluamos el filtro."""
    return SimpleNamespace(vertical=vertical)


class FiltroNichoEstrictoTest(unittest.TestCase):
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

    def _set_tipo_tienda(self, valor):
        SiteConfig.set("TIPO_TIENDA", valor, descripcion="test")
        db.session.commit()

    def test_producto_comida_visible_solo_en_comida(self):
        self._set_tipo_tienda("comida")
        self.assertTrue(_producto_pertenece_al_vertical(_prod("comida")))
        self._set_tipo_tienda("producto")
        self.assertFalse(_producto_pertenece_al_vertical(_prod("comida")))

    def test_producto_retail_visible_solo_en_producto(self):
        self._set_tipo_tienda("producto")
        self.assertTrue(_producto_pertenece_al_vertical(_prod("producto")))
        self._set_tipo_tienda("comida")
        self.assertFalse(_producto_pertenece_al_vertical(_prod("producto")))

    def test_ambos_legacy_es_invisible(self):
        """Legacy 'ambos' no aparece; la migración lo convierte a nicho activo."""
        self._set_tipo_tienda("comida")
        self.assertFalse(_producto_pertenece_al_vertical(_prod("ambos")))
        self._set_tipo_tienda("producto")
        self.assertFalse(_producto_pertenece_al_vertical(_prod("ambos")))

    def test_vertical_none_o_vacio_es_invisible(self):
        self._set_tipo_tienda("comida")
        self.assertFalse(_producto_pertenece_al_vertical(_prod(None)))
        self.assertFalse(_producto_pertenece_al_vertical(_prod("")))

    def test_producto_none_es_invisible(self):
        self._set_tipo_tienda("comida")
        self.assertFalse(_producto_pertenece_al_vertical(None))


if __name__ == "__main__":
    unittest.main()
