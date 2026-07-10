"""Tests de la asignación automática de repartidor por zona (coherente con PR #5).

Regla:
- Si el pedido tiene zona_id: prefiere repartidor con esa zona.
- Fallback a comodines (sin zona_repartidor_id) si no hay especialistas online.
- Fallback al pool completo si tampoco hay comodines online (evita entregas
  huérfanas por especialización rígida).
- Si el pedido no tiene zona: usa el pool completo.
"""
import unittest

from flask import Flask
from unittest.mock import patch

from extensions import db
from models import Order, User, ZonaEntrega


class DistribucionRepartidorZonaTest(unittest.TestCase):
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

        self.zona_norte = ZonaEntrega(nombre="Norte", precio_envio=2, tiempo_estimado_min=30, activo=True)
        self.zona_sur = ZonaEntrega(nombre="Sur", precio_envio=3, tiempo_estimado_min=40, activo=True)
        db.session.add_all([self.zona_norte, self.zona_sur])
        db.session.commit()

        self.rep_norte = self._mk_rep("Rep Norte", tel="+34600000001", zona_id=self.zona_norte.id)
        self.rep_sur = self._mk_rep("Rep Sur", tel="+34600000002", zona_id=self.zona_sur.id)
        self.rep_libre = self._mk_rep("Rep Libre", tel="+34600000003", zona_id=None)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk_rep(self, nombre, tel, zona_id):
        u = User(
            nombre=nombre,
            email=f"{tel[-4:]}@test.invalid",
            telefono=tel,
            rol="repartidor",
            activo=True,
        )
        u.set_password("test")
        u.zona_repartidor_id = zona_id
        u.disponible = True
        u.en_linea = True
        db.session.add(u)
        db.session.commit()
        return u

    def _mk_pedido(self, zona_id):
        o = Order(
            numero_pedido=f"TEST-Z-{zona_id or 'none'}",
            total=10,
            subtotal=10,
            estado="listo",
            tipo_entrega_cliente="delivery",
            zona_id=zona_id,
        )
        db.session.add(o)
        db.session.commit()
        return o

    def _distribuir(self, pedido):
        from services import distribuir_repartidor
        with patch("services.get_store_features", return_value={"delivery": True}), \
             patch("services._candidatos_disponibles", side_effect=lambda users: users), \
             patch.object(User, "pedidos_activos_como_repartidor", return_value=0):
            return distribuir_repartidor(pedido)

    def test_pedido_norte_prefiere_rep_norte(self):
        asignado = self._distribuir(self._mk_pedido(self.zona_norte.id))
        self.assertEqual(asignado.id, self.rep_norte.id)

    def test_pedido_sur_prefiere_rep_sur(self):
        asignado = self._distribuir(self._mk_pedido(self.zona_sur.id))
        self.assertEqual(asignado.id, self.rep_sur.id)

    def test_pedido_zona_sin_especialista_cae_a_comodin(self):
        self.rep_norte.activo = False
        db.session.commit()
        asignado = self._distribuir(self._mk_pedido(self.zona_norte.id))
        self.assertEqual(asignado.id, self.rep_libre.id)

    def test_pedido_sin_zona_usa_pool_completo(self):
        asignado = self._distribuir(self._mk_pedido(zona_id=None))
        self.assertIn(asignado.id, {self.rep_norte.id, self.rep_sur.id, self.rep_libre.id})

    def test_pedido_zona_sin_especialista_ni_comodin_pool_completo(self):
        self.rep_libre.activo = False
        self.rep_norte.activo = False
        db.session.commit()
        asignado = self._distribuir(self._mk_pedido(self.zona_norte.id))
        self.assertEqual(asignado.id, self.rep_sur.id)


if __name__ == "__main__":
    unittest.main()
