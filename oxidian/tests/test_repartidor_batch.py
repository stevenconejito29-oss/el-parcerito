"""HTTP tests para las rutas multi-pedido del repartidor.

Cubre `POST /repartidor/ruta/tomar-multiples` y `POST /ruta/salir-multiples`:
    * Un repartidor puede tomar varios pedidos en una sola acción.
    * Se ignoran los que ya no están disponibles (estado incorrecto o
      asignados a otro repartidor).
    * `salir-multiples` avanza cada uno a `en_ruta` y sigue con los
      demás si uno falla — la ruta del repartidor no debe romperse por
      un pedido puntual.
"""
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from extensions import db, login_manager
from models import Order, SiteConfig, User, utcnow
from routes.repartidor import repartidor_bp


class RepartidorBatchTest(unittest.TestCase):
    _seq = 0

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
        self.app.register_blueprint(repartidor_bp, url_prefix="/repartidor")

        @login_manager.user_loader
        def _load(user_id):
            return db.session.get(User, int(user_id))

        # public.index es el fallback si algo redirige; añadimos endpoint dummy.
        @self.app.route("/")
        def _root():
            return "ok"
        self.app.add_url_rule("/", endpoint="public.index", view_func=_root)

        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        SiteConfig.set("FEATURE_DELIVERY", "1", descripcion="test")

        self.cliente = self._user("Cli", "cli@t.invalid", "cliente",
                                  telefono="+34600000010")
        self.rep = self._user("Rep", "rep@t.invalid", "repartidor",
                              telefono="+34600000001")
        self.rep.en_linea = True
        self.rep.last_seen = utcnow()
        db.session.commit()

        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s["_user_id"] = str(self.rep.id)
            s["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _user(self, nombre, email, rol, **kw):
        RepartidorBatchTest._seq += 1
        u = User(nombre=nombre, email=email, rol=rol, activo=True, **kw)
        u.set_password("t")
        db.session.add(u); db.session.commit()
        return u

    def _pedido(self, estado="listo", repartidor_id=None):
        RepartidorBatchTest._seq += 1
        o = Order(
            numero_pedido=f"R-{self._seq:04d}",
            cliente_id=self.cliente.id,
            total=10, subtotal=10, estado=estado,
            tipo_entrega_cliente="delivery",
            repartidor_id=repartidor_id,
        )
        db.session.add(o); db.session.commit()
        return o

    def test_tomar_multiples_asigna_todos_disponibles(self):
        p1 = self._pedido()
        p2 = self._pedido()
        r = self.client.post("/repartidor/ruta/tomar-multiples",
                             data={"pedido_ids": [p1.id, p2.id]})
        self.assertEqual(r.status_code, 302)
        db.session.refresh(p1); db.session.refresh(p2)
        self.assertEqual(p1.repartidor_id, self.rep.id)
        self.assertEqual(p2.repartidor_id, self.rep.id)

    def _franja(self):
        from datetime import time, timedelta
        from business_time import business_today
        from models import DeliverySlot
        slot = DeliverySlot(fecha=business_today() + timedelta(days=1),
                            hora_inicio=time(12), hora_fin=time(14), capacidad_max=8)
        db.session.add(slot)
        db.session.commit()
        return slot

    def _delivery_journey(self, mode, scheduled=True):
        """Recorre roles reales desde un pedido aceptado, sin enviar avisos externos."""
        from contextlib import ExitStack
        from flask import g
        from datetime import datetime, timedelta
        from models import Product, OrderItem, Caja
        from routes.admin import admin_bp
        from routes.preparador import preparador_bp
        self.app.register_blueprint(admin_bp, url_prefix="/admin")
        self.app.register_blueprint(preparador_bp, url_prefix="/preparador")
        kitchen = self._user("Cocina QA", "kitchen-journey@test.invalid", "cocina")
        kitchen.en_linea = True
        kitchen.last_seen = utcnow()
        admin = self._user("Admin QA", "admin-journey@test.invalid", "super_admin")
        planned_slot = self._franja() if mode != "inmediato" else None
        slot = planned_slot if scheduled else None
        with self.client.session_transaction() as session:
            session["_user_id"] = str(admin.id)
        g.pop("_login_user", None)
        response = self.client.post("/admin/delivery/modo", data={"modo": mode})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SiteConfig.get("delivery_franjas_activo"), "0" if mode == "inmediato" else "1")
        order = self._pedido(estado="pendiente")
        from delivery_mode_service import resolver_plan_delivery
        from delivery_slots_service import reservar_franja, ResultadoReserva, resumen_preparacion_franjas
        plan = resolver_plan_delivery(slot.id if slot else None)
        if plan.slot_id:
            self.assertEqual(reservar_franja(plan.slot_id, order).tipo, ResultadoReserva.RESERVADA)
        order.metodo_pago = "efectivo"
        order.confirmacion_estado = "pending"
        product = Product(nombre="Compra QA", precio=10, precio_costo=3,
                          activo=True, canal_preparacion="cocina", tipo_entrega="inmediato")
        db.session.add(product)
        db.session.flush()
        db.session.add(OrderItem(pedido_id=order.id, producto_id=product.id,
                                cantidad=1, precio_unit=10, subtotal=10))
        db.session.commit()
        if slot:
            prep = resumen_preparacion_franjas([slot.id])[slot.id]
            self.assertEqual(prep["sin_confirmar"], 1)
            self.assertFalse(prep["preparacion_completa"])
        with ExitStack() as stack:
            for target in ("services.enviar_whatsapp_estado", "routes.repartidor.enviar_whatsapp_estado",
                           "routes.repartidor.solicitar_resena_pedido", "push_service.notify_order_state",
                           "push_service.notify_delivery_ready"):
                stack.enter_context(patch(target))
            with self.client.session_transaction() as session:
                session["_user_id"] = str(kitchen.id)
            g.pop("_login_user", None)
            self.client.post(f"/preparador/pedidos/{order.id}/empezar")
            db.session.refresh(order)
            self.assertEqual(order.estado, "pendiente", "WhatsApp pendiente bloquea cocina")
            order.confirmacion_estado = "confirmed"
            db.session.commit()
            self.client.post(f"/preparador/pedidos/{order.id}/empezar")
            db.session.refresh(order)
            self.assertEqual(order.estado, "armando")
            self.assertEqual(order.preparador_id, kitchen.id)
            self.client.post(f"/preparador/pedidos/{order.id}/listo")
            db.session.refresh(order)
            self.assertEqual(order.estado, "listo")
            with self.client.session_transaction() as session:
                session["_user_id"] = str(self.rep.id)
            g.pop("_login_user", None)
            if slot:
                self.assertIsNone(order.repartidor_id, "Una franja no se autoasigna como inmediato")
                self.client.post(f"/repartidor/franjas/{slot.id}/tomar")
                # Fuera de ventana no se permite despachar.
                self.client.post(f"/repartidor/franjas/{slot.id}/iniciar-reparto", data={"pedido_ids": [order.id]})
                db.session.refresh(order)
                self.assertEqual(order.estado, "listo")
                clock = datetime.combine(slot.fecha, slot.hora_inicio) + timedelta(minutes=1)
                with patch("delivery_slots_service.ahora_local_negocio", return_value=clock):
                    self.client.post(f"/repartidor/franjas/{slot.id}/iniciar-reparto", data={"pedido_ids": [order.id]})
            else:
                self.client.post(f"/repartidor/pedidos/{order.id}/tomar")
                self.client.post(f"/repartidor/pedidos/{order.id}/salir")
            db.session.refresh(order)
            self.assertEqual(order.estado, "en_ruta")
            self.assertEqual(order.repartidor_id, self.rep.id)
            order.codigo_confirmacion = "123456"
            order.codigo_confirmacion_expira_en = utcnow() + timedelta(minutes=10)
            db.session.commit()
            self.client.post(f"/repartidor/pedidos/{order.id}/entregar", data={"codigo_confirmacion": "123456"})
            db.session.refresh(order)
            self.assertEqual(order.estado, "en_ruta", "Sin cobro no hay cierre")
            self.client.post(f"/repartidor/pedidos/{order.id}/entregar", data={"codigo_confirmacion": "654321", "cobro_recibido": "1"})
            db.session.refresh(order)
            self.assertEqual(order.estado, "en_ruta", "Código incorrecto no entrega")
            for _ in range(2):
                self.client.post(f"/repartidor/pedidos/{order.id}/entregar", data={"codigo_confirmacion": "123456", "cobro_recibido": "1"})
            db.session.refresh(order)
            self.assertEqual(order.estado, "entregado")
            self.assertTrue(order.pago_confirmado)
            self.assertEqual(Caja.query.filter_by(pedido_id=order.id, tipo="ingreso").count(), 1)

    def test_immediate_journey_through_configuration_kitchen_rider_and_payment(self):
        self._delivery_journey("inmediato")

    def test_slot_journey_through_configuration_kitchen_rider_and_payment(self):
        self._delivery_journey("franjas")

    def test_mixed_mode_preserves_slot_journey(self):
        self._delivery_journey("mixto")

    def test_mixed_mode_preserves_immediate_journey(self):
        self._delivery_journey("mixto", scheduled=False)

    def test_immediate_routes_cannot_take_a_scheduled_order_in_mixed_mode(self):
        slot = self._franja()
        scheduled = self._pedido()
        scheduled.slot_id = slot.id
        immediate = self._pedido()
        SiteConfig.set("delivery_franjas_activo", "1")
        db.session.commit()
        response = self.client.post(f"/repartidor/pedidos/{scheduled.id}/tomar")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/franjas/panel"))
        self.client.post("/repartidor/ruta/tomar-multiples",
                         data={"pedido_ids": [scheduled.id, immediate.id]})
        db.session.refresh(scheduled)
        db.session.refresh(immediate)
        self.assertIsNone(scheduled.repartidor_id)
        self.assertEqual(immediate.repartidor_id, self.rep.id)

    def test_direct_departure_cannot_bypass_slot_window_or_batch(self):
        slot = self._franja()
        scheduled = self._pedido(repartidor_id=self.rep.id)
        scheduled.slot_id = slot.id
        SiteConfig.set("delivery_franjas_activo", "1")
        db.session.commit()
        for path, data in (
            (f"/repartidor/pedidos/{scheduled.id}/salir", {}),
            ("/repartidor/ruta/salir-multiples", {"pedido_ids": [scheduled.id]}),
        ):
            response = self.client.post(path, data=data)
            self.assertEqual(response.status_code, 302)
            db.session.refresh(scheduled)
            self.assertEqual(scheduled.estado, "listo")
            self.assertIsNone(scheduled.en_ruta_en)

    def test_switch_cannot_strand_immediate_orders_but_ignores_pickup(self):
        from delivery_mode_service import cambiar_modo_delivery, ErrorPlanDelivery
        self._franja()
        order = self._pedido()
        for state in ("pendiente", "armando", "listo", "en_ruta"):
            order.estado = state
            db.session.commit()
            with self.assertRaisesRegex(ErrorPlanDelivery, "apagar inmediato"):
                cambiar_modo_delivery("franjas", actor_id=self.rep.id)
            self.assertEqual(SiteConfig.get("delivery_inmediato_activo", "1"), "1")
        order.tipo_entrega_cliente = "recogida"
        db.session.commit()
        modes = cambiar_modo_delivery("franjas", actor_id=self.rep.id)
        self.assertEqual(modes, {"inmediato": False, "franjas": True})

    def test_switch_cannot_strand_scheduled_orders_and_mixed_keeps_both(self):
        from delivery_mode_service import cambiar_modo_delivery, ErrorPlanDelivery
        slot = self._franja()
        order = self._pedido()
        order.slot_id = slot.id
        db.session.commit()
        cambiar_modo_delivery("mixto", actor_id=self.rep.id)
        with self.assertRaisesRegex(ErrorPlanDelivery, "apagar franjas"):
            cambiar_modo_delivery("inmediato", actor_id=self.rep.id)
        self.assertEqual(SiteConfig.get("delivery_franjas_activo"), "1")
        order.estado = "entregado"
        db.session.commit()
        self.assertEqual(cambiar_modo_delivery("inmediato", actor_id=self.rep.id),
                         {"inmediato": True, "franjas": False})

    def test_tomar_multiples_omite_los_ya_asignados_a_otro(self):
        otro = self._user("Otro", "otro@t.invalid", "repartidor",
                          telefono="+34600000099")
        p_mio = self._pedido()
        p_otro = self._pedido(repartidor_id=otro.id)
        r = self.client.post(
            "/repartidor/ruta/tomar-multiples",
            data={"pedido_ids": [p_mio.id, p_otro.id]},
        )
        self.assertEqual(r.status_code, 302)
        db.session.refresh(p_mio); db.session.refresh(p_otro)
        self.assertEqual(p_mio.repartidor_id, self.rep.id)
        self.assertEqual(p_otro.repartidor_id, otro.id, "no puede robar pedidos")

    def test_tomar_multiples_respeta_capacidad_de_ruta(self):
        SiteConfig.set("MAX_PEDIDOS_POR_REPARTIDOR", "2", descripcion="test")
        db.session.commit()
        ya_asignado = self._pedido(repartidor_id=self.rep.id)
        candidato_a = self._pedido()
        candidato_b = self._pedido()

        response = self.client.post(
            "/repartidor/ruta/tomar-multiples",
            data={"pedido_ids": [candidato_a.id, candidato_b.id]},
        )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(ya_asignado)
        db.session.refresh(candidato_a)
        db.session.refresh(candidato_b)
        asignados = [
            pedido for pedido in (candidato_a, candidato_b)
            if pedido.repartidor_id == self.rep.id
        ]
        self.assertEqual(len(asignados), 1)
        self.assertEqual(
            Order.query.filter_by(repartidor_id=self.rep.id)
            .filter(Order.estado.in_(["listo", "en_ruta"])).count(),
            2,
        )

    def test_tomar_multiples_sin_seleccion_devuelve_warning(self):
        r = self.client.post("/repartidor/ruta/tomar-multiples", data={})
        self.assertEqual(r.status_code, 302)

    def test_salir_multiples_avanza_todos_asignados(self):
        p1 = self._pedido(repartidor_id=self.rep.id)
        p2 = self._pedido(repartidor_id=self.rep.id)
        with patch("routes.repartidor.avanzar_estado_pedido") as mav, \
             patch("routes.repartidor.enviar_whatsapp_estado"):
            def _fake(pedido, **kw):
                pedido.estado = "en_ruta"
            mav.side_effect = _fake
            r = self.client.post(
                "/repartidor/ruta/salir-multiples",
                data={"pedido_ids": [p1.id, p2.id]},
            )
        self.assertEqual(r.status_code, 302)
        db.session.refresh(p1); db.session.refresh(p2)
        self.assertEqual(p1.estado, "en_ruta")
        self.assertEqual(p2.estado, "en_ruta")

    def test_salir_multiples_continua_si_uno_falla(self):
        p1 = self._pedido(repartidor_id=self.rep.id)
        p2 = self._pedido(repartidor_id=self.rep.id)
        with patch("routes.repartidor.avanzar_estado_pedido") as mav, \
             patch("routes.repartidor.enviar_whatsapp_estado"):
            def _fake(pedido, **kw):
                if pedido.id == p1.id:
                    raise ValueError("boom")
                pedido.estado = "en_ruta"
            mav.side_effect = _fake
            r = self.client.post(
                "/repartidor/ruta/salir-multiples",
                data={"pedido_ids": [p1.id, p2.id]},
            )
        self.assertEqual(r.status_code, 302)
        db.session.refresh(p1); db.session.refresh(p2)
        self.assertEqual(p1.estado, "listo", "p1 quedó como estaba")
        self.assertEqual(p2.estado, "en_ruta", "p2 avanzó pese al fallo de p1")

    def test_salir_multiples_rechaza_nuevo_trabajo_estando_offline(self):
        pedido = self._pedido(repartidor_id=self.rep.id)
        self.rep.en_linea = False
        db.session.commit()

        with patch("routes.repartidor.avanzar_estado_pedido") as avanzar:
            response = self.client.post(
                "/repartidor/ruta/salir-multiples",
                data={"pedido_ids": [pedido.id]},
            )

        self.assertEqual(response.status_code, 302)
        db.session.refresh(pedido)
        self.assertEqual(pedido.estado, "listo")
        avanzar.assert_not_called()

    def test_ruta_no_expone_entrega_asignada_a_otro_repartidor(self):
        otro = self._user(
            "Otro en ruta",
            "otro-ruta@t.invalid",
            "repartidor",
            telefono="+34600000098",
        )
        propio = self._pedido(estado="en_ruta", repartidor_id=self.rep.id)
        ajeno = self._pedido(estado="en_ruta", repartidor_id=otro.id)

        with patch("routes.repartidor.render_template", return_value="ok") as render:
            response = self.client.get("/repartidor/ruta")

        self.assertEqual(response.status_code, 200)
        ids_visibles = {pedido.id for pedido in render.call_args.kwargs["en_ruta"]}
        self.assertEqual(ids_visibles, {propio.id})
        self.assertNotIn(ajeno.id, ids_visibles)


if __name__ == "__main__":
    unittest.main()
