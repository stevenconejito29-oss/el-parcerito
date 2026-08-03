"""Verifica AuditLog en cambios de `comision_pct` de proveedores."""
import unittest
from pathlib import Path

from flask import Flask

from extensions import db, login_manager
from models import AuditLog, Proveedor, SiteConfig, User
from routes.admin import admin_bp


class ComisionAuditTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__,
            template_folder=str(root / "templates"),
            static_folder=str(root / "static"),
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="comision-audit",
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
        SiteConfig.set("WHATSAPP_COUNTRY_CODE", "+34")

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

    def _audit(self, accion):
        return AuditLog.query.filter_by(accion=accion).all()

    def test_creacion_proveedor_registra_comision_inicial(self):
        response = self.client.post(
            "/admin/proveedores",
            data={
                "accion": "crear",
                "nombre": "Bar del audit",
                "modelo_acuerdo": "socio_porcentaje",
                "comision_pct": "20",
            },
        )
        self.assertEqual(response.status_code, 302)
        prov = Proveedor.query.filter_by(nombre="Bar del audit").one()
        self.assertEqual(prov.comision_pct, 20)
        registros = self._audit("proveedor_comision_inicial")
        self.assertEqual(len(registros), 1)
        self.assertIn("20", registros[0].detalle or "")

    def test_edicion_con_cambio_registra_actualizada(self):
        # Alta previa
        self.client.post(
            "/admin/proveedores",
            data={
                "accion": "crear",
                "nombre": "Bar edit",
                "modelo_acuerdo": "socio_porcentaje",
                "comision_pct": "20",
            },
        )
        prov = Proveedor.query.filter_by(nombre="Bar edit").one()

        response = self.client.post(
            f"/admin/proveedores/{prov.id}/editar",
            data={
                "accion": "actualizar",
                "nombre": "Bar edit",
                "modelo_acuerdo": "socio_porcentaje",
                "comision_pct": "25",
                "activo": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        registros = self._audit("proveedor_comision_actualizada")
        self.assertEqual(len(registros), 1)
        detalle = registros[0].detalle or ""
        self.assertIn("20", detalle)
        self.assertIn("25", detalle)

    def test_edicion_sin_cambio_no_registra_actualizacion(self):
        self.client.post(
            "/admin/proveedores",
            data={
                "accion": "crear",
                "nombre": "Bar noop",
                "modelo_acuerdo": "socio_porcentaje",
                "comision_pct": "20",
            },
        )
        prov = Proveedor.query.filter_by(nombre="Bar noop").one()

        self.client.post(
            f"/admin/proveedores/{prov.id}/editar",
            data={
                "accion": "actualizar",
                "nombre": "Bar noop",
                "modelo_acuerdo": "socio_porcentaje",
                "comision_pct": "20",
                "activo": "1",
            },
        )
        self.assertEqual(
            len(self._audit("proveedor_comision_actualizada")), 0
        )

    def test_alta_socio_desde_usuarios_registra_comision_inicial(self):
        response = self.client.post(
            "/admin/usuarios/crear",
            data={
                "nombre": "Operador nuevo socio",
                "email": "nuevo.socio@test.invalid",
                "password": "secret1",
                "rol": "socio_producto",
                "telefono": "+34 620 222 555",
                "socio_vinculo": "nuevo",
                "nuevo_socio_nombre": "Socio Recien Creado",
                "nuevo_socio_comision": "17",
            },
        )
        self.assertEqual(response.status_code, 302)
        prov = Proveedor.query.filter_by(nombre="Socio Recien Creado").one()
        registros = [
            a for a in self._audit("proveedor_comision_inicial")
            if a.recurso_id == prov.id
        ]
        self.assertEqual(len(registros), 1)
        self.assertIn("17", registros[0].detalle or "")


if __name__ == "__main__":
    unittest.main()
