#!/usr/bin/env python3
"""Fixture masivo y reversible para zonas, pedidos y finanzas (solo QA)."""
from __future__ import annotations

import json
import os
import random
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("OXIDIAN_SKIP_STARTUP_DB", "1")

from flask import Flask
from extensions import db
from models import (Caja, Categoria, Order, OrderItem, Product, StaffPayment,
                    User, ZonaEntrega, metadata_item_pedido, utcnow)
from services import generar_comision_entrega, registrar_ingreso_pedido
from services import asignar_zona_por_coordenadas

PREFIX = "QA-MASS"
CUSTOMERS = 100
STAFF = 5
RNG = random.Random(20260905)


def create_seed_app():
    """Usa la app real en PostgreSQL y un arnés mínimo solo para SQLite QA."""
    uri = os.environ.get("DATABASE_URL", "")
    if uri.lower().startswith("sqlite:"):
        app = Flask("qa_mass_seed")
        app.config.update(
            TESTING=True,
            SECRET_KEY=os.environ.get("SECRET_KEY", "qa-mass-ephemeral"),
            SQLALCHEMY_DATABASE_URI=uri,
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(app)
        return app
    from app import create_app
    return create_app()


def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"))


def guard():
    uri = str(db.engine.url).lower()
    if os.environ.get("ALLOW_QA_MASS_SEED") != "1" or "test" not in uri:
        raise SystemExit(
            "REHUSO: exige ALLOW_QA_MASS_SEED=1 y DATABASE_URL con 'test'. "
            f"Base detectada: {uri!r}"
        )


def cleanup():
    order_ids = [r[0] for r in db.session.query(Order.id).filter(
        Order.numero_pedido.like(f"{PREFIX}-%")).all()]
    if order_ids:
        Caja.query.filter(Caja.pedido_id.in_(order_ids)).delete(synchronize_session=False)
        StaffPayment.query.filter(StaffPayment.pedido_id.in_(order_ids)).delete(synchronize_session=False)
        OrderItem.query.filter(OrderItem.pedido_id.in_(order_ids)).delete(synchronize_session=False)
        Order.query.filter(Order.id.in_(order_ids)).delete(synchronize_session=False)
    Caja.query.filter(Caja.concepto.like(f"{PREFIX}-%")).delete(synchronize_session=False)
    StaffPayment.query.filter(StaffPayment.concepto.like(f"{PREFIX}-%")).delete(synchronize_session=False)
    Product.query.filter(Product.nombre.like(f"{PREFIX}-%")).delete(synchronize_session=False)
    Categoria.query.filter(Categoria.nombre.like(f"{PREFIX}-%")).delete(synchronize_session=False)
    User.query.filter(User.email.like("qa-mass-%@test.invalid")).delete(synchronize_session=False)
    ZonaEntrega.query.filter(ZonaEntrega.nombre.like(f"{PREFIX}-%")).delete(synchronize_session=False)
    db.session.commit()


def references():
    zones = []
    for idx, (label, fee, radius) in enumerate((("Centro", 1.5, 2), ("Periferia", 3.25, 5))):
        zone = ZonaEntrega(
            nombre=f"{PREFIX}-{label}", activo=True, es_epicentro=idx == 0,
            precio_envio=money(fee), tiempo_estimado_min=25 + idx * 15,
            orden=idx, centro_lat=37.471 + idx * .04,
            centro_lng=-5.641 - idx * .04, radio_km=radius,
        )
        db.session.add(zone)
        zones.append(zone)
    category = Categoria(nombre=f"{PREFIX}-Catálogo", activo=True)
    db.session.add(category)
    db.session.flush()
    products = []
    for label, price, cost in (("Antojo", 6.5, 2.1), ("Bebida", 2.8, .9), ("Combo", 11.9, 4.7)):
        product = Product(
            nombre=f"{PREFIX}-{label}", categoria_id=category.id,
            precio=money(price), precio_costo=money(cost), activo=True,
            es_combo=False, tipo_producto="simple", canal_preparacion="cocina",
            modalidad_entrega="ambas", stock_mostrar_en_web=False,
        )
        db.session.add(product)
        products.append(product)
    db.session.flush()
    return zones, products


def people(zones):
    roles = ("admin", "cocina", "preparacion", "repartidor", "repartidor")
    staff = []
    for idx, role in enumerate(roles, 1):
        user = User(
            nombre=f"QA Empleado {idx}", email=f"qa-mass-staff-{idx}@test.invalid",
            telefono=f"3469900{idx:04d}", rol=role, activo=True,
            salario_base=money(1250 + idx * 75),
            tarifa_entrega=money(2.25 + idx * .15) if role == "repartidor" else 0,
            en_linea=role == "repartidor",
        )
        if role == "repartidor":
            user.zona_repartidor = zones[idx % len(zones)]
        user.set_password("qa-fixture-not-for-login")
        db.session.add(user)
        staff.append(user)
    customers = []
    for idx in range(1, CUSTOMERS + 1):
        user = User(
            nombre=f"Cliente QA {idx:03d}", email=f"qa-mass-client-{idx:03d}@test.invalid",
            telefono=f"34698{idx:06d}", rol="cliente", activo=True,
            direccion=f"Calle QA {idx}, Carmona", puntos=idx * 7 % 400,
        )
        user.set_password("qa-fixture-not-for-login")
        db.session.add(user)
        customers.append(user)
    db.session.flush()
    return staff, customers


def history(staff, customers, zones, products):
    now = utcnow()
    preparers = [u for u in staff if u.rol in ("cocina", "preparacion")]
    riders = [u for u in staff if u.rol == "repartidor"]
    counters = {"orders": 0, "delivered": 0, "cancelled": 0, "active": 0}
    for ci, customer in enumerate(customers, 1):
        for seq in range(2 + ci % 5):
            created = now - timedelta(days=RNG.randrange(120), hours=RNG.randrange(20))
            roll = RNG.random()
            state = "entregado" if roll < .84 else "cancelado" if roll < .93 else RNG.choice(("pendiente", "armando", "listo", "en_ruta"))
            zone, product = zones[(ci + seq) % 2], products[(ci + seq) % 3]
            qty = 1 + (ci + seq) % 3
            subtotal = money(product.precio * qty)
            discount = money(1 if (ci + seq) % 11 == 0 else 0)
            total = money(subtotal - discount + zone.precio_envio)
            rider = riders[(ci + seq) % len(riders)]
            order = Order(
                numero_pedido=f"{PREFIX}-{ci:03d}-{seq:02d}", cliente_id=customer.id,
                estado=state, origen="online", subtotal=subtotal, descuento=discount,
                total=total, metodo_pago=("efectivo", "bizum", "tarjeta")[(ci + seq) % 3],
                pago_confirmado=state == "entregado", tipo_entrega_cliente="delivery",
                direccion_entrega=customer.direccion,
                preparador_id=preparers[(ci + seq) % len(preparers)].id,
                repartidor_id=rider.id if state in ("listo", "en_ruta", "entregado") else None,
                creado_en=created, entregado_en=created + timedelta(minutes=45) if state == "entregado" else None,
                zona_id=zone.id, zona_nombre_snapshot=zone.nombre,
                zona_precio_envio_snapshot=zone.precio_envio,
                costo_envio_snapshot=zone.precio_envio,
                zona_tiempo_estimado_min_snapshot=zone.tiempo_estimado_min,
                zona_tipo_cobertura_snapshot=zone.tipo_cobertura,
                es_entrega_epicentro=zone.es_epicentro,
            )
            db.session.add(order)
            db.session.flush()
            db.session.add(OrderItem(
                pedido_id=order.id, producto_id=product.id, cantidad=qty,
                precio_unit=product.precio, subtotal=subtotal,
                metadata_json=json.dumps(metadata_item_pedido(product, {}), ensure_ascii=False),
            ))
            if state == "entregado":
                registrar_ingreso_pedido(order, registrado_por=staff[0].id).fecha = order.entregado_en
                commission = generar_comision_entrega(order)
                if commission:
                    commission.creado_en = order.entregado_en
                counters["delivered"] += 1
            elif state == "cancelado":
                counters["cancelled"] += 1
            else:
                counters["active"] += 1
            counters["orders"] += 1
    for month in range(3):
        paid_at = now - timedelta(days=month * 30 + 2)
        for employee in staff:
            payment = StaffPayment(
                user_id=employee.id, tipo="salario", monto=employee.salario_base,
                concepto=f"{PREFIX}-Nómina-{month + 1}", pagado=True,
                fecha_pago=paid_at, creado_en=paid_at, origen="manual",
            )
            db.session.add(payment)
            db.session.flush()
            db.session.add(Caja(
                tipo="egreso", categoria="salario", monto=payment.monto,
                concepto=payment.concepto, staff_payment_id=payment.id,
                registrado_por=staff[0].id, fecha=paid_at,
            ))
        for category, amount in (("compra_insumos", 780), ("alquiler", 600), ("servicios", 145), ("marketing", 90)):
            db.session.add(Caja(
                tipo="egreso", categoria=category, monto=money(amount + month * 10),
                concepto=f"{PREFIX}-{category}-{month + 1}",
                registrado_por=staff[0].id, fecha=paid_at,
            ))
    return counters


def verify():
    orders = Order.query.filter(Order.numero_pedido.like(f"{PREFIX}-%"))
    ids = [o.id for o in orders.all()]
    delivered = orders.filter_by(estado="entregado").count()
    incomes_q = Caja.query.filter(Caja.pedido_id.in_(ids), Caja.tipo == "ingreso") if ids else None
    incomes = incomes_q.count() if incomes_q is not None else 0
    duplicate_income_entries = db.session.query(Caja.pedido_id).filter(
        Caja.pedido_id.in_(ids), Caja.tipo == "ingreso").group_by(Caja.pedido_id).having(
        db.func.count(Caja.id) > 1).count() if ids else 0
    cancelled_with_income = orders.filter(
        Order.estado == "cancelado", Order.id.in_(db.session.query(Caja.pedido_id).filter(Caja.tipo == "ingreso"))).count() if ids else 0
    orphan_items = OrderItem.query.filter(OrderItem.pedido_id.in_(ids), OrderItem.producto_id.is_(None)).count() if ids else 0
    delivery_commissions = StaffPayment.query.filter(
        StaffPayment.pedido_id.in_(ids), StaffPayment.tipo == "comision",
        StaffPayment.origen == "delivery").count() if ids else 0
    missing_zone_snapshots = orders.filter(
        Order.estado == "entregado", db.or_(Order.zona_id.is_(None),
        Order.zona_nombre_snapshot.is_(None), Order.zona_precio_envio_snapshot.is_(None))).count()
    delivered_total = money(db.session.query(db.func.coalesce(db.func.sum(Order.total), 0)).filter(
        Order.id.in_(ids), Order.estado == "entregado").scalar() or 0) if ids else money(0)
    order_income_total = money(db.session.query(db.func.coalesce(db.func.sum(Caja.monto), 0)).filter(
        Caja.pedido_id.in_(ids), Caja.tipo == "ingreso").scalar() or 0) if ids else money(0)
    salary_payments = StaffPayment.query.filter(StaffPayment.concepto.like(f"{PREFIX}-Nómina-%")).count()
    salary_expenses = Caja.query.filter(Caja.concepto.like(f"{PREFIX}-Nómina-%"), Caja.tipo == "egreso").count()
    zones = ZonaEntrega.query.filter(ZonaEntrega.nombre.like(f"{PREFIX}-%")).all()
    zone_resolution_errors = 0
    for zone in zones:
        matched, _distance = asignar_zona_por_coordenadas(
            zone.centro_lat, zone.centro_lng, zones
        )
        if not matched or matched.id != zone.id:
            zone_resolution_errors += 1
    report = {
        "staff": User.query.filter(User.email.like("qa-mass-staff-%@test.invalid")).count(),
        "customers": User.query.filter(User.email.like("qa-mass-client-%@test.invalid")).count(),
        "orders": len(ids), "delivered": delivered, "income_entries": incomes,
        "zones": ZonaEntrega.query.filter(ZonaEntrega.nombre.like(f"{PREFIX}-%")).count(),
        "duplicate_income_entries": duplicate_income_entries,
        "cancelled_with_income": cancelled_with_income, "orphan_items": orphan_items,
        "delivery_commissions": delivery_commissions,
        "missing_zone_snapshots": missing_zone_snapshots,
        "delivered_total": float(delivered_total), "order_income_total": float(order_income_total),
        "salary_payments": salary_payments, "salary_expenses": salary_expenses,
        "zone_resolution_errors": zone_resolution_errors,
    }
    report["ok"] = (
        report["staff"] == STAFF and report["customers"] == CUSTOMERS
        and len(ids) >= CUSTOMERS * 2 and delivered == incomes == delivery_commissions
        and delivered_total == order_income_total and salary_payments == salary_expenses == STAFF * 3
        and duplicate_income_entries == cancelled_with_income == orphan_items == missing_zone_snapshots == zone_resolution_errors == 0
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ok"]:
        raise SystemExit(2)


def seed():
    cleanup()
    zones, products = references()
    staff, customers = people(zones)
    print(json.dumps(history(staff, customers, zones, products), indent=2))
    db.session.commit()
    verify()


if __name__ == "__main__":
    app = create_seed_app()
    with app.app_context():
        guard()
        if os.environ.get("QA_MASS_CREATE_SCHEMA") == "1":
            db.create_all()
        command = (sys.argv[1] if len(sys.argv) > 1 else "verify").lower()
        if command == "seed": seed()
        elif command == "verify": verify()
        elif command == "cleanup": cleanup(); print("Fixture QA-MASS eliminado.")
        else: raise SystemExit("Uso: seed_finance_stress.py seed|verify|cleanup")
