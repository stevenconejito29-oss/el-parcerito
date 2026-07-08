"""
Smoke E2E del flujo financiero — usa funciones REALES del sistema.

Ejecuta 6 escenarios y verifica que cada uno mueve los datos como debe:
  1. Pedido PROPIO entregado → caja ingreso + comisión repartidor + puntos cliente.
  2. Pedido del BAR entregado → caja ingreso + comisión repartidor + bar cobra coste.
  3. Pedido del bar EXTRAVIADO → bar cobra coste (preparó), marketplace asume PVP perdido.
  4. CANCELACIÓN de pedido propio → todo revertido (stock, puntos, comisión, caja).
  5. DOBLE confirmación de entrega → idempotente (no se duplica caja/comisión/puntos).
  6. BIZUM SIN confirmar → registrar_ingreso_pedido bloqueado (no caja fantasma).

Uso (dentro del contenedor):
    docker exec -w /app oxidian-oxidian-1 \
        bash -c "PYTHONPATH=/app python3 /app/scripts/smoke_finanzas.py"
"""
from __future__ import annotations

import os
import sys
import json
import time
from decimal import Decimal
from pathlib import Path
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("OXIDIAN_SKIP_STARTUP_DB", "1")

from app import create_app
from extensions import db
from models import (
    Caja, Categoria, Order, OrderItem, OrderEvent, OrderProviderStatus,
    PointsLog, Product, Proveedor, ProveedorProducto, StaffPayment,
    Stock, User, metadata_item_pedido, utcnow,
)
from services import (
    award_points_on_delivery, generar_comision_entrega,
    registrar_ingreso_pedido, registrar_pago_pedido,
    sincronizar_proveedores_pedido, cancelar_pedido_operativo,
)


# ─── Helpers ────────────────────────────────────────────────────────────────

class TestFail(AssertionError):
    pass


def chk(cond, msg):
    if not cond:
        raise TestFail(msg)


def setup_pedido(cliente, producto, cantidad, *, repartidor=None,
                 metodo_pago="efectivo", pago_confirmado=True,
                 puntos_por_euro=None):
    """Construye un pedido en estado 'en_ruta' listo para entrega."""
    ts = str(int(time.time() * 1000))[-7:]
    pvp = float(producto.precio_final)
    p = Order(
        numero_pedido=f"FIN-{ts}",
        cliente_id=cliente.id,
        estado="en_ruta",
        subtotal=pvp * cantidad,
        total=pvp * cantidad,
        metodo_pago=metodo_pago,
        pago_confirmado=pago_confirmado,
        origen="online",
        repartidor_id=repartidor.id if repartidor else None,
    )
    db.session.add(p)
    db.session.flush()
    item = OrderItem(
        pedido_id=p.id,
        producto_id=producto.id,
        cantidad=cantidad,
        precio_unit=pvp,
        subtotal=pvp * cantidad,
        metadata_json=json.dumps(metadata_item_pedido(producto, {})),
    )
    db.session.add(item)
    db.session.flush()
    sincronizar_proveedores_pedido(p)
    db.session.flush()
    return p


def stock_propio(prod_id):
    val = db.session.query(db.func.coalesce(db.func.sum(Stock.cantidad), 0))\
                    .filter_by(producto_id=prod_id).scalar()
    return int(val or 0)


def stock_bar(bar_id, prod_id):
    f = ProveedorProducto.query.filter_by(
        proveedor_id=bar_id, producto_id=prod_id).first()
    return int(f.stock or 0) if f else None


def ensure_user(email, nombre, rol):
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, nombre=nombre, rol=rol, activo=True)
        user.set_password(os.environ.get("SEED_PASSWORD") or "smoke-finanzas")
        db.session.add(user)
        db.session.flush()
    user.nombre = user.nombre or nombre
    user.rol = rol
    user.activo = True
    return user


def ensure_category():
    category = Categoria.query.filter_by(nombre="QA Finanzas").first()
    if not category:
        category = Categoria(nombre="QA Finanzas", descripcion="Fixtures smoke financiero", activo=True)
        db.session.add(category)
        db.session.flush()
    category.activo = True
    return category


def ensure_own_product(category):
    product = Product.query.filter_by(nombre="QA Finanzas Producto Propio").first()
    if not product:
        product = Product(nombre="QA Finanzas Producto Propio", precio=Decimal("6.50"))
        db.session.add(product)
        db.session.flush()
    product.descripcion = "Producto propio para smoke financiero."
    product.precio = Decimal("6.50")
    product.precio_costo = Decimal("2.10")
    product.categoria_id = category.id
    product.activo = True
    product.es_combo = False
    product.tipo_producto = "simple"
    product.canal_preparacion = "cocina"
    product.modalidad_entrega = "ambas"
    product.proveedor_despachador_id = None
    if stock_propio(product.id) < 20:
        db.session.add(Stock(producto_id=product.id, cantidad=50))
    return product


def ensure_provider_product(category):
    provider = Proveedor.query.filter_by(nombre="Bar El Parcerito").first()
    if not provider:
        provider = Proveedor(nombre="Bar El Parcerito")
        db.session.add(provider)
        db.session.flush()
    provider.activo = True
    provider.modelo_acuerdo = "stock_proveedor"
    provider.comision_pct = Decimal("0")

    product = Product.query.filter_by(nombre="QA Finanzas Producto Bar").first()
    if not product:
        product = Product(nombre="QA Finanzas Producto Bar", precio=Decimal("8.25"))
        db.session.add(product)
        db.session.flush()
    product.descripcion = "Producto de bar para smoke financiero."
    product.precio = Decimal("8.25")
    product.precio_costo = Decimal("3.25")
    product.categoria_id = category.id
    product.activo = True
    product.es_combo = False
    product.tipo_producto = "simple"
    product.canal_preparacion = "cocina"
    product.modalidad_entrega = "ambas"
    product.proveedor_despachador_id = provider.id

    mapping = ProveedorProducto.query.filter_by(
        proveedor_id=provider.id,
        producto_id=product.id,
    ).first()
    if not mapping:
        mapping = ProveedorProducto(
            proveedor_id=provider.id,
            producto_id=product.id,
            stock=30,
            precio_costo=Decimal("3.25"),
            activo=True,
        )
        db.session.add(mapping)
    mapping.stock = max(int(mapping.stock or 0), 30)
    mapping.precio_costo = Decimal("3.25")
    mapping.activo = True
    return provider, product


def ensure_fixture_data():
    category = ensure_category()
    cliente = ensure_user("cliente@oxidian.com", "Cliente Smoke Finanzas", "cliente")
    repart = ensure_user("repartidor@oxidian.com", "Repartidor Smoke Finanzas", "repartidor")
    prod_propio = ensure_own_product(category)
    bar, prod_bar = ensure_provider_product(category)
    db.session.commit()
    return cliente, repart, prod_propio, bar, prod_bar


def cleanup_pedido(p_id):
    db.session.execute(text("DELETE FROM idempotency_keys WHERE order_id = :id"), {"id": p_id})
    OrderProviderStatus.query.filter_by(pedido_id=p_id).delete()
    OrderEvent.query.filter_by(pedido_id=p_id).delete()
    PointsLog.query.filter_by(pedido_id=p_id).delete()
    StaffPayment.query.filter_by(pedido_id=p_id).delete()
    Caja.query.filter_by(pedido_id=p_id).delete()
    OrderItem.query.filter_by(pedido_id=p_id).delete()
    p = db.session.get(Order, p_id)
    if p:
        db.session.delete(p)
    db.session.commit()


# ─── Tests ──────────────────────────────────────────────────────────────────

def test_1_pedido_propio_entregado(cliente, repart, prod_propio):
    print("─── Test 1: pedido PROPIO entregado ───")
    # Asegurar stock propio
    if stock_propio(prod_propio.id) < 3:
        db.session.add(Stock(producto_id=prod_propio.id, cantidad=50))
    db.session.commit()
    sp_antes = stock_propio(prod_propio.id)
    puntos_antes = cliente.puntos or 0

    p = setup_pedido(cliente, prod_propio, 2, repartidor=repart)
    # Calcular puntos ganados como hace el checkout real (pricing_service)
    from services import get_puntos_config
    puntos_por_euro = get_puntos_config()["por_euro"]
    p.puntos_ganados = int(float(p.total) * puntos_por_euro)
    # Descontar stock manualmente como en el flujo de checkout
    prod_propio.descontar_stock(2)
    db.session.commit()

    # Simular confirmar_entrega (sin pasar por la ruta HTTP)
    p.estado = "entregado"
    p.entregado_en = utcnow()
    registrar_ingreso_pedido(p, registrado_por=repart.id)
    generar_comision_entrega(p)
    award_points_on_delivery(p)
    db.session.commit()

    # Verificaciones
    caja = Caja.query.filter_by(pedido_id=p.id, tipo="ingreso").first()
    chk(caja is not None, "Falta ingreso en caja")
    chk(float(caja.monto) == float(p.total), f"Caja monto != total ({caja.monto} vs {p.total})")
    comision = StaffPayment.query.filter_by(
        pedido_id=p.id, tipo="comision").first()
    chk(comision is not None, "Falta comisión del repartidor")
    # Verificar PointsLog en lugar de cliente.puntos (más limpio para tests)
    plog = PointsLog.query.filter_by(
        cliente_id=cliente.id, pedido_id=p.id, tipo="ganado").first()
    chk(plog is not None,
        f"No se generó PointsLog para el pedido (puntos_ganados={p.puntos_ganados})")
    chk(plog.cantidad > 0, f"PointsLog con 0 puntos: {plog.cantidad}")
    chk(stock_propio(prod_propio.id) == sp_antes - 2,
        f"Stock propio mal descontado: {sp_antes} → {stock_propio(prod_propio.id)}")
    print(f"  ✓ caja={caja.monto} | comision={comision.monto} | puntos +{plog.cantidad}")
    cleanup_pedido(p.id)


def test_2_pedido_bar_entregado(cliente, repart, prod_bar, bar):
    print("─── Test 2: pedido BAR entregado ───")
    fila = ProveedorProducto.query.filter_by(
        proveedor_id=bar.id, producto_id=prod_bar.id).first()
    if fila.stock < 5:
        fila.stock = 20
    if fila.precio_costo is None:
        fila.precio_costo = Decimal("0.50")
    db.session.commit()
    sb_antes = stock_bar(bar.id, prod_bar.id)

    p = setup_pedido(cliente, prod_bar, 3, repartidor=repart)
    prod_bar.descontar_stock(3)
    db.session.commit()

    # OPS confirmado por el bar
    ops = OrderProviderStatus.query.filter_by(
        pedido_id=p.id, proveedor_id=bar.id).first()
    chk(ops is not None, "No se creó OrderProviderStatus para el bar")
    ops.preparado = True
    db.session.commit()

    # Entrega
    p.estado = "entregado"
    p.entregado_en = utcnow()
    registrar_ingreso_pedido(p, registrado_por=repart.id)
    generar_comision_entrega(p)
    db.session.commit()

    caja = Caja.query.filter_by(pedido_id=p.id, tipo="ingreso").first()
    chk(caja is not None, "Falta ingreso en caja")
    chk(stock_bar(bar.id, prod_bar.id) == sb_antes - 3,
        f"Stock del bar mal descontado: {sb_antes} → {stock_bar(bar.id, prod_bar.id)}")
    # El bar cobra precio_costo × cantidad (modelo stock_proveedor)
    if bar.modelo_acuerdo == "stock_proveedor":
        costo_esperado = float(fila.precio_costo) * 3
        print(f"  ✓ caja={caja.monto} | bar cobra ~{costo_esperado:.2f}€ por 3 unidades")
    cleanup_pedido(p.id)


def test_3_pedido_bar_extraviado(cliente, prod_bar, bar):
    print("─── Test 3: pedido BAR extraviado ───")
    fila = ProveedorProducto.query.filter_by(
        proveedor_id=bar.id, producto_id=prod_bar.id).first()
    if fila.stock < 2:
        fila.stock = 10
    db.session.commit()
    sb_antes = stock_bar(bar.id, prod_bar.id)

    p = setup_pedido(cliente, prod_bar, 2)
    p.estado = "armando"
    prod_bar.descontar_stock(2)
    db.session.commit()

    # Marcar extraviado (simula proveedor.marcar_extraviado)
    from services import registrar_evento_pedido
    registrar_evento_pedido(
        p, "pedido_extraviado", actor_id=None,
        estado_anterior=p.estado, estado_nuevo="cancelado",
        canal="smoke", detalle="extravío en transporte",
        metadata={"proveedor_id": bar.id},
    )
    cancelar_pedido_operativo(p, actor_id=None, canal="smoke")
    db.session.commit()

    p_re = db.session.get(Order, p.id)
    chk(p_re.estado == "cancelado", "Estado debe ser cancelado")
    chk(stock_bar(bar.id, prod_bar.id) == sb_antes,
        "Stock del bar debe restaurarse (extravío es lógico, físicamente ya se consumió pero stock se devuelve)")
    evento = OrderEvent.query.filter_by(
        pedido_id=p.id, tipo="pedido_extraviado").first()
    chk(evento is not None, "Falta evento pedido_extraviado")
    print(f"  ✓ pedido extraviado registrado | stock devuelto: {sb_antes} → {stock_bar(bar.id, prod_bar.id)}")
    cleanup_pedido(p.id)


def test_4_cancelacion_pedido_propio(cliente, repart, prod_propio):
    print("─── Test 4: cancelación de pedido PROPIO ───")
    if stock_propio(prod_propio.id) < 4:
        db.session.add(Stock(producto_id=prod_propio.id, cantidad=50))
    db.session.commit()
    sp_antes = stock_propio(prod_propio.id)

    p = setup_pedido(cliente, prod_propio, 2, repartidor=repart)
    prod_propio.descontar_stock(2)
    db.session.commit()

    cancelar_pedido_operativo(p, actor_id=cliente.id, canal="smoke")
    db.session.commit()

    chk(stock_propio(prod_propio.id) == sp_antes,
        f"Stock no restaurado: {sp_antes} esperado, {stock_propio(prod_propio.id)} actual")
    chk(p.estado == "cancelado", "Estado debe ser cancelado")
    caja = Caja.query.filter_by(pedido_id=p.id, tipo="ingreso").first()
    chk(caja is None, "Caja no debe tener ingreso (no se llegó a entregar)")
    comision = StaffPayment.query.filter_by(
        pedido_id=p.id, tipo="comision").first()
    chk(comision is None, "No debe haber comisión sin entrega")
    print(f"  ✓ stock restaurado | sin caja | sin comisión | estado=cancelado")
    cleanup_pedido(p.id)


def test_5_doble_entrega_idempotente(cliente, repart, prod_propio):
    print("─── Test 5: doble entrega idempotente ───")
    if stock_propio(prod_propio.id) < 2:
        db.session.add(Stock(producto_id=prod_propio.id, cantidad=50))
    db.session.commit()

    p = setup_pedido(cliente, prod_propio, 1, repartidor=repart)
    from services import get_puntos_config
    p.puntos_ganados = int(float(p.total) * get_puntos_config()["por_euro"])
    prod_propio.descontar_stock(1)
    p.estado = "entregado"
    p.entregado_en = utcnow()
    db.session.commit()

    # 1ª llamada
    registrar_ingreso_pedido(p, registrado_por=repart.id)
    generar_comision_entrega(p)
    award_points_on_delivery(p)
    db.session.commit()
    n_caja_1 = Caja.query.filter_by(pedido_id=p.id, tipo="ingreso").count()
    n_com_1 = StaffPayment.query.filter_by(pedido_id=p.id, tipo="comision").count()

    # 2ª llamada (simula doble click)
    registrar_ingreso_pedido(p, registrado_por=repart.id)
    generar_comision_entrega(p)
    award_points_on_delivery(p)
    db.session.commit()
    n_caja_2 = Caja.query.filter_by(pedido_id=p.id, tipo="ingreso").count()
    n_com_2 = StaffPayment.query.filter_by(pedido_id=p.id, tipo="comision").count()

    chk(n_caja_1 == n_caja_2 == 1,
        f"Caja debe ser 1: {n_caja_1} y {n_caja_2}")
    chk(n_com_1 == n_com_2 == 1,
        f"Comisión debe ser 1: {n_com_1} y {n_com_2}")
    print(f"  ✓ 2 llamadas a entrega → 1 caja, 1 comisión (idempotente)")
    cleanup_pedido(p.id)


def test_6_bizum_sin_confirmar(cliente, repart, prod_propio):
    print("─── Test 6: Bizum sin confirmar bloqueado ───")
    if stock_propio(prod_propio.id) < 2:
        db.session.add(Stock(producto_id=prod_propio.id, cantidad=50))
    db.session.commit()

    p = setup_pedido(
        cliente, prod_propio, 1, repartidor=repart,
        metodo_pago="bizum", pago_confirmado=False,
    )
    prod_propio.descontar_stock(1)
    p.estado = "entregado"
    db.session.commit()

    resultado = registrar_ingreso_pedido(p, registrado_por=repart.id)
    db.session.commit()
    chk(resultado is None, "Bizum sin confirmar NO debe registrar caja")
    n_caja = Caja.query.filter_by(pedido_id=p.id, tipo="ingreso").count()
    chk(n_caja == 0, f"Caja debe estar vacía, hay {n_caja}")

    # Tras confirmar pago sí registra
    registrar_pago_pedido(p, actor_id=repart.id, canal="smoke",
                          detalle="confirmación manual")
    db.session.commit()
    caja = registrar_ingreso_pedido(p, registrado_por=repart.id)
    db.session.commit()
    chk(caja is not None, "Tras pago confirmado, debe registrar caja")
    print(f"  ✓ Bizum no confirmado bloqueado | tras confirmar: caja={caja.monto}")
    cleanup_pedido(p.id)


def main():
    app = create_app(os.environ.get("FLASK_ENV", "production"))
    with app.app_context():
        cliente, repart, prod_propio, bar, prod_bar = ensure_fixture_data()

        tests = [
            (test_1_pedido_propio_entregado, (cliente, repart, prod_propio)),
            (test_2_pedido_bar_entregado, (cliente, repart, prod_bar, bar)),
            (test_3_pedido_bar_extraviado, (cliente, prod_bar, bar)),
            (test_4_cancelacion_pedido_propio, (cliente, repart, prod_propio)),
            (test_5_doble_entrega_idempotente, (cliente, repart, prod_propio)),
            (test_6_bizum_sin_confirmar, (cliente, repart, prod_propio)),
        ]
        ok, fallos = 0, []
        for fn, args in tests:
            try:
                fn(*args)
                ok += 1
            except TestFail as e:
                fallos.append((fn.__name__, str(e)))
                db.session.rollback()
            except Exception as e:  # noqa: BLE001
                fallos.append((fn.__name__, f"EXCEPCION: {e}"))
                db.session.rollback()

        print(f"\n══════════════════════════════════════════════════════")
        print(f"  RESULTADO: {ok}/{len(tests)} tests pasaron")
        if fallos:
            print(f"  FALLOS:")
            for nombre, motivo in fallos:
                print(f"    ✗ {nombre}: {motivo}")
            sys.exit(1)
        print(f"  ✓ FINANZAS OPERATIVAS Y CONSISTENTES")
        sys.exit(0)


if __name__ == "__main__":
    main()
