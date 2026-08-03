"""Cobertura del decorator `socio_capital_required` en routes/proveedor.py."""
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from extensions import db, login_manager
from models import (
    AuditLog, Categoria, Proveedor, ProveedorProducto, Product, SiteConfig, User,
)
from routes.admin import admin_bp
from routes.proveedor import proveedor_bp


class SocioCapitalDecoratorTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__,
            template_folder=str(root / "templates"),
            static_folder=str(root / "static"),
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="socio-capital-decorator",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SESSION_PROTECTION=None,
        )
        db.init_app(self.app)
        login_manager.init_app(self.app)
        self.app.register_blueprint(admin_bp, url_prefix="/admin")
        self.app.register_blueprint(proveedor_bp, url_prefix="/proveedor")

        @login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        SiteConfig.set("TIPO_TIENDA", "producto")

        self.category = Categoria(nombre="Bebidas", activo=True)
        self.socio_ok = Proveedor(
            nombre="Socio capital",
            activo=True,
            modelo_acuerdo="socio_porcentaje",
            comision_pct=15,
        )
        self.socio_legacy = Proveedor(
            nombre="Socio legacy",
            activo=True,
            modelo_acuerdo="stock_proveedor",
            comision_pct=10,
        )
        self.socio_inactivo = Proveedor(
            nombre="Socio inactivo",
            activo=False,
            modelo_acuerdo="socio_porcentaje",
            comision_pct=12,
        )
        db.session.add_all(
            [self.category, self.socio_ok, self.socio_legacy, self.socio_inactivo]
        )
        db.session.flush()

        self.operator_ok = self._user(
            "Op socio", "op@test.invalid", "socio_producto",
            proveedor_id=self.socio_ok.id,
        )
        self.operator_legacy = self._user(
            "Op legacy", "opl@test.invalid", "socio_producto",
            proveedor_id=self.socio_legacy.id,
        )
        self.operator_inactivo = self._user(
            "Op inactivo", "opi@test.invalid", "socio_producto",
            proveedor_id=self.socio_inactivo.id,
        )
        self.cliente = self._user("Cliente", "cli@test.invalid", "cliente")
        db.session.commit()
        self.client = self.app.test_client()

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

    def _audit_count(self, accion):
        return AuditLog.query.filter_by(accion=accion).count()

    def _assert_denied(self, response):
        # cliente pasa por proveedor_required->redirect antes de llegar al
        # decorator socio_capital_required. Aceptamos 403 o 302 como bloqueo.
        self.assertIn(response.status_code, (302, 403))

    def test_socio_legacy_bloqueado_producto_nuevo(self):
        self._login(self.operator_legacy)
        before = self._audit_count("socio_capital_denegado")
        response = self.client.get("/proveedor/productos/nuevo")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self._audit_count("socio_capital_denegado"), before + 1
        )

    def test_socio_inactivo_bloqueado_producto_nuevo(self):
        self._login(self.operator_inactivo)
        before = self._audit_count("socio_capital_denegado")
        response = self.client.get("/proveedor/productos/nuevo")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self._audit_count("socio_capital_denegado"), before + 1
        )

    def test_cliente_bloqueado_producto_nuevo(self):
        self._login(self.cliente)
        response = self.client.get("/proveedor/productos/nuevo")
        # cliente cae en proveedor_required (redirect) o socio_capital
        # (403). Cualquiera de los dos impide la entrada.
        self._assert_denied(response)

    def test_socio_valido_accede_producto_nuevo(self):
        self._login(self.operator_ok)
        with patch("routes.proveedor.render_template", return_value="ok"):
            response = self.client.get("/proveedor/productos/nuevo")
        self.assertEqual(response.status_code, 200)

    def test_socio_legacy_bloqueado_combo_nuevo(self):
        self._login(self.operator_legacy)
        before = self._audit_count("socio_capital_denegado")
        response = self.client.get("/proveedor/combos/nuevo")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self._audit_count("socio_capital_denegado"), before + 1
        )

    def test_socio_inactivo_bloqueado_combo_nuevo(self):
        self._login(self.operator_inactivo)
        before = self._audit_count("socio_capital_denegado")
        response = self.client.get("/proveedor/combos/nuevo")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self._audit_count("socio_capital_denegado"), before + 1
        )

    def test_cliente_bloqueado_combo_nuevo(self):
        self._login(self.cliente)
        response = self.client.get("/proveedor/combos/nuevo")
        self._assert_denied(response)

    def test_socio_valido_accede_combo_nuevo(self):
        self._login(self.operator_ok)
        with patch("routes.proveedor.render_template", return_value="ok"):
            response = self.client.get("/proveedor/combos/nuevo")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
