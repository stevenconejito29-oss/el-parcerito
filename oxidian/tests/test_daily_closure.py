"""Cierre de caja diario: snapshot inmutable, idempotencia por fecha, borrado."""
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from flask import Flask, g

from extensions import db, login_manager
from models import Caja, DailyClosure, Order, User
from business_time import business_today, utc_naive_bounds
from routes.admin import admin_bp, _snapshot_dia_desde_caja


class DailyClosureTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__,
            template_folder=str(root / "templates"),
            static_folder=str(root / "static"),
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="daily-closure",
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
        self.other = User(nombre="B", email="b@t.invalid", rol="admin")
        self.other.set_password("x")
        db.session.add_all([self.admin, self.other])
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

    def _cliente(self):
        # Reutilizamos el mismo cliente entre pedidos para no chocar con el
        # UNIQUE de email en escenarios con varios pedidos por test.
        existing = User.query.filter_by(email="c@t.invalid").first()
        if existing:
            return existing
        c = User(nombre="C", email="c@t.invalid", rol="cliente")
        c.set_password("x")
        db.session.add(c)
        db.session.flush()
        return c

    def _pedido_efectivo(self, monto):
        cli = self._cliente()
        # numero_pedido único con id incremental basado en existentes
        n = Order.query.count() + 1
        o = Order(
            numero_pedido=f"P{n:04d}", cliente_id=cli.id, estado="entregado",
            metodo_pago="efectivo", pago_confirmado=True,
            subtotal=Decimal(str(monto)), total=Decimal(str(monto)),
        )
        db.session.add(o)
        db.session.flush()
        db.session.add(Caja(
            tipo="ingreso", categoria="venta_online",
            monto=Decimal(str(monto)), concepto=f"venta {monto}",
            pedido_id=o.id,
        ))
        db.session.commit()

    def _egreso(self, categoria, monto):
        db.session.add(Caja(
            tipo="egreso", categoria=categoria,
            monto=Decimal(str(monto)), concepto=f"gasto {categoria}",
        ))
        db.session.commit()

    def test_cerrar_dia_congela_snapshot_por_metodo_y_grupo(self):
        self._pedido_efectivo(50)
        self._pedido_efectivo(25)
        self._egreso("salario", 30)
        self._egreso("alquiler", 20)
        self._egreso("devolucion", 5)

        self._login(self.admin)
        r = self.client.post("/admin/finanzas/cerrar-dia", data={
            "fecha": business_today().isoformat(),
            "efectivo_declarado": "70",
            "notas": "Diferencia de €5 por vuelta pendiente",
        })
        self.assertEqual(r.status_code, 302)
        cierre = DailyClosure.query.filter_by(fecha=business_today()).one()
        self.assertEqual(float(cierre.ingresos_efectivo), 75.0)
        self.assertEqual(float(cierre.ingresos_bizum), 0.0)
        self.assertEqual(float(cierre.egresos_nominas), 30.0)
        self.assertEqual(float(cierre.egresos_gastos), 20.0)
        self.assertEqual(float(cierre.egresos_devoluciones), 5.0)
        self.assertEqual(float(cierre.saldo_neto), 20.0)  # 75 - 55
        self.assertEqual(float(cierre.efectivo_declarado), 70.0)
        # 70 declarado - 75 esperado = -5 descuadre
        self.assertEqual(float(cierre.descuadre_efectivo), -5.0)

    def test_cerrar_dia_rechaza_duplicado(self):
        self._login(self.admin)
        self.client.post("/admin/finanzas/cerrar-dia", data={
            "fecha": business_today().isoformat(),
        })
        # Segundo intento mismo día → warning, sin crear duplicado.
        r = self.client.post("/admin/finanzas/cerrar-dia", data={
            "fecha": business_today().isoformat(),
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            DailyClosure.query.filter_by(fecha=business_today()).count(), 1,
        )

    def test_solo_super_admin_puede_borrar_cierre(self):
        self._login(self.admin)
        self.client.post("/admin/finanzas/cerrar-dia", data={
            "fecha": business_today().isoformat(),
        })
        cierre = DailyClosure.query.first()

        # Admin normal no puede borrar
        self._login(self.other)
        r = self.client.post(f"/admin/finanzas/cierres/{cierre.id}/borrar")
        self.assertEqual(r.status_code, 302)  # redirigido, no borrado
        self.assertEqual(DailyClosure.query.count(), 1)

        # Super_admin sí puede
        self._login(self.admin)
        r = self.client.post(f"/admin/finanzas/cierres/{cierre.id}/borrar")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(DailyClosure.query.count(), 0)

    def test_snapshot_congelado_no_cambia_al_editar_caja(self):
        self._pedido_efectivo(100)
        self._login(self.admin)
        self.client.post("/admin/finanzas/cerrar-dia", data={
            "fecha": business_today().isoformat(),
        })
        cierre_pre = DailyClosure.query.first()
        ingresos_pre = float(cierre_pre.ingresos_efectivo)
        # Añadir un movimiento retroactivo al día ya cerrado
        self._pedido_efectivo(50)
        # El cierre NO debe cambiar
        db.session.refresh(cierre_pre)
        self.assertEqual(float(cierre_pre.ingresos_efectivo), ingresos_pre)

    def test_cierre_rechaza_fecha_futura(self):
        self._login(self.admin)
        futura = business_today() + timedelta(days=1)
        response = self.client.post("/admin/finanzas/cerrar-dia", data={
            "fecha": futura.isoformat(),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DailyClosure.query.count(), 0)

    def test_snapshot_respeta_medianoche_local_del_negocio(self):
        dia = date(2026, 8, 3)
        inicio_utc, fin_utc = utc_naive_bounds(dia)
        db.session.add_all([
            Caja(
                tipo="ingreso", categoria="otro", monto=Decimal("11"),
                concepto="dentro", fecha=inicio_utc,
            ),
            Caja(
                tipo="ingreso", categoria="otro", monto=Decimal("99"),
                concepto="día anterior", fecha=inicio_utc - timedelta(seconds=1),
            ),
            Caja(
                tipo="ingreso", categoria="otro", monto=Decimal("77"),
                concepto="día siguiente", fecha=fin_utc,
            ),
        ])
        db.session.commit()

        snapshot = _snapshot_dia_desde_caja(dia)
        self.assertEqual(float(snapshot["ingresos_otros"]), 11.0)
        self.assertEqual(float(snapshot["saldo_neto"]), 11.0)


if __name__ == "__main__":
    unittest.main()
