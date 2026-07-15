"""Tests de que `notify_new_order` avisa al rol operativo correcto.

Antes: solo notificaba a admin/super_admin — cocina y preparación se
enteraban recargando la vista. Ahora un encargo dispara push a
'preparacion' y un inmediato a 'cocina', además del aviso a admins.
"""
import unittest
from unittest.mock import patch

from flask import Flask

from extensions import db
from models import Order, User


class NotifyNewOrderTest(unittest.TestCase):
    _seq = 0

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.cliente = self._mk_user("Cli", "cliente", "+34611111111")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk_user(self, nombre, rol, tel):
        NotifyNewOrderTest._seq += 1
        u = User(
            nombre=nombre,
            email=f"{nombre.lower()}-{NotifyNewOrderTest._seq}@t.invalid",
            telefono=tel,
            telefono_normalizado=tel,
            rol=rol,
            activo=True,
        )
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        return u

    def _mk_pedido(self):
        NotifyNewOrderTest._seq += 1
        o = Order(
            numero_pedido=f"TEST-N-{NotifyNewOrderTest._seq}",
            cliente_id=self.cliente.id,
            total=10,
            subtotal=10,
            estado="pendiente",
            tipo_entrega_cliente="delivery",
            origen="online",
        )
        db.session.add(o)
        db.session.commit()
        return o

    def _capturar_notif_roles(self, tipo_pedido):
        """Ejecuta notify_new_order interceptando las llamadas a notify_roles."""
        import push_service
        capturado = []

        def fake_notify_roles(roles, title, body, url="/", **_kw):
            capturado.append({"roles": roles, "title": title, "url": url})

        pedido = self._mk_pedido()
        with patch("services._tipo_pedido", return_value=tipo_pedido), \
             patch.object(push_service, "notify_roles", side_effect=fake_notify_roles):
            push_service.notify_new_order(pedido)
        return capturado

    def test_pedido_programado_avisa_preparacion(self):
        llamadas = self._capturar_notif_roles("programado")
        roles_avisados = {r for c in llamadas for r in c["roles"]}
        self.assertIn("admin", roles_avisados)
        self.assertIn("preparacion", roles_avisados)
        self.assertNotIn("cocina", roles_avisados)

    def test_pedido_inmediato_avisa_cocina(self):
        llamadas = self._capturar_notif_roles("inmediato")
        roles_avisados = {r for c in llamadas for r in c["roles"]}
        self.assertIn("admin", roles_avisados)
        self.assertIn("cocina", roles_avisados)
        self.assertNotIn("preparacion", roles_avisados)

    def test_url_del_rol_operativo_apunta_a_su_vista(self):
        llamadas = self._capturar_notif_roles("programado")
        prep_call = next(c for c in llamadas if "preparacion" in c["roles"])
        self.assertEqual(prep_call["url"], "/preparador/pedidos")

    def test_no_rompe_si_tipo_pedido_falla(self):
        """Fallo en _tipo_pedido no debe impedir el aviso a admin."""
        import push_service
        capturado = []

        def fake_notify_roles(roles, title, body, url="/", **_kw):
            capturado.append(roles)

        pedido = self._mk_pedido()
        with patch("services._tipo_pedido", side_effect=RuntimeError("boom")), \
             patch.object(push_service, "notify_roles", side_effect=fake_notify_roles):
            push_service.notify_new_order(pedido)
        # Aún debe notificar a admin aunque el helper de rol falle
        self.assertTrue(any("admin" in r for r in capturado))


if __name__ == "__main__":
    unittest.main()
