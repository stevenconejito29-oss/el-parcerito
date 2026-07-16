#!/usr/bin/env python
"""Replace the public demo catalog with a fresh mobile showcase catalog.

Uso:
  DATABASE_URL=postgresql://... python scripts/seed_showcase_catalog.py

The script hides the previous catalog instead of deleting rows, so historical
orders keep their product references. It rebuilds categories, visible products,
combos, stock, menu banners and a small promotion/coupon set.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from extensions import db
from models import Categoria, ComboItem, Coupon, MenuConfig, Product, SiteConfig, Stock
from routes.uploads import IMAGES_DIR

ASSET_DIR = IMAGES_DIR / "showcase"
ASSET_URL = "showcase"


def _svg(title: str, subtitle: str, emoji: str, a: str, b: str, c: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 860" role="img" aria-label="{title}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{a}"/>
      <stop offset="0.55" stop-color="{b}"/>
      <stop offset="1" stop-color="{c}"/>
    </linearGradient>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="24" stdDeviation="28" flood-color="#251507" flood-opacity=".24"/>
    </filter>
  </defs>
  <rect width="1200" height="860" rx="72" fill="url(#g)"/>
  <circle cx="1015" cy="120" r="190" fill="#fff" opacity=".14"/>
  <circle cx="165" cy="760" r="230" fill="#fff" opacity=".10"/>
  <g filter="url(#shadow)">
    <rect x="130" y="130" width="940" height="600" rx="58" fill="#fffdf8" opacity=".94"/>
    <text x="600" y="372" text-anchor="middle" font-size="178" font-family="Apple Color Emoji, Segoe UI Emoji, sans-serif">{emoji}</text>
    <text x="600" y="505" text-anchor="middle" fill="#18120A" font-size="58" font-weight="900" font-family="Inter, Arial, sans-serif">{title}</text>
    <text x="600" y="572" text-anchor="middle" fill="#6B5A4E" font-size="30" font-weight="700" font-family="Inter, Arial, sans-serif">{subtitle}</text>
  </g>
</svg>
"""


ASSETS = {
    "banner": _svg("Nuevo menú latino", "Pedidos móviles, combos claros y productos reales", "🥟", "#F9C74F", "#F3722C", "#2A9D8F"),
    "empanadas": _svg("Empanadas premium", "Horneadas y fritas para pedir al momento", "🥟", "#F9C74F", "#F9844A", "#CE1126"),
    "arepas": _svg("Arepas rellenas", "Maíz blanco, rellenos frescos y salsas caseras", "🫓", "#F9C74F", "#90BE6D", "#277DA1"),
    "bowls": _svg("Bowls latinos", "Platos completos, fáciles de elegir", "🍛", "#F6D365", "#FDA085", "#2F9E6D"),
    "bebidas": _svg("Bebidas naturales", "Jugos, malta y aguas frescas", "🥤", "#48CAE4", "#00B4D8", "#0077B6"),
    "postres": _svg("Postres caseros", "Dulces suaves para cerrar el pedido", "🍮", "#FBC4AB", "#F4978E", "#9D4EDD"),
    "combos": _svg("Combos listos", "Opciones configuradas para probar el flujo", "🎁", "#F9C74F", "#577590", "#003087"),
    "despensa": _svg("Despensa latina", "Productos para llevar a casa", "🛍️", "#B7E4C7", "#52B788", "#1B4332"),
}


CATEGORIES = [
    ("Empanadas", "Crujientes, horneadas o fritas", "empanadas", 1),
    ("Arepas", "Rellenas y fáciles de personalizar", "arepas", 2),
    ("Bowls", "Platos completos para almuerzo", "bowls", 3),
    ("Bebidas", "Jugos, malta y refrescos", "bebidas", 4),
    ("Postres", "Dulces caseros", "postres", 5),
    ("Despensa", "Productos latinos para llevar", "despensa", 6),
    ("Combos", "Menús armados con opciones", "combos", 7),
]


PRODUCTS = [
    dict(nombre="Empanada de ternera mechada", cat="Empanadas", img="empanadas", precio=2.90, costo=1.05, stock=90, origen="Colombia", alergenos=["gluten"], promo=10, puntos=220, desc="Masa crujiente rellena de ternera guisada, papa criolla y hogao suave."),
    dict(nombre="Empanada de pollo ají dulce", cat="Empanadas", img="empanadas", precio=2.70, costo=.95, stock=95, origen="Venezuela", alergenos=["gluten", "huevos"], desc="Pollo desmechado con sofrito latino y toque de ají dulce."),
    dict(nombre="Empanada vegana de frijol", cat="Empanadas", img="empanadas", precio=2.80, costo=1.00, stock=70, origen="Colombia", hipo=True, desc="Frijol negro, plátano maduro y comino. Sin alérgenos registrados."),
    dict(nombre="Arepa reina cremosa", cat="Arepas", img="arepas", precio=5.90, costo=2.40, stock=45, origen="Venezuela", alergenos=["huevos"], desc="Arepa de maíz blanco con pollo, aguacate y mayonesa casera."),
    dict(nombre="Arepa pabellón", cat="Arepas", img="arepas", precio=6.40, costo=2.70, stock=38, origen="Venezuela", alergenos=[], promo=8, desc="Carne mechada, caraotas, plátano maduro y queso latino."),
    dict(nombre="Bowl paisa ligero", cat="Bowls", img="bowls", precio=9.80, costo=4.30, stock=28, origen="Colombia", alergenos=["huevos"], desc="Arroz, frijoles, aguacate, chicharrón crujiente y pico de gallo."),
    dict(nombre="Bowl yuca pollo", cat="Bowls", img="bowls", precio=8.90, costo=3.60, stock=32, origen="Carmona", alergenos=[], desc="Yuca dorada, pollo especiado, ensalada fresca y salsa verde."),
    dict(nombre="Lulada fría", cat="Bebidas", img="bebidas", precio=3.20, costo=1.20, stock=80, origen="Colombia", hipo=True, desc="Bebida de lulo con hielo, lima y panela suave."),
    dict(nombre="Malta artesanal", cat="Bebidas", img="bebidas", precio=2.40, costo=.90, stock=100, origen="Colombia", hipo=True, desc="Malta dulce sin alcohol, servida fría."),
    dict(nombre="Tres leches individual", cat="Postres", img="postres", precio=3.80, costo=1.35, stock=40, origen="Latinoamérica", alergenos=["lacteos", "huevos", "gluten"], promo=12, puntos=320, desc="Bizcocho húmedo con crema de tres leches y canela."),
    dict(nombre="Oblea con arequipe", cat="Postres", img="postres", precio=3.40, costo=1.10, stock=44, origen="Colombia", alergenos=["gluten", "lacteos"], desc="Oblea fina con arequipe, queso rallado y mora."),
    dict(nombre="Café origen Huila 250g", cat="Despensa", img="despensa", precio=8.90, costo=3.80, stock=30, origen="Colombia", hipo=True, puntos=690, desc="Café molido medio, notas a cacao y panela."),
    dict(nombre="Salsa ají de la casa", cat="Despensa", img="despensa", precio=4.50, costo=1.40, stock=35, origen="Carmona", hipo=True, desc="Ají suave con cilantro, lima y toque de panela."),
    dict(nombre="Arepa familiar programada", cat="Arepas", img="arepas", precio=13.90, costo=5.60, stock=0, origen="Venezuela", alergenos=["huevos"], tipo="programado", fecha=2, desc="Pack de cuatro arepas rellenas preparado bajo reserva."),
]


COMBOS = [
    dict(nombre="Combo ejecutivo", cat="Combos", img="combos", precio=11.90, costo=5.10, origen="Carmona", promo=10, desc="Un bowl, una bebida y un postre. Pensado para probar el flujo completo.", items=[("Bowl paisa ligero", 1, False, None, 1), ("Bowl yuca pollo", 1, True, "Elige bowl", 1), ("Lulada fría", 1, True, "Elige bebida", 1), ("Malta artesanal", 1, True, "Elige bebida", 1), ("Oblea con arequipe", 1, False, None, 1)]),
    dict(nombre="Combo empanadas para dos", cat="Combos", img="combos", precio=12.80, costo=4.90, origen="Carmona", desc="Cuatro empanadas, dos bebidas y salsa de la casa.", items=[("Empanada de ternera mechada", 2, True, "Elige empanadas", 2), ("Empanada de pollo ají dulce", 2, True, "Elige empanadas", 2), ("Empanada vegana de frijol", 2, True, "Elige empanadas", 2), ("Lulada fría", 1, True, "Elige bebidas", 2), ("Malta artesanal", 1, True, "Elige bebidas", 2), ("Salsa ají de la casa", 1, False, None, 1)]),
    dict(nombre="Combo reserva arepas", cat="Combos", img="combos", precio=18.90, costo=7.20, origen="Carmona", tipo="programado", fecha=2, desc="Combo programado para validar reservas: pack de arepas, bebida y postre.", items=[("Arepa familiar programada", 1, False, None, 1), ("Lulada fría", 1, True, "Elige bebida", 1), ("Malta artesanal", 1, True, "Elige bebida", 1), ("Tres leches individual", 1, False, None, 1)]),
]


def ensure_assets() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in ASSETS.items():
        (ASSET_DIR / f"{name}.svg").write_text(content, encoding="utf-8")


def img(name: str) -> str:
    return f"{ASSET_URL}/{name}.svg"


def replace_catalog() -> dict:
    ensure_assets()
    today = date.today()

    # Hide previous visible catalog/menu. Keep rows for historical references.
    Product.query.update({"activo": False})
    Categoria.query.update({"activo": False})
    MenuConfig.query.filter(MenuConfig.pagina.in_(["home", "menu", "checkout"])).delete(synchronize_session=False)
    Coupon.query.filter(Coupon.codigo.in_(["MOVIL10", "LATINO15"])).delete(synchronize_session=False)

    cats = {}
    for nombre, desc, asset, order in CATEGORIES:
        cat = Categoria.query.filter_by(nombre=nombre).first()
        if not cat:
            cat = Categoria(nombre=nombre)
            db.session.add(cat)
        cat.descripcion = desc
        cat.imagen_url = img(asset)
        cat.orden = order
        cat.activo = True
        cats[nombre] = cat
    db.session.flush()

    created = {}

    def upsert_product(payload: dict, combo: bool = False) -> Product:
        p = Product.query.filter_by(nombre=payload["nombre"]).first()
        if not p:
            p = Product(nombre=payload["nombre"], precio=payload["precio"])
            db.session.add(p)
        p.descripcion = payload["desc"]
        p.precio = payload["precio"]
        p.precio_costo = payload.get("costo", 0)
        p.categoria_id = cats[payload["cat"]].id
        p.imagen_url = img(payload["img"])
        p.origen_pais = payload.get("origen")
        p.es_combo = combo
        p.tipo_producto = "combo" if combo else "simple"
        p.tipo_entrega = payload.get("tipo", "inmediato")
        p.fecha_llegada = today + timedelta(days=payload.get("fecha", 0)) if payload.get("tipo") == "programado" else None
        p.activo = True
        p.stock_mostrar_en_web = not combo and p.tipo_entrega == "inmediato"
        p.canjeable_con_puntos = bool(payload.get("puntos"))
        p.puntos_para_canje = payload.get("puntos")
        p.es_hipoalergenico = bool(payload.get("hipo"))
        allergens = payload.get("alergenos") or []
        p.alergenos_json = json.dumps(allergens, ensure_ascii=False) if allergens else None
        p.alergenos_info = ", ".join(allergens) if allergens else None
        p.atributos_json = json.dumps({"showcase": True, "sku": f"SHOW-{p.nombre[:3].upper()}"}, ensure_ascii=False)
        db.session.flush()

        if not combo:
            Stock.query.filter_by(producto_id=p.id).delete()
            if p.tipo_entrega == "inmediato":
                db.session.add(Stock(
                    producto_id=p.id,
                    cantidad=payload.get("stock", 0),
                    unidad="unidad",
                    lote="SHOWCASE",
                    fecha_entrada=today,
                    fecha_caducidad=today + timedelta(days=30),
                    alerta_dias=7,
                    ubicacion="Showcase",
                ))
        else:
            ComboItem.query.filter_by(combo_id=p.id).delete()
        created[p.nombre] = p
        return p

    for payload in PRODUCTS:
        upsert_product(payload)

    for payload in COMBOS:
        combo = upsert_product(payload, combo=True)
        for product_name, qty, selectable, group, max_sel in payload["items"]:
            component = created.get(product_name) or Product.query.filter_by(nombre=product_name).first()
            if component:
                db.session.add(ComboItem(
                    combo_id=combo.id,
                    producto_id=component.id,
                    cantidad=qty,
                    es_seleccionable=selectable,
                    grupo_seleccion=group,
                    max_selecciones=max_sel,
                ))

    db.session.flush()

    highlights = ["Combo ejecutivo", "Empanada de ternera mechada", "Tres leches individual"]
    db.session.add(MenuConfig(
        pagina="home",
        tipo="banner",
        titulo="Nuevo menú móvil",
        contenido="Catálogo renovado para probar pedidos, puntos, combos y pagos.",
        imagen_url=img("banner"),
        enlace_url="/",
        orden=1,
        activo=True,
    ))
    for i, name in enumerate(highlights, start=2):
        product = Product.query.filter_by(nombre=name).first()
        if product:
            db.session.add(MenuConfig(
                pagina="home",
                tipo="producto_destacado",
                titulo="Recomendado",
                contenido=product.descripcion,
                imagen_url=product.imagen_url,
                producto_id=product.id,
                orden=i,
                activo=True,
            ))

    db.session.add(Coupon(
        codigo="MOVIL10",
        descripcion="10% probando el nuevo menú móvil",
        tipo="porcentaje",
        valor=10,
        minimo_pedido=10,
        activo=True,
        fecha_inicio=today,
        fecha_fin=today + timedelta(days=45),
    ))
    db.session.add(Coupon(
        codigo="LATINO15",
        descripcion="15% en pedidos grandes",
        tipo="porcentaje",
        valor=15,
        minimo_pedido=25,
        activo=True,
        fecha_inicio=today,
        fecha_fin=today + timedelta(days=45),
    ))

    SiteConfig.set("COLOR_PRIMARIO", "#F4C542")
    SiteConfig.set("COLOR_SECUNDARIO", "#DA4D40")
    SiteConfig.set("COLOR_ACENTO", "#245A9A")
    SiteConfig.set("PWA_VERSION", today.isoformat())

    db.session.commit()
    return {
        "categories": len(CATEGORIES),
        "products": len(PRODUCTS),
        "combos": len(COMBOS),
        "assets": len(ASSETS),
    }


if __name__ == "__main__":
    app = create_app(os.environ.get("FLASK_ENV", "development"))
    with app.app_context():
        result = replace_catalog()
    print(
        "Showcase catalog ready: "
        f"{result['categories']} categories, {result['products']} products, "
        f"{result['combos']} combos, {result['assets']} SVG images."
    )
