"""Invariantes del sistema de canje con puntos (Fase G).

Cubre las reglas críticas del producto `solo_canje`:
- Un producto solo_canje TIENE que costar 0 en euros (precio=0).
- Un producto solo_canje NO se puede añadir al carrito por la ruta normal
  de compra: debe pasar por el flujo de canje del Club de puntos.
"""

import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from flask import Flask
from werkzeug.datastructures import MultiDict

from extensions import db
from models import Product, User
from routes.admin import _parsear_campos_producto
from routes.public import _canjeables_payload, public_bp


class CanjeSoloCanjeTest(unittest.TestCase):
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

    def _producto_solo_canje(self, puntos=200):
        # solo_canje IMPLICA precio=0 y canjeable_con_puntos=True.
        # Simulamos la salida esperada de _parse_producto_form (admin.py:2002-2004).
        p = Product(
            nombre="Regalo Cumple",
            precio=Decimal("0.00"),
            activo=True,
            tipo_entrega="inmediato",
            modalidad_entrega="ambas",
            canjeable_con_puntos=True,
            solo_canje=True,
            puntos_para_canje=puntos,
        )
        db.session.add(p)
        db.session.commit()
        return p

    def test_solo_canje_forces_zero_price(self):
        # Invariante estructural: un producto solo_canje persistido
        # con precio > 0 sería un bug. Verifica que el modelo lo acepta a 0.
        p = self._producto_solo_canje()
        self.assertEqual(float(p.precio or 0), 0.0)
        self.assertTrue(p.canjeable_con_puntos)
        self.assertTrue(p.solo_canje)
        self.assertGreater(int(p.puntos_para_canje or 0), 0)

    def test_admin_parser_creates_exclusive_reward_with_zero_price(self):
        """Regresión: precio=0 se validaba antes de leer `solo_canje`."""
        fields, error = _parsear_campos_producto(MultiDict({
            "nombre": "Empanada de regalo",
            "precio": "0.00",
            "modalidad_entrega": "ambas",
            "canjeable_con_puntos": "on",
            "solo_canje": "on",
            "puntos_para_canje": "250",
        }))

        self.assertIsNone(error)
        self.assertEqual(fields["precio"], 0.0)
        self.assertTrue(fields["canjeable_con_puntos"])
        self.assertTrue(fields["solo_canje"])
        self.assertEqual(fields["puntos_para_canje"], 250)

    def test_admin_parser_still_rejects_zero_price_for_normal_product(self):
        fields, error = _parsear_campos_producto(MultiDict({
            "nombre": "Producto normal",
            "precio": "0.00",
            "modalidad_entrega": "ambas",
        }))

        self.assertIsNone(fields)
        self.assertIn("mayor que 0", error)

    def test_domain_policy_rejects_invalid_points_and_non_finite_price(self):
        with self.assertRaisesRegex(ValueError, "puntos"):
            Product.normalizar_configuracion_canje(
                canjeable=True,
                solo_canje=True,
                puntos_para_canje=0,
                precio=0,
            )
        with self.assertRaisesRegex(ValueError, "precio"):
            Product.normalizar_configuracion_canje(
                canjeable=False,
                solo_canje=False,
                puntos_para_canje=None,
                precio="nan",
            )

    def test_edit_preserves_reward_configuration_while_points_module_is_off(self):
        reward = self._producto_solo_canje(puntos=300)
        features = {
            "puntos": False,
            "delivery": True,
            "recogida": True,
            "pedidos_programados": True,
        }
        with patch("routes.admin.get_store_features", return_value=features):
            fields, error = _parsear_campos_producto(MultiDict({
                "_producto_id": str(reward.id),
                "nombre": reward.nombre,
                "precio": "0.00",
                "modalidad_entrega": "ambas",
            }))

        self.assertIsNone(error)
        self.assertTrue(fields["solo_canje"])
        self.assertTrue(fields["canjeable_con_puntos"])
        self.assertEqual(fields["puntos_para_canje"], 300)

    def test_solo_canje_cannot_be_added_via_normal_cart(self):
        # Regla en routes/public.py:agregar_carrito (línea 858):
        # producto solo_canje redirige al Club de puntos.
        p = self._producto_solo_canje()
        response = self.client.post(
            f"/carrito/agregar/{p.id}",
            data={"cantidad": "1", "origen": "propio"},
            headers={"X-Ajax": "1"},
        )
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertIn("granitos de café", payload["msg"])

    def test_regular_canjeable_product_can_be_added_normally(self):
        # Producto canjeable_con_puntos=True pero solo_canje=False:
        # sí se compra con dinero, y opcionalmente el cliente puede canjearlo.
        p = Product(
            nombre="Cerveza",
            precio=Decimal("2.50"),
            activo=True,
            tipo_entrega="inmediato",
            modalidad_entrega="ambas",
            canjeable_con_puntos=True,
            solo_canje=False,
            puntos_para_canje=100,
        )
        db.session.add(p)
        db.session.commit()

        response = self.client.post(
            f"/carrito/agregar/{p.id}",
            data={"cantidad": "1", "origen": "propio"},
            headers={"X-Ajax": "1"},
        )
        self.assertTrue(response.get_json()["ok"])

    def test_verified_catalog_includes_zero_price_reward(self):
        """El OTP debe revelar también recompensas exclusivas de precio cero."""
        reward = self._producto_solo_canje(puntos=200)
        cliente = User(
            nombre="Cliente prueba",
            email="cliente@example.test",
            rol="cliente",
            puntos=250,
        )
        cliente.set_password("test-only")
        db.session.add(cliente)
        db.session.commit()

        with (
            patch("routes.public._producto_canjeable_en_origen", return_value=True),
            patch("routes.public.get_puntos_config", return_value={"ratio": 100}),
        ):
            payload = _canjeables_payload(cliente, "propio")

        self.assertEqual([item["id"] for item in payload["canjeables"]], [reward.id])
        self.assertEqual(payload["canjeables"][0]["precio"], 0.0)

    def test_otp_response_lists_zero_price_reward_for_checkout(self):
        """Reproduce el flujo AJAX que usa el selector tras confirmar código."""
        cart_product = Product(
            nombre="Producto del pedido",
            precio=Decimal("8.00"),
            activo=True,
            tipo_entrega="inmediato",
            modalidad_entrega="ambas",
        )
        reward = self._producto_solo_canje(puntos=200)
        cliente = User(
            nombre="Cliente con puntos",
            email="cliente.otp@example.test",
            telefono="+34600111222",
            rol="cliente",
            puntos=250,
        )
        cliente.set_password("test-only")
        db.session.add_all([cart_product, cliente])
        db.session.commit()
        code = cliente.generar_cod_puntos()
        db.session.commit()

        with self.client.session_transaction() as browser_session:
            browser_session["carrito"] = {str(cart_product.id): 1}
            browser_session["carrito_origen"] = "propio"

        features = {
            "puntos": True,
            "delivery": True,
            "recogida": True,
            "pedidos_programados": True,
        }
        with (
            patch("routes.public.buscar_cliente_por_telefono", return_value=(cliente, None)),
            patch("routes.public.get_store_features", return_value=features),
            patch("routes.public.get_puntos_config", return_value={"ratio": 100}),
            patch("loyalty_service.bloquear_cliente_puntos", return_value=cliente),
        ):
            response = self.client.post(
                "/puntos/verificar-codigo",
                json={"telefono": cliente.telefono, "codigo": code},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual([item["id"] for item in payload["canjeables"]], [reward.id])


if __name__ == "__main__":
    unittest.main()
