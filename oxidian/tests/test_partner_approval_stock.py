"""Verifica reglas de stock/visibilidad en `revisar_producto_socio`."""
import unittest
from pathlib import Path

from flask import Flask

from extensions import db, login_manager
from models import (
    Categoria, Product, Proveedor, ProveedorProducto, SiteConfig, User,
)
from routes.admin import admin_bp


class PartnerApprovalStockTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__,
            template_folder=str(root / "templates"),
            static_folder=str(root / "static"),
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="partner-approval-stock",
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
        SiteConfig.set("TIPO_TIENDA", "producto")

        self.category = Categoria(nombre="Cat", activo=True)
        self.socio = Proveedor(
            nombre="Socio aprobación", activo=True,
            modelo_acuerdo="socio_porcentaje", comision_pct=15,
        )
        db.session.add_all([self.category, self.socio])
        db.session.flush()

        self.superadmin = self._user(
            "Super", "super@test.invalid", "super_admin",
            telefono="+34600000000",
        )
        db.session.commit()
        self.client = self.app.test_client()
        self._login(self.superadmin)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _user(self, nombre, email, rol, **kwargs):
        user = User(nombre=nombre, email=email, rol=rol, **kwargs)
        user.set_password("password")
        db.session.add(user)
        db.session.flush()
        return user

    def _login(self, user):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True

    def _pending_product(self, *, stock, mostrar_web):
        p = Product(
            nombre=f"Prod s{stock}m{int(mostrar_web)}",
            precio=5, categoria_id=self.category.id,
            activo=False, es_combo=False, vertical="producto",
            tipo_entrega="inmediato", modalidad_entrega="ambas",
            canal_preparacion="almacen",
            proveedor_despachador_id=self.socio.id,
            partner_submission_status="pending",
            stock_mostrar_en_web=mostrar_web,
        )
        db.session.add(p)
        db.session.flush()
        db.session.add(ProveedorProducto(
            proveedor_id=self.socio.id, producto_id=p.id,
            stock=stock, activo=True,
        ))
        db.session.commit()
        return p

    def test_stock_cero_con_visibilidad_web_bloquea_aprobacion(self):
        p = self._pending_product(stock=0, mostrar_web=True)
        response = self.client.post(
            f"/admin/productos/{p.id}/revision-socio",
            data={"accion": "aprobar"},
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(p)
        self.assertEqual(p.partner_submission_status, "pending")
        self.assertFalse(p.activo)

    def test_stock_cero_sin_visibilidad_web_aprueba(self):
        p = self._pending_product(stock=0, mostrar_web=False)
        response = self.client.post(
            f"/admin/productos/{p.id}/revision-socio",
            data={"accion": "aprobar"},
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(p)
        self.assertEqual(p.partner_submission_status, "approved")
        self.assertTrue(p.activo)

    def test_stock_positivo_con_visibilidad_web_aprueba(self):
        p = self._pending_product(stock=5, mostrar_web=True)
        response = self.client.post(
            f"/admin/productos/{p.id}/revision-socio",
            data={"accion": "aprobar"},
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(p)
        self.assertEqual(p.partner_submission_status, "approved")
        self.assertTrue(p.activo)


if __name__ == "__main__":
    unittest.main()
