import unittest
from decimal import Decimal

from flask import Flask, session
from werkzeug.datastructures import MultiDict

from extensions import db
from models import AffiliateCode, AffiliateUse, Order, StaffPayment, User
from routes.public import public_bp
from routes.admin import _parse_affiliate_form
from services import registrar_uso_afiliado


class AffiliateFlowTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="affiliate-test",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(public_bp)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()
        self.customer = self._user("Cliente", "cliente", "+34610000010")
        self.employee = self._user("Afiliado", "repartidor", "+34610000011")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _user(self, name, role, phone):
        user = User(
            nombre=name,
            email=f"{name.lower()}@affiliate.invalid",
            telefono=phone,
            rol=role,
            activo=True,
        )
        user.set_password("test-only")
        db.session.add(user)
        db.session.commit()
        return user

    def _code(self, **overrides):
        values = {
            "codigo": "REFERIDO",
            "activo": True,
            "descuento_tipo": "porcentaje",
            "descuento_valor": Decimal("10"),
            "comision_tipo": "porcentaje",
            "comision_valor": Decimal("5"),
            "user_id": self.employee.id,
        }
        values.update(overrides)
        code = AffiliateCode(**values)
        db.session.add(code)
        db.session.commit()
        return code

    def _order(self, number="AFF-001", total=20):
        order = Order(
            numero_pedido=number,
            cliente_id=self.customer.id,
            subtotal=Decimal(str(total)),
            descuento=0,
            total=Decimal(str(total)),
            estado="pendiente",
        )
        db.session.add(order)
        db.session.commit()
        return order

    def test_preview_uses_the_same_discount_cap_as_checkout(self):
        self._code(descuento_valor=Decimal("80"))
        response = self.client.post(
            "/carrito/afiliado",
            json={"codigo": "referido", "subtotal": 100},
        )
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["descuento"], 30.0)

    def test_invalid_code_removes_stale_session_selection(self):
        with self.client.session_transaction() as browser_session:
            browser_session["cart_afiliado"] = {"codigo": "BORRADO"}
        response = self.client.post(
            "/carrito/afiliado", json={"codigo": "BORRADO", "subtotal": 20},
        )
        self.assertFalse(response.get_json()["ok"])
        with self.client.session_transaction() as browser_session:
            self.assertNotIn("cart_afiliado", browser_session)

    def test_commission_only_code_is_registered_and_paid_to_assignee(self):
        code = self._code(descuento_tipo=None, descuento_valor=0)
        order = self._order()
        use = registrar_uso_afiliado(code, order, self.customer, 0)
        db.session.commit()

        self.assertEqual(AffiliateUse.query.count(), 1)
        self.assertEqual(float(use.descuento_aplicado), 0)
        self.assertGreater(float(use.comision_generada), 0)
        payment = StaffPayment.query.filter_by(origen="affiliate").one()
        self.assertFalse(payment.disponible_para_pago)
        order.estado = "entregado"
        db.session.commit()
        self.assertTrue(payment.disponible_para_pago)
        self.assertEqual(code.usos_actuales, 1)

    def test_registration_is_idempotent_for_the_same_order(self):
        code = self._code()
        order = self._order()
        first = registrar_uso_afiliado(code, order, self.customer, 2)
        db.session.flush()
        second = registrar_uso_afiliado(code, order, self.customer, 2)
        db.session.commit()

        self.assertEqual(first.id, second.id)
        self.assertEqual(AffiliateUse.query.count(), 1)
        self.assertEqual(code.usos_actuales, 1)

    def test_admin_rejects_configuration_that_checkout_would_cap(self):
        form = MultiDict({
            "codigo": "MUCHO",
            "tipo": "externo",
            "descuento_tipo": "porcentaje",
            "descuento_valor": "31",
            "comision_tipo": "",
            "comision_valor": "0",
        })
        with self.assertRaisesRegex(ValueError, "30%"):
            _parse_affiliate_form(form)

    def test_staff_code_requires_an_active_assignee(self):
        form = MultiDict({
            "codigo": "STAFF01",
            "tipo": "staff",
            "descuento_tipo": "",
            "descuento_valor": "0",
            "comision_tipo": "porcentaje",
            "comision_valor": "5",
        })
        with self.assertRaisesRegex(ValueError, "asignado"):
            _parse_affiliate_form(form)

    def test_admin_rejects_inverted_validity_dates(self):
        form = MultiDict({
            "codigo": "FECHAS",
            "tipo": "externo",
            "descuento_tipo": "",
            "descuento_valor": "0",
            "comision_tipo": "",
            "comision_valor": "0",
            "fecha_inicio": "2026-08-02",
            "fecha_fin": "2026-08-01",
        })
        with self.assertRaisesRegex(ValueError, "fecha final"):
            _parse_affiliate_form(form)


if __name__ == "__main__":
    unittest.main()
