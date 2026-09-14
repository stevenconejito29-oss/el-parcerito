import json
import unittest
from datetime import date
from pathlib import Path

from flask import Flask, render_template
from models import Order, OrderItem, Product


class OrderSnapshotIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.product = Product(nombre="Catálogo editado", fecha_llegada=date(2027, 1, 2),
                               imagen_url="nueva.png", origen_pais="Nuevo origen",
                               alergenos_json='["leche"]', proveedor_despachador_id=42)
        self.item = OrderItem(id=7, producto=self.product, cantidad=2, precio_unit=10,
                              subtotal=20, metadata_json=json.dumps({"producto": {
                                  "nombre": "Compra original", "fecha_llegada": None,
                                  "imagen_url": None, "origen_pais": "",
                                  "alergenos_json": [], "proveedor_despachador_id": None,
                                  "tipo_entrega": "inmediato", "canal_preparacion": "cocina",
                                  "tiene_sabores": False, "sabor_requerido": False,
                              }}))

    def test_explicit_empty_snapshot_does_not_inherit_edited_catalogue(self):
        self.assertEqual(self.item.display_nombre, "Compra original")
        self.assertIsNone(self.item.display_fecha_entrega)
        self.assertIsNone(self.item.display_imagen_url)
        self.assertEqual(self.item.display_origen_pais, "")
        self.assertEqual(self.item.display_alergenos, [])
        order = Order(items=[self.item])
        self.assertIsNone(order.fecha_entrega_programada)
        self.assertFalse(order.es_programado)

    def test_legacy_missing_keys_keep_compatibility(self):
        self.item.metadata_json = "{}"
        self.assertEqual(self.item.display_fecha_entrega, date(2027, 1, 2))
        self.assertEqual(self.item.display_alergenos, ["leche"])

    def test_invalid_metadata_root_is_safe_to_read(self):
        for raw in ("[]", "null", '"text"', "broken"):
            self.item.metadata_json = raw
            self.assertEqual(self.item.get_metadata(), {})
            self.assertEqual(self.item.producto_snapshot, {})
        self.item.metadata_json = '{"producto": [1]}'
        self.assertEqual(self.item.producto_snapshot, {})

    def test_preparation_renders_original_options_and_own_origin(self):
        metadata = self.item.get_metadata()
        metadata.update({"presentacion": {"tamaño": "grande", "label": "Grande original"},
                         "sabores": {"opciones": [{"nombre": "Sabor original", "cantidad": 1}]},
                         "extras": {"opciones": [{"nombre": "Extra original", "cantidad": 1}]}})
        self.item.metadata_json = json.dumps(metadata)
        self.item.notas = "Nota de la compra"
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
        with app.app_context():
            html = render_template("_order_item_combo.html", item=self.item)
        for text in ("Compra original", "Grande original", "Sabor original", "Extra original", "Nota de la compra"):
            self.assertIn(text, html)
        self.assertNotIn("Socio de productos", html)
        self.assertNotIn("Catálogo editado", html)
