"""Regresión: acumular puntos no depende de habilitar su consulta/canje."""

import unittest

from flask import Flask

from extensions import db
from models import Order, PointsLog, SiteConfig, User
from services import (
    award_points_on_delivery,
    calcular_puntos_ganados,
    mensaje_estado_pedido,
)


class PointsAccumulationVisibilityTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-only",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.cliente = User(
            nombre="Cliente puntos",
            email="puntos@example.test",
            password_hash="test",
            rol="cliente",
            puntos=0,
        )
        db.session.add(self.cliente)
        db.session.flush()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _set(self, key, value):
        SiteConfig.set(key, str(value))
        db.session.commit()

    def _pedido(self, *, numero, total="12.75", puntos=0):
        pedido = Order(
            numero_pedido=numero,
            cliente_id=self.cliente.id,
            estado="entregado",
            origen="online",
            subtotal=total,
            total=total,
            puntos_ganados=puntos,
        )
        db.session.add(pedido)
        db.session.flush()
        return pedido

    def test_feature_off_still_calculates_and_awards_points_once(self):
        self._set("FEATURE_PUNTOS", "0")
        self._set("PUNTOS_POR_EURO", "2")
        pedido = self._pedido(numero="PTS-OFF-1")

        self.assertEqual(calcular_puntos_ganados(pedido.total), 25)
        self.assertEqual(award_points_on_delivery(pedido), 25)
        self.assertEqual(pedido.puntos_ganados, 25)
        self.assertEqual(self.cliente.puntos, 25)

        # La operación es idempotente incluso antes del commit de la entrega.
        self.assertEqual(award_points_on_delivery(pedido), 0)
        self.assertEqual(self.cliente.puntos, 25)
        self.assertEqual(
            PointsLog.query.filter_by(pedido_id=pedido.id, tipo="ganado").count(),
            1,
        )

    def test_feature_off_hides_points_from_delivery_message(self):
        self._set("FEATURE_PUNTOS", "0")
        pedido = self._pedido(numero="PTS-HIDDEN-1", puntos=30)

        mensaje = mensaje_estado_pedido(pedido)

        self.assertIn("Pedido entregado", mensaje)
        self.assertNotIn("puntos", mensaje.lower())

    def test_feature_on_shows_positive_points_but_never_zero(self):
        self._set("FEATURE_PUNTOS", "1")
        pedido_con_puntos = self._pedido(numero="PTS-ON-1", puntos=30)
        pedido_sin_puntos = self._pedido(numero="PTS-ZERO-1", total="0", puntos=0)

        self.assertIn("30 cafecitos", mensaje_estado_pedido(pedido_con_puntos))
        self.assertNotIn("0 cafecitos", mensaje_estado_pedido(pedido_sin_puntos))

    def test_invalid_totals_do_not_generate_points(self):
        self._set("PUNTOS_POR_EURO", "2")

        self.assertEqual(calcular_puntos_ganados(None), 0)
        self.assertEqual(calcular_puntos_ganados(-5), 0)
        self.assertEqual(calcular_puntos_ganados("no-es-un-total"), 0)


if __name__ == "__main__":
    unittest.main()
