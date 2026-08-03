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
        SiteConfig.set("STORE_FEATURE_DELIVERY", "1", descripcion="test")

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
