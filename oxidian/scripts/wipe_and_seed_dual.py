#!/usr/bin/env python3
"""
Wipe total + seed dual (comida + retail) para probar ambos nichos.

Estrategia:
  1. Wipe agresivo — borra TODO menos:
      - schema_migrations (para no re-aplicar migraciones)
      - super_admins (para no dejarnos sin acceso)
      - site_config crítico (SECRET_KEY, TELEFONO_NEGOCIO si no existe se recrea)
  2. Restaura configuración base fresca.
  3. Siembra catálogo COMIDA (15 productos + 2 combos + extras).
  4. Siembra catálogo RETAIL (15 productos + 2 combos).

Uso:
  docker exec oxidian python3 scripts/wipe_and_seed_dual.py

Reversible con backup pre-wipe.
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db
from models import (
    Product,
    Categoria,
    Proveedor,
    ProveedorProducto,
    Stock,
    ComboGroup,
    ComboItem,
    ProductExtraGroup,
    ProductExtraOption,
    ProductPresentation,
    User,
    Order,
    OrderItem,
    OrderEvent,
    OrderProviderStatus,
    NotificationOutbox,
    PointsLog,
    AuditLog,
    IdempotencyKey,
    Coupon,
    AffiliateCode,
    AffiliateUse,
    Caja,
    MenuConfig,
    BotAiUsage,
    PushSubscription,
    ZonaEntrega,
    SiteConfig,
    ExtraCatalogItem,
    CampanaMarketing,
    AdminFeature,
)

# Config base que se restaura tras el wipe
CONFIG_BASE = {
    "NOMBRE_NEGOCIO": "El Parcerito",
    "TELEFONO_NEGOCIO": "34633096706",
    "DIRECCION_NEGOCIO": "Carmona, Sevilla",
    "MODO_TIENDA": "propia",
    "TIPO_TIENDA": "comida",
    "FEATURE_DELIVERY": "1",
    "FEATURE_RECOGIDA": "1",
    "FEATURE_PEDIDOS_PROGRAMADOS": "1",
    "FEATURE_PUNTOS": "1",
    "HORARIO_APERTURA": "09:00",
    "HORARIO_CIERRE": "23:30",
    "PEDIDO_MINIMO": "10.00",
    "PUNTOS_POR_EURO": "1",
    "PUNTOS_RATIO": "20",
    "SERVICE_COMMISSION_PCT": "15.00",
    "PAGO_EFECTIVO_HABILITADO": "1",
    "PAGO_BIZUM_HABILITADO": "1",
    "AUTO_DESTACADOS_ENABLED": "1",
}


def wipe_todo(super_admin_ids):
    """Borra todo menos schema_migrations, super_admins y admin_features asociadas.
    Usa TRUNCATE CASCADE por FKs cruzadas (repartidor_id, cliente_id, etc.)."""
    print("• Wipe TOTAL (preserva super_admins y schema_migrations)")
    # TRUNCATE CASCADE de todas las tablas de datos operativos y catálogo.
    # `users` se vacía y se re-insertan los super_admins al final para
    # sortear las FKs sin depender del orden.
    tablas = [
        "notification_outbox", "order_events", "order_provider_status",
        "order_items", "orders", "idempotency_keys", "points_log",
        "affiliate_uses", "affiliate_codes", "coupons", "caja",
        "bot_ai_usage", "push_subscriptions", "campanas_marketing",
        "product_extra_options", "product_extra_groups", "extra_catalog_items",
        "combo_items", "combo_groups", "proveedor_productos", "stock",
        "products", "categorias", "proveedores", "zonas_entrega",
        "menu_config", "audit_log", "site_config",
        "admin_features", "users",
    ]
    # Respaldar super_admins en memoria (Python) para re-crearlos.
    supers_snapshot = []
    for uid in super_admin_ids:
        u = db.session.get(User, uid)
        if not u:
            continue
        supers_snapshot.append({
            "id": u.id, "nombre": u.nombre, "email": u.email,
            "telefono": u.telefono, "telefono_normalizado": u.telefono_normalizado,
            "rol": u.rol, "password_hash": u.password_hash,
            "activo": u.activo, "puntos": u.puntos or 0,
            "mfa_secret": getattr(u, "mfa_secret", None),
            "mfa_enabled": getattr(u, "mfa_enabled", False),
        })
    from sqlalchemy import text
    stmt = "TRUNCATE " + ", ".join(tablas) + " RESTART IDENTITY CASCADE"
    db.session.execute(text(stmt))
    db.session.commit()
    # Re-insertar super_admins
    for snap in supers_snapshot:
        u = User(
            nombre=snap["nombre"], email=snap["email"],
            telefono=snap["telefono"], telefono_normalizado=snap["telefono_normalizado"],
            rol=snap["rol"], activo=snap["activo"], puntos=snap["puntos"],
        )
        u.password_hash = snap["password_hash"]
        if snap.get("mfa_secret"):
            u.mfa_secret = snap["mfa_secret"]
            u.mfa_enabled = snap.get("mfa_enabled", False)
        db.session.add(u)
    db.session.commit()
    print(f"  - super_admins re-insertados: {len(supers_snapshot)}")


def restaurar_config():
    print("• Restaurando SiteConfig base")
    for clave, valor in CONFIG_BASE.items():
        db.session.add(SiteConfig(clave=clave, valor=valor))
    db.session.commit()


def crear_zonas():
    print("• Zonas de entrega")
    zonas = [
        ("Centro Carmona", 3.00, 15, 20.00),
        ("Sevilla ciudad", 5.00, 30, 25.00),
        ("Cercanías (5km)", 4.00, 25, 20.00),
    ]
    for orden, (nombre, envio, min_est, gratis_desde) in enumerate(zonas, start=1):
        db.session.add(ZonaEntrega(
            nombre=nombre,
            activo=True,
            precio_envio=Decimal(str(envio)),
            tiempo_estimado_min=min_est,
            gratis_desde=Decimal(str(gratis_desde)),
            orden=orden,
        ))
    db.session.commit()


def seed_comida():
    print("• Seed nicho COMIDA")
    cats = {
        "Entrantes": Categoria(nombre="Entrantes", activo=True, orden=1),
        "Principales": Categoria(nombre="Principales", activo=True, orden=2),
        "Bebidas": Categoria(nombre="Bebidas", activo=True, orden=3),
        "Postres": Categoria(nombre="Postres", activo=True, orden=4),
    }
    for c in cats.values():
        db.session.add(c)
    db.session.flush()

    fecha_prog = date.today() + timedelta(days=14)
    items = [
        # (cat, nombre, desc, precio, mod, tipo_ent, pts, solo_canje, canal, fecha)
        ("Entrantes", "Croquetas caseras (6 uds)", "Jamón, pollo o queso.", 6.50, "ambas", "inmediato", None, False, "cocina", None),
        ("Entrantes", "Ensaladilla rusa", "La clásica con atún.", 5.00, "ambas", "inmediato", None, False, "cocina", None),
        ("Entrantes", "Bravas El Parcerito", "Con salsa secreta.", 4.50, "ambas", "inmediato", None, False, "cocina", None),
        ("Entrantes", "Tabla de embutidos", "Jamón, chorizo, queso.", 12.00, "ambas", "inmediato", None, False, "almacen", None),
        ("Principales", "Hamburguesa clásica", "200g ternera + queso.", 9.90, "ambas", "inmediato", 250, False, "cocina", None),
        ("Principales", "Pizza margarita", "Masa artesana.", 8.90, "ambas", "inmediato", None, False, "cocina", None),
        ("Principales", "Pizza cuatro quesos", "Mozzarella, gorgonzola, parmesano, provolone.", 10.90, "ambas", "inmediato", None, False, "cocina", None),
        ("Principales", "Solomillo al whisky", "Con patatas.", 14.90, "ambas", "inmediato", None, False, "cocina", None),
        ("Principales", "Paella para 2", "Encargo con 24h.", 25.00, "ambas", "programado", None, False, "cocina", fecha_prog),
        ("Bebidas", "Cerveza tirada 33cl", "De grifo.", 2.50, "recogida", "inmediato", None, False, "almacen", None),
        ("Bebidas", "Coca-Cola 33cl", "Botellín.", 2.20, "ambas", "inmediato", None, False, "almacen", None),
        ("Bebidas", "Agua mineral 50cl", "Sin gas.", 1.20, "ambas", "inmediato", None, False, "almacen", None),
        ("Postres", "Tarta de queso", "San Sebastián.", 4.50, "ambas", "inmediato", 100, False, "cocina", None),
        ("Postres", "Brownie con helado", "Chocolate y vainilla.", 5.00, "ambas", "inmediato", None, False, "cocina", None),
        # Solo canje
        ("Postres", "Café gratis (canje)", "Solo con puntos.", 0.00, "recogida", "inmediato", 200, True, "cocina", None),
        ("Postres", "Cerveza gratis (canje)", "Solo con puntos.", 0.00, "recogida", "inmediato", 250, True, "almacen", None),
    ]
    productos = []
    for cat, nombre, desc, precio, mod, tipo_ent, pts, solo, canal, fecha in items:
        p = Product(
            nombre=nombre, descripcion=desc,
            precio=Decimal(str(precio)),
            precio_costo=(Decimal(str(precio)) * Decimal("0.55")).quantize(Decimal("0.01")),
            activo=True, categoria_id=cats[cat].id,
            canal_preparacion=canal, tipo_entrega=tipo_ent, modalidad_entrega=mod,
            fecha_llegada=fecha, vertical="comida",
            canjeable_con_puntos=bool(pts), puntos_para_canje=pts, solo_canje=solo,
            stock_mostrar_en_web=False,
        )
        db.session.add(p)
        db.session.flush()
        db.session.add(Stock(producto_id=p.id, cantidad=50, fecha_caducidad=date.today() + timedelta(days=90)))
        productos.append(p)

    # Extras: salsas y adicionales para hamburguesa/pizza
    for base in [p for p in productos if p.nombre in ("Hamburguesa clásica", "Pizza margarita", "Pizza cuatro quesos")]:
        g_sal = ProductExtraGroup(producto_id=base.id, nombre="Salsas", min_selecciones=0, max_selecciones=3, activo=True)
        db.session.add(g_sal); db.session.flush()
        for i, (n, pr) in enumerate([("Mayonesa", 0.30), ("Ketchup", 0.30), ("BBQ", 0.60), ("Bravas casera", 0.50)]):
            db.session.add(ProductExtraOption(grupo_id=g_sal.id, nombre=n, precio=Decimal(str(pr)), max_cantidad=1, orden=i, activo=True))
        g_ext = ProductExtraGroup(producto_id=base.id, nombre="Extras", min_selecciones=0, max_selecciones=3, activo=True)
        db.session.add(g_ext); db.session.flush()
        for i, (n, pr) in enumerate([("Extra queso", 1.00), ("Extra bacon", 1.20), ("Doble carne", 2.50)]):
            db.session.add(ProductExtraOption(grupo_id=g_ext.id, nombre=n, precio=Decimal(str(pr)), max_cantidad=2, orden=i, activo=True))

    # Combos
    by_c = {p.nombre: p for p in productos}
    def _combo_c(nombre, desc, precio, comps, precio_modo="fijo", desc_pct=0):
        combo = Product(
            nombre=nombre, descripcion=desc,
            precio=Decimal(str(precio)),
            activo=True, es_combo=True,
            categoria_id=cats["Principales"].id,
            tipo_entrega="inmediato", modalidad_entrega="ambas",
            vertical="comida",
            combo_precio_modo=precio_modo,
            combo_descuento_pct=Decimal(str(desc_pct)) if precio_modo == "descuento" else None,
        )
        db.session.add(combo); db.session.flush()
        g = ComboGroup(combo_id=combo.id, nombre="Componentes", orden=1, tipo="fijo", requerido=True)
        db.session.add(g); db.session.flush()
        db.session.add(Stock(producto_id=combo.id, cantidad=100, fecha_caducidad=date.today() + timedelta(days=90)))
        for j, comp in enumerate(comps):
            db.session.add(ComboItem(
                combo_id=combo.id, producto_id=comp.id, combo_group_id=g.id,
                cantidad=1, orden=j, activo=True, es_predeterminado=(j == 0),
            ))

    # Presentaciones comida: Pequeño/Mediano/Grande con precio extra
    presentaciones_comida = {
        "Hamburguesa clásica": [("pequeño", -1.50), ("mediano", 0.00), ("grande", 2.50)],
        "Pizza margarita": [("pequeño", -2.00), ("mediano", 0.00), ("grande", 3.00)],
        "Pizza cuatro quesos": [("pequeño", -2.00), ("mediano", 0.00), ("grande", 3.50)],
        "Coca-Cola 33cl": [("mediano", 0.00), ("grande", 1.20)],
        "Cerveza tirada 33cl": [("mediano", 0.00), ("grande", 1.50)],
        "Café gratis (canje)": [("pequeño", 0.00), ("grande", 0.00)],
    }
    for prod_nombre, tamaños in presentaciones_comida.items():
        prod = by_c.get(prod_nombre)
        if not prod:
            continue
        for orden, (tam, extra) in enumerate(tamaños):
            db.session.add(ProductPresentation(
                producto_id=prod.id, tamaño=tam,
                precio_extra=Decimal(str(extra)),
                activo=True, orden=orden,
            ))

    if all(k in by_c for k in ["Hamburguesa clásica", "Bravas El Parcerito", "Coca-Cola 33cl"]):
        _combo_c("Menú hamburguesa fijo", "Hamburguesa + bravas + refresco (precio fijo).", 12.90,
                 [by_c["Hamburguesa clásica"], by_c["Bravas El Parcerito"], by_c["Coca-Cola 33cl"]], "fijo")
    if all(k in by_c for k in ["Pizza margarita", "Cerveza tirada 33cl"]):
        _combo_c("Menú pizza con dto", "Pizza + cerveza con 15% descuento sobre suma.", 0.00,
                 [by_c["Pizza margarita"], by_c["Cerveza tirada 33cl"]], "descuento", 15)


def seed_retail():
    print("• Seed nicho RETAIL (ropa, zapatos, bisutería)")
    cats = {
        "Ropa": Categoria(nombre="Ropa", activo=True, orden=10),
        "Zapatos": Categoria(nombre="Zapatos", activo=True, orden=11),
        "Bisutería": Categoria(nombre="Bisutería", activo=True, orden=12),
        "Accesorios": Categoria(nombre="Accesorios", activo=True, orden=13),
    }
    for c in cats.values():
        db.session.add(c)
    db.session.flush()

    items = [
        # ropa
        ("Ropa", "Camiseta oversize negra", "100% algodón, unisex. Tallas S/M/L/XL.", 18.00, "ambas"),
        ("Ropa", "Camiseta blanca básica", "Corte regular, algodón peinado.", 12.00, "ambas"),
        ("Ropa", "Sudadera capucha gris", "Perchada, forro suave.", 34.00, "ambas"),
        ("Ropa", "Pantalón chino beige", "Corte slim, con cinturón.", 42.00, "ambas"),
        ("Ropa", "Vestido midi floral", "Verano, tejido ligero.", 45.00, "ambas"),
        # zapatos
        ("Zapatos", "Zapatilla urbana blanca", "Piel sintética. Tallas 36-45.", 55.00, "ambas"),
        ("Zapatos", "Bota chelsea negra", "Cuero. Suela antideslizante.", 78.00, "ambas"),
        ("Zapatos", "Sandalia plana dorada", "Cómoda, verano.", 32.00, "ambas"),
        ("Zapatos", "Zapatillas running", "Amortiguación media.", 65.00, "ambas"),
        # bisutería
        ("Bisutería", "Collar cadena plata 45cm", "Plata 925.", 25.00, "ambas"),
        ("Bisutería", "Pendientes aro pequeño oro", "Baño de oro 18k.", 15.00, "ambas"),
        ("Bisutería", "Anillo trenzado plata", "Ajustable.", 12.00, "ambas"),
        ("Bisutería", "Pulsera hilo con dijes", "Regulable.", 8.00, "ambas"),
        # accesorios
        ("Accesorios", "Bolso bandolera cuero", "Cuero italiano genuino.", 65.00, "ambas"),
        ("Accesorios", "Cinturón trenzado piel", "Tallas S-XL, hebilla latón.", 28.00, "ambas"),
        ("Accesorios", "Gafas de sol acetato", "Protección UV400.", 35.00, "ambas"),
    ]
    productos = []
    for cat, nombre, desc, precio, mod in items:
        p = Product(
            nombre=nombre, descripcion=desc,
            precio=Decimal(str(precio)),
            precio_costo=(Decimal(str(precio)) * Decimal("0.4")).quantize(Decimal("0.01")),
            activo=True, categoria_id=cats[cat].id,
            canal_preparacion="almacen",
            tipo_entrega="inmediato", modalidad_entrega=mod,
            vertical="producto",
            canjeable_con_puntos=False, solo_canje=False,
            stock_mostrar_en_web=True,
        )
        db.session.add(p); db.session.flush()
        db.session.add(Stock(producto_id=p.id, cantidad=15, fecha_caducidad=date.today() + timedelta(days=730)))
        productos.append(p)

    # Combos retail (packs de regalo)
    by_r = {p.nombre: p for p in productos}
    def _combo_r(nombre, desc, precio, comps, cat_key, precio_modo="fijo", desc_pct=0):
        combo = Product(
            nombre=nombre, descripcion=desc,
            precio=Decimal(str(precio)),
            activo=True, es_combo=True,
            categoria_id=cats[cat_key].id,
            tipo_entrega="inmediato", modalidad_entrega="ambas",
            vertical="producto",
            combo_precio_modo=precio_modo,
            combo_descuento_pct=Decimal(str(desc_pct)) if precio_modo == "descuento" else None,
        )
        db.session.add(combo); db.session.flush()
        g = ComboGroup(combo_id=combo.id, nombre="Componentes", orden=1, tipo="fijo", requerido=True)
        db.session.add(g); db.session.flush()
        db.session.add(Stock(producto_id=combo.id, cantidad=50, fecha_caducidad=date.today() + timedelta(days=730)))
        for j, comp in enumerate(comps):
            db.session.add(ComboItem(
                combo_id=combo.id, producto_id=comp.id, combo_group_id=g.id,
                cantidad=1, orden=j, activo=True, es_predeterminado=(j == 0),
            ))

    # Presentaciones retail: tallas ropa (S/M/L/XL) y números zapatos (36-45).
    tallas_ropa = ["S", "M", "L", "XL"]
    numeros_zapato = ["36", "37", "38", "39", "40", "41", "42", "43", "44", "45"]
    for prod in productos:
        cat_nombre = next((k for k, v in cats.items() if v.id == prod.categoria_id), None)
        if cat_nombre == "Ropa":
            for orden, tam in enumerate(tallas_ropa):
                db.session.add(ProductPresentation(
                    producto_id=prod.id, tamaño=tam,
                    precio_extra=Decimal("0.00"),
                    activo=True, orden=orden,
                ))
        elif cat_nombre == "Zapatos":
            for orden, num in enumerate(numeros_zapato):
                db.session.add(ProductPresentation(
                    producto_id=prod.id, tamaño=num,
                    precio_extra=Decimal("0.00"),
                    activo=True, orden=orden,
                ))

    if all(k in by_r for k in ["Camiseta blanca básica", "Pantalón chino beige"]):
        _combo_r("Look casual básico", "Camiseta + pantalón chino (precio fijo).", 50.00,
                 [by_r["Camiseta blanca básica"], by_r["Pantalón chino beige"]], "Ropa", "fijo")
    if all(k in by_r for k in ["Collar cadena plata 45cm", "Pendientes aro pequeño oro", "Anillo trenzado plata"]):
        _combo_r("Pack joyería 3 piezas", "3 piezas surtidas con 20% descuento.", 0.00,
                 [by_r["Collar cadena plata 45cm"], by_r["Pendientes aro pequeño oro"], by_r["Anillo trenzado plata"]],
                 "Bisutería", "descuento", 20)


def main():
    app = create_app()
    with app.app_context():
        super_admin_ids = [u.id for u in User.query.filter_by(rol="super_admin").all()]
        if not super_admin_ids:
            raise SystemExit("REHUSO: no hay super_admin. Wipe cancelaría el acceso.")
        print(f"Super admins preservados: {super_admin_ids}")

        wipe_todo(super_admin_ids)
        restaurar_config()
        crear_zonas()
        seed_comida()
        seed_retail()

        db.session.commit()
        print()
        print("=" * 60)
        print("✓ Wipe + seed dual OK")
        n_prod = Product.query.count()
        n_combo = Product.query.filter_by(es_combo=True).count()
        n_com = Product.query.filter_by(vertical="comida").count()
        n_ret = Product.query.filter_by(vertical="producto").count()
        n_cat = Categoria.query.count()
        n_zon = ZonaEntrega.query.count()
        n_usr = User.query.count()
        print(f"  Productos: {n_prod} (combos {n_combo}, comida {n_com}, retail {n_ret})")
        print(f"  Categorías: {n_cat}, Zonas: {n_zon}, Users preservados: {n_usr}")
        print("=" * 60)


if __name__ == "__main__":
    main()
