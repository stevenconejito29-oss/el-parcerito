import unittest

from flask import Flask

from models import CUSTOMER_INTERNAL_EMAIL_DOMAIN, ROLES_AUTENTICABLES, User, internal_customer_email
from routes.auth import _complete_login, auth_bp
from routes.public import public_bp


class EmployeeAuthBoundaryTest(unittest.TestCase):
    def test_only_internal_roles_are_authenticable(self):
        expected = {
            "super_admin", "admin", "preparacion", "repartidor", "cocina",
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
        self.assertEqual(
            internal_customer_email("+34 610 000 001"),
            f"cliente.34610000001@{CUSTOMER_INTERNAL_EMAIL_DOMAIN}",
        )
        customer.email = internal_customer_email("+34 610 000 001")
        self.assertIsNone(customer.email_visible)
        customer.email = "cliente@example.com"
        self.assertEqual(customer.email_visible, "cliente@example.com")

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
