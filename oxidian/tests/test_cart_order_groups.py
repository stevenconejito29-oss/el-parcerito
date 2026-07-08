import unittest
from datetime import date, timedelta
from decimal import Decimal

from flask import Flask

from extensions import db
from models import Product, SiteConfig
from routes.public import public_bp


class CartOrderGroupTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-only",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SKIP_DELIVERY_VALIDATION=True,
        )
        db.init_app(self.app)
        self.app.register_blueprint(public_bp)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _product(
        self,
        name,
        channel,
        group=None,
        tipo_entrega="programado",
        modalidad="ambas",
        activo=True,
    ):
        product = Product(
            nombre=name,
            precio=Decimal("5.00"),
            activo=activo,
            canal_preparacion=channel,
            tipo_entrega=tipo_entrega,
            modalidad_entrega=modalidad,
            fecha_llegada=date.today() + timedelta(days=2),
            grupo_pedido=group,
        )
        db.session.add(product)
        db.session.commit()
        return product

    def _add(self, product, origen="propio"):
        return self.client.post(
            f"/carrito/agregar/{product.id}",
            data={"cantidad": "1", "origen": origen},
            headers={"X-Ajax": "1"},
        )

    def test_kitchen_and_warehouse_can_share_the_general_cart(self):
        meal = self._product("Comida", "cocina")
        drink = self._product("Bebida", "almacen")

        self.assertTrue(self._add(meal).get_json()["ok"])
        self.assertTrue(self._add(drink).get_json()["ok"])

        with self.client.session_transaction() as session:
            self.assertEqual(session["carrito"], {str(meal.id): 1, str(drink.id): 1})

    def test_different_configured_groups_require_separate_orders(self):
        cold = self._product("Postre frío", "almacen", "Cadena de frío")
        hot = self._product("Plato caliente", "cocina", "Entrega caliente")

        self.assertTrue(self._add(cold).get_json()["ok"])
        response = self._add(hot).get_json()

        self.assertFalse(response["ok"])
        self.assertIn("Cadena de frío", response["msg"])
        self.assertIn("Entrega caliente", response["msg"])

    def test_delivery_only_and_pickup_only_cannot_share_cart(self):
        # Regla: no se puede mezclar producto solo-delivery con producto solo-recogida.
        delivery_only = self._product("Solo llevar", "cocina", modalidad="delivery")
        pickup_only = self._product("Solo recoger", "cocina", modalidad="recogida")

        self.assertTrue(self._add(delivery_only).get_json()["ok"])
        response = self._add(pickup_only).get_json()

        self.assertFalse(response["ok"])
        self.assertIn("modalidad", response["msg"].lower())

    def test_immediate_and_scheduled_families_cannot_share_cart(self):
        # Regla: pedido inmediato y pedido con fecha fija son familias distintas.
        inmediato = self._product("Bocadillo", "cocina", tipo_entrega="inmediato")
        programado = self._product("Cesta navideña", "almacen", tipo_entrega="programado")

        self.assertTrue(self._add(inmediato).get_json()["ok"])
        response = self._add(programado).get_json()

        self.assertFalse(response["ok"])
        self.assertIn("fecha", response["msg"].lower())

    def test_inactive_product_cannot_be_added(self):
        # Regla: producto inactivo no puede añadirse (404 del get_or_404).
        producto = self._product("Retirado", "cocina", activo=False)
        response = self.client.post(
            f"/carrito/agregar/{producto.id}",
            data={"cantidad": "1", "origen": "propio"},
            headers={"X-Ajax": "1"},
        )
        # Producto activo=False: get_or_404 aplica filter_by(activo=True) → 404
        self.assertIn(response.status_code, (200, 404))
        if response.status_code == 200:
            self.assertFalse(response.get_json()["ok"])

    def test_closed_store_blocks_adding_products_when_validation_active(self):
        # Regla nueva Fase 2: cuando la tienda propia está forzada cerrada,
        # el carrito bloquea en /carrito/agregar (antes solo en checkout).
        self.app.config["SKIP_DELIVERY_VALIDATION"] = False
        try:
            SiteConfig(clave="TIENDA_FORZAR_CERRADA", valor="1").save() if hasattr(SiteConfig, "save") else None
            db.session.add(SiteConfig(clave="TIENDA_FORZAR_CERRADA", valor="1"))
            db.session.add(SiteConfig(clave="TIENDA_MENSAJE_CIERRE", valor="Cerrado por vacaciones"))
            db.session.commit()

            producto = self._product("Cualquiera", "cocina", tipo_entrega="inmediato")
            response = self._add(producto).get_json()

            self.assertFalse(response["ok"])
            self.assertTrue(
                "cerrad" in response["msg"].lower() or "vacaciones" in response["msg"].lower()
            )
        finally:
            self.app.config["SKIP_DELIVERY_VALIDATION"] = True

    def test_empty_cart_after_add_and_compat_diagnostic_ok(self):
        # Smoke: un solo producto compatible se añade sin issues.
        producto = self._product("Único", "cocina", tipo_entrega="inmediato")
        response = self._add(producto).get_json()

        self.assertTrue(response["ok"])
        with self.client.session_transaction() as session:
            self.assertEqual(session["carrito"], {str(producto.id): 1})

    def test_producto_del_vertical_opuesto_no_puede_agregarse(self):
        # Regla nicho: si TIPO_TIENDA=producto (retail), un producto con
        # vertical=comida no debe poder añadirse al carrito.
        db.session.add(SiteConfig(clave="TIPO_TIENDA", valor="producto"))
        db.session.commit()

        producto = Product(
            nombre="Pizza de solo comida",
            precio=Decimal("8.00"),
            activo=True,
            canal_preparacion="cocina",
            tipo_entrega="inmediato",
            modalidad_entrega="ambas",
            vertical="comida",  # marcado explícito, no ambos
        )
        db.session.add(producto)
        db.session.commit()

        response = self._add(producto).get_json()
        # El producto no debe siquiera aparecer disponible en modo retail.
        self.assertFalse(response["ok"])

    def test_producto_vertical_ambos_pasa_en_cualquier_nicho(self):
        # vertical=ambos siempre pasa, sea comida o retail.
        for tipo in ("comida", "producto"):
            SiteConfig.query.filter_by(clave="TIPO_TIENDA").delete()
            db.session.add(SiteConfig(clave="TIPO_TIENDA", valor=tipo))
            db.session.commit()
            with self.client.session_transaction() as s:
                s.pop("carrito", None)

            producto = Product(
                nombre=f"Universal {tipo}",
                precio=Decimal("5.00"),
                activo=True,
                canal_preparacion="almacen",
                tipo_entrega="inmediato",
                modalidad_entrega="ambas",
                vertical="ambos",
            )
            db.session.add(producto)
            db.session.commit()
            self.assertTrue(self._add(producto).get_json()["ok"], f"falló en {tipo}")


if __name__ == "__main__":
    unittest.main()
