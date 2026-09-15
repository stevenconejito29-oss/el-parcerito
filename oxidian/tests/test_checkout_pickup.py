"""Checkout real en SQLite aislado, sin notificaciones ni pedidos externos."""
import os
import unittest
from unittest.mock import patch
from config import DevelopmentConfig
from app import create_app
from extensions import db
from models import Product, SiteConfig, Order


class CheckoutPickupTest(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {'OXIDIAN_SKIP_STARTUP_DB':'1'}), patch.object(DevelopmentConfig,'SQLALCHEMY_DATABASE_URI','sqlite://'):
            self.app=create_app('development')
        self.app.config.update(TESTING=True,WTF_CSRF_ENABLED=False)
        with self.app.app_context():
            db.create_all()
            for key,value in {'FEATURE_DELIVERY':'1','FEATURE_RECOGIDA':'1','TIENDA_FORZAR_ABIERTA':'1','TIENDA_FORZAR_CERRADA':'0','DELIVERY_MODO':'franjas','EFECTIVO_HABILITADO':'1'}.items():SiteConfig.set(key,value)
            product=Product(nombre='Compra de recogida',precio=10,activo=True,canal_preparacion='cocina',tipo_entrega='inmediato')
            db.session.add(product);db.session.commit();self.product_id=product.id
        self.client=self.app.test_client()
        with self.client.session_transaction() as s:s['carrito']={str(self.product_id):1}
        self.form={'acepta_condiciones':'1','nombre_invitado':'Prueba recogida','telefono_invitado':'+34610000001','tipo_entrega_cliente':'recogida','metodo_pago':'efectivo','slot_id':'999','direccion':'No debe heredarse','direccion_lat':'45','direccion_lng':'8','zona_id':'999'}

    def tearDown(self):
        with self.app.app_context():db.session.remove();db.drop_all()

    @patch('routes.public.enviar_whatsapp_estado',return_value=False)
    @patch('push_service.notify_new_order')
    @patch('delivery_mode_service.resolver_plan_delivery')
    def test_pickup_ignores_forged_delivery_fields_and_preserves_first_order_gate(self,plan,push,whatsapp):
        result=self.client.post('/checkout',data=self.form)
        self.assertEqual(result.status_code,302)
        self.assertRegex(result.location,r'/pedido/\d+/confirmado$')
        plan.assert_not_called()
        with self.app.app_context():
            order=Order.query.one()
            self.assertEqual(order.tipo_entrega_cliente,'recogida')
            self.assertEqual(order.estado,'pendiente')
            self.assertEqual(order.confirmacion_estado,'pending')
            self.assertFalse(order.pago_confirmado)
            self.assertFalse(order.direccion_entrega)
            self.assertIsNone(order.slot_id);self.assertIsNone(order.zona_id)
            self.assertIsNone(order.repartidor_id);self.assertIsNone(order.preparador_id)
            self.assertEqual(float(order.costo_envio),0)
        with self.client.session_transaction() as s:secret=s['guest_order_tokens']['1']['token']
        page=self.client.get(result.location)
        self.assertNotIn(secret.encode(),page.data)
        self.assertEqual(self.app.test_client().get('/pedido/1/estado').status_code,403)
        self.assertEqual(self.app.test_client().get('/pedido/1/estado?token='+secret).status_code,403)
        self.assertEqual(self.client.get('/pedido/1/estado').status_code,200)

    @patch('routes.public.enviar_whatsapp_estado',return_value=False)
    @patch('push_service.notify_new_order')
    def test_disabled_pickup_rejects_forged_submission(self,push,whatsapp):
        with self.app.app_context():SiteConfig.set('FEATURE_RECOGIDA','0');db.session.commit()
        result=self.client.post('/checkout',data=self.form)
        self.assertEqual(result.status_code,302)
        with self.app.app_context():self.assertEqual(Order.query.count(),0)
        whatsapp.assert_not_called();push.assert_not_called()
