import json
import unittest
from datetime import date, datetime, timedelta

from flask import Flask

from extensions import db
from models import Order, OrderItem, Product
from services import pedido_programado_disponible_para_preparar


class ScheduledOrderFlowTest(unittest.TestCase):
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

    @staticmethod
    def _scheduled_order(*fechas):
        pedido = Order(estado="pendiente")
        for index, fecha in enumerate(fechas, 1):
            producto = Product(
                nombre=f"Programado {index}",
                precio=10,
                activo=True,
                tipo_entrega="programado",
                fecha_llegada=fecha,
            )
            item = OrderItem(
                producto=producto,
                cantidad=1,
                precio_unit=10,
                subtotal=10,
                metadata_json=json.dumps({
                    "entrega_programada": fecha.isoformat(),
                    "producto": {
                        "nombre": producto.nombre,
                        "tipo_entrega": "programado",
                        "fecha_llegada": fecha.isoformat(),
                    },
                }),
            )
            pedido.items.append(item)
        return pedido

    def test_order_exposes_one_canonical_date_from_frozen_items(self):
        entrega = date.today() + timedelta(days=5)
        pedido = self._scheduled_order(entrega, entrega)

        self.assertTrue(pedido.es_programado)
        self.assertEqual(pedido.fechas_entrega_programadas, (entrega,))
        self.assertEqual(pedido.fecha_entrega_programada, entrega)

        # Cambiar el producto vivo no modifica el compromiso ya confirmado.
        pedido.items[0].producto.fecha_llegada = entrega + timedelta(days=3)
        self.assertEqual(pedido.fecha_entrega_programada, entrega)

    def test_conflicting_historical_dates_are_not_silently_selected(self):
        first = date.today() + timedelta(days=2)
        second = date.today() + timedelta(days=3)
        pedido = self._scheduled_order(first, second)

        self.assertEqual(pedido.fechas_entrega_programadas, (first, second))
        self.assertIsNone(pedido.fecha_entrega_programada)
        self.assertFalse(pedido_programado_disponible_para_preparar(pedido))

    def test_future_order_waits_but_today_order_can_start(self):
        now = datetime(2026, 7, 14, 12, 0)
        today = self._scheduled_order(date(2026, 7, 14))
        future = self._scheduled_order(date(2026, 7, 16))

        self.assertTrue(pedido_programado_disponible_para_preparar(today, ahora=now))
        self.assertFalse(pedido_programado_disponible_para_preparar(future, ahora=now))


if __name__ == "__main__":
    unittest.main()
