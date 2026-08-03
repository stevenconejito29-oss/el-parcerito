"""Verifica que la elección de tamaño del cliente en un combo del socio se
refleje en `precio_combo_para_seleccion` — y por tanto en `OrderItem.subtotal`
y en la obligación de la liquidación.

Auditoría automática sospechaba que el `precio_extra` de la presentación
elegida no llegaba a la liquidación del socio. Este test lo confirma o
descarta directamente contra el modelo, sin montar el checkout entero.
"""
import unittest
from decimal import Decimal
from pathlib import Path

from flask import Flask

from extensions import db
from models import (
    Categoria, ComboGroup, ComboItem, Product, ProductPresentation, Proveedor,
    ProveedorProducto,
)


class ComboPresentationPriceTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(root / "templates"))
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.cat = Categoria(nombre="Comida", activo=True)
        self.socio = Proveedor(
            nombre="Socio", activo=True,
            modelo_acuerdo="socio_porcentaje", comision_pct=20,
        )
        db.session.add_all([self.cat, self.socio])
        db.session.flush()

        # Coca con 2 presentaciones: 33cl base (extra 0), 50cl (+1.50).
        self.coca = Product(
            nombre="Coca", precio=Decimal("2.00"),
            categoria_id=self.cat.id, activo=True,
            proveedor_despachador_id=self.socio.id,
        )
        db.session.add(self.coca)
        db.session.flush()
        self.pres_33 = ProductPresentation(
            producto_id=self.coca.id, tamaño="33cl",
            precio_extra=Decimal("0"), orden=0, activo=True,
        )
        self.pres_50 = ProductPresentation(
            producto_id=self.coca.id, tamaño="50cl",
            precio_extra=Decimal("1.50"), orden=1, activo=True,
        )
        db.session.add_all([self.pres_33, self.pres_50])
        db.session.flush()

        # Combo del socio: 1 Coca (cliente elige tamaño entre las 2).
        self.combo = Product(
            nombre="Combo Coca", precio=Decimal("5.00"),
            categoria_id=self.cat.id, activo=True,
            es_combo=True, tipo_producto="combo",
            proveedor_despachador_id=self.socio.id,
            combo_precio_modo="fijo",
            combo_precio_base=Decimal("5.00"),
        )
        db.session.add(self.combo)
        db.session.flush()
        db.session.add_all([
            ProveedorProducto(proveedor_id=self.socio.id,
                              producto_id=self.coca.id, stock=100, activo=True),
        ])
        grp = ComboGroup(
            combo_id=self.combo.id, nombre="Fijos", tipo="fijo",
            min_selecciones=0, max_selecciones=1, orden=0, requerido=True,
        )
        db.session.add(grp)
        db.session.flush()
        self.item = ComboItem(
            combo_id=self.combo.id, combo_group_id=grp.id,
            producto_id=self.coca.id, cantidad=1, orden=0, activo=True,
            es_seleccionable=False,
            presentation_id=self.pres_33.id,
        )
        # allowed_presentations M2M con 2 filas → picker en cliente.
        self.item.allowed_presentations = [self.pres_33, self.pres_50]
        db.session.add(self.item)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_precio_base_sin_seleccion_de_tamaño(self):
        # Sin selección del cliente → precio base del combo.
        precio = self.combo.precio_combo_para_seleccion()
        self.assertEqual(precio, Decimal("5.00"))

    def test_precio_incluye_extra_cuando_cliente_elige_tamaño_mayor(self):
        # Cliente elige la 50cl (+1.50). El precio del combo debe subir 1.50.
        precio = self.combo.precio_combo_para_seleccion(
            presentation_ids_by_item={str(self.item.id): str(self.pres_50.id)},
        )
        self.assertEqual(precio, Decimal("6.50"))

    def test_precio_baja_a_base_si_cliente_elige_tamaño_igual_al_default(self):
        precio = self.combo.precio_combo_para_seleccion(
            presentation_ids_by_item={str(self.item.id): str(self.pres_33.id)},
        )
        self.assertEqual(precio, Decimal("5.00"))


if __name__ == "__main__":
    unittest.main()
