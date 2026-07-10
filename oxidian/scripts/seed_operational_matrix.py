#!/usr/bin/env python3
"""Create and verify a production-safe operational test matrix.

The fixture is idempotent and never deletes orders. Products with the same
``catalog_key`` are independent inventory variants but share one public card.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from extensions import db
from models import (
    Categoria,
    ComboItem,
    Product,
    Proveedor,
    ProveedorProducto,
    Stock,
    User,
    ZonaEntrega,
)
MATRIX_VERSION = "operational-v1"
CREDENTIALS_FILE = Path(os.environ.get(
    "MATRIX_CREDENTIALS_FILE",
    "/app/bot-data/operational-matrix-credentials.json",
))


def _category(name, order):
    row = Categoria.query.filter_by(nombre=name).first()
    if not row:
        row = Categoria(nombre=name)
        db.session.add(row)
    row.activo = True
    row.orden = order
    row.descripcion = "Matriz operativa para validar catálogo, stock y reparto."
    db.session.flush()
    return row


def _provider(name, agreement, commission=0):
    row = Proveedor.query.filter_by(nombre=name).first()
    if not row:
        row = Proveedor(nombre=name)
        db.session.add(row)
    row.activo = True
    row.modelo_acuerdo = agreement
    row.comision_pct = Decimal(str(commission))
    row.notas = f"Matriz QA {MATRIX_VERSION}"
    db.session.flush()
    return row


def _attributes(code, catalog_key, source):
    return {
        "matrix_fixture": MATRIX_VERSION,
        "matrix_code": code,
        "catalog_key": catalog_key,
        "source": source,
    }


def _find_product(code):
    for product in Product.query.all():
        if product.get_atributos().get("matrix_code") == code:
            return product
    return None


def _product(
    code,
    name,
    category,
    price,
    cost,
    catalog_key,
    source,
    provider=None,
    channel="cocina",
    delivery="inmediato",
    redeem_points=None,
):
    row = _find_product(code)
    if not row:
        row = Product(nombre=name, precio=Decimal(str(price)))
        db.session.add(row)
    row.nombre = name
    row.descripcion = f"Producto de prueba {source}; inventario aislado por origen."
    row.precio = Decimal(str(price))
    row.precio_costo = Decimal(str(cost))
    row.categoria_id = category.id
    row.imagen_url = "/static/uploads/showcase/bebidas.svg" if channel == "almacen" else "/static/uploads/showcase/arepas.svg"
    row.origen_pais = "Carmona"
    row.es_combo = False
    row.tipo_producto = "simple"
    row.canal_preparacion = channel
    row.tipo_entrega = delivery
    row.fecha_llegada = date.today() + timedelta(days=2) if delivery == "programado" else None
    row.proveedor_despachador_id = None
    row.activo = True
    row.stock_mostrar_en_web = True
    row.canjeable_con_puntos = bool(redeem_points)
    row.puntos_para_canje = redeem_points
    row.set_atributos(_attributes(code, catalog_key, source))
    db.session.flush()
    return row


def _own_stock(product, quantity):
    Stock.query.filter_by(producto_id=product.id).delete()
    db.session.add(Stock(
        producto_id=product.id,
        cantidad=quantity,
        unidad="unidad",
        lote=f"MATRIX-{product.id}",
        fecha_entrada=date.today(),
        fecha_caducidad=date.today() + timedelta(days=30),
        alerta_dias=5,
        ubicacion="Almacén propio QA",
    ))


def _provider_stock(provider, product, quantity, cost):
    row = ProveedorProducto.query.filter_by(
        proveedor_id=provider.id,
        producto_id=product.id,
    ).first()
    if not row:
        row = ProveedorProducto(
            proveedor_id=provider.id,
            producto_id=product.id,
        )
        db.session.add(row)
    row.stock = quantity
    row.precio_costo = Decimal(str(cost))
    row.activo = True


def _combo(code, name, category, price, source, components, provider=None):
    row = _find_product(code)
    if not row:
        row = Product(nombre=name, precio=Decimal(str(price)))
        db.session.add(row)
    row.nombre = name
    row.descripcion = f"Combo completo preparado por {source}."
    row.precio = Decimal(str(price))
    row.precio_costo = Decimal("0")
    row.categoria_id = category.id
    row.imagen_url = "/static/uploads/showcase/combos.svg"
    row.origen_pais = "Carmona"
    row.es_combo = True
    row.tipo_producto = "combo"
    row.canal_preparacion = "cocina"
    row.tipo_entrega = "inmediato"
    row.proveedor_despachador_id = provider.id if provider else None
    row.activo = True
    row.stock_mostrar_en_web = True
    row.set_atributos(_attributes(code, code, source))
    db.session.flush()

    ComboItem.query.filter_by(combo_id=row.id).delete()
    for order, (product, quantity) in enumerate(components):
        db.session.add(ComboItem(
            combo_id=row.id,
            producto_id=product.id,
            cantidad=quantity,
            orden=order,
            es_seleccionable=False,
            max_selecciones=1,
            activo=True,
        ))
    db.session.flush()
    return row


def _account(email, name, role, credentials, provider=None):
    row = User.query.filter_by(email=email).first()
    if not row:
        row = User(email=email, nombre=name, rol=role, activo=True)
        password = secrets.token_urlsafe(18)
        row.set_password(password)
        credentials[email] = password
        db.session.add(row)
    row.nombre = name
    row.rol = role
    row.activo = True
    row.proveedor_id = provider.id if provider else None
    db.session.flush()
    return row


def seed():
    categories = {
        "comidas": _category("Comidas QA", 1),
        "bebidas": _category("Bebidas QA", 2),
        "combos": _category("Combos QA", 3),
        "encargos": _category("Encargos QA", 4),
    }
    north = _provider("Bar Norte QA", "stock_proveedor")
    south = _provider("Bar Sur QA", "stock_propio_bar", commission=18)

    products = {}
    definitions = (
        ("arepa-own", "Arepa clásica QA", "comidas", 5.50, 2.00, "arepa-classic", "Stock propio", None, "cocina", "inmediato", 450, 30),
        ("cola-own", "Cola 330 ml QA", "bebidas", 2.00, 0.70, "cola-330", "Stock propio", None, "almacen", "inmediato", 180, 40),
        ("empanada-own", "Empanada de la casa QA", "comidas", 3.00, 1.10, "empanada-house", "Stock propio", None, "cocina", "inmediato", None, 35),
        ("burger-north", "Burger Norte QA", "comidas", 9.50, 4.10, "burger-north", "Bar Norte QA", north, "cocina", "inmediato", None, 15),
        ("perro-south", "Perro Sur QA", "comidas", 8.90, 3.80, "perro-south", "Bar Sur QA", south, "cocina", "inmediato", None, 14),
        ("torta-own", "Torta por encargo QA", "encargos", 22.00, 9.00, "torta-order", "Stock propio", None, "cocina", "programado", None, 0),
    )
    for code, name, cat, price, cost, key, source, provider, channel, delivery, points, stock in definitions:
        product = _product(
            code, name, categories[cat], price, cost, key, source,
            provider=provider, channel=channel, delivery=delivery,
            redeem_points=points,
        )
        products[code] = product
        if delivery == "inmediato":
            if provider:
                _provider_stock(provider, product, stock, cost)
            else:
                _own_stock(product, stock)

    _provider_stock(north, products["arepa-own"], 18, 2.20)
    _provider_stock(south, products["arepa-own"], 16, 2.10)
    _provider_stock(north, products["cola-own"], 24, 0.75)
    _provider_stock(south, products["cola-own"], 22, 0.72)

    for legacy_code in ("arepa-north", "arepa-south", "cola-north", "cola-south"):
        legacy = _find_product(legacy_code)
        if legacy:
            legacy.activo = False
            legacy.proveedor_despachador_id = None

    combos = {
        "combo-own": _combo(
            "combo-own", "Combo Casa QA", categories["combos"], 7.90,
            "Stock propio",
            [(products["empanada-own"], 2), (products["cola-own"], 1)],
        ),
        "combo-north": _combo(
            "combo-north", "Combo Norte QA", categories["combos"], 10.90,
            "Bar Norte QA",
            [(products["burger-north"], 1), (products["cola-own"], 1)],
            provider=north,
        ),
        "combo-south": _combo(
            "combo-south", "Combo Sur QA", categories["combos"], 10.40,
            "Bar Sur QA",
            [(products["perro-south"], 1), (products["cola-own"], 1)],
            provider=south,
        ),
    }

    zone = ZonaEntrega.query.filter_by(nombre="Carmona QA").first()
    if not zone:
        zone = ZonaEntrega(nombre="Carmona QA")
        db.session.add(zone)
    zone.activo = True
    zone.es_epicentro = True
    zone.precio_envio = Decimal("3.00")
    zone.gratis_desde = Decimal("25.00")
    zone.tiempo_estimado_min = 35
    zone.orden = 1

    credentials = {}
    _account("qa.superadmin@elparcerito.local", "QA Super Admin", "super_admin", credentials)
    _account("qa.admin@elparcerito.local", "QA Admin", "admin", credentials)
    _account("qa.cocina@elparcerito.local", "QA Cocina", "cocina", credentials)
    _account("qa.preparacion@elparcerito.local", "QA Preparación", "preparacion", credentials)
    _account("qa.repartidor@elparcerito.local", "QA Repartidor", "repartidor", credentials)
    _account("qa.norte@elparcerito.local", "QA Bar Norte", "proveedor", credentials, north)
    _account("qa.sur@elparcerito.local", "QA Bar Sur", "proveedor", credentials, south)

    db.session.commit()
    if credentials:
        CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if CREDENTIALS_FILE.exists():
            existing = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        existing.update(credentials)
        CREDENTIALS_FILE.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        CREDENTIALS_FILE.chmod(0o600)
    return products, combos, north, south


def verify():
    matrix_products = [
        product for product in Product.query.filter_by(activo=True).all()
        if product.get_atributos().get("matrix_fixture") == MATRIX_VERSION
    ]
    assert matrix_products, "La matriz no contiene productos"

    propios = [
        product for product in matrix_products
        if product.pertenece_a_origen("propio")
        and product.disponible_para_venta_en_origen("propio")
    ]
    keys = [product.clave_catalogo for product in propios]
    assert len(keys) == len(set(keys)), "El catálogo mantiene claves repetidas"
    assert sum(p.nombre == "Arepa clásica QA" for p in propios) == 1
    assert sum(p.nombre == "Cola 330 ml QA" for p in propios) == 1

    for combo in [p for p in matrix_products if p.es_combo]:
        for item in combo.combo_items:
            if combo.proveedor_despachador_id:
                row = ProveedorProducto.query.filter_by(
                    proveedor_id=combo.proveedor_despachador_id,
                    producto_id=item.producto_id,
                    activo=True,
                ).first()
                assert row, f"{combo.nombre} mezcla o pierde el SKU {item.producto_id}"
            else:
                assert not item.componente.proveedor_despachador_id, (
                    f"{combo.nombre} incluye componente externo"
                )
        combo.validar_stock_combo_seleccion(
            1,
            origen=combo.origen_operativo_key,
        )

    own = _find_product("cola-own")
    north_provider = Proveedor.query.filter_by(nombre="Bar Norte QA").one()
    south_provider = Proveedor.query.filter_by(nombre="Bar Sur QA").one()
    own_before = own.stock_en_origen("propio")
    north_before = own.stock_en_origen(f"proveedor:{north_provider.id}")
    south_before = own.stock_en_origen(f"proveedor:{south_provider.id}")
    own.descontar_stock_en_origen("propio", 1)
    own.descontar_stock_en_origen(f"proveedor:{north_provider.id}", 1)
    db.session.flush()
    assert own.stock_en_origen("propio") == own_before - 1
    assert own.stock_en_origen(f"proveedor:{north_provider.id}") == north_before - 1
    assert own.stock_en_origen(f"proveedor:{south_provider.id}") == south_before
    db.session.rollback()

    expected_roles = {"super_admin", "admin", "preparacion", "repartidor", "proveedor"}
    qa_roles = {
        user.rol for user in User.query.filter(User.email.like("qa.%@elparcerito.local")).all()
    }
    assert expected_roles.issubset(qa_roles)
    assert "cliente" not in qa_roles

    return {
        "matrix_products": len(matrix_products),
        "public_cards": len(propios),
        "providers": Proveedor.query.filter(Proveedor.nombre.in_(["Bar Norte QA", "Bar Sur QA"])).count(),
        "qa_accounts": User.query.filter(User.email.like("qa.%@elparcerito.local")).count(),
        "credentials_file": str(CREDENTIALS_FILE),
    }


def main():
    action = (sys.argv[1] if len(sys.argv) > 1 else "seed").strip().lower()
    app = create_app("production")
    with app.app_context():
        if action in {"seed", "create"}:
            seed()
            print(json.dumps(verify(), ensure_ascii=False))
        elif action == "verify":
            print(json.dumps(verify(), ensure_ascii=False))
        else:
            raise SystemExit("Uso: seed_operational_matrix.py seed|verify")


if __name__ == "__main__":
    main()
