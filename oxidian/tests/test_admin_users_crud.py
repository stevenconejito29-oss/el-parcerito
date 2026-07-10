import unittest
from pathlib import Path

from flask import Flask

from extensions import db, login_manager
from models import Proveedor, SiteConfig, StaffPayment, User
from routes.admin import admin_bp


class AdminUsersCrudTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__,
            template_folder=str(root / "templates"),
            static_folder=str(root / "static"),
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-only",
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
            "Super Admin",
            "super@test.invalid",
            "super_admin",
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

    def test_allows_multiple_accounts_for_same_role_but_never_customer_role(self):
        for index in (1, 2):
            response = self.client.post(
                "/admin/usuarios/crear",
                data={
                    "nombre": f"Preparador {index}",
                    "email": f"prep{index}@test.invalid",
                    "password": "secret1",
                    "rol": "preparacion",
                    "telefono": f"+3462000000{index}",
                },
            )
            self.assertEqual(response.status_code, 302)

        self.assertEqual(User.query.filter_by(rol="preparacion").count(), 2)

        for index in (1, 2):
            response = self.client.post(
                "/admin/usuarios/crear",
                data={
                    "nombre": f"Cocina {index}",
                    "email": f"cocina{index}@test.invalid",
                    "password": "secret1",
                    "rol": "cocina",
                    "telefono": f"+3463000000{index}",
                },
            )
            self.assertEqual(response.status_code, 302)
        self.assertEqual(User.query.filter_by(rol="cocina").count(), 2)

        self.client.post(
            "/admin/usuarios/crear",
            data={
                "nombre": "Cliente interno",
                "email": "cliente@test.invalid",
                "password": "secret1",
                "rol": "cliente",
            },
        )
        self.assertIsNone(User.query.filter_by(email="cliente@test.invalid").first())

        self.client.post(
            "/admin/usuarios/crear",
            data={
                "nombre": "Salario invalido",
                "email": "salary@test.invalid",
                "password": "secret1",
                "rol": "preparacion",
                "salario_base": "-1",
            },
        )
        self.assertIsNone(User.query.filter_by(email="salary@test.invalid").first())

    def test_legacy_provider_role_is_always_rejected(self):
        self.client.post(
            "/admin/usuarios/crear",
            data={
                "nombre": "Operador",
                "email": "operador@test.invalid",
                "password": "secret1",
                "rol": "proveedor",
            },
        )
        self.assertIsNone(User.query.filter_by(email="operador@test.invalid").first())

    def test_internal_accounts_require_unique_phone_for_bot_permissions(self):
        response = self.client.post(
            "/admin/usuarios/crear",
            data={
                "nombre": "Sin telefono",
                "email": "sintelefono@test.invalid",
                "password": "secret1",
                "rol": "admin",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(User.query.filter_by(email="sintelefono@test.invalid").first())

        self.client.post(
            "/admin/usuarios/crear",
            data={
                "nombre": "Admin con telefono",
                "email": "adminphone@test.invalid",
                "password": "secret1",
                "rol": "admin",
                "telefono": "+34 620 111 222",
            },
        )
        self.assertIsNotNone(User.query.filter_by(email="adminphone@test.invalid").first())

        self.client.post(
            "/admin/usuarios/crear",
            data={
                "nombre": "Duplicado",
                "email": "duplicado@test.invalid",
                "password": "secret1",
                "rol": "repartidor",
                "telefono": "620111222",
            },
        )
        self.assertIsNone(User.query.filter_by(email="duplicado@test.invalid").first())

    def test_internal_accounts_reject_local_phone_without_country_prefix(self):
        SiteConfig.set("WHATSAPP_COUNTRY_CODE", "")
        db.session.commit()

        self.client.post(
            "/admin/usuarios/crear",
            data={
                "nombre": "Telefono ambiguo",
                "email": "ambiguo@test.invalid",
                "password": "secret1",
                "rol": "admin",
                "telefono": "620 111 333",
            },
        )

        self.assertIsNone(User.query.filter_by(email="ambiguo@test.invalid").first())

        provider = Proveedor(nombre="Bar activo", activo=True)
        db.session.add(provider)
        db.session.commit()
        self.client.post(
            "/admin/usuarios/crear",
            data={
                "nombre": "Operador",
                "email": "operador@test.invalid",
                "password": "secret1",
                "rol": "proveedor",
                "proveedor_id": str(provider.id),
            },
        )
        self.assertIsNone(User.query.filter_by(email="operador@test.invalid").first())

    def test_disabled_modules_reject_new_operational_roles(self):
        SiteConfig.set("FEATURE_DELIVERY", "0")
        SiteConfig.set("FEATURE_PEDIDOS_PROGRAMADOS", "0")
        db.session.commit()

        for rol in ("repartidor", "preparacion"):
            self.client.post(
                "/admin/usuarios/crear",
                data={
                    "nombre": f"Rol {rol}",
                    "email": f"{rol}@disabled.invalid",
                    "password": "secret1",
                    "rol": rol,
                },
            )

        self.assertIsNone(User.query.filter_by(email="repartidor@disabled.invalid").first())
        self.assertIsNone(User.query.filter_by(email="preparacion@disabled.invalid").first())

    def test_inactive_account_can_be_fully_edited(self):
        user = self._user(
            "Nombre anterior",
            "anterior@test.invalid",
            "repartidor",
            activo=False,
        )
        db.session.commit()

        response = self.client.post(
            f"/admin/usuarios/{user.id}/editar",
            data={
                "nombre": "Nombre nuevo",
                "email": "nuevo@test.invalid",
                "rol": "preparacion",
                "telefono": "612 345 678",
                "puesto_trabajo": "Preparacion",
                "salario_base": "1200.50",
                "tarifa_entrega": "0",
                "nueva_password": "newpass",
            },
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(user)
        self.assertEqual(user.nombre, "Nombre nuevo")
        self.assertEqual(user.email, "nuevo@test.invalid")
        self.assertEqual(user.rol, "preparacion")
        self.assertEqual(str(user.salario_base), "1200.50")
        self.assertTrue(user.check_password("newpass"))

    def test_last_active_superadmin_cannot_be_deactivated_or_demoted(self):
        self.client.post(f"/admin/usuarios/{self.superadmin.id}/toggle")
        db.session.refresh(self.superadmin)
        self.assertTrue(self.superadmin.activo)

        self.client.post(
            f"/admin/usuarios/{self.superadmin.id}/editar",
            data={
                "nombre": self.superadmin.nombre,
                "email": self.superadmin.email,
                "rol": "admin",
                "telefono": self.superadmin.telefono,
                "salario_base": "0",
                "tarifa_entrega": "0",
            },
        )
        db.session.refresh(self.superadmin)
        self.assertEqual(self.superadmin.rol, "super_admin")

    def test_cannot_delete_self_and_confirmation_is_required(self):
        self.client.post(
            f"/admin/usuarios/{self.superadmin.id}/eliminar",
            data={"confirmacion": "ELIMINAR"},
        )
        self.assertIsNotNone(db.session.get(User, self.superadmin.id))

        user = self._user("Temporal", "temp@test.invalid", "preparacion")
        db.session.commit()
        self.client.post(
            f"/admin/usuarios/{user.id}/eliminar",
            data={"confirmacion": "si"},
        )
        self.assertIsNotNone(db.session.get(User, user.id))

    def test_pristine_user_is_deleted_physically(self):
        user = self._user("Temporal", "temp@test.invalid", "preparacion")
        user_id = user.id
        db.session.commit()

        self.client.post(
            f"/admin/usuarios/{user_id}/eliminar",
            data={"confirmacion": "ELIMINAR"},
        )

        self.assertIsNone(db.session.get(User, user_id))

    def test_user_with_history_is_deactivated_and_anonymized(self):
        user = self._user("Repartidor historico", "hist@test.invalid", "repartidor")
        db.session.add(StaffPayment(
            user_id=user.id,
            tipo="comision",
            monto=5,
            origen="manual",
        ))
        user_id = user.id
        db.session.commit()

        self.client.post(
            f"/admin/usuarios/{user_id}/eliminar",
            data={"confirmacion": "ELIMINAR"},
        )

        anonymized = db.session.get(User, user_id)
        self.assertIsNotNone(anonymized)
        self.assertFalse(anonymized.activo)
        self.assertEqual(anonymized.email, f"eliminado-{user_id}@usuarios.invalid")
        self.assertIsNone(anonymized.telefono)
        self.assertEqual(StaffPayment.query.filter_by(user_id=user_id).count(), 1)


if __name__ == "__main__":
    unittest.main()
