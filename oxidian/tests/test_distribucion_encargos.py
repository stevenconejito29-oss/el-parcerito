"""Tests de la asignación de pedidos programados (encargos) al rol correcto.

Antes: `distribuir_pedido` caía a `cocina` como alternativa cuando no había
staff `preparacion` online. Efecto en producción: los encargos aparecían en
el dashboard de cocina (que filtra `not _es_encargo`), invisibles para el
rol correcto y visibles para el equivocado.

Ahora: los encargos NO tienen rol_alternativo. Si no hay preparación
online, quedan sin asignar (visibles en cola de admin) hasta que alguien
del rol correcto se ponga online — momento en que
`redistribuir_pendientes_sin_asignar` los reparte.
"""
import unittest
from unittest.mock import patch

from flask import Flask

from extensions import db
from models import Order, User


class DistribucionEncargosTest(unittest.TestCase):
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

        self.cliente = self._mk_user("Cliente", "cliente", "+34611111111")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk_user(self, nombre, rol, telefono):
        DistribucionEncargosTest._seq += 1
        u = User(
            nombre=nombre,
            email=f"{nombre.lower()}-{DistribucionEncargosTest._seq}@t.invalid",
            telefono=telefono,
            telefono_normalizado=telefono,
            rol=rol,
            activo=True,
        )
        u.set_password("x")
        u.disponible = True
        u.en_linea = True
        db.session.add(u)
        db.session.commit()
        return u

    def _mk_pedido(self):
        DistribucionEncargosTest._seq += 1
        o = Order(
            numero_pedido=f"TEST-E-{DistribucionEncargosTest._seq}",
            cliente_id=self.cliente.id,
            total=10,
            subtotal=10,
            estado="pendiente",
            tipo_entrega_cliente="delivery",
        )
        db.session.add(o)
        db.session.commit()
        return o

    def _distribuir(self, pedido, tipo):
        from services import distribuir_pedido
        with patch("services._tipo_pedido", return_value=tipo), \
             patch("services._canal_pedido", return_value="cocina"), \
             patch("services._candidatos_disponibles", side_effect=lambda users: users), \
             patch("services.es_pedido_solo_bar", return_value=False), \
             patch("services.max_pedidos_por_preparador", return_value=100), \
             patch("services.carga_actual_preparadores", return_value={}):
            return distribuir_pedido(pedido)

    def test_encargo_va_a_preparacion_no_a_cocina(self):
        prep = self._mk_user("Prep", "preparacion", "+34600111111")
        self._mk_user("Cocinero", "cocina", "+34600222222")
        asignado = self._distribuir(self._mk_pedido(), "programado")
        self.assertIsNotNone(asignado)
        self.assertEqual(asignado.id, prep.id)
        self.assertEqual(asignado.rol, "preparacion")

    def test_encargo_sin_preparacion_no_cae_a_cocina(self):
        # Solo hay cocina online — el encargo debe quedar sin asignar
        # o caer a admin (comodín), NUNCA a cocina.
        self._mk_user("Cocinero", "cocina", "+34600222222")
        asignado = self._distribuir(self._mk_pedido(), "programado")
        # No hay preparacion ni admin → sin asignar
        self.assertIsNone(asignado)

    def test_encargo_sin_preparacion_cae_a_admin_comodin(self):
        # Solo hay cocina y admin — cae a admin, NO a cocina.
        self._mk_user("Cocinero", "cocina", "+34600222222")
        admin = self._mk_user("Admin", "admin", "+34600333333")
        asignado = self._distribuir(self._mk_pedido(), "programado")
        self.assertIsNotNone(asignado)
        self.assertEqual(asignado.id, admin.id)

    def test_inmediato_va_a_cocina(self):
        cocinero = self._mk_user("Cocinero", "cocina", "+34600222222")
        self._mk_user("Prep", "preparacion", "+34600111111")
        asignado = self._distribuir(self._mk_pedido(), "inmediato")
        self.assertEqual(asignado.id, cocinero.id)

    def test_inmediato_si_puede_caer_a_preparacion(self):
        # Compatibilidad hacia atrás: si no hay cocina, el inmediato SÍ
        # cae a preparacion (staff de encargos echa mano). Ese fallback
        # solo se cortó para programados.
        prep = self._mk_user("Prep", "preparacion", "+34600111111")
        asignado = self._distribuir(self._mk_pedido(), "inmediato")
        self.assertEqual(asignado.id, prep.id)


if __name__ == "__main__":
    unittest.main()
