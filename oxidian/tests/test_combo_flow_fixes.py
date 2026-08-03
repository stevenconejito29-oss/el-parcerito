"""Tests de los fixes al flujo de combos en carrito/checkout.

Cubre:
- `_parse_combo_selection`: si un grupo tiene opciones pero ninguna disponible,
  se rechaza con mensaje claro (antes: se aceptaba empty y se lanzaba
  "stock insuficiente" downstream — confuso).
"""
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from werkzeug.datastructures import MultiDict

from flask import Flask

from extensions import db
from models import ComboItem, Product, ProductPresentation
from routes.public import _parse_combo_selection
from routes.api_bot import _combo_requires_web_configuration


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

    def test_id_de_otro_combo_no_se_inyecta_en_la_seleccion(self):
        """El servidor conserva sólo IDs que pertenecen al grupo consultado."""
        opciones = [
            self._mk_item(
                1, es_seleccionable=True, grupo="Bebida",
                max_sel=1, stock_ok=True,
            ),
        ]
        producto = self._mk_producto_combo("Combo Menú", opciones)
        form = MultiDict([
            ("combo_item_Bebida", "999999"),
            ("combo_item_Bebida", "1"),
        ])
        with patch("routes.public.ComboItem") as mock_combo_item:
            query_result = MagicMock()
            query_result.order_by.return_value.all.return_value = opciones
            mock_combo_item.query.filter_by.return_value = query_result
            seleccion, error = _parse_combo_selection(producto, form)

        self.assertIsNone(error)
        self.assertEqual(seleccion["Bebida"], {1: 1})
        self.assertNotIn(999999, seleccion["Bebida"])

    def test_distribucion_sabores_conserva_cantidades(self):
        item = self._mk_item(7, stock_ok=True)
        item.es_seleccionable = False
        item.cantidad = 5
        item.permite_sabor_cliente = True
        item.presentaciones_disponibles = []
        item.presentacion = None
        item.componente.nombre = "Arepa"
        mango = MagicMock(id=31, nombre="Mango")
        lulo = MagicMock(id=32, nombre="Lulo")
        item.sabores_disponibles_para.return_value = [mango, lulo]
        producto = self._mk_producto_combo("Combo sabores", [item])
        form = MultiDict({
            "combo_flavor_qty_7_31": "3",
            "combo_flavor_qty_7_32": "2",
        })
        with patch("routes.public.ComboItem") as mock_combo_item:
            query_result = MagicMock()
            query_result.order_by.return_value.all.return_value = [item]
            mock_combo_item.query.filter_by.return_value = query_result
            seleccion, error = _parse_combo_selection(producto, form)

        self.assertIsNone(error)
        self.assertEqual(
            seleccion["__flavors__"]["7"],
            {"31": 3, "32": 2},
        )

    def test_sabor_fijo_se_incluye_sin_intervencion_del_cliente(self):
        item = self._mk_item(8, stock_ok=True)
        item.es_seleccionable = False
        item.cantidad = 4
        item.permite_sabor_cliente = False
        item.presentaciones_disponibles = []
        item.presentacion = None
        item.fixed_flavor_option_id = 41
        item.fixed_flavor_option = MagicMock(id=41, nombre="Queso")
        item.componente.nombre = "Dedo"
        item.sabores_disponibles_para.return_value = [item.fixed_flavor_option]
        producto = self._mk_producto_combo("Combo fijo", [item])
        with patch("routes.public.ComboItem") as mock_combo_item:
            query_result = MagicMock()
            query_result.order_by.return_value.all.return_value = [item]
            mock_combo_item.query.filter_by.return_value = query_result
            seleccion, error = _parse_combo_selection(producto, MultiDict())

        self.assertIsNone(error)
        self.assertEqual(seleccion["__flavors__"]["8"], {"41": 4})

    def test_unidades_del_mismo_componente_conservan_tamano_y_sabor_en_pares(self):
        item = self._mk_item(9, stock_ok=True)
        item.es_seleccionable = False
        item.cantidad = 2
        item.permite_sabor_cliente = True
        item.fixed_flavor_option_id = None
        item.fixed_flavor_option = None
        item.componente.nombre = "Galletas Festival"
        pequeño = MagicMock(id=51, label="Pequeño")
        grande = MagicMock(id=52, label="Grande")
        fresa = MagicMock(id=61, nombre="Fresa")
        chocolate = MagicMock(id=62, nombre="Chocolate")
        item.presentaciones_disponibles = [pequeño, grande]
        item.presentacion = pequeño
        item.sabores_disponibles_para.side_effect = (
            lambda presentation: [fresa] if presentation.id == pequeño.id else [chocolate]
        )
        producto = self._mk_producto_combo("Combo Festival", [item])
        form = MultiDict({
            "combo_unit_presentation_9_1": "51",
            "combo_unit_flavor_9_1": "61",
            "combo_unit_presentation_9_2": "52",
            "combo_unit_flavor_9_2": "62",
        })
        with patch("routes.public.ComboItem") as mock_combo_item:
            query_result = MagicMock()
            query_result.order_by.return_value.all.return_value = [item]
            mock_combo_item.query.filter_by.return_value = query_result
            seleccion, error = _parse_combo_selection(producto, form)

        self.assertIsNone(error)
        self.assertEqual(seleccion["__units__"]["9"], [
            {"presentation_id": 51, "flavor_option_id": 61},
            {"presentation_id": 52, "flavor_option_id": 62},
        ])
        self.assertNotIn("__presentations__", seleccion)
        self.assertNotIn("__flavors__", seleccion)

    def test_rechaza_sabor_que_no_pertenece_al_tamano_de_esa_unidad(self):
        item = self._mk_item(10, stock_ok=True)
        item.es_seleccionable = False
        item.cantidad = 2
        item.permite_sabor_cliente = True
        item.fixed_flavor_option_id = None
        item.fixed_flavor_option = None
        item.componente.nombre = "Galletas Festival"
        pequeño = MagicMock(id=71)
        grande = MagicMock(id=72)
        fresa = MagicMock(id=81, nombre="Fresa")
        chocolate = MagicMock(id=82, nombre="Chocolate")
        item.presentaciones_disponibles = [pequeño, grande]
        item.presentacion = pequeño
        item.sabores_disponibles_para.side_effect = (
            lambda presentation: [fresa] if presentation.id == pequeño.id else [chocolate]
        )
        producto = self._mk_producto_combo("Combo Festival", [item])
        form = MultiDict({
            "combo_unit_presentation_10_1": "71",
            "combo_unit_flavor_10_1": "82",
            "combo_unit_presentation_10_2": "72",
            "combo_unit_flavor_10_2": "82",
        })
        with patch("routes.public.ComboItem") as mock_combo_item:
            query_result = MagicMock()
            query_result.order_by.return_value.all.return_value = [item]
            mock_combo_item.query.filter_by.return_value = query_result
            seleccion, error = _parse_combo_selection(producto, form)

        self.assertEqual(seleccion, {})
        self.assertIn("unidad 1", error)

    def test_precio_descuento_usa_presentacion_elegida_del_componente(self):
        componente = Product(
            nombre="Producto tamaños", precio=Decimal("10.00"), activo=True
        )
        combo = Product(
            nombre="Combo tamaños", precio=Decimal("1.00"), activo=True,
            es_combo=True, tipo_producto="combo",
            combo_precio_modo="descuento_porcentaje",
            combo_descuento_pct=Decimal("10.00"),
        )
        db.session.add_all([componente, combo])
        db.session.flush()
        pequeño = ProductPresentation(
            producto_id=componente.id, tamaño="pequeño",
            precio_extra=Decimal("0.00"), activo=True,
        )
        grande = ProductPresentation(
            producto_id=componente.id, tamaño="grande",
            precio_extra=Decimal("5.00"), activo=True,
        )
        db.session.add_all([pequeño, grande])
        db.session.flush()
        item = ComboItem(
            combo_id=combo.id, producto_id=componente.id, cantidad=1,
            presentation_id=pequeño.id, activo=True,
        )
        item.allowed_presentations = [pequeño, grande]
        db.session.add(item)
        db.session.commit()

        precio = combo.precio_combo_para_seleccion(
            [], {str(item.id): grande.id}
        )
        self.assertEqual(precio, Decimal("13.50"))
        self.assertTrue(_combo_requires_web_configuration(combo))

    def test_precio_fijo_aplica_diferencia_de_tamano_por_cada_unidad(self):
        componente = Product(
            nombre="Festival", precio=Decimal("2.00"), activo=True
        )
        combo = Product(
            nombre="Combo mixto", precio=Decimal("10.00"),
            combo_precio_base=Decimal("10.00"), activo=True,
            es_combo=True, tipo_producto="combo", combo_precio_modo="fijo",
        )
        db.session.add_all([componente, combo])
        db.session.flush()
        pequeño = ProductPresentation(
            producto_id=componente.id, tamaño="pequeño",
            precio_extra=Decimal("0.00"), activo=True,
        )
        grande = ProductPresentation(
            producto_id=componente.id, tamaño="grande",
            precio_extra=Decimal("1.50"), activo=True,
        )
        db.session.add_all([pequeño, grande])
        db.session.flush()
        item = ComboItem(
            combo_id=combo.id, producto_id=componente.id, cantidad=2,
            presentation_id=pequeño.id, activo=True,
        )
        item.allowed_presentations = [pequeño, grande]
        db.session.add(item)
        db.session.commit()

        precio = combo.precio_combo_para_seleccion(
            [], {}, {str(item.id): [
                {"presentation_id": pequeño.id},
                {"presentation_id": grande.id},
            ]}
        )
        self.assertEqual(precio, Decimal("11.50"))


if __name__ == "__main__":
    unittest.main()
