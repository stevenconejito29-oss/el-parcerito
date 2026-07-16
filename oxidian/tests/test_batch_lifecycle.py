"""Ciclo de vida de un `ProductBatch` integrado con `Order`/`OrderItem`.

Cubre las dos piezas que se enchufan sobre el modelo `ProductBatch`:
    * `services._liberar_tandas_pedido` — al cancelar un pedido, las
      tandas reservadas vuelven al batch (respetando el metadata_json
      congelado en checkout).
    * `routes.preparador._lotes_agregados` — vista agregada por batch
      que suma `tandas_reservadas` de todos los pedidos vivos.

Estos tests reproducen el estado *post-checkout* (metadata_json ya
poblado con `batch_id`/`tandas_reservadas`) sin ejercer la ruta HTTP:
así el test es determinista y no depende del cliente Flask ni de auth.
"""
import json
import unittest
from datetime import date, timedelta

from flask import Flask

from extensions import db
from models import Product, ProductBatch, Order, OrderItem, User


class BatchLifecycleTest(unittest.TestCase):
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
        self.cliente = self._mk_cliente()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk_cliente(self):
        BatchLifecycleTest._seq += 1
        u = User(
            nombre=f"Cli{self._seq}",
            email=f"cli{self._seq}@t.invalid",
            telefono=f"+3460100{self._seq:04d}",
            rol="cliente",
            activo=True,
        )
        u.set_password("t")
        db.session.add(u); db.session.commit()
        return u

    def _mk_producto(self, por_lote=4):
        BatchLifecycleTest._seq += 1
        p = Product(
            nombre=f"Empanadas {self._seq}",
            precio=5.0,
            activo=True,
            tipo_entrega="programado",
            cantidad_por_lote=por_lote,
        )
        db.session.add(p)
        db.session.commit()
        return p

    def _mk_batch(self, producto, maximo=10, dias=7):
        b = ProductBatch(
            producto_id=producto.id,
            fecha_entrega=date.today() + timedelta(days=dias),
            cantidad_por_tanda=producto.cantidad_por_lote or 4,
            cantidad_maxima_tandas=maximo,
        )
        db.session.add(b)
        db.session.commit()
        return b

    def _mk_pedido_con_tandas(self, producto, batch, tandas, estado="pendiente"):
        BatchLifecycleTest._seq += 1
        o = Order(
            numero_pedido=f"BAT-{self._seq:04d}",
            cliente_id=self.cliente.id,
            total=producto.precio * tandas,
            subtotal=producto.precio * tandas,
            estado=estado,
        )
        db.session.add(o)
        db.session.flush()
        it = OrderItem(
            pedido_id=o.id,
            producto_id=producto.id,
            cantidad=tandas,
            precio_unit=producto.precio,
            subtotal=producto.precio * tandas,
            metadata_json=json.dumps({
                "batch_id": batch.id,
                "tandas_reservadas": tandas,
            }),
        )
        db.session.add(it)
        # Reservamos en el batch como haría el checkout real.
        assert batch.reservar_tandas(tandas), "capacidad insuficiente en fixture"
        db.session.commit()
        return o

    # ── _liberar_tandas_pedido ─────────────────────────────────────
    def test_cancelar_libera_tandas_al_batch(self):
        from services import _liberar_tandas_pedido
        prod = self._mk_producto()
        batch = self._mk_batch(prod, maximo=5)
        pedido = self._mk_pedido_con_tandas(prod, batch, 3)
        self.assertEqual(batch.cantidad_vendida_tandas, 3)
        _liberar_tandas_pedido(pedido)
        db.session.commit()
        db.session.refresh(batch)
        self.assertEqual(batch.cantidad_vendida_tandas, 0)

    def test_liberar_sin_metadata_es_noop(self):
        from services import _liberar_tandas_pedido
        prod = self._mk_producto()
        BatchLifecycleTest._seq += 1
        o = Order(
            numero_pedido=f"BAT-N-{self._seq}",
            cliente_id=self.cliente.id,
            total=5.0, subtotal=5.0, estado="pendiente",
        )
        db.session.add(o); db.session.flush()
        it = OrderItem(pedido_id=o.id, producto_id=prod.id,
                       cantidad=1, precio_unit=5.0, subtotal=5.0,
                       metadata_json="{}")
        db.session.add(it); db.session.commit()
        # No debe romper aunque no haya batch_id en metadata.
        _liberar_tandas_pedido(o)

    def test_liberar_reabre_batch_agotado(self):
        from services import _liberar_tandas_pedido
        prod = self._mk_producto()
        batch = self._mk_batch(prod, maximo=3)
        pedido = self._mk_pedido_con_tandas(prod, batch, 3)
        db.session.refresh(batch)
        self.assertEqual(batch.estado, "agotado")
        _liberar_tandas_pedido(pedido)
        db.session.commit()
        db.session.refresh(batch)
        self.assertEqual(batch.estado, "abierto")
        self.assertEqual(batch.cantidad_vendida_tandas, 0)

    # ── _lotes_agregados ───────────────────────────────────────────
    def test_agregado_suma_tandas_de_pedidos_vivos(self):
        from routes.preparador import _lotes_agregados
        prod = self._mk_producto()
        batch = self._mk_batch(prod, maximo=20)
        self._mk_pedido_con_tandas(prod, batch, 2, estado="pendiente")
        self._mk_pedido_con_tandas(prod, batch, 3, estado="armando")
        lotes = _lotes_agregados(fecha=batch.fecha_entrega)
        self.assertEqual(len(lotes), 1)
        self.assertEqual(lotes[0]["tandas_totales"], 5)
        self.assertEqual(lotes[0]["unidades_totales"], 5 * batch.cantidad_por_tanda)
        self.assertEqual(lotes[0]["pedidos_total"], 2)
        self.assertEqual(
            lotes[0]["unidades_por_estado"],
            {
                "pendiente": 2 * batch.cantidad_por_tanda,
                "armando": 3 * batch.cantidad_por_tanda,
                "listo": 0,
            },
        )

    # ── integración con el flujo real de cancelación ─────────────
    def test_ejecutar_cancelacion_libera_tandas(self):
        """Cierra el ciclo: cancelar pedido de lote via el flujo real
        (`_ejecutar_cancelacion_pedido`) devuelve las tandas al batch
        junto a los demás efectos (stock, puntos, etc.).
        """
        from services import _ejecutar_cancelacion_pedido
        prod = self._mk_producto()
        batch = self._mk_batch(prod, maximo=6)
        pedido = self._mk_pedido_con_tandas(prod, batch, 4)
        self.assertEqual(batch.cantidad_vendida_tandas, 4)
        _ejecutar_cancelacion_pedido(pedido)
        db.session.commit()
        db.session.refresh(batch); db.session.refresh(pedido)
        self.assertEqual(pedido.estado, "cancelado")
        self.assertEqual(batch.cantidad_vendida_tandas, 0)

    # ── _encargos_agregados_por_fecha (unificado con no-lote) ──────
    def test_agregado_unificado_incluye_no_lote(self):
        """El agregado debe listar batches Y encargos programados sueltos.

        El preparador ve el total del día para ambos, con `es_lote=True`
        para los batches (tandas) y `es_lote=False` para los individuales
        (unidades sueltas). Sin doble contabilidad.
        """
        from routes.preparador import _encargos_agregados_por_fecha
        # Producto con lote (usa ProductBatch)
        prod_lote = self._mk_producto(por_lote=4)
        batch = self._mk_batch(prod_lote, maximo=10)
        self._mk_pedido_con_tandas(prod_lote, batch, 2, estado="pendiente")

        # Producto programado sin lote (mismo día)
        BatchLifecycleTest._seq += 1
        prod_solo = Product(
            nombre=f"Suelto {self._seq}",
            precio=3.0, activo=True,
            tipo_entrega="programado",
            fecha_llegada=batch.fecha_entrega,
        )
        db.session.add(prod_solo); db.session.commit()
        BatchLifecycleTest._seq += 1
        o = Order(
            numero_pedido=f"SL-{self._seq:04d}",
            cliente_id=self.cliente.id,
            total=3.0, subtotal=3.0, estado="pendiente",
        )
        db.session.add(o); db.session.flush()
        it = OrderItem(
            pedido_id=o.id, producto_id=prod_solo.id, cantidad=5,
            precio_unit=3.0, subtotal=15.0, metadata_json="{}",
        )
        db.session.add(it); db.session.commit()

        agregado = _encargos_agregados_por_fecha(batch.fecha_entrega)
        self.assertEqual(len(agregado), 2)
        por_id = {r["producto_id"]: r for r in agregado}
        self.assertTrue(por_id[prod_lote.id]["es_lote"])
        self.assertEqual(por_id[prod_lote.id]["tandas_totales"], 2)
        self.assertEqual(por_id[prod_lote.id]["unidades_totales"], 8)
        self.assertFalse(por_id[prod_solo.id]["es_lote"])
        self.assertEqual(por_id[prod_solo.id]["unidades_totales"], 5)

    def test_agregado_no_lote_respeta_snapshot_aunque_cambie_catalogo(self):
        """La planificación pertenece al pedido, no al producto editable."""
        from routes.preparador import _encargos_agregados_por_fecha

        entrega_original = date.today() + timedelta(days=4)
        producto = self._mk_producto(por_lote=0)
        producto.nombre = "Nombre actualizado"
        producto.fecha_llegada = entrega_original + timedelta(days=10)
        db.session.commit()

        BatchLifecycleTest._seq += 1
        pedido = Order(
            numero_pedido=f"SNAP-{self._seq:04d}",
            cliente_id=self.cliente.id,
            total=12,
            subtotal=12,
            estado="pendiente",
        )
        db.session.add(pedido)
        db.session.flush()
        db.session.add(OrderItem(
            pedido_id=pedido.id,
            producto_id=producto.id,
            cantidad=3,
            precio_unit=4,
            subtotal=12,
            metadata_json=json.dumps({
                "entrega_programada": entrega_original.isoformat(),
                "producto": {
                    "nombre": "Producto confirmado",
                    "tipo_entrega": "programado",
                    "fecha_llegada": entrega_original.isoformat(),
                },
            }),
        ))
        db.session.commit()

        agregado = _encargos_agregados_por_fecha(entrega_original)
        self.assertEqual(len(agregado), 1)
        self.assertEqual(agregado[0]["producto_nombre"], "Producto confirmado")
        self.assertEqual(agregado[0]["unidades_totales"], 3)
        self.assertEqual(agregado[0]["pedidos_total"], 1)

    def test_agregado_excluye_cancelados_y_entregados(self):
        from routes.preparador import _lotes_agregados
        prod = self._mk_producto()
        batch = self._mk_batch(prod, maximo=20)
        self._mk_pedido_con_tandas(prod, batch, 2, estado="pendiente")
        self._mk_pedido_con_tandas(prod, batch, 4, estado="cancelado")
        self._mk_pedido_con_tandas(prod, batch, 7, estado="entregado")
        lotes = _lotes_agregados(fecha=batch.fecha_entrega)
        self.assertEqual(lotes[0]["tandas_totales"], 2)


if __name__ == "__main__":
    unittest.main()
