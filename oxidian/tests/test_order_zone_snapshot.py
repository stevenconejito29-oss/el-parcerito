import unittest
from decimal import Decimal

from flask import Flask

from extensions import db
from models import Order, User, ZonaEntrega
from services import aplicar_snapshot_zona_pedido


class OrderZoneSnapshotTest(unittest.TestCase):
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

        self.customer = User(
            nombre="Cliente zona",
            email="zona@test.invalid",
            telefono="+34610000001",
            rol="cliente",
            activo=True,
        )
        self.customer.set_password("test-only-password")
        self.zone = ZonaEntrega(
            nombre="Carmona centro",
            precio_envio=Decimal("2.50"),
            tiempo_estimado_min=28,
            activo=True,
            cobertura_geojson={
                "type": "Polygon",
                "coordinates": [[
                    [-5.66, 37.46], [-5.63, 37.46], [-5.63, 37.49],
                    [-5.66, 37.49], [-5.66, 37.46],
                ]],
            },
        )
        db.session.add_all([self.customer, self.zone])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_order_keeps_applied_zone_and_shipping_after_zone_changes(self):
        order = Order(
            numero_pedido="WEB-ZONE-001",
            cliente_id=self.customer.id,
            subtotal=Decimal("20.00"),
            descuento=Decimal("0.00"),
            total=Decimal("22.50"),
            zona_id=self.zone.id,
            direccion_entrega="Calle Real 1, Carmona",
        )
        aplicar_snapshot_zona_pedido(order, self.zone, Decimal("2.50"))
        db.session.add(order)
        db.session.commit()

        self.zone.nombre = "Zona renombrada"
        self.zone.precio_envio = Decimal("7.00")
        self.zone.tiempo_estimado_min = 55
        db.session.commit()
        db.session.refresh(order)

        self.assertEqual(order.zona_nombre_aplicada, "Carmona centro")
        self.assertEqual(order.zona_tiempo_estimado_aplicado, 28)
        self.assertEqual(order.costo_envio_aplicado, 2.5)
        self.assertEqual(float(order.zona_precio_envio_snapshot), 2.5)
        self.assertEqual(order.zona_tipo_cobertura_snapshot, "poligono")

    def test_pickup_order_has_zero_shipping_and_no_zone_snapshot(self):
        order = Order(
            numero_pedido="WEB-PICKUP-001",
            cliente_id=self.customer.id,
            subtotal=Decimal("20.00"),
            descuento=Decimal("0.00"),
            total=Decimal("20.00"),
            tipo_entrega_cliente="recogida",
        )
        aplicar_snapshot_zona_pedido(order, None, 0)

        self.assertEqual(order.costo_envio_aplicado, 0)
        self.assertIsNone(order.zona_nombre_snapshot)
        self.assertIsNone(order.zona_tipo_cobertura_snapshot)


if __name__ == "__main__":
    unittest.main()
