"""Tests de los fixes al flujo de combos en carrito/checkout.

Cubre:
- `_parse_combo_selection`: si un grupo tiene opciones pero ninguna disponible,
  se rechaza con mensaje claro (antes: se aceptaba empty y se lanzaba
  "stock insuficiente" downstream — confuso).
"""
import unittest
from unittest.mock import MagicMock
from werkzeug.datastructures import MultiDict

from flask import Flask

from extensions import db
from routes.public import _parse_combo_selection


class ComboFlowFixesTest(unittest.TestCase):
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

    def _mk_producto_combo(self, nombre, componentes):
        """Crea un combo sintético con MagicMock — no persiste, prueba lógica pura."""
        producto = MagicMock()
        producto.es_combo = True
        producto.nombre = nombre
        # `combo_item_stock_disponible(item, cantidad, origen)` — controlado por item.
        def _stock(item, cantidad=1, origen=None):
            return getattr(item, "stock_ok", False)
        producto.combo_item_stock_disponible = _stock
        producto.combo_items = componentes
        return producto

    def _mk_item(self, item_id, es_seleccionable=False, grupo=None, max_sel=1,
                stock_ok=True):
        item = MagicMock()
        item.id = item_id
        item.es_seleccionable = es_seleccionable
        item.grupo_seleccion = grupo
        item.grupo = None
        item.max_selecciones = max_sel
        item.stock_ok = stock_ok
        item.orden = 0
        return item

    def test_grupo_sin_stock_disponible_rechaza(self):
        # Grupo "Bebida" con 2 opciones, ambas sin stock → debe rechazar.
        opciones = [
            self._mk_item(1, es_seleccionable=True, grupo="Bebida", max_sel=1, stock_ok=False),
            self._mk_item(2, es_seleccionable=True, grupo="Bebida", max_sel=1, stock_ok=False),
        ]
        producto = self._mk_producto_combo("Combo Menú", opciones)

        # ComboItem.query.filter_by no aplica aquí — mockeamos la query.
        # `_parse_combo_selection` llama `ComboItem.query.filter_by(...)` para
        # obtener componentes. Parcheamos localmente.
        from unittest.mock import patch
        query_result = MagicMock()
        query_result.order_by.return_value.all.return_value = opciones
        filter_result = MagicMock(return_value=query_result)
        with patch("routes.public.ComboItem") as mock_ComboItem:
            mock_ComboItem.query.filter_by = filter_result
            seleccion, error = _parse_combo_selection(producto, MultiDict(), cantidad=1, origen="propio")

        self.assertIsNotNone(error)
        self.assertIn("sin stock", error.lower())
        self.assertEqual(seleccion, {})

    def test_grupo_con_al_menos_uno_disponible_ok(self):
        # Una opción con stock; usuario elige esa → ok.
        opciones = [
            self._mk_item(1, es_seleccionable=True, grupo="Bebida", max_sel=1, stock_ok=True),
            self._mk_item(2, es_seleccionable=True, grupo="Bebida", max_sel=1, stock_ok=False),
        ]
        producto = self._mk_producto_combo("Combo Menú", opciones)

        from unittest.mock import patch
        query_result = MagicMock()
        query_result.order_by.return_value.all.return_value = opciones
        filter_result = MagicMock(return_value=query_result)
        form = MultiDict([("combo_item_Bebida", "1")])
        with patch("routes.public.ComboItem") as mock_ComboItem:
            mock_ComboItem.query.filter_by = filter_result
            seleccion, error = _parse_combo_selection(producto, form, cantidad=1, origen="propio")

        self.assertIsNone(error, f"unexpected: {error}")
        self.assertIn("Bebida", seleccion)
        self.assertEqual(seleccion["Bebida"], {1: 1})


if __name__ == "__main__":
    unittest.main()
