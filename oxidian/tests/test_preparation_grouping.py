import unittest
from types import SimpleNamespace

from services import agrupar_items_por_producto


class PreparationGroupingTest(unittest.TestCase):
    def test_groups_variants_and_preserves_each_line(self):
        pequeño_fresa = SimpleNamespace(
            id=1, producto_id=7, display_nombre="Festival", cantidad=2,
        )
        grande_vainilla = SimpleNamespace(
            id=2, producto_id=7, display_nombre="Festival", cantidad=1,
        )
        bebida = SimpleNamespace(
            id=3, producto_id=8, display_nombre="Colombiana", cantidad=3,
        )

        grupos = agrupar_items_por_producto(
            [pequeño_fresa, grande_vainilla, bebida]
        )

        self.assertEqual([g["producto_id"] for g in grupos], [7, 8])
        self.assertEqual(grupos[0]["cantidad_total"], 3)
        self.assertEqual(grupos[0]["lineas"], [pequeño_fresa, grande_vainilla])
        self.assertEqual(grupos[1]["cantidad_total"], 3)

    def test_historical_lines_without_product_are_not_mixed(self):
        uno = SimpleNamespace(id=10, producto_id=None, display_nombre="Antiguo", cantidad=1)
        dos = SimpleNamespace(id=11, producto_id=None, display_nombre="Otro", cantidad=2)

        grupos = agrupar_items_por_producto([uno, dos])

        self.assertEqual(len(grupos), 2)
        self.assertEqual([g["cantidad_total"] for g in grupos], [1, 2])


if __name__ == "__main__":
    unittest.main()
