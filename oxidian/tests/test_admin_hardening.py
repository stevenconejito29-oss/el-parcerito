import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from werkzeug.datastructures import MultiDict

from combo_validators import validate_combo_structure
from routes.admin import (
    _FEATURE_URL_MAP,
    _parse_acuerdo_proveedor,
    _parsear_campos_producto,
    _payload_estructura_combo,
)
from routes.proveedor import _decimal_no_negativo, _entero_no_negativo


class AdminHardeningTest(unittest.TestCase):
    def test_provider_agreement_rejects_invalid_model_and_commission(self):
        with self.assertRaisesRegex(ValueError, "modelo"):
            _parse_acuerdo_proveedor(MultiDict({
                "modelo_acuerdo": "inventado",
                "comision_pct": "10",
            }))

        for value in ("-0.01", "100.01", "nan", "texto"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _parse_acuerdo_proveedor(MultiDict({
                        "modelo_acuerdo": "stock_proveedor",
                        "comision_pct": value,
                    }))

    def test_simple_product_cannot_set_dispatch_provider(self):
        fields, error = _parsear_campos_producto(MultiDict({
            "nombre": "Producto simple",
            "precio": "5.00",
            "proveedor_despachador_id": "7",
        }))

        self.assertIsNone(fields)
        self.assertIn("solo se configura en combos", error)

    def test_provider_inventory_numbers_are_strict_and_nonnegative(self):
        self.assertEqual(_entero_no_negativo("0", "Stock"), 0)
        self.assertEqual(str(_decimal_no_negativo("0.00", "Coste")), "0.00")

        for value in ("-1", "1.5", "texto", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _entero_no_negativo(value, "Stock")

        for value in ("-0.01", "nan", "texto"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _decimal_no_negativo(value, "Coste")

    def test_removing_component_cannot_leave_invalid_selection_group(self):
        items = [
            SimpleNamespace(
                producto_id=1,
                cantidad=1,
                es_seleccionable=True,
                grupo_display="Bebida",
                max_selecciones=2,
                es_predeterminado=False,
                activo=True,
            ),
        ]

        valid, error = validate_combo_structure(_payload_estructura_combo(items), 99)

        self.assertFalse(valid)
        self.assertIn("solo tiene 1 opción", error)

    def test_product_feature_covers_providers_and_combo_builder(self):
        self.assertEqual(_FEATURE_URL_MAP["/admin/proveedores"], "productos")
        self.assertEqual(_FEATURE_URL_MAP["/admin/combos"], "productos")

    def test_admin_and_provider_post_forms_include_csrf(self):
        templates = Path(__file__).resolve().parents[1] / "templates"
        post_form = re.compile(
            r'<form\b(?=[^>]*\bmethod=["\']POST["\'])[^>]*>(.*?)</form>',
            re.IGNORECASE | re.DOTALL,
        )
        missing = []
        for folder in ("admin", "proveedor"):
            for path in (templates / folder).glob("*.html"):
                for index, body in enumerate(post_form.findall(path.read_text()), start=1):
                    if 'name="csrf_token"' not in body:
                        missing.append(f"{path.name} form {index}")
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
