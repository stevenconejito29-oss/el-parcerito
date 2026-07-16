#!/usr/bin/env python3
"""
Reset controlado de datos + seed showcase comida/retail.

Uso en servidor:
  docker compose --env-file .env.cosmos.local -f cosmos-compose.yml -p oxidian \
    exec -T oxidian python scripts/reset_showcase_dual.py

La operación limpia catálogo, pedidos, caja, staff no-superadmin, clientes,
cupones, reseñas y datos operativos. Preserva:
- usuarios super_admin existentes, con contraseña/MFA;
- configuración crítica de integraciones (bot, Evolution, URLs públicas,
  secretos y teléfonos propietarios).

Luego crea un catálogo dual coherente para probar:
- comida colombiana con alérgenos, extras, tamaños, programados y combos;
- retail con atributos, tallas/números, stock visible, programados y combos;
- canjes con puntos, cupones, afiliado, zonas, reviews y staff de prueba.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from extensions import db
from models import (
    ADMIN_FEATURES,
    AdminFeature,
    AffiliateCode,
    AuditLog,
    Categoria,
    ComboGroup,
    ComboItem,
    Coupon,
    ExtraCatalogItem,
    MenuConfig,
    PointsLog,
    PriceHistory,
    Product,
    ProductExtraGroup,
    ProductExtraOption,
    ProductPresentation,
    Review,
    SiteConfig,
    StaffPayment,
    Stock,
    User,
    ZonaEntrega,
    internal_customer_email,
    utcnow,
)
from store_config import STORE_DEFAULTS
from combo_validators import validate_combo_structure
from routes.admin import _payload_estructura_combo
from routes.uploads import IMAGES_DIR


MARKER = "SHOWCASE-2026"
ASSET_DIR = IMAGES_DIR / "showcase_dual"
ASSET_PREFIX = "showcase_dual"

PRESERVE_CONFIG_PREFIXES = (
    "BOT_",
    "EVOLUTION_",
    "WEBHOOK_",
    "VAPID_",
    "OXIDIAN_",
)
PRESERVE_CONFIG_KEYS = {
    "SECRET_KEY",
    "OWNER_NUMBER",
    "SUPERADMINS",
    "BOT_ADMIN_NUMBERS",
    "TIENDA_URL",
    "OXIDIAN_PUBLIC_URL",
    "WHATSAPP_COUNTRY_CODE",
}

TABLES_EXCLUDED = {"schema_migrations"}


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def svg_asset(slug: str, title: str, subtitle: str, emoji: str, a: str, b: str, c: str) -> str:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    path = ASSET_DIR / f"{slug}.svg"
    path.write_text(
        f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 860" role="img" aria-label="{title}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{a}"/>
      <stop offset="0.55" stop-color="{b}"/>
      <stop offset="1" stop-color="{c}"/>
    </linearGradient>
    <filter id="s" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="26" stdDeviation="28" flood-color="#101010" flood-opacity=".24"/>
    </filter>
  </defs>
  <rect width="1200" height="860" rx="72" fill="url(#g)"/>
  <circle cx="1040" cy="124" r="190" fill="#fff" opacity=".15"/>
  <circle cx="145" cy="742" r="230" fill="#fff" opacity=".10"/>
  <g filter="url(#s)">
    <rect x="122" y="126" width="956" height="610" rx="54" fill="#fffdf8" opacity=".95"/>
    <text x="600" y="363" text-anchor="middle" font-size="174" font-family="Apple Color Emoji, Segoe UI Emoji, sans-serif">{emoji}</text>
    <text x="600" y="500" text-anchor="middle" fill="#18120A" font-size="58" font-weight="900" font-family="Inter, Arial, sans-serif">{title}</text>
    <text x="600" y="566" text-anchor="middle" fill="#65584D" font-size="30" font-weight="700" font-family="Inter, Arial, sans-serif">{subtitle}</text>
  </g>
</svg>
""",
        encoding="utf-8",
    )
    return f"{ASSET_PREFIX}/{slug}.svg"


def ensure_assets() -> dict[str, str]:
    return {
        "hero": svg_asset("hero", "Catálogo dual", "Comida colombiana y retail en un solo sistema", "🛒", "#F9C74F", "#2A9D8F", "#003087"),
        "empanadas": svg_asset("empanadas", "Empanadas", "Masa de maíz, rellenos y ají", "🥟", "#F9C74F", "#F9844A", "#CE1126"),
        "arepas": svg_asset("arepas", "Arepas", "Rellenas, gratinadas y personalizables", "🫓", "#FFE066", "#90BE6D", "#277DA1"),
        "platos": svg_asset("platos", "Platos", "Bandeja, bowls y cocina caliente", "🍛", "#F6D365", "#FDA085", "#2F9E6D"),
        "bebidas": svg_asset("bebidas", "Bebidas", "Jugos naturales, malta y refrescos", "🥤", "#48CAE4", "#00B4D8", "#0077B6"),
        "postres": svg_asset("postres", "Postres", "Dulces colombianos y cafés", "🍮", "#FBC4AB", "#F4978E", "#9D4EDD"),
        "retail": svg_asset("retail", "Retail", "Tallas, stock y productos físicos", "🛍️", "#B7E4C7", "#52B788", "#1B4332"),
        "moda": svg_asset("moda", "Moda", "Ropa y calzado con variantes", "👕", "#D8E2DC", "#84A59D", "#3D405B"),
        "joyeria": svg_asset("joyeria", "Bisutería", "Piezas listas para regalo", "💍", "#FFE5EC", "#FFB3C6", "#9D4EDD"),
        "combos": svg_asset("combos", "Combos", "Selecciones guiadas y precios claros", "🎁", "#F9C74F", "#577590", "#003087"),
    }


def snapshot_superadmins() -> list[dict]:
    supers = []
    for user in User.query.filter_by(rol="super_admin").all():
        supers.append({
            "nombre": user.nombre,
            "email": user.email,
            "password_hash": user.password_hash,
            "telefono": user.telefono,
            "direccion": user.direccion,
            "puntos": user.puntos or 0,
            "activo": user.activo,
            "mfa_secret": user.mfa_secret,
            "mfa_enabled": user.mfa_enabled,
            "mfa_session_version": user.mfa_session_version or 0,
            "puesto_trabajo": user.puesto_trabajo,
        })
    return supers


def snapshot_config() -> dict[str, str]:
    preserved = {}
    for row in SiteConfig.query.all():
        key = row.clave or ""
        if key in PRESERVE_CONFIG_KEYS or any(key.startswith(prefix) for prefix in PRESERVE_CONFIG_PREFIXES):
            preserved[key] = row.valor or ""
    return preserved


def existing_tables() -> list[str]:
    rows = db.session.execute(text(
        "select tablename from pg_tables where schemaname = 'public' order by tablename"
    )).scalars().all()
    return [row for row in rows if row not in TABLES_EXCLUDED]


def truncate_database() -> None:
    tables = existing_tables()
    if not tables:
        return
    stmt = "TRUNCATE " + ", ".join(qident(t) for t in tables) + " RESTART IDENTITY CASCADE"
    db.session.execute(text(stmt))
    db.session.commit()


def restore_superadmins(snapshot: list[dict]) -> list[User]:
    restored = []
    for row in snapshot:
        user = User(
            nombre=row["nombre"],
            email=row["email"],
            telefono=row["telefono"],
            direccion=row.get("direccion"),
            rol="super_admin",
            activo=row.get("activo", True),
            puntos=row.get("puntos", 0),
            puesto_trabajo=row.get("puesto_trabajo") or "SuperAdmin",
        )
        user.password_hash = row["password_hash"]
        user.mfa_secret = row.get("mfa_secret")
        user.mfa_enabled = row.get("mfa_enabled", False)
        user.mfa_session_version = row.get("mfa_session_version", 0)
        db.session.add(user)
        restored.append(user)
    db.session.flush()
    return restored


def seed_config(preserved: dict[str, str]) -> None:
    config = {
        **STORE_DEFAULTS,
        **preserved,
        "NOMBRE_NEGOCIO": "El Parcerito",
        "SLOGAN_NEGOCIO": "Comida colombiana y retail de prueba",
        "DESCRIPCION_NEGOCIO": "Catálogo showcase para validar pedidos, combos, puntos, delivery, recogida y retail.",
        "DIRECCION_NEGOCIO": "Carmona, Sevilla",
        "CIUDAD_NEGOCIO": "Carmona",
        "PROVINCIA_NEGOCIO": "Sevilla",
        "PAIS_NEGOCIO": "España",
        "PAIS_CODIGO_ISO": "es",
        "TIPO_TIENDA": "comida",
        "MODO_TIENDA": "propia",
        "FEATURE_DELIVERY": "1",
        "FEATURE_RECOGIDA": "1",
        "FEATURE_PEDIDOS_PROGRAMADOS": "1",
        "FEATURE_PUNTOS": "1",
        "BIZUM_HABILITADO": "1",
        "EFECTIVO_HABILITADO": "1",
        "TARJETA_HABILITADA": "0",
        "PEDIDO_MINIMO_EUR": "10.00",
        "PUNTOS_POR_EURO": "1",
        "PUNTOS_RATIO": "20",
        "SERVICE_COMMISSION_PCT": "12.00",
        "HORARIO_APERTURA": "09:00",
        "HORARIO_CIERRE": "23:30",
        "TIENDA_FORZAR_CERRADA": "0",
        "COLOR_PRIMARIO": "#F4C542",
        "COLOR_SECUNDARIO": "#DA4D40",
        "COLOR_ACENTO": "#245A9A",
        "BRAND_FALLBACK_EMOJI": "🥟",
        "HERO_IMAGE_URL": f"{ASSET_PREFIX}/hero.svg",
        "UI_CART_EMPTY_TEXT": "Explora el catálogo y encuentra tu favorito",
        "UI_CART_VIEW_MENU": "Ver catálogo completo",
        "UI_CART_POINTS_READY": "Tus puntos están verificados. El canje se aplica al confirmar.",
        "UI_PWA_OFFLINE": "Guardar recursos",
        "PWA_VERSION": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
    }
    for key, value in config.items():
        SiteConfig.set(key, value)


def create_user(nombre: str, email: str, rol: str, telefono: str, password: str = "Test2026!", **kwargs) -> User:
    user = User(nombre=nombre, email=email, rol=rol, telefono=telefono, activo=True, **kwargs)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    return user


def seed_users(superadmins: list[User]) -> dict[str, User]:
    users = {}
    users["admin"] = create_user("Admin Operativo Showcase", "admin.showcase@elparcerito.test", "admin", "34630001001", puesto_trabajo="Administrador")
    users["cocina"] = create_user("Cocina Showcase", "cocina.showcase@elparcerito.test", "cocina", "34630001002", puesto_trabajo="Cocina caliente", salario_base=money("1250"))
    users["preparacion"] = create_user("Preparación Showcase", "prep.showcase@elparcerito.test", "preparacion", "34630001003", puesto_trabajo="Empaque y almacén", salario_base=money("1180"))
    users["repartidor"] = create_user("Repartidor Showcase", "reparto.showcase@elparcerito.test", "repartidor", "34630001004", puesto_trabajo="Delivery", tarifa_entrega=money("2.50"), en_linea=True, last_seen=utcnow())
    users["cliente"] = create_user("Cliente Showcase", internal_customer_email("34630001005"), "cliente", "34630001005", direccion="Calle Real 12, Carmona", puntos=850)
    users["afiliado"] = create_user("Afiliado Showcase", "afiliado.showcase@elparcerito.test", "admin", "34630001006", puesto_trabajo="Afiliado externo")
    for feature in ADMIN_FEATURES:
        db.session.add(AdminFeature(
            user_id=users["admin"].id,
            feature=feature,
            activo=True,
            actualizado_por=superadmins[0].id if superadmins else None,
        ))
    db.session.add(PointsLog(cliente_id=users["cliente"].id, tipo="ganado", cantidad=850, descripcion="Saldo inicial showcase"))
    return users


def seed_zones() -> None:
    zones = [
        ("Centro Carmona", "Zona urbana principal", True, "2.50", 25, "18.00", 1, 37.4712, -5.6461, 4.5),
        ("Sevilla Este", "Entrega extendida con coste superior", False, "5.50", 45, "35.00", 2, 37.3891, -5.9845, 8.0),
        ("Recogida sin envío", "Referencia para pedidos de recogida", True, "0.00", 10, None, 3, None, None, None),
    ]
    for nombre, desc, epi, precio, minutos, gratis, orden, lat, lng, radio in zones:
        db.session.add(ZonaEntrega(
            nombre=nombre,
            descripcion=desc,
            es_epicentro=epi,
            activo=True,
            precio_envio=money(precio),
            tiempo_estimado_min=minutos,
            gratis_desde=money(gratis) if gratis else None,
            orden=orden,
            centro_lat=lat,
            centro_lng=lng,
            radio_km=radio,
        ))


def seed_categories(assets: dict[str, str]) -> dict[str, Categoria]:
    data = [
        ("Empanadas", "Comida", 1, assets["empanadas"]),
        ("Arepas", "Comida", 2, assets["arepas"]),
        ("Platos fuertes", "Comida", 3, assets["platos"]),
        ("Bebidas", "Comida", 4, assets["bebidas"]),
        ("Postres y café", "Comida", 5, assets["postres"]),
        ("Combos comida", "Comida", 6, assets["combos"]),
        ("Ropa", "Retail", 20, assets["moda"]),
        ("Calzado", "Retail", 21, assets["moda"]),
        ("Bisutería", "Retail", 22, assets["joyeria"]),
        ("Accesorios", "Retail", 23, assets["retail"]),
        ("Combos retail", "Retail", 24, assets["combos"]),
        ("Canjes", "Puntos", 30, assets["combos"]),
    ]
    cats = {}
    for nombre, desc, orden, image in data:
        cat = Categoria(nombre=nombre, descripcion=desc, orden=orden, activo=True, imagen_url=image)
        db.session.add(cat)
        cats[nombre] = cat
    db.session.flush()
    return cats


def add_stock(product: Product, qty: int, days: int = 120, location: str = "Principal") -> None:
    db.session.add(Stock(
        producto_id=product.id,
        cantidad=qty,
        unidad="unidad",
        lote=f"{MARKER}-{product.id or 'NEW'}",
        fecha_entrada=date.today(),
        fecha_caducidad=date.today() + timedelta(days=days) if days else None,
        alerta_dias=10,
        ubicacion=location,
    ))


def add_presentations(product: Product, rows: list[tuple[str, str | float]]) -> None:
    for idx, (label, extra) in enumerate(rows):
        db.session.add(ProductPresentation(
            producto_id=product.id,
            tamaño=str(label),
            precio_extra=money(extra),
            activo=True,
            orden=idx,
        ))


def add_extras(product: Product, groups: list[dict]) -> None:
    for gidx, group in enumerate(groups):
        extra_group = ProductExtraGroup(
            producto_id=product.id,
            nombre=group["nombre"],
            descripcion=group.get("descripcion"),
            min_selecciones=group.get("min", 0),
            max_selecciones=group.get("max", 1),
            orden=gidx,
            activo=True,
        )
        db.session.add(extra_group)
        db.session.flush()
        for oidx, (nombre, precio, max_qty) in enumerate(group["opciones"]):
            db.session.add(ProductExtraOption(
                grupo_id=extra_group.id,
                nombre=nombre,
                precio=money(precio),
                max_cantidad=max_qty,
                orden=oidx,
                activo=True,
            ))


def add_product(products: dict[str, Product], cats: dict[str, Categoria], *, nombre: str, categoria: str,
                precio: str | float, descripcion: str, vertical: str, image: str,
                costo: str | float | None = None, stock: int = 30, stock_visible: bool = True,
                canal: str = "almacen", tipo_entrega: str = "inmediato", modalidad: str = "ambas",
                grupo_pedido: str | None = None, fecha_llegada=None, origen_pais: str | None = None,
                canje_puntos: int | None = None, solo_canje: bool = False,
                alergenos: list[str] | None = None, hipo: bool = False,
                atributos: dict | None = None, tipo_producto: str = "simple") -> Product:
    price = money(0 if solo_canje else precio)
    product = Product(
        nombre=nombre,
        descripcion=descripcion,
        precio=price,
        precio_costo=money(costo if costo is not None else max(float(price) * 0.45, 0)),
        categoria_id=cats[categoria].id,
        imagen_url=image,
        origen_pais=origen_pais,
        es_combo=False,
        tipo_producto=tipo_producto,
        atributos_json=json.dumps({**(atributos or {}), "showcase": MARKER}, ensure_ascii=False),
        activo=True,
        vertical=vertical,
        canal_preparacion=canal,
        tipo_entrega=tipo_entrega,
        modalidad_entrega=modalidad,
        grupo_pedido=grupo_pedido,
        fecha_llegada=fecha_llegada,
        stock_mostrar_en_web=stock_visible,
        canjeable_con_puntos=bool(canje_puntos),
        puntos_para_canje=canje_puntos,
        solo_canje=solo_canje,
        es_hipoalergenico=hipo,
        alergenos_json=json.dumps(alergenos or [], ensure_ascii=False) if alergenos else None,
        alergenos_info=", ".join(alergenos or []) if alergenos else None,
    )
    db.session.add(product)
    db.session.flush()
    if stock or stock_visible or tipo_entrega == "inmediato":
        add_stock(product, stock, 730 if vertical == "producto" else 90, "Almacén" if canal == "almacen" else "Cocina")
    products[nombre] = product
    return product


def add_combo(products: dict[str, Product], cats: dict[str, Categoria], *, nombre: str, categoria: str,
              descripcion: str, vertical: str, image: str, precio_fijo: str | float | None = None,
              descuento_pct: str | float | None = None, tipo_entrega: str = "inmediato",
              modalidad: str = "ambas", fecha_llegada=None, groups: list[dict],
              canal: str = "cocina") -> Product:
    mode = "descuento_porcentaje" if descuento_pct is not None else "fijo"
    combo = Product(
        nombre=nombre,
        descripcion=descripcion,
        precio=money(precio_fijo or 0),
        precio_costo=money("0.00"),
        categoria_id=cats[categoria].id,
        imagen_url=image,
        es_combo=True,
        tipo_producto="combo",
        atributos_json=json.dumps({"showcase": MARKER}, ensure_ascii=False),
        activo=True,
        vertical=vertical,
        canal_preparacion=canal,
        tipo_entrega=tipo_entrega,
        modalidad_entrega=modalidad,
        fecha_llegada=fecha_llegada,
        stock_mostrar_en_web=False,
        combo_precio_modo=mode,
        combo_precio_base=money(precio_fijo or 0),
        combo_descuento_pct=money(descuento_pct or 0),
    )
    db.session.add(combo)
    db.session.flush()
    add_stock(combo, 999, 730 if vertical == "producto" else 90, "Combo virtual")

    base_for_discount = Decimal("0.00")
    for gidx, group in enumerate(groups):
        cg = ComboGroup(
            combo_id=combo.id,
            nombre=group["nombre"],
            tipo="seleccion" if group.get("seleccionable") else "fijo",
            min_selecciones=group.get("min", 0 if not group.get("seleccionable") else 1),
            max_selecciones=group.get("max", 1),
            orden=gidx,
            requerido=group.get("requerido", True),
            descripcion=group.get("descripcion"),
        )
        db.session.add(cg)
        db.session.flush()
        for oidx, row in enumerate(group["items"]):
            product = products[row["producto"]]
            qty = int(row.get("cantidad", 1))
            base_for_discount += money(product.precio) * qty if not group.get("seleccionable") else Decimal("0.00")
            db.session.add(ComboItem(
                combo_id=combo.id,
                combo_group_id=cg.id,
                producto_id=product.id,
                cantidad=qty,
                orden=oidx,
                precio_extra=money(row.get("precio_extra", 0)),
                es_predeterminado=row.get("default", oidx == 0),
                activo=True,
                notas_preparacion=row.get("notas"),
                es_seleccionable=bool(group.get("seleccionable")),
                grupo_seleccion=group["nombre"] if group.get("seleccionable") else None,
                max_selecciones=group.get("max", 1),
            ))
    if descuento_pct is not None:
        # Referencia comercial: suma de fijos + opción predeterminada de cada grupo.
        base = Decimal("0.00")
        for group in groups:
            candidates = []
            for row in group["items"]:
                product = products[row["producto"]]
                val = money(product.precio) * int(row.get("cantidad", 1))
                if group.get("seleccionable"):
                    candidates.append((row.get("default", False), val))
                else:
                    base += val
            defaults = [val for is_default, val in candidates if is_default]
            if defaults:
                base += defaults[0]
            elif candidates:
                base += min(val for _is_default, val in candidates)
        combo.combo_precio_base = base
        combo.precio = combo.precio_desde_descuento_combo(base=base, descuento_pct=combo.combo_descuento_pct_float)

    products[nombre] = combo
    return combo


def seed_food(products: dict[str, Product], cats: dict[str, Categoria], assets: dict[str, str]) -> None:
    future = date.today() + timedelta(days=3)
    p = add_product(products, cats, nombre="Empanada valluna de carne", categoria="Empanadas", precio="2.90",
                    costo="1.05", descripcion="Masa de maíz crujiente con carne desmechada, papa criolla y hogao.",
                    vertical="comida", image=assets["empanadas"], stock=90, stock_visible=True, canal="cocina",
                    origen_pais="Colombia", alergenos=["gluten"])
    add_extras(p, [{"nombre": "Salsas", "max": 3, "opciones": [("Ají colombiano", "0.40", 2), ("Hogao extra", "0.60", 2), ("Guacamole", "1.00", 1)]}])
    add_presentations(p, [("3 uds", "0.00"), ("6 uds", "5.20"), ("12 uds", "15.60")])

    p = add_product(products, cats, nombre="Empanada de pollo y ají dulce", categoria="Empanadas", precio="2.70",
                    costo="0.95", descripcion="Pollo guisado con ají dulce, comino y cilantro fresco.",
                    vertical="comida", image=assets["empanadas"], stock=85, stock_visible=True, canal="cocina",
                    origen_pais="Colombia", alergenos=["gluten"])
    add_presentations(p, [("3 uds", "0.00"), ("6 uds", "4.80"), ("12 uds", "14.40")])

    add_product(products, cats, nombre="Empanada vegana de fríjol", categoria="Empanadas", precio="2.80",
                costo="1.00", descripcion="Fríjol negro, plátano maduro y especias. Opción vegetal.",
                vertical="comida", image=assets["empanadas"], stock=60, stock_visible=True, canal="cocina",
                origen_pais="Colombia", hipo=True)

    arepa = add_product(products, cats, nombre="Arepa reina con aguacate", categoria="Arepas", precio="6.20",
                        costo="2.50", descripcion="Arepa de maíz blanco rellena de pollo, aguacate y mayonesa suave.",
                        vertical="comida", image=assets["arepas"], stock=45, stock_visible=False, canal="cocina",
                        origen_pais="Colombia", alergenos=["huevos"])
    add_presentations(arepa, [("pequeña", "-1.20"), ("normal", "0.00"), ("grande", "2.50")])
    add_extras(arepa, [
        {"nombre": "Proteína extra", "max": 2, "opciones": [("Pollo extra", "1.80", 1), ("Carne mechada", "2.20", 1), ("Queso costeño", "1.50", 1)]},
        {"nombre": "Salsas", "max": 3, "opciones": [("Ají", "0.40", 2), ("Rosada", "0.40", 2), ("Cilantro limón", "0.50", 2)]},
    ])

    add_product(products, cats, nombre="Arepa pabellón", categoria="Arepas", precio="7.10", costo="2.90",
                descripcion="Carne mechada, caraotas, plátano maduro y queso latino.",
                vertical="comida", image=assets["arepas"], stock=35, stock_visible=False, canal="cocina",
                origen_pais="Colombia", alergenos=["lacteos"])

    bandeja = add_product(products, cats, nombre="Bandeja paisa personal", categoria="Platos fuertes", precio="14.90",
                          costo="6.40", descripcion="Fríjoles, arroz, carne molida, chicharrón, huevo, aguacate y maduro.",
                          vertical="comida", image=assets["platos"], stock=28, stock_visible=False, canal="cocina",
                          origen_pais="Colombia", canje_puntos=900, alergenos=["huevos"])
    add_presentations(bandeja, [("media", "-3.50"), ("personal", "0.00"), ("grande", "4.00")])

    add_product(products, cats, nombre="Ajiaco santafereño", categoria="Platos fuertes", precio="11.90",
                costo="4.80", descripcion="Sopa colombiana con pollo, tres papas, guasca, alcaparras y crema.",
                vertical="comida", image=assets["platos"], stock=30, stock_visible=False, canal="cocina",
                origen_pais="Colombia", alergenos=["lacteos"])
    add_product(products, cats, nombre="Sancocho familiar programado", categoria="Platos fuertes", precio="28.00",
                costo="11.50", descripcion="Olla familiar bajo reserva. Disponible con fecha programada.",
                vertical="comida", image=assets["platos"], stock=0, stock_visible=False, canal="cocina",
                tipo_entrega="programado", grupo_pedido="Encargos de cocina", fecha_llegada=future,
                origen_pais="Colombia")

    add_product(products, cats, nombre="Lulada caleña", categoria="Bebidas", precio="3.50", costo="1.20",
                descripcion="Lulo, hielo, lima y panela. Refrescante y sin alcohol.",
                vertical="comida", image=assets["bebidas"], stock=80, stock_visible=True, canal="almacen",
                origen_pais="Colombia", hipo=True)
    add_product(products, cats, nombre="Jugo de maracuyá", categoria="Bebidas", precio="3.20", costo="1.10",
                descripcion="Natural, ácido y fresco.",
                vertical="comida", image=assets["bebidas"], stock=75, stock_visible=True, canal="almacen",
                origen_pais="Colombia", hipo=True)
    add_product(products, cats, nombre="Malta colombiana", categoria="Bebidas", precio="2.50", costo="0.85",
                descripcion="Bebida de malta sin alcohol, servida fría.",
                vertical="comida", image=assets["bebidas"], stock=110, stock_visible=True, canal="almacen",
                modalidad="ambas", origen_pais="Colombia", hipo=True)

    add_product(products, cats, nombre="Tres leches de café", categoria="Postres y café", precio="4.20",
                costo="1.45", descripcion="Bizcocho húmedo con crema de café colombiano.",
                vertical="comida", image=assets["postres"], stock=35, stock_visible=True, canal="cocina",
                origen_pais="Colombia", canje_puntos=320, alergenos=["gluten", "lacteos", "huevos"])
    add_product(products, cats, nombre="Oblea con arequipe", categoria="Postres y café", precio="3.40",
                costo="1.10", descripcion="Oblea fina con arequipe, queso rallado y mora.",
                vertical="comida", image=assets["postres"], stock=42, stock_visible=True, canal="cocina",
                origen_pais="Colombia", alergenos=["gluten", "lacteos"])
    add_product(products, cats, nombre="Café colombiano gratis (canje)", categoria="Canjes", precio="0.00",
                descripcion="Café americano de origen Huila, exclusivo para canje con puntos.",
                vertical="comida", image=assets["postres"], stock=100, stock_visible=True, canal="cocina",
                modalidad="recogida", origen_pais="Colombia", canje_puntos=180, solo_canje=True, hipo=True)

    add_combo(products, cats, nombre="Combo almuerzo colombiano", categoria="Combos comida",
              descripcion="Elige plato fuerte, bebida y postre. Ideal para validar combos seleccionables.",
              vertical="comida", image=assets["combos"], precio_fijo="17.90", groups=[
                  {"nombre": "Plato principal", "seleccionable": True, "min": 1, "max": 1, "items": [
                      {"producto": "Bandeja paisa personal", "default": True},
                      {"producto": "Ajiaco santafereño"},
                      {"producto": "Arepa reina con aguacate"},
                  ]},
                  {"nombre": "Bebida", "seleccionable": True, "min": 1, "max": 1, "items": [
                      {"producto": "Lulada caleña", "default": True},
                      {"producto": "Jugo de maracuyá"},
                      {"producto": "Malta colombiana"},
                  ]},
                  {"nombre": "Postre", "seleccionable": True, "min": 1, "max": 1, "items": [
                      {"producto": "Tres leches de café", "default": True},
                      {"producto": "Oblea con arequipe"},
                  ]},
              ])
    add_combo(products, cats, nombre="Combo empanadas para compartir", categoria="Combos comida",
              descripcion="Elige 6 empanadas y 2 bebidas. Prueba selección múltiple.",
              vertical="comida", image=assets["combos"], precio_fijo="18.50", groups=[
                  {"nombre": "Empanadas", "seleccionable": True, "min": 1, "max": 3, "items": [
                      {"producto": "Empanada valluna de carne", "cantidad": 2, "default": True},
                      {"producto": "Empanada de pollo y ají dulce", "cantidad": 2},
                      {"producto": "Empanada vegana de fríjol", "cantidad": 2},
                  ]},
                  {"nombre": "Bebidas", "seleccionable": True, "min": 1, "max": 2, "items": [
                      {"producto": "Lulada caleña", "default": True},
                      {"producto": "Jugo de maracuyá"},
                      {"producto": "Malta colombiana"},
                  ]},
                  {"nombre": "Incluye", "items": [{"producto": "Oblea con arequipe", "cantidad": 1}]},
              ])
    add_combo(products, cats, nombre="Reserva familiar colombiana", categoria="Combos comida",
              descripcion="Combo programado: sancocho familiar, postre y bebidas.",
              vertical="comida", image=assets["combos"], precio_fijo="34.90",
              tipo_entrega="programado", fecha_llegada=future, groups=[
                  {"nombre": "Base", "items": [{"producto": "Sancocho familiar programado", "cantidad": 1}]},
                  {"nombre": "Bebida familiar", "seleccionable": True, "min": 1, "max": 2, "items": [
                      {"producto": "Lulada caleña", "default": True},
                      {"producto": "Jugo de maracuyá"},
                      {"producto": "Malta colombiana"},
                  ]},
                  {"nombre": "Postre", "items": [{"producto": "Tres leches de café", "cantidad": 2}]},
              ])


def seed_retail(products: dict[str, Product], cats: dict[str, Categoria], assets: dict[str, str]) -> None:
    future = date.today() + timedelta(days=21)
    tshirt = add_product(products, cats, nombre="Camiseta algodón Bogotá", categoria="Ropa", precio="18.00",
                         costo="7.00", descripcion="Camiseta unisex 100% algodón con estampado Bogotá.",
                         vertical="producto", image=assets["moda"], stock=36, stock_visible=True,
                         atributos={"sku": "RET-TSH-BOG", "marca": "Parcerito Wear", "color": "negro", "material": "algodón", "genero": "unisex"})
    add_presentations(tshirt, [("S", "0"), ("M", "0"), ("L", "0"), ("XL", "2.00")])

    hoodie = add_product(products, cats, nombre="Sudadera Medellín gris", categoria="Ropa", precio="39.00",
                         costo="16.00", descripcion="Sudadera con capucha, interior perchado y bordado Medellín.",
                         vertical="producto", image=assets["moda"], stock=22, stock_visible=True,
                         atributos={"sku": "RET-HOO-MDE", "marca": "Parcerito Wear", "color": "gris", "material": "algodón/poliéster"})
    add_presentations(hoodie, [("S", "0"), ("M", "0"), ("L", "0"), ("XL", "3.00")])

    dress = add_product(products, cats, nombre="Vestido feria floral", categoria="Ropa", precio="46.00",
                        costo="18.00", descripcion="Vestido midi ligero con estampado floral tropical.",
                        vertical="producto", image=assets["moda"], stock=14, stock_visible=True,
                        atributos={"sku": "RET-DRE-FLO", "marca": "Parcerito Wear", "color": "azul", "material": "viscosa"})
    add_presentations(dress, [("XS", "0"), ("S", "0"), ("M", "0"), ("L", "0")])

    add_product(products, cats, nombre="Chaqueta denim preventa", categoria="Ropa", precio="72.00",
                costo="31.00", descripcion="Producto programado para probar reservas retail.",
                vertical="producto", image=assets["moda"], stock=0, stock_visible=True,
                tipo_entrega="programado", grupo_pedido="Preventa retail", fecha_llegada=future,
                atributos={"sku": "RET-JAC-PRE", "marca": "Parcerito Wear", "color": "denim"})

    sneakers = add_product(products, cats, nombre="Zapatilla urbana blanca", categoria="Calzado", precio="58.00",
                           costo="24.00", descripcion="Zapatilla casual con suela antideslizante.",
                           vertical="producto", image=assets["moda"], stock=28, stock_visible=True,
                           atributos={"sku": "RET-SHO-WHT", "marca": "Parcerito Shoes", "color": "blanco"})
    add_presentations(sneakers, [(str(n), "0") for n in range(36, 46)])
    boots = add_product(products, cats, nombre="Bota chelsea cuero negro", categoria="Calzado", precio="84.00",
                        costo="36.00", descripcion="Bota de cuero con elástico lateral.",
                        vertical="producto", image=assets["moda"], stock=12, stock_visible=True,
                        atributos={"sku": "RET-BOOT-BLK", "marca": "Parcerito Shoes", "material": "cuero"})
    add_presentations(boots, [(str(n), "0") for n in range(37, 44)])

    necklace = add_product(products, cats, nombre="Collar cadena plata 45cm", categoria="Bisutería", precio="25.00",
                           costo="8.50", descripcion="Plata 925, cierre seguro, talla única.",
                           vertical="producto", image=assets["joyeria"], stock=25, stock_visible=True,
                           atributos={"sku": "RET-JWL-COL", "material": "plata 925"})
    earrings = add_product(products, cats, nombre="Pendientes aro dorado", categoria="Bisutería", precio="16.00",
                           costo="5.20", descripcion="Aros pequeños con baño dorado.",
                           vertical="producto", image=assets["joyeria"], stock=32, stock_visible=True,
                           atributos={"sku": "RET-JWL-ARO", "material": "baño dorado"})
    ring = add_product(products, cats, nombre="Anillo trenzado ajustable", categoria="Bisutería", precio="14.00",
                       costo="4.80", descripcion="Anillo ajustable en acero/plata.",
                       vertical="producto", image=assets["joyeria"], stock=18, stock_visible=True,
                       atributos={"sku": "RET-JWL-RNG", "material": "acero"})

    bag = add_product(products, cats, nombre="Bolso bandolera cuero", categoria="Accesorios", precio="65.00",
                      costo="27.00", descripcion="Bandolera en cuero marrón con cierre magnético.",
                      vertical="producto", image=assets["retail"], stock=10, stock_visible=True,
                      atributos={"sku": "RET-ACC-BAG", "material": "cuero", "color": "marrón"})
    cap = add_product(products, cats, nombre="Gorra Parcerito ajustable", categoria="Accesorios", precio="19.00",
                      costo="6.70", descripcion="Gorra negra bordada, cierre ajustable.",
                      vertical="producto", image=assets["retail"], stock=40, stock_visible=True,
                      atributos={"sku": "RET-ACC-CAP", "color": "negro"})
    add_product(products, cats, nombre="Empaque regalo premium", categoria="Accesorios", precio="4.00",
                costo="1.20", descripcion="Caja rígida, papel seda y tarjeta para regalo.",
                vertical="producto", image=assets["retail"], stock=80, stock_visible=True,
                atributos={"sku": "RET-ACC-GIFT", "tipo": "empaque"})
    add_product(products, cats, nombre="Gafas de sol UV400", categoria="Accesorios", precio="35.00",
                costo="11.50", descripcion="Montura carey con protección UV400.",
                vertical="producto", image=assets["retail"], stock=16, stock_visible=True,
                canje_puntos=760, atributos={"sku": "RET-ACC-GLS", "material": "acetato"})
    add_product(products, cats, nombre="Vale regalo retail 10€ (canje)", categoria="Canjes", precio="0.00",
                descripcion="Vale exclusivo de puntos para compras retail.",
                vertical="producto", image=assets["combos"], stock=999, stock_visible=True,
                canje_puntos=500, solo_canje=True, canal="almacen",
                atributos={"sku": "RET-GFT-10", "tipo": "vale"})

    add_combo(products, cats, nombre="Outfit urbano completo", categoria="Combos retail",
              descripcion="Elige prenda superior y calzado; incluye gorra. Combo retail con opciones.",
              vertical="producto", image=assets["combos"], descuento_pct="12", canal="almacen", groups=[
                  {"nombre": "Prenda superior", "seleccionable": True, "min": 1, "max": 1, "items": [
                      {"producto": "Camiseta algodón Bogotá", "default": True},
                      {"producto": "Sudadera Medellín gris"},
                  ]},
                  {"nombre": "Calzado", "seleccionable": True, "min": 1, "max": 1, "items": [
                      {"producto": "Zapatilla urbana blanca", "default": True},
                      {"producto": "Bota chelsea cuero negro", "precio_extra": "12.00"},
                  ]},
                  {"nombre": "Incluye", "items": [{"producto": "Gorra Parcerito ajustable", "cantidad": 1}]},
              ])
    add_combo(products, cats, nombre="Pack regalo bisutería", categoria="Combos retail",
              descripcion="Elige dos piezas y recibe empaque de regalo.",
              vertical="producto", image=assets["combos"], precio_fijo="36.00", canal="almacen", groups=[
                  {"nombre": "Piezas", "seleccionable": True, "min": 1, "max": 2, "items": [
                      {"producto": "Collar cadena plata 45cm", "default": True},
                      {"producto": "Pendientes aro dorado", "default": True},
                      {"producto": "Anillo trenzado ajustable"},
                  ]},
                  {"nombre": "Empaque", "items": [{"producto": "Empaque regalo premium", "cantidad": 1}]},
              ])
    add_combo(products, cats, nombre="Preventa denim + accesorio", categoria="Combos retail",
              descripcion="Combo programado para probar el flujo de retail en reserva.",
              vertical="producto", image=assets["combos"], precio_fijo="99.00", canal="almacen",
              tipo_entrega="programado", fecha_llegada=future, groups=[
                  {"nombre": "Base", "items": [{"producto": "Chaqueta denim preventa", "cantidad": 1}]},
                  {"nombre": "Accesorio", "seleccionable": True, "min": 1, "max": 1, "items": [
                      {"producto": "Bolso bandolera cuero", "default": True},
                      {"producto": "Gafas de sol UV400"},
                  ]},
              ])


def seed_marketing_and_finance(users: dict[str, User], products: dict[str, Product], assets: dict[str, str]) -> None:
    today = date.today()
    db.session.add(Coupon(codigo="SHOWCASE10", descripcion="10% para probar el checkout", tipo="porcentaje",
                          valor=money("10"), minimo_pedido=money("10"), usos_maximos=200,
                          activo=True, fecha_inicio=today, fecha_fin=today + timedelta(days=90)))
    db.session.add(Coupon(codigo="RETAIL5", descripcion="5€ en pedidos retail desde 40€", tipo="monto_fijo",
                          valor=money("5"), minimo_pedido=money("40"), usos_maximos=100,
                          activo=True, fecha_inicio=today, fecha_fin=today + timedelta(days=90)))
    db.session.add(AffiliateCode(codigo="PARCERO", descripcion="Afiliado demo 7% dto / 5% comisión",
                                 tipo="externo", user_id=users["afiliado"].id,
                                 descuento_tipo="porcentaje", descuento_valor=money("7"),
                                 comision_tipo="porcentaje", comision_valor=money("5"),
                                 activo=True, fecha_inicio=today, fecha_fin=today + timedelta(days=90),
                                 creado_por=users["admin"].id))
    for role in ("cocina", "preparacion"):
        db.session.add(StaffPayment(user_id=users[role].id, tipo="salario", monto=users[role].salario_base,
                                    concepto="Sueldo base pendiente showcase", periodo_inicio=today.replace(day=1),
                                    periodo_fin=today, origen="manual", pagado=False, registrado_por=users["admin"].id))
    db.session.add(MenuConfig(pagina="home", tipo="banner", titulo="Catálogo showcase dual",
                              contenido="Prueba comida colombiana, retail, puntos, combos y pedidos programados.",
                              imagen_url=assets["hero"], enlace_url="/", orden=1, activo=True,
                              creado_por=users["admin"].id))
    for idx, name in enumerate(["Combo almuerzo colombiano", "Outfit urbano completo", "Pack regalo bisutería"], start=2):
        p = products[name]
        db.session.add(MenuConfig(pagina="home", tipo="producto_destacado", titulo=p.nombre,
                                  contenido=p.descripcion, imagen_url=p.imagen_url, producto_id=p.id,
                                  orden=idx, activo=True, creado_por=users["admin"].id))


def seed_reviews(users: dict[str, User], products: dict[str, Product]) -> None:
    rows = [
        ("Bandeja paisa personal", 5, "Porción generosa y llegó caliente."),
        ("Combo almuerzo colombiano", 5, "Elegir bebida y postre fue muy claro."),
        ("Empanada valluna de carne", 4, "Crujiente, el ají suma mucho."),
        ("Outfit urbano completo", 5, "Las tallas estaban claras y el combo se entiende."),
        ("Pack regalo bisutería", 4, "Buen pack para regalo, fácil de seleccionar."),
    ]
    for name, rating, comment in rows:
        db.session.add(Review(producto_id=products[name].id, cliente_id=users["cliente"].id,
                              calificacion=rating, comentario=comment, aprobada=True))


def validate_seed(products: dict[str, Product]) -> dict:
    db.session.flush()
    errors = []
    for combo in Product.query.filter_by(es_combo=True).all():
        valid, error = validate_combo_structure(_payload_estructura_combo(list(combo.combo_items)), combo.id)
        if not valid:
            errors.append(f"{combo.nombre}: {error}")
    if errors:
        raise SystemExit("Combos inválidos:\n- " + "\n- ".join(errors))

    report = {
        "productos_total": Product.query.count(),
        "productos_activos": Product.query.filter_by(activo=True).count(),
        "comida": Product.query.filter_by(vertical="comida").count(),
        "retail": Product.query.filter_by(vertical="producto").count(),
        "combos": Product.query.filter_by(es_combo=True).count(),
        "extras_grupos": ProductExtraGroup.query.count(),
        "extras_opciones": ProductExtraOption.query.count(),
        "presentaciones": ProductPresentation.query.count(),
        "canjeables": Product.query.filter_by(canjeable_con_puntos=True).count(),
        "solo_canje": Product.query.filter_by(solo_canje=True).count(),
        "categorias": Categoria.query.count(),
        "reviews_aprobadas": Review.query.filter_by(aprobada=True).count(),
        "cupones": Coupon.query.count(),
        "zonas": ZonaEntrega.query.count(),
        "staff": User.query.filter(User.rol.in_(["admin", "cocina", "preparacion", "repartidor"])).count(),
        "cliente_puntos": users_points(),
    }
    return report


def users_points() -> int:
    user = User.query.filter_by(telefono_normalizado="34630001005", rol="cliente").first()
    return int(user.puntos or 0) if user else 0


def main() -> None:
    app = create_app(os.environ.get("FLASK_ENV", "production"))
    with app.app_context():
        superadmins_snapshot = snapshot_superadmins()
        if not superadmins_snapshot:
            raise SystemExit("REHUSO: no hay super_admin para preservar acceso.")
        config_snapshot = snapshot_config()
        print(f"Preservando {len(superadmins_snapshot)} super_admin(s) y {len(config_snapshot)} claves críticas.")

        truncate_database()
        assets = ensure_assets()
        superadmins = restore_superadmins(superadmins_snapshot)
        seed_config(config_snapshot)
        users = seed_users(superadmins)
        seed_zones()
        cats = seed_categories(assets)
        products: dict[str, Product] = {}
        seed_food(products, cats, assets)
        seed_retail(products, cats, assets)
        seed_marketing_and_finance(users, products, assets)
        seed_reviews(users, products)
        db.session.add(AuditLog(user_id=superadmins[0].id if superadmins else None,
                                accion="reset_showcase_dual", recurso="system",
                                detalle="Reset controlado de datos y seed comida/retail"))
        report = validate_seed(products)
        db.session.commit()

        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        print("Cuentas demo creadas con contraseña: Test2026!")


if __name__ == "__main__":
    main()
