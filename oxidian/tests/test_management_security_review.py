"""Regresiones de permisos, importes y autenticación con datos aislados."""
import time
import unittest
from decimal import Decimal
from unittest.mock import patch

from flask import Flask
from werkzeug.datastructures import MultiDict

from extensions import db, login_manager
from models import AdminFeature, Caja, Order, OrderItem, PriceHistory, Product, User
from routes.admin import admin_bp, _money, _parsear_campos_producto
from routes.auth import auth_bp, _next_is_safe_get


class ManagementSecurityReviewTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="test-only",
                               SQLALCHEMY_DATABASE_URI="sqlite://",
                               WTF_CSRF_ENABLED=False, SESSION_PROTECTION=None)
        db.init_app(self.app)
        login_manager.init_app(self.app)
        self.app.register_blueprint(admin_bp, url_prefix="/admin")
        self.app.register_blueprint(auth_bp, url_prefix="/auth")
        login_manager.user_loader(lambda uid: db.session.get(User, int(uid)))
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.user = User(nombre="QA", email="audit@test.invalid", rol="super_admin", activo=True)
        self.user.set_password("test-only-password")
        self.product = Product(nombre="Producto QA", precio=10, activo=True)
        db.session.add_all([self.user, self.product])
        db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user.id)
            session["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_finance_routes_require_cash_permission_before_running_view(self):
        self.user.rol = "admin"
        db.session.commit()
        for path in ("/admin/finanzas", "/admin/finanzas/cierres"):
            with self.subTest(path=path):
                endpoint = self.app.url_map.bind("localhost").match(path)[0]
                with patch.dict(self.app.view_functions, {endpoint: lambda: "allowed"}):
                    denied = self.client.get(path)
                    self.assertEqual(denied.status_code, 302)
                    self.assertNotEqual(denied.data, b"allowed")
        permission = AdminFeature(user_id=self.user.id, feature="caja", activo=True)
        db.session.add(permission)
        db.session.commit()
        with patch.dict(self.app.view_functions, {"admin.finanzas": lambda: "allowed"}):
            self.assertEqual(self.client.get("/admin/finanzas").data, b"allowed")
            permission.activo = False
            db.session.commit()
            self.assertEqual(self.client.get("/admin/finanzas").status_code, 302)
            self.user.rol = "super_admin"
            db.session.commit()
            self.assertEqual(self.client.get("/admin/finanzas").data, b"allowed")

    def test_cash_rejects_invalid_amounts_without_writing(self):
        for value in ("NaN", "Infinity", "-Infinity", "sNaN", "abc", "-1", "0", ""):
            with self.subTest(value=value):
                response = self.client.post("/admin/caja/movimiento", data={
                    "tipo": "ingreso", "monto": value, "concepto": "QA", "categoria": "otro",
                })
                self.assertEqual(response.status_code, 302)
                self.assertEqual(Caja.query.count(), 0)

    def test_cash_accepts_regular_decimal_amount(self):
        response = self.client.post("/admin/caja/movimiento", data={
            "tipo": "ingreso", "monto": "12.35", "concepto": "QA", "categoria": "otro",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Caja.query.one().monto, Decimal("12.35"))

    def test_price_change_rejects_invalid_values_without_history(self):
        for value in ("NaN", "Infinity", "-Infinity", "sNaN", "abc", "0", "-1"):
            with self.subTest(value=value):
                response = self.client.post(f"/admin/productos/{self.product.id}/precio/cambiar",
                                            data={"precio": value})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(self.product.precio, 10)
                self.assertEqual(PriceHistory.query.count(), 0)

    @patch("routes.admin.notificar_bot_sync")
    def test_price_change_keeps_history_and_reward_invariants(self, sync):
        path = f"/admin/productos/{self.product.id}/precio/cambiar"
        self.client.post(path, data={"precio": "12.35"})
        self.assertEqual(self.product.precio, Decimal("12.35"))
        self.assertEqual(PriceHistory.query.one().precio_anterior, 10)
        sync.assert_called_once()
        self.product.solo_canje = True
        self.product.canjeable_con_puntos = True
        self.product.puntos_para_canje = 20
        self.product.precio = 0
        db.session.commit()
        self.client.post(path, data={"precio": "15"})
        self.assertEqual(self.product.precio, 0)
        self.assertEqual(PriceHistory.query.count(), 1)

    def test_product_cost_rejects_invalid_input(self):
        with patch("routes.admin.get_store_features", return_value={"puntos": False}):
            for value in ("NaN", "Infinity", "-Infinity", "abc", "-1"):
                with self.subTest(value=value):
                    fields, error = _parsear_campos_producto(MultiDict(
                        {"nombre": "QA", "precio": "10", "precio_costo": value}))
                    self.assertIsNone(fields)
                    self.assertIn("costo", error)

    def test_deleting_sold_product_preserves_order_and_archives_catalog_entry(self):
        from sqlalchemy import text
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        order = Order(numero_pedido="AUDIT-1", cliente_id=self.user.id,
                      subtotal=10, total=10, estado="entregado")
        item = OrderItem(producto_id=self.product.id, cantidad=1, precio_unit=10,
                         subtotal=10, metadata_json='{"producto":{"nombre":"Compra original"}}')
        order.items.append(item)
        db.session.add(order)
        db.session.commit()
        response = self.client.post(f"/admin/productos/{self.product.id}/eliminar")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.product.activo)
        self.assertEqual(OrderItem.query.one().display_nombre, "Compra original")
        self.assertEqual(OrderItem.query.one().precio_unit, 10)

    def test_money_rejects_nonfinite_and_rounds_valid_amount(self):
        from combo_validators import validate_combo_pricing
        for value in ("NaN", "Infinity", "-Infinity", "sNaN", "abc"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _money(value)
        self.assertEqual(_money("1.235"), Decimal("1.24"))
        for value in (float("nan"), float("inf"), float("-inf")):
            self.assertFalse(validate_combo_pricing(value)[0])
            self.assertFalse(validate_combo_pricing(10, value)[0])

    def test_login_redirect_only_accepts_unambiguous_internal_get(self):
        for value in (None, "", "https://evil.test", "//evil.test", "/\\evil.test",
                      "/\n/evil.test", "/\t/evil.test", "admin", "http://[",
                      "/admin/productos/1/eliminar"):
            with self.subTest(value=value):
                self.assertFalse(_next_is_safe_get(value))
        self.assertTrue(_next_is_safe_get("/admin/pedidos?page=2"))

    def test_password_replacement_revokes_existing_session_version(self):
        version = self.user.mfa_session_version
        self.user.set_password("replacement-test-password")
        db.session.commit()
        self.assertEqual(self.user.mfa_session_version, version + 1)
        self.assertTrue(self.user.check_password("replacement-test-password"))
        self.assertFalse(self.user.check_password("test-only-password"))

    def pending_mfa(self, timestamp, password_hash):
        self.user.mfa_enabled = True
        self.user.mfa_secret = "JBSWY3DPEHPK3PXP"
        db.session.commit()
        with self.client.session_transaction() as session:
            session.clear()
            session["mfa_pending_user_id"] = self.user.id
            if timestamp is not None:
                session["mfa_pending_at"] = timestamp
            if password_hash is not None:
                session["mfa_pending_pw_hash"] = password_hash

    @patch("routes.auth._verify_totp", return_value=True)
    def test_mfa_rejects_missing_malformed_expired_or_future_timestamp(self, verify):
        for timestamp in (None, 0, "invalid", int(time.time()) - 301, int(time.time()) + 60):
            with self.subTest(timestamp=timestamp):
                self.pending_mfa(timestamp, self.user.password_hash)
                response = self.client.post("/auth/login/mfa", data={"code": "123456"})
                self.assertEqual(response.location, "/auth/login")
                with self.client.session_transaction() as session:
                    self.assertNotIn("mfa_pending_user_id", session)
                    self.assertNotIn("_user_id", session)
        verify.assert_not_called()

    @patch("routes.auth._verify_totp", return_value=True)
    def test_mfa_rejects_missing_or_changed_password_binding(self, verify):
        for password_hash in (None, "", "changed-password-hash"):
            self.pending_mfa(int(time.time()), password_hash)
            response = self.client.post("/auth/login/mfa", data={"code": "123456"})
            self.assertEqual(response.location, "/auth/login")
        verify.assert_not_called()

    @patch("routes.auth._complete_login")
    @patch("routes.auth._verify_totp", return_value=True)
    def test_valid_mfa_challenge_completes_login(self, verify, complete):
        self.pending_mfa(int(time.time()), self.user.password_hash)
        with self.client.session_transaction() as session:
            session["mfa_pending_next"] = "/admin/pedidos"
        response = self.client.post("/auth/login/mfa", data={"code": "123456"})
        self.assertEqual(response.location, "/admin/pedidos")
        complete.assert_called_once_with(self.user)

    @patch("routes.auth.render_template", return_value="enabled")
    @patch("routes.auth._verify_totp", return_value=True)
    def test_enabled_mfa_cannot_be_overwritten_by_stale_setup(self, verify, render):
        self.user.mfa_enabled = True
        self.user.mfa_secret = "JBSWY3DPEHPK3PXP"
        db.session.commit()
        with self.client.session_transaction() as session:
            session["mfa_setup_secret"] = "ANOTHERSECRET"
        response = self.client.post("/auth/perfil/mfa", data={"code": "123456"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.user.mfa_secret, "JBSWY3DPEHPK3PXP")
        verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
