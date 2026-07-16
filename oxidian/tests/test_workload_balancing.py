"""Tests del workload balancing y rebalanceo de huérfanos.

Cubre:
- `_elegir_menos_cargado` respeta tope y política de fallback.
- `carga_actual_preparadores` / `carga_actual_repartidores` computan cargas
  en 1 sola query (verificado por igualdad de resultado).
- `rebalancear_pedidos_huerfanos` reasigna pedidos de empleado offline.
"""
import unittest
from unittest.mock import MagicMock

from flask import Flask

from extensions import db
from models import Order, SiteConfig, User


class WorkloadBalancingTest(unittest.TestCase):
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

        # Cliente para los pedidos.
        self.cliente = User(
            nombre="Cliente Test",
            email="c@test.invalid",
            telefono="+34600000000",
            rol="cliente",
            activo=True,
        )
        self.cliente.set_password("test")
        db.session.add(self.cliente)
        db.session.commit()
        self._seq = 0

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    _user_seq = 0

    def _mk_user(self, nombre, rol="preparacion", activo=True, disponible=True):
        WorkloadBalancingTest._user_seq += 1
        seq = WorkloadBalancingTest._user_seq
        u = User(
            nombre=nombre,
            email=f"{nombre}-{seq}@test.invalid",
            telefono=f"+3460000{seq:05d}",
            rol=rol,
            activo=activo,
        )
        u.set_password("test")
        u.en_linea = bool(disponible)
        db.session.add(u)
        db.session.commit()
        return u

    def _mk_pedido(self, estado, preparador_id=None, repartidor_id=None,
                   tipo_entrega="delivery"):
        self._seq += 1
        o = Order(
            numero_pedido=f"TEST-{self._seq:04d}",
            cliente_id=self.cliente.id,
            total=10,
            subtotal=10,
            estado=estado,
            preparador_id=preparador_id,
            repartidor_id=repartidor_id,
            tipo_entrega_cliente=tipo_entrega,
        )
        db.session.add(o)
        db.session.commit()
        return o

    # ── Tope + política de fallback ─────────────────────────────────

    def test_elegir_menos_cargado_respeta_tope(self):
        from services import _elegir_menos_cargado
        a = self._mk_user("Ana")
        b = self._mk_user("Ben")
        # Ana con 10 (sobre tope=8), Ben con 3 → gana Ben.
        cargas = {a.id: 10, b.id: 3}
        res = _elegir_menos_cargado([a, b], cargas, tope=8)
        self.assertEqual(res[0].id, b.id)
        self.assertFalse(res[2])  # not overloaded

    def test_elegir_menos_cargado_fallback_si_todos_sobre_tope(self):
        from services import _elegir_menos_cargado
        a = self._mk_user("Ana")
        b = self._mk_user("Ben")
        # Ambos sobre tope → gana el menor (Ana con 9 < Ben con 10).
        cargas = {a.id: 9, b.id: 10}
        res = _elegir_menos_cargado([a, b], cargas, tope=8)
        self.assertEqual(res[0].id, a.id)
        self.assertTrue(res[2])  # overloaded flag True

    def test_elegir_menos_cargado_lista_vacia(self):
        from services import _elegir_menos_cargado
        self.assertIsNone(_elegir_menos_cargado([], {}, tope=8))

    # ── Carga bulk (evita N+1) ─────────────────────────────────────

    def test_carga_preparadores_bulk(self):
        from services import carga_actual_preparadores
        a = self._mk_user("Ana")
        b = self._mk_user("Ben")
        # Ana con 3 pedidos activos, Ben con 1.
        for _ in range(3):
            self._mk_pedido("armando", preparador_id=a.id)
        self._mk_pedido("pendiente", preparador_id=b.id)
        # Ruido: pedido cancelado no cuenta.
        self._mk_pedido("cancelado", preparador_id=b.id)

        cargas = carga_actual_preparadores([a.id, b.id])
        self.assertEqual(cargas[a.id], 3)
        self.assertEqual(cargas[b.id], 1)

    def test_carga_preparadores_incluye_ceros(self):
        from services import carga_actual_preparadores
        a = self._mk_user("Ana")
        cargas = carga_actual_preparadores([a.id])
        # Ana sin pedidos → 0 (no ausente).
        self.assertEqual(cargas[a.id], 0)

    def test_carga_repartidores_bulk(self):
        from services import carga_actual_repartidores
        a = self._mk_user("Ana", rol="repartidor")
        b = self._mk_user("Ben", rol="repartidor")
        # Ana en_ruta con 2, Ben listo con 1.
        self._mk_pedido("en_ruta", repartidor_id=a.id)
        self._mk_pedido("en_ruta", repartidor_id=a.id)
        self._mk_pedido("listo", repartidor_id=b.id)
        # Ruido: entregado no cuenta.
        self._mk_pedido("entregado", repartidor_id=b.id)

        cargas = carga_actual_repartidores([a.id, b.id])
        self.assertEqual(cargas[a.id], 2)
        self.assertEqual(cargas[b.id], 1)

    def test_cola_sin_repartidor_excluye_recogidas(self):
        from services import pedidos_delivery_sin_repartidor_query
        delivery = self._mk_pedido("listo", tipo_entrega="delivery")
        self._mk_pedido("listo", tipo_entrega="recogida")

        ids = {p.id for p in pedidos_delivery_sin_repartidor_query().all()}
        self.assertEqual(ids, {delivery.id})

    def test_no_se_puede_asignar_repartidor_a_recogida(self):
        from services import reasignar_responsable_pedido
        repartidor = self._mk_user("Rider", rol="repartidor")
        recogida = self._mk_pedido("listo", tipo_entrega="recogida")

        with self.assertRaisesRegex(ValueError, "recoger"):
            reasignar_responsable_pedido(
                recogida, "repartidor_id", repartidor.id, canal="test"
            )
        self.assertIsNone(recogida.repartidor_id)

    # ── Rebalanceo huérfanos ───────────────────────────────────────

    def test_rebalanceo_no_duplica_preparacion_ya_iniciada(self):
        from services import rebalancear_pedidos_huerfanos
        # Un pedido ya armando puede estar físicamente con el cocinero.
        offline = self._mk_user("Off", rol="cocina", disponible=False)
        self._mk_user("On", rol="cocina", disponible=True)
        pedido = self._mk_pedido("armando", preparador_id=offline.id)

        res = rebalancear_pedidos_huerfanos()
        db.session.refresh(pedido)

        self.assertEqual(res["preparador"], 0)
        self.assertEqual(pedido.preparador_id, offline.id)

    def test_rebalanceo_no_mueve_pedido_que_ya_esta_en_ruta(self):
        from services import rebalancear_pedidos_huerfanos
        offline = self._mk_user("OffRoute", rol="repartidor", disponible=False)
        self._mk_user("OnRoute", rol="repartidor", disponible=True)
        pedido = self._mk_pedido("en_ruta", repartidor_id=offline.id)

        res = rebalancear_pedidos_huerfanos()
        db.session.refresh(pedido)

        self.assertEqual(res["repartidor"], 0)
        self.assertEqual(pedido.repartidor_id, offline.id)

    def test_rebalanceo_no_toca_pedidos_de_empleados_disponibles(self):
        from services import rebalancear_pedidos_huerfanos
        activo = self._mk_user("Act", rol="cocina", disponible=True)
        pedido = self._mk_pedido("armando", preparador_id=activo.id)

        res = rebalancear_pedidos_huerfanos()
        db.session.refresh(pedido)

        self.assertEqual(res["preparador"], 0)
        self.assertEqual(pedido.preparador_id, activo.id)

    # ── Tope configurable via SiteConfig ───────────────────────────

    def test_max_pedidos_por_preparador_default_8(self):
        from services import max_pedidos_por_preparador
        self.assertEqual(max_pedidos_por_preparador(), 8)

    def test_max_pedidos_por_preparador_configurable(self):
        from services import max_pedidos_por_preparador
        SiteConfig.set("MAX_PEDIDOS_POR_PREPARADOR", "15", descripcion="test")
        db.session.commit()
        self.assertEqual(max_pedidos_por_preparador(), 15)

    def test_max_pedidos_por_preparador_cap_defensivo(self):
        from services import max_pedidos_por_preparador
        SiteConfig.set("MAX_PEDIDOS_POR_PREPARADOR", "9999", descripcion="test")
        db.session.commit()
        # Cap 100.
        self.assertEqual(max_pedidos_por_preparador(), 100)

    def test_max_pedidos_por_preparador_invalido_fallback(self):
        from services import max_pedidos_por_preparador
        SiteConfig.set("MAX_PEDIDOS_POR_PREPARADOR", "abc", descripcion="test")
        db.session.commit()
        self.assertEqual(max_pedidos_por_preparador(), 8)


if __name__ == "__main__":
    unittest.main()
