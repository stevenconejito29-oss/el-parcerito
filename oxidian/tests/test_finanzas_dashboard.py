"""Dashboard financiero: KPIs, agrupación por concepto y validación categorías."""
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g

from extensions import db, login_manager
from models import (
    CATEGORIAS_CAJA, CATEGORIAS_CAJA_MANUAL_EGRESO,
    Caja, StaffPayment, User, Categoria, Order,
    caja_categoria_meta,
)
from routes.admin import admin_bp


class FinanzasDashboardTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__,
            template_folder=str(root / "templates"),
            static_folder=str(root / "static"),
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="finanzas-test",
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
        self.admin = User(nombre="A", email="a@t.invalid", rol="super_admin")
        self.admin.set_password("x")
        db.session.add(self.admin)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user):
        g.pop("_login_user", None)
        with self.client.session_transaction() as s:
            s.clear()
            s["_user_id"] = str(user.id)
            s["_fresh"] = True

    def _mov(self, tipo, categoria, monto, concepto="x"):
        db.session.add(Caja(
            tipo=tipo, categoria=categoria, monto=Decimal(str(monto)),
            concepto=concepto,
        ))
        db.session.commit()

    def test_taxonomia_categorias_es_coherente(self):
        # Cada categoría del catálogo tiene grupo válido.
        grupos_validos = {"ventas", "nominas", "liquidaciones", "gastos",
                          "devoluciones", "otros"}
        for cat, meta in CATEGORIAS_CAJA.items():
            self.assertIn(meta["grupo"], grupos_validos,
                          f"{cat} tiene grupo inválido: {meta['grupo']}")

    def test_meta_desconocida_cae_a_otros_sin_romper(self):
        meta = caja_categoria_meta("legacy_no_registrada")
        self.assertEqual(meta["grupo"], "otros")
        self.assertTrue(meta["label"])

    def test_dashboard_agrupa_egresos_por_concepto(self):
        self._mov("ingreso", "venta_online", 100)
        self._mov("egreso", "salario", 40)
        self._mov("egreso", "comision_repartidor", 5)
        self._mov("egreso", "alquiler", 30)
        self._mov("egreso", "devolucion", 10)
        self._mov("egreso", "liquidacion_socio", 8)

        self._login(self.admin)
        captured = {}

        def fake_render(template, **ctx):
            captured["template"] = template
            captured.update(ctx)
            return "OK"

        with patch("routes.admin.render_template", side_effect=fake_render):
            r = self.client.get("/admin/finanzas?preset=hoy")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(captured["template"], "admin/finanzas.html")
        self.assertEqual(float(captured["ingresos_total"]), 100.0)
        self.assertEqual(float(captured["egresos_total"]), 93.0)
        self.assertEqual(float(captured["saldo_neto"]), 7.0)
        # Nóminas suma salario + comisión repartidor
        self.assertEqual(float(captured["por_grupo"]["nominas"]["egreso"]), 45.0)
        self.assertEqual(float(captured["por_grupo"]["gastos"]["egreso"]), 30.0)
        self.assertEqual(float(captured["por_grupo"]["devoluciones"]["egreso"]), 10.0)
        self.assertEqual(float(captured["por_grupo"]["liquidaciones"]["egreso"]), 8.0)
        self.assertEqual(float(captured["por_grupo"]["ventas"]["ingreso"]), 100.0)

    def test_dashboard_kpis_pendientes(self):
        # Un StaffPayment pendiente (nómina) y uno pagado (no debe contar)
        db.session.add_all([
            StaffPayment(user_id=self.admin.id, tipo="salario",
                         monto=Decimal("50"), pagado=False),
            StaffPayment(user_id=self.admin.id, tipo="salario",
                         monto=Decimal("25"), pagado=True),
            StaffPayment(user_id=self.admin.id, tipo="liquidacion_proveedor",
                         monto=Decimal("12"), pagado=False),
        ])
        db.session.commit()

        self._login(self.admin)
        captured = {}

        def fake_render(template, **ctx):
            captured.update(ctx)
            return "OK"

        with patch("routes.admin.render_template", side_effect=fake_render):
            self.client.get("/admin/finanzas?preset=hoy")
        self.assertEqual(float(captured["pagos_staff_pendientes_total"]), 50.0)
        self.assertEqual(float(captured["liquidaciones_pendientes_total"]), 12.0)

    def test_movimiento_manual_rechaza_categoria_no_permitida(self):
        self._login(self.admin)
        # venta_online es del sistema (viene del checkout), no debe aceptarse
        # como alta manual.
        r = self.client.post("/admin/caja/movimiento", data={
            "tipo": "egreso",
            "categoria": "venta_online",  # no está en CATEGORIAS_CAJA_MANUAL_EGRESO
            "monto": "10",
            "concepto": "intento",
        })
        self.assertEqual(r.status_code, 302)
        # Ningún egreso creado.
        self.assertEqual(Caja.query.filter_by(tipo="egreso").count(), 0)

    def test_movimiento_manual_acepta_categoria_valida(self):
        self._login(self.admin)
        r = self.client.post("/admin/caja/movimiento", data={
            "tipo": "egreso",
            "categoria": "alquiler",
            "monto": "500",
            "concepto": "Alquiler local julio",
        })
        self.assertEqual(r.status_code, 302)
        mov = Caja.query.filter_by(tipo="egreso").first()
        self.assertIsNotNone(mov)
        self.assertEqual(mov.categoria, "alquiler")
        self.assertEqual(float(mov.monto), 500.0)

    def test_pendientes_pago_excluye_pedidos_entregados(self):
        """Pedido entregado con pago_confirmado=False es legacy, no pendiente."""
        cliente = User(nombre="C", email="c@t.invalid", rol="cliente")
        cliente.set_password("x")
        db.session.add(cliente)
        db.session.flush()
        vivo = Order(
            numero_pedido="P1", cliente_id=cliente.id, estado="pendiente",
            metodo_pago="bizum", pago_confirmado=False,
            subtotal=Decimal("25"), total=Decimal("25"),
        )
        entregado_legacy = Order(
            numero_pedido="P2", cliente_id=cliente.id, estado="entregado",
            metodo_pago="bizum", pago_confirmado=False,
            subtotal=Decimal("15"), total=Decimal("15"),
        )
        db.session.add_all([vivo, entregado_legacy])
        db.session.commit()

        self._login(self.admin)
        captured = {}

        def fake_render(template, **ctx):
            captured.update(ctx)
            return "OK"

        with patch("routes.admin.render_template", side_effect=fake_render):
            self.client.get("/admin/finanzas?preset=mes")
        # Solo el pedido vivo debe contar
        pendientes = list(captured["pendientes_pago"])
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(pendientes[0].numero_pedido, "P1")
        self.assertEqual(float(captured["pendientes_pago_total"]), 25.0)

    def test_export_csv_incluye_grupo_y_metodo(self):
        self._mov("egreso", "salario", 60)
        self._login(self.admin)
        r = self.client.get("/admin/caja/exportar")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        # Cabecera nueva
        self.assertIn("Grupo", body)
        self.assertIn("Categoria_display", body)
        self.assertIn("Metodo_pago", body)
        self.assertIn("Staff_payment", body)
        # Fila del salario clasificada en nominas
        self.assertIn("nominas", body)
        self.assertIn("Salario", body)


if __name__ == "__main__":
    unittest.main()
