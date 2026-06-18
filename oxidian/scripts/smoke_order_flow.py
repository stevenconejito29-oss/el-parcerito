#!/usr/bin/env python3
"""Prueba transaccional del flujo web -> preparacion -> reparto.

Crea datos QA aislados, recorre las rutas HTTP reales con CSRF habilitado y
elimina todos los registros temporales al terminar, incluso si la prueba falla.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from decimal import Decimal
from pathlib import Path

from flask import g
from sqlalchemy import text
from werkzeug.datastructures import MultiDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from extensions import db
from models import (
    AuditLog,
    Categoria,
    NotificationOutbox,
    Order,
    OrderEvent,
    OrderItem,
    Product,
    SiteConfig,
    Stock,
    User,
    ZonaEntrega,
)


CSRF_RE = re.compile(rb'name="csrf_token"\s+value="([^"]+)"')


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def csrf_from(response) -> str:
    match = CSRF_RE.search(response.data)
    require(match is not None, f"No se encontro CSRF en {response.request.path}")
    return match.group(1).decode("utf-8")


def reset_request_cache() -> None:
    # El app_context exterior permite consultar la BD entre requests, pero g
    # debe comportarse como si cada cliente estuviera en un proceso separado.
    g.pop("csrf_token", None)
    g.pop("_login_user", None)


def new_client(app, ip: str):
    client = app.test_client()
    client.qa_environ = {"REMOTE_ADDR": ip}
    return client


def get_ok(client, path: str, contains: str | None = None):
    reset_request_cache()
    response = client.get(
        path,
        follow_redirects=True,
        environ_overrides=client.qa_environ,
    )
    require(response.status_code == 200, f"GET {path}: HTTP {response.status_code}")
    if contains:
        require(contains.encode("utf-8") in response.data, f"GET {path}: falta {contains!r}")
    return response


def post_form(client, path: str, source_path: str, data: dict):
    source = get_ok(client, source_path)
    payload = MultiDict(data)
    payload.setlist("csrf_token", [csrf_from(source)])
    reset_request_cache()
    response = client.post(
        path,
        data=payload,
        follow_redirects=True,
        environ_overrides=client.qa_environ,
    )
    require(response.status_code == 200, f"POST {path}: HTTP {response.status_code}")
    return response


def login(client, email: str, password: str) -> None:
    response = post_form(
        client,
        "/auth/login",
        "/auth/login",
        {"email": email, "password": password},
    )
    require(b"Email o contrase" not in response.data, f"No se pudo iniciar sesion como {email}")


def cleanup(order_ids, customer_id, combo_id, product_ids, category_id, zone_id, created_zone) -> None:
    db.session.rollback()
    for order_id in [oid for oid in (order_ids or []) if oid]:
        db.session.execute(
            text("DELETE FROM audit_log WHERE recurso = 'order' AND recurso_id = :id"),
            {"id": order_id},
        )
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
    if customer_id:
        db.session.execute(
            text("DELETE FROM notification_outbox WHERE user_id = :id"),
            {"id": customer_id},
        )
        db.session.execute(text("DELETE FROM users WHERE id = :id"), {"id": customer_id})
    if combo_id:
        db.session.execute(text("DELETE FROM combo_items WHERE combo_id = :id"), {"id": combo_id})
        db.session.execute(text("DELETE FROM combo_groups WHERE combo_id = :id"), {"id": combo_id})
        db.session.execute(text("DELETE FROM products WHERE id = :id"), {"id": combo_id})
    for product_id in [pid for pid in (product_ids or []) if pid]:
        db.session.execute(text("DELETE FROM stock WHERE producto_id = :id"), {"id": product_id})
        db.session.execute(text("DELETE FROM products WHERE id = :id"), {"id": product_id})
    if category_id:
        db.session.execute(text("DELETE FROM categorias WHERE id = :id"), {"id": category_id})
    if created_zone and zone_id:
        db.session.execute(text("DELETE FROM zonas_entrega WHERE id = :id"), {"id": zone_id})
    db.session.commit()


def main() -> int:
    marker = uuid.uuid4().hex[:10]
    phone_suffix = str(uuid.uuid4().int)[-8:]
    password = f"Smoke-{uuid.uuid4().hex}"

    app = create_app("production")
    order_id = pos_order_id = customer_id = combo_id = product_id = product_option_id = category_id = zone_id = None
    created_zone = False
    staff_original = {}
    password_original = {}
    config_original = {}
    pending_unassigned_ids = []
    ready_unassigned_ids = []
    config_keys = {
        "TIENDA_FORZAR_CERRADA": "0",
        "HORARIO_APERTURA": "00:00",
        "HORARIO_CIERRE": "23:59",
        "VALIDAR_RADIO_ENTREGA": "0",
        "BLOQUEAR_DIRECCION_NO_VERIFICADA": "0",
        "WHATSAPP_SIMULATE_SEND": "1",
    }

    with app.app_context():
        try:
            superadmin = User.query.filter_by(rol="super_admin", activo=True).first()
            preparador = User.query.filter(
                User.rol.in_(["cocina", "preparacion"]), User.activo.is_(True)
            ).first()
            repartidor = User.query.filter_by(rol="repartidor", activo=True).first()
            require(superadmin is not None, "Falta el super admin")
            require(preparador is not None, "Falta el usuario de preparacion")
            require(repartidor is not None, "Falta el repartidor")
            for user in (superadmin, preparador, repartidor):
                password_original[user.id] = user.password_hash
                user.set_password(password)
            pending_unassigned_ids = [
                row.id for row in Order.query.filter_by(
                    estado="pendiente",
                    preparador_id=None,
                ).all()
            ]
            ready_unassigned_ids = [
                row.id for row in Order.query.filter_by(
                    estado="listo",
                    repartidor_id=None,
                ).all()
            ]

            for user in (preparador, repartidor):
                staff_original[user.id] = (user.en_linea, user.last_seen)
                user.en_linea = False
                user.last_seen = None

            for key, value in config_keys.items():
                entry = SiteConfig.query.filter_by(clave=key).first()
                config_original[key] = (entry is not None, entry.valor if entry else None)
                SiteConfig.set(key, value)

            zone = ZonaEntrega.query.filter_by(activo=True).order_by(ZonaEntrega.orden).first()
            if zone is None:
                zone = ZonaEntrega(
                    nombre=f"QA {marker}",
                    activo=True,
                    es_epicentro=True,
                    precio_envio=Decimal("0"),
                    tiempo_estimado_min=30,
                    orden=9999,
                )
                db.session.add(zone)
                db.session.flush()
                created_zone = True
            zone_id = zone.id

            category = Categoria(nombre=f"QA {marker}", activo=True, orden=9999)
            db.session.add(category)
            db.session.flush()
            category_id = category.id

            product = Product(
                nombre=f"Producto QA {marker}",
                descripcion="Producto temporal para prueba integral",
                precio=Decimal("9.90"),
                precio_costo=Decimal("3.00"),
                categoria_id=category.id,
                activo=True,
                es_combo=False,
                tipo_producto="simple",
                tipo_entrega="inmediato",
            )
            db.session.add(product)
            db.session.flush()
            product_id = product.id
            db.session.add(Stock(producto_id=product.id, cantidad=10, lote=f"QA-{marker}"))

            product_option = Product(
                nombre=f"Opcion QA {marker}",
                descripcion="Opcion temporal para combo",
                precio=Decimal("2.00"),
                precio_costo=Decimal("0.50"),
                categoria_id=category.id,
                activo=True,
                es_combo=False,
                tipo_producto="simple",
                tipo_entrega="inmediato",
                canjeable_con_puntos=True,
                puntos_para_canje=100,
            )
            db.session.add(product_option)
            db.session.flush()
            product_option_id = product_option.id
            db.session.add(Stock(producto_id=product_option.id, cantidad=10, lote=f"QA-OPT-{marker}"))
            db.session.commit()

            admin_client = new_client(app, "127.0.0.22")
            login(admin_client, superadmin.email, password)
            combo_name = f"Combo QA {marker}"
            combo_response = post_form(
                admin_client,
                "/admin/combos/nuevo",
                "/admin/combos/nuevo",
                [
                    ("nombre", combo_name),
                    ("descripcion", "Combo temporal para prueba integral"),
                    ("precio", "1.00"),
                    ("precio_costo", "3.00"),
                    ("categoria_id", str(category.id)),
                    ("tipo_producto", "combo"),
                    ("tipo_entrega", "inmediato"),
                    ("combo_precio_modo", "descuento_porcentaje"),
                    ("combo_descuento_pct", "10"),
                    ("combo_group_uid", "qa-base"),
                    ("combo_group_name", "Incluido"),
                    ("combo_group_type", "fijo"),
                    ("combo_group_max_sel", "1"),
                    ("combo_group_order", "0"),
                    ("comp_prod_id", str(product.id)),
                    ("comp_cantidad", "1"),
                    ("comp_tipo", "fijo"),
                    ("comp_grupo", ""),
                    ("comp_max_sel", "1"),
                    ("comp_precio_extra", "0"),
                    ("comp_default", "0"),
                    ("comp_notas_preparacion", "Incluir producto QA"),
                    ("comp_group_uid", "qa-base"),
                    ("combo_group_uid", "qa-choice"),
                    ("combo_group_name", "Acompañamiento"),
                    ("combo_group_type", "sel"),
                    ("combo_group_max_sel", "1"),
                    ("combo_group_order", "1"),
                    ("comp_prod_id", str(product_option.id)),
                    ("comp_cantidad", "1"),
                    ("comp_tipo", "sel"),
                    ("comp_grupo", "Acompañamiento"),
                    ("comp_max_sel", "1"),
                    ("comp_precio_extra", "0.50"),
                    ("comp_default", "1"),
                    ("comp_notas_preparacion", "Opcion QA seleccionada"),
                    ("comp_group_uid", "qa-choice"),
                ],
            )
            combo = Product.query.filter_by(nombre=combo_name, es_combo=True).first()
            require(combo is not None, "El formulario no creo el combo")
            combo_id = combo.id
            require(combo.combo_items.count() == 2, "El combo no guardo sus componentes")
            require(combo.combo_groups.count() == 2, "El combo no guardo sus secciones")
            require(combo.combo_precio_modo_normalizado == "descuento_porcentaje", "Modo de precio incorrecto")
            require(Decimal(combo.precio) == Decimal("10.71"), f"Precio porcentual incorrecto: {combo.precio}")
            require(combo.nombre.encode("utf-8") in combo_response.data, "Fallo la vista de detalle del combo")
            selectable = combo.combo_items.filter_by(es_seleccionable=True).first()
            require(selectable is not None, "Falta la opcion seleccionable del combo")

            customer_phone = f"+3499{phone_suffix}"
            customer = User(
                nombre=f"Cliente QA {marker}",
                email=f"qa-{marker}@oxidian.invalid",
                telefono=customer_phone,
                rol="cliente",
                activo=True,
                puntos=500,
            )
            customer.set_password(uuid.uuid4().hex)
            db.session.add(customer)
            db.session.flush()
            customer_id = customer.id
            db.session.commit()

            pos_page = get_ok(admin_client, "/pos/", "Punto de Venta")
            reset_request_cache()
            pos_response = admin_client.post(
                "/pos/cobrar",
                json={
                    "items": [{
                        "producto_id": combo.id,
                        "cantidad": 1,
                        "combo_item_ids": [selectable.id],
                    }],
                    "metodo_pago": "efectivo",
                    "cliente_id": customer.id,
                    "descuento_manual": 0,
                    "notas": "Venta POS QA",
                },
                headers={"X-CSRFToken": csrf_from(pos_page)},
                environ_overrides=admin_client.qa_environ,
            )
            require(pos_response.status_code == 200, f"POS: HTTP {pos_response.status_code}")
            require(pos_response.is_json and pos_response.json.get("ok"), "El POS no creo la venta")
            require(
                Decimal(str(pos_response.json.get("total"))) == Decimal("11.21"),
                f"Precio dinamico POS incorrecto: {pos_response.json.get('total')}",
            )
            pos_order_id = pos_response.json["pedido_id"]
            pos_item = OrderItem.query.filter_by(pedido_id=pos_order_id, producto_id=combo.id).first()
            require(pos_item is not None, "El POS no guardo la linea del combo")
            require(
                (pos_item.get_metadata().get("combo") or {}).get("selecciones"),
                "El POS no guardo las elecciones del combo",
            )
            db.session.expire_all()
            points_before_checkout = int(db.session.get(User, customer_id).puntos or 0)

            public = new_client(app, "127.0.0.21")
            get_ok(public, "/", combo.nombre)
            get_ok(public, f"/producto/{combo.id}", combo.nombre)
            post_form(
                public,
                f"/carrito/agregar/{combo.id}",
                f"/producto/{combo.id}",
                {
                    "cantidad": "2",
                    "combo_item_Acompañamiento": str(selectable.id),
                },
            )
            get_ok(public, "/carrito", combo.nombre)
            checkout = get_ok(public, "/checkout", "Confirmar Pedido")
            checkout_csrf = csrf_from(checkout)

            reset_request_cache()
            otp_response = public.post(
                "/puntos/solicitar-codigo",
                json={"telefono": customer_phone},
                headers={"X-CSRFToken": checkout_csrf},
                environ_overrides=public.qa_environ,
            )
            require(otp_response.status_code == 200, f"OTP puntos: HTTP {otp_response.status_code}")
            require(otp_response.is_json and otp_response.json.get("ok"), "No se envió OTP de puntos")
            db.session.expire_all()
            customer = db.session.get(User, customer_id)
            otp_code = customer.cod_puntos
            require(bool(otp_code), "No se persistió el OTP de puntos")

            reset_request_cache()
            verify_response = public.post(
                "/puntos/verificar-codigo",
                json={
                    "telefono": customer_phone,
                    "codigo": otp_code,
                    "puntos": 0,
                    "producto_canje_id": None,
                },
                headers={"X-CSRFToken": checkout_csrf},
                environ_overrides=public.qa_environ,
            )
            require(verify_response.status_code == 200, f"Verificar puntos: HTTP {verify_response.status_code}")
            require(verify_response.is_json and verify_response.json.get("ok"), "No se verificó el OTP")
            require(
                any(p["id"] == product_option.id for p in verify_response.json.get("canjeables", [])),
                "El producto canjeable no apareció después de verificar WhatsApp",
            )

            reset_request_cache()
            response = public.post(
                "/checkout",
                data={
                    "csrf_token": checkout_csrf,
                    "telefono_invitado": customer_phone,
                    "nombre_invitado": f"Cliente QA {marker}",
                    "direccion": "Calle QA 1, Carmona",
                    "metodo_pago": "efectivo",
                    "zona_id": str(zone.id),
                    "puntos_usar": "0",
                    "producto_canje_id": str(product_option.id),
                },
                follow_redirects=True,
                environ_overrides=public.qa_environ,
            )
            require(response.status_code == 200, f"Checkout: HTTP {response.status_code}")

            order_item = OrderItem.query.filter_by(producto_id=combo.id).order_by(OrderItem.id.desc()).first()
            require(order_item is not None, "Checkout no creo la linea del pedido")
            require(
                Decimal(order_item.precio_unit) == Decimal("11.21"),
                f"Precio dinamico web incorrecto: {order_item.precio_unit}",
            )
            order_id = order_item.pedido_id
            order = db.session.get(Order, order_id)
            require(order is not None, "Checkout no creo el pedido")
            customer = db.session.get(User, order.cliente_id)
            require(customer is not None, "Checkout no creo el cliente invitado")
            customer_id = customer.id
            require(customer.telefono == customer_phone, "El checkout altero el telefono normalizado")
            require(order.estado == "pendiente", "El pedido no inicio pendiente")
            require(order.items.count() == 2, "El pedido no guardó la compra y el producto de canje")
            require(product.stock_total == 7, "POS + checkout no descontaron el componente fijo esperado")
            require(product_option.stock_total == 6, "El combo y el canje no descontaron el stock esperado")
            reward_item = next((item for item in order.items if item.es_canje_puntos), None)
            require(reward_item is not None, "El producto gratis no quedó marcado como canje")
            require(reward_item.precio_unit == 0 and reward_item.subtotal == 0, "El canje alteró el total monetario")
            require(reward_item.reward_metadata.get("puntos") == 100, "Falta el costo en puntos del canje")
            require(order.puntos_usados == 100, "El pedido no registró los puntos usados")
            customer = db.session.get(User, customer_id)
            require(
                customer.puntos == points_before_checkout - 100,
                f"Saldo de puntos incorrecto tras canje: {customer.puntos}",
            )
            require(order.eventos.count() >= 1, "El pedido no genero auditoria de estados")
            require(len(order.movimientos_caja) == 0, "El efectivo entro en caja antes de cobrarse")
            require(order.numero_pedido.encode("utf-8") in response.data, "Fallo la confirmacion publica")

            get_ok(admin_client, "/superadmin/dashboard")
            get_ok(admin_client, "/admin/pedidos", order.numero_pedido)

            prep_client = new_client(app, "127.0.0.23")
            login(prep_client, preparador.email, password)
            toggle = post_form(
                prep_client,
                "/preparador/toggle-disponible",
                "/preparador/pedidos",
                {},
            )
            require(toggle.is_json and toggle.json.get("en_linea"), "Preparacion no quedo online")
            db.session.expire_all()
            order = db.session.get(Order, order_id)
            require(order.preparador_id == preparador.id, "No se redistribuyo el pedido al preparador")
            get_ok(prep_client, "/preparador/pedidos", order.numero_pedido)
            get_ok(prep_client, "/preparador/pedidos", "Canje con puntos")
            post_form(
                prep_client,
                f"/preparador/pedidos/{order.id}/empezar",
                "/preparador/pedidos",
                {},
            )
            db.session.expire_all()
            require(db.session.get(Order, order_id).estado == "armando", "No avanzo a armando")
            post_form(
                prep_client,
                f"/preparador/pedidos/{order.id}/listo",
                "/preparador/pedidos",
                {},
            )
            db.session.expire_all()
            require(db.session.get(Order, order_id).estado == "listo", "No avanzo a listo")

            delivery_client = new_client(app, "127.0.0.24")
            login(delivery_client, repartidor.email, password)
            toggle = post_form(
                delivery_client,
                "/repartidor/toggle-disponible",
                "/repartidor/ruta",
                {},
            )
            require(toggle.is_json and toggle.json.get("en_linea"), "Reparto no quedo online")
            get_ok(delivery_client, "/repartidor/ruta", order.numero_pedido)
            get_ok(delivery_client, "/repartidor/ruta", "Canje con puntos")
            post_form(
                delivery_client,
                f"/repartidor/pedidos/{order.id}/salir",
                "/repartidor/ruta",
                {},
            )
            db.session.expire_all()
            order = db.session.get(Order, order_id)
            require(order.estado == "en_ruta", "No avanzo a en_ruta")
            require(order.codigo_confirmacion, "No se genero codigo de entrega")
            post_form(
                delivery_client,
                f"/repartidor/pedidos/{order.id}/entregar",
                "/repartidor/ruta",
                {
                    "codigo_confirmacion": order.codigo_confirmacion,
                    "cobro_recibido": "1",
                },
            )
            db.session.expire_all()
            order = db.session.get(Order, order_id)
            require(order.estado == "entregado", "No avanzo a entregado")
            require(order.pago_confirmado, "La entrega no confirmo el pago")
            require(len(order.movimientos_caja) == 1, "El cobro entregado no entro en caja")
            customer = db.session.get(User, customer_id)
            require(
                customer.puntos == points_before_checkout - 100 + order.puntos_ganados,
                f"Saldo de puntos incorrecto tras entrega: {customer.puntos}",
            )
            states = [
                event.estado_nuevo
                for event in OrderEvent.query.filter_by(pedido_id=order.id)
                .order_by(OrderEvent.id)
                .all()
                if event.estado_nuevo
            ]
            require(
                all(state in states for state in ("pendiente", "armando", "listo", "en_ruta", "entregado")),
                f"Timeline incompleto: {states}",
            )
            require(
                NotificationOutbox.query.filter_by(pedido_id=order.id).count() >= 4,
                "Faltan notificaciones de estado en el outbox",
            )
            get_ok(admin_client, "/admin/pedidos", order.numero_pedido)

            print(
                "OK: menu, producto, carrito, checkout, POS con combo, superadmin, "
                "canje por producto, preparacion, reparto, pago, stock, caja, auditoria y notificaciones."
            )
            return 0
        finally:
            for key, (existed, value) in config_original.items():
                entry = SiteConfig.query.filter_by(clave=key).first()
                if existed:
                    SiteConfig.set(key, value)
                elif entry:
                    db.session.delete(entry)
            for user_id, (online, last_seen) in staff_original.items():
                user = db.session.get(User, user_id)
                if user:
                    user.en_linea = online
                    user.last_seen = last_seen
            for user_id, password_hash in password_original.items():
                user = db.session.get(User, user_id)
                if user:
                    user.password_hash = password_hash
            if preparador:
                Order.query.filter(
                    Order.id.in_(pending_unassigned_ids),
                    Order.estado == "pendiente",
                    Order.preparador_id == preparador.id,
                ).update(
                    {Order.preparador_id: None},
                    synchronize_session=False,
                )
            if repartidor:
                Order.query.filter(
                    Order.id.in_(ready_unassigned_ids),
                    Order.estado == "listo",
                    Order.repartidor_id == repartidor.id,
                ).update(
                    {Order.repartidor_id: None},
                    synchronize_session=False,
                )
            db.session.commit()
            cleanup(
                [order_id, pos_order_id],
                customer_id,
                combo_id,
                [product_id, product_option_id],
                category_id,
                zone_id,
                created_zone,
            )
            from routes.api_bot import notificar_bot_sync
            notificar_bot_sync()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
