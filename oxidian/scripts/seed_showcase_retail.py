#!/usr/bin/env python3
"""
Seed SHOWCASE RETAIL — añade productos del nicho retail (ropa, zapatos,
bisutería, accesorios) SIN borrar los de comida.

Cubre variedad realista:
- Ropa con tallas S/M/L/XL y atributos (marca, color, material, género).
- Zapatos con números 36-45.
- Bisutería sin tallas (talla única).
- Accesorios con o sin tallas.
- Vale regalo solo_canje (puntos).
- Producto de temporada (programado con fecha).
- Producto con stock bajo (visible en web).
- Combos con precio fijo, descuento %, selección de talla, mix fijos+seleccionables.

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


def _prod(nombre, precio, cat, marca=None, color=None, material=None, genero=None,
          stock=25, **kw):
    """Producto retail con atributos serializados en atributos_json."""
    defaults = dict(
        activo=True,
        canal_preparacion="almacen",
        tipo_entrega="inmediato",
        modalidad_entrega="ambas",
        vertical="producto",
        stock_mostrar_en_web=True,
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
        cantidad=stock,
        fecha_caducidad=date.today() + timedelta(days=730),
    ))
    return p


def _pres(prod, tamaños):
    for i, (tam, extra) in enumerate(tamaños):
        db.session.add(ProductPresentation(
            producto_id=prod.id, tamaño=tam,
            precio_extra=Decimal(str(extra)),
            activo=True, orden=i,
        ))


def _combo(nombre, cat, componentes, precio_fijo=None, descuento_pct=None,
           combo_kwargs=None):
    """componentes: lista de (producto, seleccionable, es_predeterminado,
    grupo_seleccion, precio_extra). Auto-calcula combo_precio_base como
    la suma de componentes fijos (o todos si no hay seleccionables)."""
    modo = "descuento_porcentaje" if descuento_pct is not None else "fijo"
    ck = dict(combo_kwargs or {})
    fijos = [c for (c, sel, *_r) in componentes if not sel]
    seleccionables = [c for (c, sel, *_r) in componentes if sel]
    # Base representativa: fijos + mediana de seleccionables (o suma total si
    # no hay selección). No es "el precio real" pero da referencia para el
    # descuento y para el 'Antes €X'.
    base = sum((Decimal(str(c.precio)) for c in fijos), Decimal("0"))
    if seleccionables:
        media_sel = (
            sum((Decimal(str(c.precio)) for c in seleccionables), Decimal("0"))
            / Decimal(len(seleccionables))
        )
        base += media_sel  # asume 1 selección por grupo
    combo = Product(
        nombre=nombre, precio=base, activo=True, es_combo=True,
        categoria_id=cat.id, canal_preparacion="almacen",
        tipo_entrega="inmediato", modalidad_entrega="ambas",
        vertical="producto",
        combo_precio_modo=modo,
        combo_precio_base=Decimal(str(precio_fijo)) if precio_fijo is not None else base,
        combo_descuento_pct=Decimal(str(descuento_pct)) if descuento_pct is not None else Decimal("0"),
        **ck,
    )
    db.session.add(combo)
    db.session.flush()
    db.session.add(Stock(
        producto_id=combo.id, cantidad=30,
        fecha_caducidad=date.today() + timedelta(days=730),
    ))
    grupos = {}
    for comp, sel, pred, grupo_seleccion, precio_extra in componentes:
        key = grupo_seleccion if sel else "Incluye"
        if key not in grupos:
            g = ComboGroup(
                combo_id=combo.id, nombre=key,
                orden=len(grupos) + 1,
                tipo=("seleccion" if sel else "fijo"),
                min_selecciones=(1 if sel else 0),
                max_selecciones=1,
                requerido=True,
            )
            db.session.add(g); db.session.flush()
            grupos[key] = g
        db.session.add(ComboItem(
            combo_id=combo.id, producto_id=comp.id,
            combo_group_id=grupos[key].id,
            cantidad=1, orden=0, activo=True, es_predeterminado=pred,
            precio_extra=Decimal(str(precio_extra)),
        ))
    return combo


def seed_retail():
    print("• Categorías RETAIL")
    ropa = _cat("Ropa", 10)
    zapatos = _cat("Zapatos", 11)
    bisuteria = _cat("Bisutería", 12)
    accesorios = _cat("Accesorios", 13)
    regalos = _cat("Regalos", 14)

    print("• Productos RETAIL — variedad")

    # ── ROPA ────────────────────────────────────────────────
    camiseta = _prod("Camiseta básica algodón", 15.90, ropa,
                     marca="Genérica", color="Blanco",
                     material="100% algodón peinado", genero="unisex", stock=40)
    _pres(camiseta, [("S", 0), ("M", 0), ("L", 0), ("XL", 1.50)])

    camiseta_negra = _prod("Camiseta oversize negra", 18.00, ropa,
                           marca="Genérica", color="Negro",
                           material="100% algodón", genero="unisex", stock=30)
    _pres(camiseta_negra, [("S", 0), ("M", 0), ("L", 0), ("XL", 2.00)])

    sudadera = _prod("Sudadera con capucha", 34.00, ropa,
                     marca="Genérica", color="Gris jaspeado",
                     material="80% algodón, 20% poliéster", genero="unisex", stock=25)
    _pres(sudadera, [("S", 0), ("M", 0), ("L", 0), ("XL", 3.00)])

    pantalon = _prod("Pantalón chino slim", 42.00, ropa,
                     marca="Genérica", color="Beige",
                     material="97% algodón, 3% elastano", genero="hombre", stock=20)
    _pres(pantalon, [("S", 0), ("M", 0), ("L", 0), ("XL", 2.00), ("XXL", 4.00)])

    vestido = _prod("Vestido midi floral", 45.00, ropa,
                    marca="Genérica", color="Azul flores",
                    material="Tejido ligero viscosa", genero="mujer", stock=15)
    _pres(vestido, [("XS", 0), ("S", 0), ("M", 0), ("L", 0)])

    # Prenda de temporada — programada con fecha
    _prod("Abrigo lana temporada invierno", 89.00, ropa,
          marca="Genérica", color="Camel", material="70% lana, 30% viscosa",
          genero="unisex", tipo_entrega="programado",
          fecha_llegada=date.today() + timedelta(days=30), stock=10)

    # Stock bajo (aparece badge "Quedan N")
    _prod("Camisa lino edición limitada", 55.00, ropa,
          marca="Genérica", color="Blanco crudo", material="100% lino",
          genero="unisex", stock=3)

    # ── ZAPATOS ─────────────────────────────────────────────
    zapatilla = _prod("Zapatilla urbana clásica", 55.00, zapatos,
                      marca="Genérica", color="Blanco",
                      material="Piel sintética", genero="unisex", stock=35)
    _pres(zapatilla, [(str(n), 0) for n in range(36, 46)])

    bota = _prod("Bota chelsea cuero", 78.00, zapatos,
                 marca="Genérica", color="Negro",
                 material="Cuero + goma", genero="mujer", stock=15)
    _pres(bota, [(str(n), 0) for n in range(36, 42)])

    sandalia = _prod("Sandalia plana verano", 32.00, zapatos,
                     marca="Genérica", color="Dorado",
                     material="Sintético", genero="mujer", stock=20)
    _pres(sandalia, [(str(n), 0) for n in range(36, 42)])

    running = _prod("Zapatilla running amortiguada", 65.00, zapatos,
                    marca="Genérica", color="Azul/Blanco",
                    material="Malla + EVA", genero="unisex", stock=25)
    _pres(running, [(str(n), 0) for n in range(38, 46)])

    # ── BISUTERÍA ───────────────────────────────────────────
    collar = _prod("Collar cadena plata 45cm", 25.00, bisuteria,
                   marca="Genérica", material="Plata 925")
    pendientes = _prod("Pendientes aro dorado pequeño", 15.00, bisuteria,
                       marca="Genérica", material="Baño de oro 18k")
    anillo = _prod("Anillo trenzado plata ajustable", 12.00, bisuteria,
                   marca="Genérica", material="Plata 925")
    _prod("Pulsera hilo con dijes", 8.00, bisuteria,
          marca="Genérica", color="Multicolor", material="Hilo + acero")

    # ── ACCESORIOS ──────────────────────────────────────────
    bolso = _prod("Bolso bandolera cuero", 65.00, accesorios,
                  marca="Genérica", color="Marrón",
                  material="Cuero italiano", genero="mujer", stock=12)
    _prod("Cinturón trenzado piel", 28.00, accesorios,
          marca="Genérica", color="Negro", material="100% piel",
          genero="hombre")
    _prod("Gafas de sol acetato UV400", 35.00, accesorios,
          marca="Genérica", color="Carey", material="Acetato",
          genero="unisex", stock=20)
    gorra = _prod("Gorra baseball ajustable", 18.00, accesorios,
                  marca="Genérica", color="Negro",
                  material="Algodón + poliéster", genero="unisex", stock=30)

    # ── REGALOS / CANJE ─────────────────────────────────────
    _prod("Vale regalo 10€", 0, regalos,
          marca="Regalo casa", solo_canje=True,
          canjeable_con_puntos=True, puntos_para_canje=500,
          modalidad_entrega="ambas")
    _prod("Vale regalo 25€", 0, regalos,
          marca="Regalo casa", solo_canje=True,
          canjeable_con_puntos=True, puntos_para_canje=1200,
          modalidad_entrega="ambas")
    # Producto canjeable con precio también
    caja_regalo = _prod("Caja regalo personalizada", 15.00, regalos,
                        marca="Regalo casa", material="Cartón + cinta",
                        canjeable_con_puntos=True, puntos_para_canje=350)

    # ── COMBOS COHERENTES ───────────────────────────────────
    print("• Combos retail")

    # 1) Pack look casual — fijos con 10% dto (Camiseta + Sudadera)
    _combo("Pack look casual (10% dto)", ropa, [
        (camiseta, False, True, None, 0),
        (sudadera, False, True, None, 0),
    ], descuento_pct=10)

    # 2) Pack joyería — 3 seleccionables con precio fijo
    _combo("Pack joyería 3 piezas (fijo €40)", bisuteria, [
        (collar, True, True, "Elige tu pieza", 0),
        (pendientes, True, False, "Elige tu pieza", 0),
        (anillo, True, False, "Elige tu pieza", 0),
    ], precio_fijo=40.00)

    # 3) Outfit completo (mixto): pantalón fijo + selección camiseta + selección zapato
    _combo("Outfit completo (15% dto)", ropa, [
        (pantalon, False, True, None, 0),  # fijo
        (camiseta, True, True, "Camiseta", 0),
        (camiseta_negra, True, False, "Camiseta", 0),
        (zapatilla, True, True, "Calzado", 0),
        (running, True, False, "Calzado", 10.00),  # running con recargo
    ], descuento_pct=15)

    # 4) Regalo cumpleaños — precio fijo bajo con bolso + collar + gafas seleccionable
    _combo("Regalo cumpleaños (fijo €75)", regalos, [
        (bolso, False, True, None, 0),  # fijo bolso
        (collar, True, True, "Añade una joya", 0),
        (pendientes, True, False, "Añade una joya", 0),
        (anillo, True, False, "Añade una joya", 0),
    ], precio_fijo=75.00)


def main():
    app = create_app()
    with app.app_context():
        # Limpiar retail previo (borra productos retail para reseed limpio)
        from sqlalchemy import text
        print("• Limpieza previa retail (preserva comida)")
        # Borrar en orden por FK
        for tabla, cond in [
            ("combo_items", "combo_id IN (SELECT id FROM products WHERE vertical='producto')"),
            ("combo_items", "producto_id IN (SELECT id FROM products WHERE vertical='producto')"),
            ("combo_groups", "combo_id IN (SELECT id FROM products WHERE vertical='producto')"),
            ("product_presentations", "producto_id IN (SELECT id FROM products WHERE vertical='producto')"),
            ("product_extra_options", "grupo_id IN (SELECT id FROM product_extra_groups WHERE producto_id IN (SELECT id FROM products WHERE vertical='producto'))"),
            ("product_extra_groups", "producto_id IN (SELECT id FROM products WHERE vertical='producto')"),
            ("stock", "producto_id IN (SELECT id FROM products WHERE vertical='producto')"),
            ("proveedor_productos", "producto_id IN (SELECT id FROM products WHERE vertical='producto')"),
            ("products", "vertical='producto'"),
        ]:
            db.session.execute(text(f"DELETE FROM {tabla} WHERE {cond}"))
        db.session.commit()

        seed_retail()
        db.session.commit()

        n_prod = Product.query.filter_by(vertical="producto").count()
        n_combo = Product.query.filter_by(vertical="producto", es_combo=True).count()
        n_pres = (db.session.query(ProductPresentation)
                  .join(Product, Product.id == ProductPresentation.producto_id)
                  .filter(Product.vertical == "producto").count())
        n_canje = Product.query.filter_by(vertical="producto",
                                          canjeable_con_puntos=True).count()
        print()
        print("=" * 60)
        print(f"✓ Retail sembrado (limpio):")
        print(f"  Productos retail: {n_prod} ({n_combo} combos)")
        print(f"  Presentaciones/tallas: {n_pres}")
        print(f"  Canjeables con puntos: {n_canje}")
        print(f"  Modo actual (TIPO_TIENDA): {SiteConfig.get('TIPO_TIENDA','comida')}")
        print("=" * 60)


if __name__ == "__main__":
    main()
