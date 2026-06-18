import unittest

from flask import Flask

from models import ROLES_AUTENTICABLES, User
from routes.auth import _complete_login, auth_bp
from routes.public import public_bp


class EmployeeAuthBoundaryTest(unittest.TestCase):
    def test_only_internal_roles_are_authenticable(self):
        expected = {
            "super_admin", "admin", "preparacion", "repartidor", "proveedor",
            "cocina", "staff",
        }
        self.assertEqual(set(ROLES_AUTENTICABLES), expected)

        customer = User(
            nombre="Cliente prueba",
            email="cliente@test.invalid",
            rol="cliente",
            activo=True,
        )
        customer.set_password("unused-password")
        self.assertFalse(customer.puede_iniciar_sesion)

    def test_login_helper_rejects_customer_records(self):
        customer = User(
            nombre="Cliente prueba",
            email="cliente@test.invalid",
            rol="cliente",
            activo=True,
        )
        customer.set_password("unused-password")

        app = Flask(__name__)
        app.secret_key = "test-only"
        with app.test_request_context("/auth/login", method="POST"):
            with self.assertRaises(ValueError):
                _complete_login(customer)

    def test_public_customer_account_routes_do_not_exist(self):
        app = Flask(__name__)
        app.register_blueprint(auth_bp, url_prefix="/auth")
        app.register_blueprint(public_bp)
        endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}

        self.assertNotIn("auth.registro", endpoints)
        self.assertNotIn("public.perfil", endpoints)
        self.assertNotIn("public.editar_perfil", endpoints)
        self.assertNotIn("public.dejar_resena", endpoints)
        self.assertNotIn("public.auth_verificar_puntos", endpoints)


if __name__ == "__main__":
    unittest.main()
