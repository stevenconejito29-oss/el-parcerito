"""Fase E: verifica que los feature toggles (FEATURE_DELIVERY / FEATURE_RECOGIDA /
FEATURE_PEDIDOS_PROGRAMADOS / FEATURE_PUNTOS) y el MODO_TIENDA se propagan de
forma coherente por get_store_features(), evitando drift entre módulos.

Estos toggles cambian el comportamiento del sistema (web + roles + bot). Sin
un test central, apagar una feature podría dejar caminos huérfanos visibles.
"""

import unittest

from flask import Flask

from extensions import db
from models import SiteConfig
from store_config import (
    get_store_features,
    is_service_mode,
    is_provider_flow_enabled,
)


class FeatureTogglesTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-only",
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

    def _set(self, key, value):
        row = SiteConfig.query.filter_by(clave=key).first()
        if row:
            row.valor = value
        else:
            db.session.add(SiteConfig(clave=key, valor=value))
        db.session.commit()

    def test_defaults_are_permissive(self):
        # Sin config previa: todo activo, modo propia.
        f = get_store_features()
        self.assertEqual(f["modo_tienda"], "propia")
        self.assertTrue(f["delivery"])
        self.assertTrue(f["recogida"])
        self.assertTrue(f["pedidos_programados"])
        self.assertTrue(f["puntos"])
        self.assertFalse(f["proveedores"])
        self.assertFalse(is_service_mode())
        self.assertFalse(is_provider_flow_enabled())

    def test_apagar_delivery_apaga_solo_delivery(self):
        self._set("FEATURE_DELIVERY", "0")
        f = get_store_features()
        self.assertFalse(f["delivery"])
        self.assertTrue(f["recogida"])  # No colateral

    def test_apagar_puntos_apaga_solo_puntos(self):
        self._set("FEATURE_PUNTOS", "0")
        f = get_store_features()
        self.assertFalse(f["puntos"])
        self.assertTrue(f["delivery"])
        self.assertTrue(f["recogida"])

    def test_apagar_ambas_modalidades_forcea_recogida(self):
        # Regla de seguridad en store_config.py:242-243: no dejar tienda sin
        # ninguna forma de entrega, se fuerza recogida.
        self._set("FEATURE_DELIVERY", "0")
        self._set("FEATURE_RECOGIDA", "0")
        f = get_store_features()
        self.assertFalse(f["delivery"])
        self.assertTrue(f["recogida"])

    def test_modo_bar_servicio_activa_flag(self):
        self._set("MODO_TIENDA", "bar_servicio")
        self.assertTrue(is_service_mode())
        self.assertEqual(get_store_features()["modo_tienda"], "bar_servicio")

    def test_modo_desconocido_cae_a_propia(self):
        # Defensa: MODO_TIENDA con valor inválido no debe romper.
        self._set("MODO_TIENDA", "pirateria")
        self.assertFalse(is_service_mode())
        self.assertEqual(get_store_features()["modo_tienda"], "propia")

    def test_provider_flow_siempre_desactivado(self):
        # Diseño: el flujo multi-proveedor queda apagado a nivel global.
        # Los pedidos con proveedor viajan por rutas normales del bar interno.
        self._set("FEATURE_PROVEEDORES", "1")  # aunque se intente activar
        self.assertFalse(is_provider_flow_enabled())


if __name__ == "__main__":
    unittest.main()
