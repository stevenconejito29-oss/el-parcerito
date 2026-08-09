import unittest
from pathlib import Path

from flask import Flask

from extensions import db, login_manager
from models import SiteConfig, User
from routes.preparador import preparador_bp


class KitchenPrinterPreferenceTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(root / "templates"))
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="printer-test",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SESSION_PROTECTION=None,
        )
        db.init_app(self.app)
        login_manager.init_app(self.app)
        self.app.register_blueprint(preparador_bp, url_prefix="/preparador")

        @login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.user = User(nombre="Cocina", email="cocina@printer.test", rol="cocina")
        self.user.set_password("secret")
        db.session.add(self.user)
        db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user.id)
            session["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_round_trip_and_delete_printer_hint(self):
        payload = {"transport": "bt", "device_id": "opaque-device-id", "name": "POS58 Cocina"}
        saved = self.client.put("/preparador/impresora", json=payload)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["printer"], payload)
        loaded = self.client.get("/preparador/impresora")
        self.assertEqual(loaded.get_json()["printer"], payload)
        self.assertIsNotNone(SiteConfig.query.filter_by(clave=f"THERMAL_PRINTER_U_{self.user.id}").first())
        self.assertEqual(self.client.delete("/preparador/impresora").status_code, 200)
        self.assertIsNone(self.client.get("/preparador/impresora").get_json()["printer"])

    def test_rejects_bluetooth_without_opaque_device_id(self):
        response = self.client.put("/preparador/impresora", json={"transport": "bt", "name": "POS58"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
