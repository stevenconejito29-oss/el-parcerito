"""Tests unitarios del módulo `combo_validators` — 8 validadores + ComboLimits.

Cubren la puerta de entrada a la creación / edición de combos. Sin estos
tests el código de validación era una caja negra de 500 líneas — hoy
cada rama tiene al menos un test que verifica el mensaje de error o la
aceptación esperada.
"""
import os
import unittest
from unittest.mock import patch

from flask import Flask

from extensions import db
from models import SiteConfig
from combo_validators import (
    ComboLimits,
    validate_component_quantity,
    validate_selections_per_group,
    validate_group_name,
    validate_combo_structure,
    validate_combo_pricing,
    validate_parallel_arrays,
)


class ComboValidatorsTest(unittest.TestCase):
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

    # ── ComboLimits (fuente única) ─────────────────────────────────────

    def test_limits_lee_de_site_config(self):
        SiteConfig.set("COMBO_MAX_QTY_COMPONENT", "42", descripcion="test")
        SiteConfig.set("COMBO_MAX_DISCOUNT_PCT", "30", descripcion="test")
        SiteConfig.set("COMBO_MAX_PRICE_EUR", "500", descripcion="test")
        db.session.commit()
        self.assertEqual(ComboLimits.max_qty_per_component(), 42)
        self.assertEqual(ComboLimits.max_discount_percentage(), 30.0)
        self.assertEqual(ComboLimits.max_price_eur(), 500.0)

    def test_max_price_eur_cap_defensivo_superior(self):
        SiteConfig.set("COMBO_MAX_PRICE_EUR", "99999999", descripcion="test")
        db.session.commit()
        self.assertEqual(ComboLimits.max_price_eur(), 100000.0)

    def test_max_price_eur_cap_defensivo_inferior(self):
        SiteConfig.set("COMBO_MAX_PRICE_EUR", "0", descripcion="test")
        db.session.commit()
        self.assertEqual(ComboLimits.max_price_eur(), 1.0)

    def test_max_price_eur_default_cuando_invalido(self):
        SiteConfig.set("COMBO_MAX_PRICE_EUR", "abc", descripcion="test")
        db.session.commit()
        self.assertEqual(ComboLimits.max_price_eur(), 1000.0)

    def test_min_components_al_menos_uno(self):
        # Antes: si el admin ponía 0, quedaba a 0 y aceptaba combos vacíos.
        SiteConfig.set("COMBO_MIN_COMPONENTS", "0", descripcion="test")
        db.session.commit()
        # ComboLimits.min_components() aplica max(1, ...) para no bajar de 1
        self.assertGreaterEqual(ComboLimits.min_components(), 1)

    # ── validate_component_quantity ────────────────────────────────────

    def test_qty_valida_positiva(self):
        ok, err = validate_component_quantity(3)
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_qty_rechaza_cero_o_negativa(self):
        ok, err = validate_component_quantity(0)
        self.assertFalse(ok)
        self.assertIn("mayor a 0", err)
        ok, err = validate_component_quantity(-5)
        self.assertFalse(ok)

    def test_qty_rechaza_no_entero(self):
        # Los formularios pueden mandar float por error — el validador lo rechaza.
        ok, err = validate_component_quantity(2.5)
        self.assertFalse(ok)

    def test_qty_respeta_max_configurable(self):
        SiteConfig.set("COMBO_MAX_QTY_COMPONENT", "5", descripcion="test")
        db.session.commit()
        ok, err = validate_component_quantity(6)
        self.assertFalse(ok)
        self.assertIn("5", err)

    # ── validate_selections_per_group ──────────────────────────────────

    def test_selections_valida_uno_o_mas(self):
        ok, err = validate_selections_per_group(2)
        self.assertTrue(ok)

    def test_selections_rechaza_cero_o_negativo(self):
        ok, err = validate_selections_per_group(0)
        self.assertFalse(ok)
        ok, err = validate_selections_per_group(-1)
        self.assertFalse(ok)

    def test_selections_respeta_max_configurable(self):
        SiteConfig.set("COMBO_MAX_SELECTIONS_GROUP", "3", descripcion="test")
        db.session.commit()
        ok, err = validate_selections_per_group(4)
        self.assertFalse(ok)

    # ── validate_group_name ────────────────────────────────────────────

    def test_grupo_seleccionable_exige_nombre(self):
        ok, err = validate_group_name("", is_selectable=True)
        self.assertFalse(ok)
        self.assertIn("nombre", err.lower())

    def test_grupo_seleccionable_acepta_nombre_valido(self):
        ok, err = validate_group_name("Bebida", is_selectable=True)
        self.assertTrue(ok)

    def test_grupo_fijo_no_exige_nombre(self):
        ok, err = validate_group_name("", is_selectable=False)
        self.assertTrue(ok)

    def test_grupo_rechaza_nombre_demasiado_largo(self):
        largo = "X" * 300
        ok, err = validate_group_name(largo, is_selectable=True)
        self.assertFalse(ok)

    # ── validate_combo_structure ───────────────────────────────────────

    def _fixed(self, prod_id, cantidad=1):
        return {"prod_id": prod_id, "cantidad": cantidad, "es_sel": False,
                "grupo": "", "max_sel": 1}

    def _selectable(self, prod_id, grupo, max_sel=1, cantidad=1):
        return {"prod_id": prod_id, "cantidad": cantidad, "es_sel": True,
                "grupo": grupo, "max_sel": max_sel}

    def test_structure_rechaza_vacio(self):
        ok, err = validate_combo_structure([])
        self.assertFalse(ok)

    def test_structure_acepta_un_solo_componente_fijo(self):
        ok, err = validate_combo_structure([self._fixed(1)])
        self.assertTrue(ok)

    def test_structure_rechaza_producto_fijo_duplicado(self):
        ok, err = validate_combo_structure([self._fixed(1), self._fixed(1)])
        self.assertFalse(ok)
        self.assertIn("ya existe", err)

    def test_structure_rechaza_repetir_producto_en_mismo_grupo(self):
        comps = [
            self._selectable(1, "Bebida"),
            self._selectable(1, "Bebida"),
        ]
        ok, err = validate_combo_structure(comps)
        self.assertFalse(ok)
        self.assertIn("repetir", err.lower())

    def test_structure_max_sel_mayor_que_opciones_rechaza(self):
        # Grupo con 1 opción pero max_sel=2 → imposible.
        comps = [self._selectable(1, "Bebida", max_sel=2)]
        ok, err = validate_combo_structure(comps)
        self.assertFalse(ok)
        self.assertIn("2 selecciones pero solo tiene", err)

    def test_structure_max_sel_inconsistente_en_grupo(self):
        # Dos productos del mismo grupo con max_sel distinto → error.
        comps = [
            self._selectable(1, "Bebida", max_sel=1),
            self._selectable(2, "Bebida", max_sel=2),
        ]
        ok, err = validate_combo_structure(comps)
        self.assertFalse(ok)
        self.assertIn("mismo máximo", err)

    def test_structure_retail_rechaza_seleccionables(self):
        # vertical=producto (retail) → bundles fijos, no permite grupos.
        comps = [self._selectable(1, "Talla", max_sel=1)]
        ok, err = validate_combo_structure(comps, parent_vertical="producto")
        self.assertFalse(ok)
        self.assertIn("bundles fijos", err)

    def test_structure_retail_acepta_todo_fijo(self):
        comps = [self._fixed(1), self._fixed(2)]
        ok, err = validate_combo_structure(comps, parent_vertical="producto")
        self.assertTrue(ok)

    def test_structure_max_componentes_configurable(self):
        SiteConfig.set("COMBO_MAX_COMPONENTS", "3", descripcion="test")
        db.session.commit()
        comps = [self._fixed(i) for i in range(1, 5)]  # 4 fijos
        ok, err = validate_combo_structure(comps)
        self.assertFalse(ok)
        self.assertIn("no puede tener más", err)

    # ── validate_combo_pricing ─────────────────────────────────────────

    def test_pricing_rechaza_precio_cero_o_negativo(self):
        ok, err = validate_combo_pricing(0)
        self.assertFalse(ok)
        ok, err = validate_combo_pricing(-1)
        self.assertFalse(ok)

    def test_pricing_acepta_precio_positivo(self):
        ok, err = validate_combo_pricing(15.50)
        self.assertTrue(ok)

    def test_pricing_respeta_max_price_configurable(self):
        SiteConfig.set("COMBO_MAX_PRICE_EUR", "50", descripcion="test")
        db.session.commit()
        ok, err = validate_combo_pricing(75)
        self.assertFalse(ok)
        self.assertIn("50€", err)

    def test_pricing_rechaza_descuento_fuera_de_rango(self):
        ok, err = validate_combo_pricing(20, descuento_porcentaje=101)
        self.assertFalse(ok)
        ok, err = validate_combo_pricing(20, descuento_porcentaje=-5)
        self.assertFalse(ok)

    def test_pricing_descuento_respeta_max_config(self):
        SiteConfig.set("COMBO_MAX_DISCOUNT_PCT", "30", descripcion="test")
        db.session.commit()
        ok, err = validate_combo_pricing(20, descuento_porcentaje=40)
        self.assertFalse(ok)
        self.assertIn("30", err)

    def test_pricing_descuento_none_es_ok(self):
        # Descuento opcional: None significa "no aplicar"
        ok, err = validate_combo_pricing(20, descuento_porcentaje=None)
        self.assertTrue(ok)

    # ── validate_parallel_arrays ───────────────────────────────────────

    def test_parallel_arrays_todos_misma_longitud(self):
        ok, err = validate_parallel_arrays([1, 2], [3, 4], [5, 6])
        self.assertTrue(ok)

    def test_parallel_arrays_rechaza_desiguales(self):
        ok, err = validate_parallel_arrays([1, 2], [3])
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
