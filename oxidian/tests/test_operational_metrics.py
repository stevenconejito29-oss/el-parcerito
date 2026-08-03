import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g

from extensions import db, login_manager
from models import Order, OrderEvent, User
from operational_metrics_service import calcular_metricas_operativas
from routes.admin import admin_bp
from services import (
    asignar_repartidor_pedido,
    avanzar_estado_pedido,
)


class OperationalMetricsTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__,
            template_folder=str(root / "templates"),
            static_folder=str(root / "static"),
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="metricas-test",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SESSION_PROTECTION=None,
        )
        db.init_app(self.app)
        login_manager.init_app(self.app)
        self.app.register_blueprint(admin_bp, url_prefix="/admin")

        @login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.cliente = User(
            nombre="Cliente métricas",
            email="metricas-cliente@test.invalid",
            rol="cliente",
            activo=True,
        )
        self.repartidor = User(
            nombre="Repartidor métricas",
            email="metricas-repartidor@test.invalid",
            rol="repartidor",
            activo=True,
        )
        self.admin = User(
            nombre="Admin métricas",
            email="metricas-admin@test.invalid",
            rol="super_admin",
            activo=True,
        )
        for usuario in (self.cliente, self.repartidor, self.admin):
            usuario.set_password("test")
        db.session.add_all([self.cliente, self.repartidor, self.admin])
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pedido(self, numero="MET-1", estado="pendiente"):
        pedido = Order(
            numero_pedido=numero,
            cliente_id=self.cliente.id,
            estado=estado,
            origen="online",
            subtotal=Decimal("10"),
            total=Decimal("10"),
            tipo_entrega_cliente="delivery",
        )
        db.session.add(pedido)
        db.session.flush()
        return pedido

    def _login_admin(self):
        g.pop("_login_user", None)
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True

    def test_lifecycle_persists_ready_route_and_delivery_timestamps(self):
        pedido = self._pedido()
        avanzar_estado_pedido(pedido, canal="test")
        self.assertIsNone(pedido.preparado_en)

        avanzar_estado_pedido(pedido, canal="test")
        self.assertEqual(pedido.estado, "listo")
        self.assertIsNotNone(pedido.preparado_en)

        asignar_repartidor_pedido(
            pedido,
            self.repartidor.id,
            actor_id=self.repartidor.id,
            canal="test",
            aceptado=True,
        )
        self.assertIsNotNone(pedido.repartidor_asignado_en)
        self.assertIsNotNone(pedido.repartidor_tomado_en)

        avanzar_estado_pedido(pedido, canal="test")
        self.assertIsNotNone(pedido.en_ruta_en)
        avanzar_estado_pedido(pedido, canal="test")
        self.assertIsNotNone(pedido.entregado_en)

    def test_acceptance_is_idempotent_and_reassignment_restarts_driver_cycle(self):
        pedido = self._pedido(estado="listo")
        pedido.preparado_en = datetime(2026, 7, 29, 10, 0)
        asignar_repartidor_pedido(
            pedido,
            self.repartidor.id,
            actor_id=self.repartidor.id,
            canal="test",
            aceptado=True,
        )
        primera_toma = pedido.repartidor_tomado_en
        asignar_repartidor_pedido(
            pedido,
            self.repartidor.id,
            actor_id=self.repartidor.id,
            canal="test",
            aceptado=True,
        )
        self.assertEqual(pedido.repartidor_tomado_en, primera_toma)
        self.assertEqual(
            OrderEvent.query.filter_by(
                pedido_id=pedido.id,
                tipo="repartidor_tomado",
            ).count(),
            1,
        )

        asignar_repartidor_pedido(pedido, None, canal="test")
        self.assertIsNone(pedido.repartidor_asignado_en)
        self.assertIsNone(pedido.repartidor_tomado_en)

    def test_report_separates_stages_and_detects_slowest_one(self):
        inicio = datetime(2026, 7, 29, 10, 0)
        pedido = self._pedido(estado="entregado")
        pedido.creado_en = inicio
        pedido.preparado_en = inicio + timedelta(minutes=20)
        pedido.repartidor_asignado_en = inicio + timedelta(minutes=25)
        pedido.repartidor_tomado_en = inicio + timedelta(minutes=28)
        pedido.en_ruta_en = inicio + timedelta(minutes=30)
        pedido.entregado_en = inicio + timedelta(minutes=45)
        pedido.repartidor_id = self.repartidor.id
        db.session.commit()

        reporte = calcular_metricas_operativas(
            date(2026, 7, 29),
            date(2026, 7, 29),
        )
        valores = {etapa["key"]: etapa["promedio"] for etapa in reporte["etapas"]}
        self.assertEqual(valores["preparacion"], 20.0)
        self.assertEqual(valores["asignacion"], 5.0)
        self.assertEqual(valores["aceptacion"], 3.0)
        self.assertEqual(valores["despacho"], 2.0)
        self.assertEqual(valores["reparto"], 15.0)
        self.assertEqual(reporte["total"]["promedio"], 45.0)
        self.assertEqual(reporte["cuello_botella"]["key"], "preparacion")

    def test_missing_timestamp_is_not_counted_as_zero(self):
        pedido = self._pedido(estado="listo")
        pedido.creado_en = datetime(2026, 7, 29, 10, 0)
        pedido.preparado_en = datetime(2026, 7, 29, 10, 15)
        db.session.commit()

        reporte = calcular_metricas_operativas(
            date(2026, 7, 29),
            date(2026, 7, 29),
        )
        etapas = {etapa["key"]: etapa for etapa in reporte["etapas"]}
        self.assertEqual(etapas["preparacion"]["promedio"], 15.0)
        self.assertIsNone(etapas["asignacion"]["promedio"])
        self.assertEqual(etapas["asignacion"]["muestras"], 0)

    def test_admin_report_receives_metrics_and_exports_csv_without_personal_data(self):
        inicio = datetime(2026, 7, 29, 10, 0)
        pedido = self._pedido(estado="entregado")
        pedido.creado_en = inicio
        pedido.preparado_en = inicio + timedelta(minutes=10)
        pedido.entregado_en = inicio + timedelta(minutes=30)
        db.session.commit()
        self._login_admin()

        captured = {}

        def fake_render(template, **context):
            captured["template"] = template
            captured.update(context)
            return "OK"

        with patch("routes.admin.render_template", side_effect=fake_render):
            response = self.client.get(
                "/admin/analytics?fecha_ini=2026-07-29&fecha_fin=2026-07-29"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["template"], "admin/analytics.html")
        self.assertEqual(captured["metricas_operativas"]["total"]["promedio"], 30.0)

        response = self.client.get(
            "/admin/analytics/tiempos.csv"
            "?fecha_ini=2026-07-29&fecha_fin=2026-07-29"
        )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("pedido_utc", body)
        self.assertIn("MET-1", body)
        self.assertNotIn(self.cliente.email, body)


if __name__ == "__main__":
    unittest.main()
