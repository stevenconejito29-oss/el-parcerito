import unittest
from decimal import Decimal
from unittest.mock import patch

from flask import Flask
from extensions import db, login_manager
from models import User, ZonaEntrega
from routes.admin import admin_bp


class DeliveryAdminPanelTest(unittest.TestCase):
    def test_pickup_readiness_requires_business_address(self):
        from models import SiteConfig
        from commerce_readiness import commerce_readiness
        SiteConfig.set("FEATURE_RECOGIDA", "1")
        SiteConfig.set("DIRECCION_NEGOCIO", "")
        db.session.commit()
        check = lambda: next(row for row in commerce_readiness()["checks"] if row["key"] == "pickup_address")
        self.assertFalse(check()["ok"])
        SiteConfig.set("DIRECCION_NEGOCIO", "Calle QA 12")
        db.session.commit()
        self.assertTrue(check()["ok"])

    def test_unconfigured_bizum_does_not_count_as_available_payment(self):
        from models import SiteConfig
        from commerce_readiness import commerce_readiness
        for key, value in {"EFECTIVO_HABILITADO":"0", "TARJETA_HABILITADA":"0", "BIZUM_HABILITADO":"1", "BIZUM_TELEFONO":""}.items():
            SiteConfig.set(key,value)
        db.session.commit()
        check = lambda: next(row for row in commerce_readiness()["checks"] if row["key"] == "payments")
        self.assertFalse(check()["ok"])
        SiteConfig.set("BIZUM_TELEFONO", "+34610000001")
        db.session.commit()
        self.assertTrue(check()["ok"])
        self.assertIn("recibir o recoger", check()["detail"])

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="test-only",
                               SQLALCHEMY_DATABASE_URI="sqlite://",
                               WTF_CSRF_ENABLED=False, SESSION_PROTECTION=None)
        db.init_app(self.app)
        login_manager.init_app(self.app)
        self.app.register_blueprint(admin_bp, url_prefix="/admin")
        login_manager.user_loader(lambda user_id: db.session.get(User, int(user_id)))
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.user = User(nombre="Gestión QA", email="delivery-admin@test.invalid",
                         rol="super_admin", activo=True)
        self.user.set_password("test-only-password")
        db.session.add(self.user)
        db.session.add_all([
            ZonaEntrega(nombre="Zona A", activo=True, precio_envio=2),
            ZonaEntrega(nombre="Zona B", activo=True, precio_envio=4),
            ZonaEntrega(nombre="Zona apagada", activo=False, precio_envio=99),
        ])
        db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user.id)
            session["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    @patch("routes.admin.render_template", return_value="panel")
    def test_planning_loads_with_slots_disabled_and_only_active_zone_fees(self, render):
        response = self.client.get("/admin/delivery/franjas/panel")
        self.assertEqual(response.status_code, 200)
        context = render.call_args.kwargs
        self.assertEqual(context["delivery_zone_count"], 2)
        self.assertEqual(context["delivery_fee_min"], Decimal("2"))
        self.assertEqual(context["delivery_fee_max"], Decimal("4"))
        self.assertFalse(context["modulo_activo"])
        self.assertTrue(context["can_switch_mode"])

    def test_readiness_uses_bookable_slots_and_distinguishes_mixed_mode(self):
        from datetime import datetime, time, timedelta
        from business_time import business_today
        from models import DeliverySlot, SiteConfig, Order
        from commerce_readiness import commerce_readiness
        tomorrow = business_today() + timedelta(days=1)
        slot = DeliverySlot(fecha=tomorrow, hora_inicio=time(12), hora_fin=time(14),
                            capacidad_max=1, activo=True)
        db.session.add(slot)
        SiteConfig.set("FEATURE_DELIVERY", "1")
        SiteConfig.set("delivery_inmediato_activo", "0")
        SiteConfig.set("delivery_franjas_activo", "1")
        db.session.commit()

        def status():
            return next(row for row in commerce_readiness()["checks"] if row["key"] == "slots")

        with patch("delivery_slots_service.asegurar_horizonte_recurrente") as recurring:
            self.assertTrue(status()["ok"])
            recurring.assert_not_called()
        with patch("delivery_slots_service.ahora_local_negocio", return_value=datetime.combine(tomorrow, time(12, 1))):
            self.assertFalse(status()["ok"], "Una franja iniciada no admite compras")
            self.assertFalse(status()["warning"], "Solo franjas sin cupo es bloqueo")
        order = Order(numero_pedido="READINESS-FULL", cliente_id=self.user.id,
                      estado="pendiente", total=10, subtotal=10, slot_id=slot.id)
        db.session.add(order)
        db.session.commit()
        self.assertFalse(status()["ok"], "Una franja llena tampoco admite compras")
        SiteConfig.set("delivery_inmediato_activo", "1")
        db.session.commit()
        self.assertTrue(status()["warning"], "En mixto queda disponible inmediato")
