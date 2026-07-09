#!/usr/bin/env python3
"""
Seed SHOWCASE RETAIL — añade productos del nicho retail (ropa, zapatos,
bisutería, accesorios) SIN borrar los de comida. Ambos nichos coexisten
en BD; el catálogo público muestra el que corresponda a TIPO_TIENDA.

Cubre:
- Ropa con tallas S/M/L/XL y atributos (marca, color, material, género).
- Zapatos con números 36-45.
- Bisutería sin tallas (talla única).
- Accesorios con o sin tallas.
- Combo retail con precio fijo.
- Producto de canje retail (solo puntos).

Uso: docker exec oxidian python3 scripts/seed_showcase_retail.py
"""

import os
import sys
import json
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db
from models import (
    Product, Categoria, Stock,
    ComboGroup, ComboItem,
    ProductPresentation,
    SiteConfig,
)


def _cat(nombre, orden=10):
    existente = Categoria.query.filter(
        db.func.lower(Categoria.nombre) == nombre.lower()
    ).first()
    if existente:
        return existente
    c = Categoria(nombre=nombre, activo=True, orden=orden)
    db.session.add(c)
    db.session.flush()
    return c


def _prod(nombre, precio, cat, marca=None, color=None, material=None, genero=None, **kw):
    """Helper: producto retail con atributos serializados en atributos_json."""
    defaults = dict(
        activo=True,
        canal_preparacion="almacen",   # retail siempre almacén
        tipo_entrega="inmediato",
        modalidad_entrega="ambas",
        vertical="producto",           # retail vertical
        stock_mostrar_en_web=True,     # retail suele mostrar unidades
    )
    defaults.update(kw)
    atrs = {}
    if marca: atrs["marca"] = marca
    if color: atrs["color"] = color
    if material: atrs["material"] = material
    if genero: atrs["genero"] = genero
    if atrs:
        defaults["atributos_json"] = json.dumps(atrs, ensure_ascii=False)

    if defaults.get("solo_canje"):
        precio_val = Decimal("0.00")
        defaults["canjeable_con_puntos"] = True
    else:
        precio_val = Decimal(str(precio))
    p = Product(nombre=nombre, precio=precio_val, categoria_id=cat.id, **defaults)
    db.session.add(p)
    db.session.flush()
    db.session.add(Stock(
        producto_id=p.id,
        cantidad=25,
        fecha_caducidad=date.today() + timedelta(days=730),
    ))
    return p


def _pres(prod, tamaños):
    """tamaños: lista de (label, precio_extra_float)."""
    for i, (tam, extra) in enumerate(tamaños):
        db.session.add(ProductPresentation(
            producto_id=prod.id, tamaño=tam,
            precio_extra=Decimal(str(extra)),
            activo=True, orden=i,
        ))


def seed_retail():
    print("• Categorías RETAIL")
    ropa = _cat("Ropa", 10)
    zapatos = _cat("Zapatos", 11)
    bisuteria = _cat("Bisutería", 12)
    accesorios = _cat("Accesorios", 13)

    print("• Productos RETAIL — cada característica")

    # ROPA — tallas S/M/L/XL
    camiseta = _prod(
        "Camiseta oversize", 18.00, ropa,
        marca="Genérica", color="Negro", material="100% algodón", genero="unisex",
    )
    _pres(camiseta, [("S", 0), ("M", 0), ("L", 0), ("XL", 2.00)])  # XL con recargo

    sudadera = _prod(
        "Sudadera con capucha", 34.00, ropa,
        marca="Genérica", color="Gris", material="80% algodón, 20% poliéster",
    )
    _pres(sudadera, [("S", 0), ("M", 0), ("L", 0), ("XL", 3.00)])

    pantalon = _prod(
        "Pantalón chino", 42.00, ropa,
        marca="Genérica", color="Beige", material="97% algodón, 3% elastano",
        genero="hombre",
    )
    _pres(pantalon, [("S", 0), ("M", 0), ("L", 0), ("XL", 2.00), ("XXL", 4.00)])

    vestido = _prod(
        "Vestido midi floral", 45.00, ropa,
        marca="Genérica", color="Azul con flores", material="Tejido ligero",
        genero="mujer",
    )
    _pres(vestido, [("XS", 0), ("S", 0), ("M", 0), ("L", 0)])

    # Solo delivery en ropa (ej: prenda que no cabe en local para llevar)
    _prod(
        "Abrigo largo lana", 89.00, ropa,
        marca="Genérica", color="Camel", material="70% lana, 30% viscosa",
        modalidad_entrega="delivery",
    )

    # ZAPATOS — números 36-45
    zapatilla = _prod(
        "Zapatilla urbana", 55.00, zapatos,
        marca="Genérica", color="Blanco", material="Piel sintética",
    )
    _pres(zapatilla, [(str(n), 0) for n in range(36, 46)])

    bota = _prod(
        "Bota chelsea", 78.00, zapatos,
        marca="Genérica", color="Negro", material="Cuero", genero="mujer",
    )
    _pres(bota, [(str(n), 0) for n in range(36, 42)])

    sandalia = _prod(
        "Sandalia plana", 32.00, zapatos,
        marca="Genérica", color="Dorado", material="Sintético",
        genero="mujer",
    )
    _pres(sandalia, [(str(n), 0) for n in range(36, 42)])

    # BISUTERÍA — sin tallas (talla única)
    collar = _prod(
        "Collar cadena 45cm", 25.00, bisuteria,
        marca="Genérica", material="Plata 925",
    )
    pendientes = _prod(
        "Pendientes aro pequeño", 15.00, bisuteria,
        marca="Genérica", material="Baño de oro 18k",
    )
    anillo = _prod(
        "Anillo trenzado ajustable", 12.00, bisuteria,
        marca="Genérica", material="Plata 925",
    )
    _prod(
        "Pulsera hilo con dijes", 8.00, bisuteria,
        marca="Genérica", color="Multicolor", material="Hilo + acero",
    )

    # ACCESORIOS
    bolso = _prod(
        "Bolso bandolera", 65.00, accesorios,
        marca="Genérica", color="Marrón", material="Cuero italiano",
        genero="mujer",
    )
    _prod(
        "Cinturón trenzado piel", 28.00, accesorios,
        marca="Genérica", color="Negro", material="100% piel", genero="hombre",
    )
    _prod(
        "Gafas de sol acetato", 35.00, accesorios,
        marca="Genérica", color="Carey", material="Acetato · UV400",
    )

    # CANJE retail (vale regalo con puntos)
    _prod(
        "Vale regalo 10€ (canje)", 0, accesorios,
        marca="Regalo casa", color="Regalo",
        solo_canje=True, canjeable_con_puntos=True,
        puntos_para_canje=500,
        modalidad_entrega="ambas",
    )

    # ── COMBO RETAIL ────────────────────────────────────────
    print("• Combo retail")
    combo = Product(
        nombre="Pack look casual (10% dto)",
        precio=Decimal("0"),  # calculado por combo
        activo=True, es_combo=True,
        categoria_id=ropa.id,
        canal_preparacion="almacen",
        tipo_entrega="inmediato", modalidad_entrega="ambas",
        vertical="producto",
        combo_precio_modo="descuento_porcentaje",
        combo_precio_base=Decimal("0"),
        combo_descuento_pct=Decimal("10"),
    )
    db.session.add(combo)
    db.session.flush()
    db.session.add(Stock(producto_id=combo.id, cantidad=50,
                         fecha_caducidad=date.today() + timedelta(days=730)))
    g_fijos = ComboGroup(
        combo_id=combo.id, nombre="Incluye", orden=1, tipo="fijo",
        min_selecciones=0, max_selecciones=1, requerido=False,
    )
    db.session.add(g_fijos); db.session.flush()
    for i, comp in enumerate([camiseta, sudadera]):
        db.session.add(ComboItem(
            combo_id=combo.id, producto_id=comp.id,
            combo_group_id=g_fijos.id,
            cantidad=1, orden=i, activo=True, es_predeterminado=True,
            precio_extra=Decimal("0"),
        ))

    # Combo con selección — pack joyería 3 piezas (elige 3 de 3, precio fijo)
    combo2 = Product(
        nombre="Pack joyería (precio fijo)",
        precio=Decimal("0"),
        activo=True, es_combo=True,
        categoria_id=bisuteria.id,
        canal_preparacion="almacen",
        tipo_entrega="inmediato", modalidad_entrega="ambas",
        vertical="producto",
        combo_precio_modo="fijo",
        combo_precio_base=Decimal("40.00"),
        combo_descuento_pct=Decimal("0"),
    )
    db.session.add(combo2)
    db.session.flush()
    db.session.add(Stock(producto_id=combo2.id, cantidad=30,
                         fecha_caducidad=date.today() + timedelta(days=730)))
    g_sel = ComboGroup(
        combo_id=combo2.id, nombre="Elige tus piezas", orden=1, tipo="seleccion",
        min_selecciones=1, max_selecciones=3, requerido=True,
    )
    db.session.add(g_sel); db.session.flush()
    for i, comp in enumerate([collar, pendientes, anillo]):
        db.session.add(ComboItem(
            combo_id=combo2.id, producto_id=comp.id,
            combo_group_id=g_sel.id,
            cantidad=1, orden=i, activo=True, es_predeterminado=(i == 0),
            precio_extra=Decimal("0"),
        ))


def main():
    app = create_app()
    with app.app_context():
        seed_retail()
        db.session.commit()
        n_prod = Product.query.filter_by(vertical="producto").count()
        n_combo = Product.query.filter_by(vertical="producto", es_combo=True).count()
        n_pres = (db.session.query(ProductPresentation)
                  .join(Product, Product.id == ProductPresentation.producto_id)
                  .filter(Product.vertical == "producto").count())
        print()
        print("=" * 60)
        print(f"✓ Retail sembrado:")
        print(f"  Productos retail: {n_prod} ({n_combo} combos)")
        print(f"  Presentaciones retail: {n_pres}")
        print(f"  Modo actual (TIPO_TIENDA): {SiteConfig.get('TIPO_TIENDA', 'comida')}")
        print("=" * 60)
        print(f"  Para verlos en catálogo:")
        print(f"    docker exec oxidian-db psql -U oxidian -d oxidian \\")
        print(f"      -c \"UPDATE site_config SET valor='producto' WHERE clave='TIPO_TIENDA';\"")


if __name__ == "__main__":
    main()
