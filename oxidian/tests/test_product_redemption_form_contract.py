"""Contrato del formulario reutilizado para crear y editar recompensas."""
from pathlib import Path
import unittest


TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


class ProductRedemptionFormContractTest(unittest.TestCase):
    def test_quick_editor_serializes_exclusive_reward_state(self):
        source = (TEMPLATES / "admin/productos.html").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("'solo_canje':"), 2)
        self.assertIn("solo_canje: d.solo_canje", source)

    def test_shared_form_synchronizes_reward_price_and_points(self):
        source = (TEMPLATES / "admin/productos.html").read_text(encoding="utf-8")
        self.assertIn("function sincronizarCamposCanje(form)", source)
        self.assertIn("precio.value = '0.00'", source)
        self.assertIn("puntos.required = estaHabilitado", source)


if __name__ == "__main__":
    unittest.main()
