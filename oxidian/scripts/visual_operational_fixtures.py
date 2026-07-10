"""Fixtures temporales para capturar preparación y reparto con un canje visible."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app import create_app
from extensions import db
from models import (
    Categoria,
    Order,
    OrderItem,
    OrderProviderStatus,
    Product,
    Proveedor,
    ProveedorProducto,
    User,
    ZonaEntrega,
    metadata_item_pedido,
    utcnow,
)


QA_EMAIL = "qa-visual-operations@oxidian.local"
QA_PROVIDER_EMAIL = "qa-visual-provider@oxidian.local"
PREFIX = "QA-VIS-"
STATE_FILE = Path("/app/bot-data/visual-operations-state.json")


def cleanup() -> int:
    order_ids = [
        row[0]
        for row in db.session.execute(
            text("SELECT id FROM orders WHERE numero_pedido LIKE :prefix"),
            {"prefix": f"{PREFIX}%"},
        ).fetchall()
    ]
    for order_id in order_ids:
        for table in (
            "notification_outbox",
            "reviews",
            "affiliate_uses",
            "points_log",
            "caja",
            "staff_payments",
            "order_events",
            "order_items",
        ):
            db.session.execute(text(f"DELETE FROM {table} WHERE pedido_id = :id"), {"id": order_id})
        db.session.execute(text("DELETE FROM orders WHERE id = :id"), {"id": order_id})
    db.session.execute(text("DELETE FROM users WHERE email = :email"), {"email": QA_EMAIL})
    db.session.execute(text("DELETE FROM users WHERE email = :email"), {"email": QA_PROVIDER_EMAIL})
    db.session.execute(
        text("""
            DELETE FROM proveedor_productos
            WHERE proveedor_id IN (SELECT id FROM proveedores WHERE nombre LIKE :prefix)
               OR producto_id IN (SELECT id FROM products WHERE nombre LIKE :prefix)
        """),
        {"prefix": f"{PREFIX}%"},
    )
    db.session.execute(text("DELETE FROM products WHERE nombre LIKE :prefix"), {"prefix": f"{PREFIX}%"})
    db.session.execute(text("DELETE FROM proveedores WHERE nombre LIKE :prefix"), {"prefix": f"{PREFIX}%"})
    db.session.execute(text("DELETE FROM categorias WHERE nombre LIKE :prefix"), {"prefix": f"{PREFIX}%"})

    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
        for user_id, values in state.items():
            user = db.session.get(User, int(user_id))
            if user:
                user.en_linea = bool(values["en_linea"])
                user.last_seen = (
                    datetime.fromisoformat(values["last_seen"])
                    if values.get("last_seen") else None
                )
        STATE_FILE.unlink(missing_ok=True)
    db.session.commit()
    return len(order_ids)


def create() -> int:
    cleanup()
    # El pedido inmediato de demostración debe probar la cola real de Cocina;
    # `preparacion` queda reservado para encargos con fecha.
    preparador = User.query.filter_by(rol="cocina", activo=True).first()
    repartidor = User.query.filter_by(rol="repartidor", activo=True).first()
    product = Product.query.filter_by(activo=True).first()
    reward = Product.query.filter_by(activo=True, canjeable_con_puntos=True).first()
    zone = ZonaEntrega.query.filter_by(activo=True).first()
    if not all((preparador, repartidor, product, reward, zone)):
        raise RuntimeError("Faltan datos operativos para crear las capturas")

    state = {
        str(preparador.id): {
            "en_linea": bool(preparador.en_linea),
            "last_seen": preparador.last_seen.isoformat() if preparador.last_seen else None,
        },
        str(repartidor.id): {
            "en_linea": bool(repartidor.en_linea),
            "last_seen": repartidor.last_seen.isoformat() if repartidor.last_seen else None,
        },
    }
    STATE_FILE.write_text(json.dumps(state))
    for user in (preparador, repartidor):
        user.en_linea = True
        user.last_seen = utcnow()

    customer = User(
        nombre="Cliente visual",
        email=QA_EMAIL,
        rol="cliente",
        telefono="+34699222333",
        direccion="Calle Visual 12, Carmona",
        puntos=140,
        activo=True,
    )
    customer.set_password("visual-fixture-not-for-login")
    db.session.add(customer)
    db.session.flush()

    provider_category = Categoria(nombre=f"{PREFIX}Proveedor", activo=True)
    provider = Proveedor(nombre=f"{PREFIX}Bar X", activo=True, modelo_acuerdo="stock_proveedor")
    db.session.add_all([provider_category, provider])
    db.session.flush()
    provider_user = User(
        nombre="Bar X visual",
        email=QA_PROVIDER_EMAIL,
        rol="proveedor",
        proveedor_id=provider.id,
        activo=True,
    )
    provider_user.set_password(os.environ.get("SEED_PASSWORD") or "visual-fixture-not-for-login")
    db.session.add(provider_user)
    provider_product = Product(
        nombre=f"{PREFIX}Producto Bar X",
        precio=Decimal("7.50"),
        precio_costo=Decimal("3.25"),
        categoria_id=provider_category.id,
        activo=True,
        es_combo=False,
        tipo_producto="simple",
        tipo_entrega="inmediato",
        canal_preparacion="cocina",
        proveedor_despachador_id=provider.id,
    )
    db.session.add(provider_product)
    db.session.flush()
    db.session.add(ProveedorProducto(
        proveedor_id=provider.id,
        producto_id=provider_product.id,
        stock=12,
        precio_costo=Decimal("3.25"),
        activo=True,
    ))

    orders = [
        Order(
            numero_pedido=f"{PREFIX}PREP",
            cliente_id=customer.id,
            preparador_id=preparador.id,
            estado="pendiente",
            origen="online",
            subtotal=Decimal("5.00"),
            descuento=Decimal("0"),
            total=Decimal("8.00"),
            puntos_usados=int(reward.puntos_para_canje or 0),
            puntos_ganados=8,
            metodo_pago="efectivo",
            direccion_entrega=customer.direccion,
            notas="Canje visual: preparar el regalo junto al pedido.",
            zona_id=zone.id,
            es_entrega_epicentro=bool(zone.es_epicentro),
        ),
        Order(
            numero_pedido=f"{PREFIX}RUTA",
            cliente_id=customer.id,
            preparador_id=preparador.id,
            repartidor_id=repartidor.id,
            estado="listo",
            origen="online",
            subtotal=Decimal("5.00"),
            descuento=Decimal("0"),
            total=Decimal("8.00"),
            puntos_usados=int(reward.puntos_para_canje or 0),
            puntos_ganados=8,
            metodo_pago="efectivo",
            direccion_entrega=customer.direccion,
            notas="Entregar también el producto gratuito de puntos.",
            zona_id=zone.id,
            es_entrega_epicentro=bool(zone.es_epicentro),
        ),
        Order(
            numero_pedido=f"{PREFIX}PROV",
            cliente_id=customer.id,
            estado="pendiente",
            origen="online",
            subtotal=Decimal("7.50"),
            descuento=Decimal("0"),
            total=Decimal("10.50"),
            puntos_usados=0,
            puntos_ganados=10,
            metodo_pago="efectivo",
            direccion_entrega=customer.direccion,
            notas="Pedido visual para el panel del proveedor externo.",
            zona_id=zone.id,
            es_entrega_epicentro=bool(zone.es_epicentro),
        ),
    ]
    db.session.add_all(orders)
    db.session.flush()

    for order in orders[:2]:
        db.session.add(OrderItem(
            pedido_id=order.id,
            producto_id=product.id,
            cantidad=1,
            precio_unit=product.precio_final,
            subtotal=product.precio_final,
            metadata_json=json.dumps(metadata_item_pedido(product), ensure_ascii=False),
        ))
        db.session.add(OrderItem(
            pedido_id=order.id,
            producto_id=reward.id,
            cantidad=1,
            precio_unit=0,
            subtotal=0,
            metadata_json=json.dumps(
                metadata_item_pedido(
                    reward,
                    {
                        "reward": {
                            "tipo": "producto_puntos",
                            "puntos": int(reward.puntos_para_canje or 0),
                            "cliente_id": customer.id,
                        }
                    },
                ),
                ensure_ascii=False,
            ),
        ))
    provider_order = orders[2]
    db.session.add(OrderItem(
        pedido_id=provider_order.id,
        producto_id=provider_product.id,
        cantidad=2,
        precio_unit=provider_product.precio_final,
        subtotal=Decimal(str(provider_product.precio_final)) * 2,
        metadata_json=json.dumps(metadata_item_pedido(provider_product), ensure_ascii=False),
    ))
    db.session.add(OrderProviderStatus(
        pedido_id=provider_order.id,
        proveedor_id=provider.id,
    ))
    db.session.commit()
    return len(orders)


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    app = create_app("production")
    with app.app_context():
        if action == "create":
            print({"created": create()})
        elif action == "cleanup":
            print({"deleted": cleanup()})
        else:
            raise SystemExit("Uso: visual_operational_fixtures.py create|cleanup")


if __name__ == "__main__":
    main()
