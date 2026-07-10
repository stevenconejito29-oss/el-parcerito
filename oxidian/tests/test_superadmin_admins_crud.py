import unittest
from pathlib import Path

from flask import Flask

from extensions import db, login_manager
from models import SiteConfig, User
from routes.superadmin import superadmin_bp


class SuperadminAdminsCrudTest(unittest.TestCase):
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
        self.app.register_blueprint(superadmin_bp, url_prefix="/superadmin")

        @login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        SiteConfig.set("WHATSAPP_COUNTRY_CODE", "+34")
        self.superadmin = User(
            nombre="Super Admin",
            email="super@test.invalid",
            rol="super_admin",
            telefono="+34600000000",
        )
        self.superadmin.set_password("password")
        db.session.add(self.superadmin)
        db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.superadmin.id)
            session["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_superadmin_requires_phone_when_creating_admin_accounts(self):
        response = self.client.post(
            "/superadmin/admins/crear",
            data={
                "nombre": "Admin sin telefono",
                "email": "admin-sin-telefono@test.invalid",
                "password": "secret123456",
                "rol": "admin",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(User.query.filter_by(email="admin-sin-telefono@test.invalid").first())

    def test_superadmin_creates_admin_with_unique_bot_phone(self):
        response = self.client.post(
            "/superadmin/admins/crear",
            data={
                "nombre": "Admin tienda",
                "email": "admin-tienda@test.invalid",
                "password": "secret123456",
                "rol": "admin",
                "telefono": "+34 620 333 444",
            },
        )

        self.assertEqual(response.status_code, 302)
        created = User.query.filter_by(email="admin-tienda@test.invalid").first()
        self.assertIsNotNone(created)
        self.assertEqual(created.telefono, "+34620333444")

        self.client.post(
            "/superadmin/admins/crear",
            data={
                "nombre": "Admin duplicado",
                "email": "admin-duplicado@test.invalid",
                "password": "secret123456",
                "rol": "admin",
                "telefono": "620333444",
            },
        )
        self.assertIsNone(User.query.filter_by(email="admin-duplicado@test.invalid").first())

    def test_superadmin_rejects_local_phone_without_country_prefix(self):
        SiteConfig.set("WHATSAPP_COUNTRY_CODE", "")
        db.session.commit()

        self.client.post(
            "/superadmin/admins/crear",
            data={
                "nombre": "Admin ambiguo",
                "email": "admin-ambiguo@test.invalid",
                "password": "secret123456",
                "rol": "admin",
                "telefono": "620 333 555",
            },
        )

        self.assertIsNone(User.query.filter_by(email="admin-ambiguo@test.invalid").first())


if __name__ == "__main__":
    unittest.main()
