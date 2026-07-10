"""Tests del filtro nicho comida vs retail y del validador de combos-bundle.

Verifica la lógica pura sin arrancar Flask:
- `combo_validators.validate_combo_structure(parent_vertical="producto")`
  rechaza componentes seleccionables.
- `validate_combo_structure(parent_vertical="comida")` los permite.
- El validador es retro-compatible: si `parent_vertical=None` no aplica la regla.
"""
import unittest

from combo_validators import validate_combo_structure


def _mk(prod_id, cantidad=1, es_sel=False, grupo="", max_sel=1):
    return {
        "prod_id": prod_id,
        "cantidad": cantidad,
        "es_sel": es_sel,
        "grupo": grupo,
        "max_sel": max_sel,
    }


class ComboNichoRetailTest(unittest.TestCase):
    def test_retail_bundle_permite_todo_fijo(self):
        # 2 componentes fijos → válido para bundle retail.
        comps = [_mk(1), _mk(2)]
        ok, err = validate_combo_structure(comps, combo_id=99, parent_vertical="producto")
        self.assertTrue(ok, err)

    def test_retail_bundle_rechaza_seleccionable(self):
        comps = [_mk(1), _mk(2, es_sel=True, grupo="Color", max_sel=1)]
        ok, err = validate_combo_structure(comps, combo_id=99, parent_vertical="producto")
        self.assertFalse(ok)
        self.assertIn("bundle", (err or "").lower())

    def test_comida_permite_seleccionables(self):
        # 3 opciones para poder cumplir el mínimo del grupo (2 opciones por grupo
        # seleccionable). Añadimos un componente base fijo también.
        comps = [
            _mk(1),
            _mk(2, es_sel=True, grupo="Bebida", max_sel=1),
            _mk(3, es_sel=True, grupo="Bebida", max_sel=1),
        ]
        ok, err = validate_combo_structure(comps, combo_id=99, parent_vertical="comida")
        self.assertTrue(ok, err)

    def test_sin_parent_vertical_es_retrocompat(self):
        # Llamar sin `parent_vertical` no aplica la nueva restricción.
        comps = [
            _mk(1),
            _mk(2, es_sel=True, grupo="Bebida", max_sel=1),
            _mk(3, es_sel=True, grupo="Bebida", max_sel=1),
        ]
        ok, err = validate_combo_structure(comps, combo_id=99)
        self.assertTrue(ok, err)

    def test_ambos_no_aplica_restriccion(self):
        # vertical=ambos NO se considera retail; sigue permitiendo seleccionables.
        comps = [
            _mk(1),
            _mk(2, es_sel=True, grupo="Bebida", max_sel=1),
            _mk(3, es_sel=True, grupo="Bebida", max_sel=1),
        ]
        ok, err = validate_combo_structure(comps, combo_id=99, parent_vertical="ambos")
        self.assertTrue(ok, err)


if __name__ == "__main__":
    unittest.main()
