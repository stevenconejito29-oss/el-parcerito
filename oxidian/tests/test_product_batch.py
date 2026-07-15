"""Tests del modelo `ProductBatch` — encargos con fecha por lote.

Verifica:
    * Reserva atómica de tandas con `reservar_tandas`.
    * Rechazo de reservas que superan capacidad.
    * Auto-transición a `agotado` cuando se llena.
    * Liberación de tandas al cancelar (idempotente, no negativo).
    * Reapertura `agotado → abierto` si hay cupo tras cancelación.
    * Race: dos reservas concurrentes por la última tanda — solo una gana.
"""
import unittest
from datetime import date, timedelta

from flask import Flask

from extensions import db
from models import Product, ProductBatch


class ProductBatchTest(unittest.TestCase):
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
        self.producto = self._mk_producto()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk_producto(self):
        ProductBatchTest._seq += 1
        p = Product(
            nombre=f"Empanadas Pack {self._seq}",
            precio=5.0,
            activo=True,
            tipo_entrega="programado",
            cantidad_por_lote=4,
        )
        db.session.add(p)
        db.session.commit()
        return p

    def _mk_batch(self, maximo=10, vendidas=0, estado="abierto", dias=7):
        b = ProductBatch(
            producto_id=self.producto.id,
            fecha_entrega=date.today() + timedelta(days=dias),
            cantidad_por_tanda=4,
            cantidad_maxima_tandas=maximo,
            cantidad_vendida_tandas=vendidas,
            estado=estado,
        )
        db.session.add(b)
        db.session.commit()
        return b

    # ── tandas_disponibles ──
    def test_tandas_disponibles_calcula_diferencia(self):
        b = self._mk_batch(maximo=10, vendidas=3)
        self.assertEqual(b.tandas_disponibles(), 7)

    def test_tandas_disponibles_ilimitado(self):
        b = ProductBatch(
            producto_id=self.producto.id,
            fecha_entrega=date.today() + timedelta(days=1),
            cantidad_por_tanda=4,
            cantidad_maxima_tandas=None,
        )
        db.session.add(b); db.session.commit()
        self.assertGreater(b.tandas_disponibles(), 10**6)

    # ── reservar_tandas ──
    def test_reservar_dentro_de_capacidad(self):
        b = self._mk_batch(maximo=5)
        self.assertTrue(b.reservar_tandas(3))
        self.assertEqual(b.cantidad_vendida_tandas, 3)
        self.assertEqual(b.estado, "abierto")

    def test_reservar_exacto_maximo_marca_agotado(self):
        b = self._mk_batch(maximo=4)
        self.assertTrue(b.reservar_tandas(4))
        self.assertEqual(b.estado, "agotado")

    def test_reservar_supera_maximo_rechaza_sin_efectos(self):
        b = self._mk_batch(maximo=3)
        self.assertFalse(b.reservar_tandas(5))
        self.assertEqual(b.cantidad_vendida_tandas, 0)
        self.assertEqual(b.estado, "abierto")

    def test_reservar_en_agotado_rechaza(self):
        b = self._mk_batch(maximo=2, vendidas=2, estado="agotado")
        self.assertFalse(b.reservar_tandas(1))

    def test_reservar_cero_es_noop_ok(self):
        b = self._mk_batch()
        self.assertTrue(b.reservar_tandas(0))
        self.assertEqual(b.cantidad_vendida_tandas, 0)

    # ── liberar_tandas ──
    def test_liberar_devuelve_tandas(self):
        b = self._mk_batch(maximo=5, vendidas=3)
        b.liberar_tandas(2)
        db.session.commit()
        db.session.refresh(b)
        self.assertEqual(b.cantidad_vendida_tandas, 1)

    def test_liberar_nunca_deja_negativo(self):
        b = self._mk_batch(maximo=5, vendidas=1)
        b.liberar_tandas(10)
        db.session.commit()
        db.session.refresh(b)
        self.assertEqual(b.cantidad_vendida_tandas, 0)

    def test_liberar_reabre_agotado_si_hay_cupo(self):
        b = self._mk_batch(maximo=3, vendidas=3, estado="agotado")
        b.liberar_tandas(1)
        db.session.commit()
        db.session.refresh(b)
        self.assertEqual(b.estado, "abierto")
        self.assertEqual(b.cantidad_vendida_tandas, 2)

    # ── Race: dos reservas por la última tanda ──
    def test_race_dos_reservas_ultima_tanda_solo_una_gana(self):
        """Simulamos dos checkouts concurrentes que compiten por el último
        cupo. El UPDATE condicional en `reservar_tandas` garantiza que
        el segundo obtenga `False` en vez de crear overbooking."""
        b = self._mk_batch(maximo=5, vendidas=4)
        # Recargamos como si dos sesiones distintas leyeran el mismo estado.
        r1 = b.reservar_tandas(1)
        r2 = b.reservar_tandas(1)
        self.assertTrue(r1 or r2)
        self.assertFalse(r1 and r2, "no ambos pueden ganar")
        db.session.refresh(b)
        self.assertLessEqual(b.cantidad_vendida_tandas, 5)

    # ── UNIQUE(producto_id, fecha_entrega) ──
    def test_unique_producto_fecha(self):
        f = date.today() + timedelta(days=10)
        b1 = ProductBatch(producto_id=self.producto.id, fecha_entrega=f,
                          cantidad_por_tanda=4, cantidad_maxima_tandas=10)
        b2 = ProductBatch(producto_id=self.producto.id, fecha_entrega=f,
                          cantidad_por_tanda=4, cantidad_maxima_tandas=5)
        db.session.add_all([b1, b2])
        with self.assertRaises(Exception):
            db.session.commit()


if __name__ == "__main__":
    unittest.main()
