#!/usr/bin/env python3
"""Smoke test del catálogo maestro con inventario aislado por ubicación."""

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
from models import Order, OrderItem, Product, User, metadata_item_pedido
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
        arepa = by_code("arepa-own")
        cola = by_code("cola-own")
        burger = by_code("burger-north")
        perro = by_code("perro-south")
        combo_own = by_code("combo-own")
        combo_north = by_code("combo-north")
        combo_south = by_code("combo-south")
        north_id = combo_north.proveedor_despachador_id
        south_id = combo_south.proveedor_despachador_id
        assert north_id and south_id and north_id != south_id

        client = app.test_client()
        own_menu = client.get("/")
        assert own_menu.status_code == 200
        own_html = own_menu.get_data(as_text=True)
        assert f"/producto/{arepa.id}" in own_html
        assert f"/producto/{cola.id}" in own_html
        assert f"/producto/{burger.id}" not in own_html

        north_menu = client.get(f"/bar/{north_id}")
        assert north_menu.status_code == 200
        north_html = north_menu.get_data(as_text=True)
        assert f"/producto/{arepa.id}" in north_html
        assert f"/producto/{cola.id}" in north_html
        assert f"/producto/{burger.id}" in north_html
        assert f"/producto/{perro.id}" not in north_html

        response = client.post(
            f"/carrito/agregar/{burger.id}",
            data={"cantidad": "1", "origen": f"proveedor:{north_id}"},
            headers={"X-Ajax": "1"},
        )
        assert response.get_json()["ok"] is True
        response = client.post(
            f"/carrito/agregar/{arepa.id}",
            data={"cantidad": "1", "origen": f"proveedor:{north_id}"},
            headers={"X-Ajax": "1"},
        )
        assert response.get_json()["ok"] is True
        with client.session_transaction() as session:
            assert session["carrito_origen"] == f"proveedor:{north_id}"
            assert str(arepa.id) in session["carrito"]

        response = client.post(
            f"/carrito/agregar/{perro.id}",
            data={"cantidad": "1", "origen": f"proveedor:{south_id}"},
            headers={"X-Ajax": "1"},
        )
        payload = response.get_json()
        assert payload["ok"] is False
        assert "establecimiento" in payload["msg"].lower()

        with client.session_transaction() as session:
            session.clear()
        assert client.post(
            f"/carrito/agregar/{combo_own.id}",
            data={"cantidad": "1", "origen": "propio"},
            headers={"X-Ajax": "1"},
        ).get_json()["ok"] is True
        payload = client.post(
            f"/carrito/agregar/{combo_north.id}",
            data={"cantidad": "1", "origen": f"proveedor:{north_id}"},
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
        for code, origin, expected_provider in (
            ("empanada-own", "propio", None),
            ("burger-north", f"proveedor:{north_id}", north_id),
            ("perro-south", f"proveedor:{south_id}", south_id),
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
                    metadata_item_pedido(product, origen_operativo=origin),
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
            "own_menu_products": [arepa.id, cola.id],
            "north_menu_shared_product": arepa.id,
            "cross_provider_rejected": True,
            "cross_combo_rejected": True,
            "order_routing": routing,
            "transient_orders_rolled_back": True,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
