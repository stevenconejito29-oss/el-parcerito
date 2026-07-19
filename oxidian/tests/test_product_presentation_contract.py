import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProductPresentationContractTest(unittest.TestCase):
    def test_create_and_quick_edit_forms_persist_presentations(self):
        admin_source = (ROOT / "routes" / "admin.py").read_text(encoding="utf-8")
        create_flow = admin_source.split("def crear_producto():", 1)[1].split(
            '@admin_bp.route("/combos/nuevo"', 1
        )[0]
        self.assertIn("_sync_presentaciones(p, request.form)", create_flow)

        form_source = (ROOT / "templates" / "admin" / "_form_producto.html").read_text(
            encoding="utf-8"
        )
        for size in ("pequeño", "mediano", "grande"):
            self.assertIn(f'name="pres_{{{{ size }}}}_activo"', form_source)
            self.assertIn(f'name="pres_{{{{ size }}}}_extra"', form_source)

    def test_operational_views_use_shared_presentation_summary(self):
        expected = {
            "_order_item_combo.html",
            "admin/pedidos.html",
            "public/pedido_confirmado.html",
            "proveedor/pedidos.html",
        }
        for relative in expected:
            with self.subTest(template=relative):
                source = (ROOT / "templates" / relative).read_text(encoding="utf-8")
                self.assertIn("order_item_presentation.html", source)


if __name__ == "__main__":
    unittest.main()
