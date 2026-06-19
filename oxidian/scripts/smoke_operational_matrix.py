#!/usr/bin/env python3
"""Smoke test for the seeded multi-origin catalog without persisting orders."""

from __future__ import annotations

import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from extensions import db
from models import (
    Order,
    OrderItem,
    Product,
    User,
    metadata_item_pedido,
)
from services import es_pedido_solo_bar, sincronizar_proveedores_pedido


def by_code(code):
    for product in Product.query.filter_by(activo=True).all():
        if product.get_atributos().get("matrix_code") == code:
            return product
    raise AssertionError(f"Falta producto matrix_code={code}")


def main():
    app = create_app("production")
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        arepa_own = by_code("arepa-own")
        arepa_north = by_code("arepa-north")
        arepa_south = by_code("arepa-south")
        cola_own = by_code("cola-own")
        cola_north = by_code("cola-north")
        cola_south = by_code("cola-south")
        burger_north = by_code("burger-north")
        perro_south = by_code("perro-south")
        combo_own = by_code("combo-own")
        combo_north = by_code("combo-north")

        client = app.test_client()
        menu = client.get("/")
        assert menu.status_code == 200
        html = menu.get_data(as_text=True)
        assert f'href="/producto/{arepa_own.id}"' in html
        assert f'href="/producto/{arepa_north.id}"' not in html
        assert f'href="/producto/{arepa_south.id}"' not in html
        assert f'href="/producto/{cola_own.id}"' in html
        assert f'href="/producto/{cola_north.id}"' not in html
        assert f'href="/producto/{cola_south.id}"' not in html

        response = client.post(
            f"/carrito/agregar/{burger_north.id}",
            data={"cantidad": "1"},
            headers={"X-Ajax": "1"},
        )
        assert response.get_json()["ok"] is True

        response = client.post(
            f"/carrito/agregar/{arepa_own.id}",
            data={"cantidad": "1"},
            headers={"X-Ajax": "1"},
        )
        assert response.get_json()["ok"] is True
        with client.session_transaction() as session:
            cart = session["carrito"]
            assert str(arepa_north.id) in cart
            assert str(arepa_own.id) not in cart

        response = client.post(
            f"/carrito/agregar/{perro_south.id}",
            data={"cantidad": "1"},
            headers={"X-Ajax": "1"},
        )
        payload = response.get_json()
        assert payload["ok"] is False
        assert "establecimiento" in payload["msg"].lower()

        with client.session_transaction() as session:
            session.clear()
        assert client.post(
            f"/carrito/agregar/{combo_own.id}",
            data={"cantidad": "1"},
            headers={"X-Ajax": "1"},
        ).get_json()["ok"] is True
        payload = client.post(
            f"/carrito/agregar/{combo_north.id}",
            data={"cantidad": "1"},
            headers={"X-Ajax": "1"},
        ).get_json()
        assert payload["ok"] is False

        customer = User(
            nombre="Cliente smoke origen",
            email=f"smoke-origin-{uuid.uuid4().hex}@test.invalid",
            rol="cliente",
            activo=True,
        )
        customer.set_password(uuid.uuid4().hex)
        db.session.add(customer)
        db.session.flush()
        routing = {}
        for code, expected_provider in (
            ("empanada-own", None),
            ("burger-north", burger_north.proveedor_despachador_id),
            ("perro-south", perro_south.proveedor_despachador_id),
        ):
            product = by_code(code)
            order = Order(
                numero_pedido=f"Q{uuid.uuid4().hex[:12].upper()}",
                cliente_id=customer.id,
                estado="pendiente",
                origen="online",
                subtotal=Decimal(str(product.precio_final)),
                descuento=Decimal("0"),
                total=Decimal(str(product.precio_final)),
                metodo_pago="efectivo",
                direccion_entrega="Calle Smoke 1, Carmona",
            )
            db.session.add(order)
            db.session.flush()
            db.session.add(OrderItem(
                pedido_id=order.id,
                producto_id=product.id,
                cantidad=1,
                precio_unit=Decimal(str(product.precio_final)),
                subtotal=Decimal(str(product.precio_final)),
                metadata_json=json.dumps(
                    metadata_item_pedido(product),
                    ensure_ascii=False,
                ),
            ))
            db.session.flush()
            sincronizar_proveedores_pedido(order)
            db.session.flush()
            providers = {state.proveedor_id for state in order.estados_proveedor}
            expected = {expected_provider} if expected_provider else set()
            assert providers == expected
            assert es_pedido_solo_bar(order) is bool(expected_provider)
            routing[code] = sorted(providers)
        db.session.rollback()

        print(json.dumps({
            "menu_arepa_cards": 1,
            "menu_cola_cards": 1,
            "variant_switched_to": arepa_north.id,
            "cross_provider_rejected": True,
            "cross_combo_rejected": True,
            "order_routing": routing,
            "transient_orders_rolled_back": True,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
