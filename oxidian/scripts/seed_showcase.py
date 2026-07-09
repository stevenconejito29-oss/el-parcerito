#!/usr/bin/env python3
"""
Seed SHOWCASE — un producto por cada característica del sistema, más combos
coherentes que ejercitan seleccionables, fijos, precio fijo, descuento
porcentual, extras y tamaños.

Sirve para:
- Probar visualmente cada tipo de producto en el catálogo público.
- Validar que el carrito respeta cada regla.
- Ejercitar el flujo de canje (solo canje de productos, nunca descuento).

Prerequisito: `wipe_generico.py` ejecutado antes (BD vacía + config placeholder).

Uso: docker exec oxidian python3 scripts/seed_showcase.py
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db
from models import (
    Product, Categoria, Stock,
    ComboGroup, ComboItem,
    ProductExtraGroup, ProductExtraOption,
    ProductPresentation,
    ZonaEntrega, SiteConfig,
)


def _cat(nombre, orden=0):
    c = Categoria(nombre=nombre, activo=True, orden=orden)
    db.session.add(c)
    db.session.flush()
    return c


def _prod(nombre, precio, cat, **kw):
    """Helper: crea producto + stock propio con lote lejano."""
    defaults = dict(
        activo=True,
        canal_preparacion="cocina",
        tipo_entrega="inmediato",
        modalidad_entrega="ambas",
        vertical="comida",
        stock_mostrar_en_web=False,
        canjeable_con_puntos=False,
        solo_canje=False,
    )
    defaults.update(kw)
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
        cantidad=100,
        fecha_caducidad=date.today() + timedelta(days=180),
    ))
    return p


def _pres(prod, tamaños):
    """tamaños: lista de tuplas (label, precio_extra_float)."""
    for i, (tam, extra) in enumerate(tamaños):
        db.session.add(ProductPresentation(
            producto_id=prod.id, tamaño=tam,
            precio_extra=Decimal(str(extra)),
            activo=True, orden=i,
        ))


def _extra_group(prod, nombre, min_sel, max_sel, opciones):
    """opciones: lista (label, precio_float, max_cant)."""
    g = ProductExtraGroup(
        producto_id=prod.id, nombre=nombre,
        min_selecciones=min_sel, max_selecciones=max_sel, activo=True,
    )
    db.session.add(g)
    db.session.flush()
    for i, (label, precio, max_c) in enumerate(opciones):
        db.session.add(ProductExtraOption(
            grupo_id=g.id, nombre=label,
            precio=Decimal(str(precio)),
            max_cantidad=max_c, orden=i, activo=True,
        ))
    return g


def _combo(nombre, cat, componentes, precio_fijo=None, descuento_pct=None):
    """componentes: lista de tuplas (producto, seleccionable, es_predeterminado, grupo_seleccion, precio_extra).
    - precio_fijo: si se pasa, el combo cuesta ese fijo.
    - descuento_pct: si se pasa (0-100), el combo aplica ese % sobre la suma.
    Solo uno de los dos debe usarse."""
    modo = "descuento_porcentaje" if descuento_pct is not None else "fijo"
    base = Decimal("0")
    for comp, _sel, _pred, _g, _pe in componentes:
        base += Decimal(str(comp.precio))
    combo = Product(
        nombre=nombre, precio=base, activo=True, es_combo=True,
        categoria_id=cat.id, canal_preparacion="cocina",
        tipo_entrega="inmediato", modalidad_entrega="ambas",
        vertical="comida",
        combo_precio_modo=modo,
        combo_precio_base=Decimal(str(precio_fijo)) if precio_fijo is not None else base,
        combo_descuento_pct=Decimal(str(descuento_pct)) if descuento_pct is not None else Decimal("0"),
    )
    db.session.add(combo)
    db.session.flush()
    db.session.add(Stock(producto_id=combo.id, cantidad=100,
                         fecha_caducidad=date.today() + timedelta(days=180)))
    grupos = {}
    # Agrupamos por grupo_seleccion; los "fijos" (sel=False) van a un grupo "Incluye" único.
    for comp, sel, pred, grupo_seleccion, precio_extra in componentes:
        key = grupo_seleccion if sel else "Incluye"
        if key not in grupos:
            g = ComboGroup(
                combo_id=combo.id, nombre=key, orden=len(grupos) + 1,
                tipo=("seleccion" if sel else "fijo"),
                min_selecciones=(1 if sel else 0),
                max_selecciones=1,
                requerido=(sel or not sel),
            )
            db.session.add(g); db.session.flush()
            grupos[key] = g
        db.session.add(ComboItem(
            combo_id=combo.id, producto_id=comp.id,
            combo_group_id=grupos[key].id,
            cantidad=1, orden=len(grupos[key].items.all()) if hasattr(grupos[key], "items") else 0,
            activo=True, es_predeterminado=pred,
            precio_extra=Decimal(str(precio_extra)),
        ))
    return combo


def crear_zonas():
    if ZonaEntrega.query.first():
        return
    print("• 2 zonas de entrega (placeholders)")
    db.session.add_all([
        ZonaEntrega(nombre="Centro", descripcion="Zona urbana principal",
                    es_epicentro=True, activo=True,
                    precio_envio=Decimal("0.00"), tiempo_estimado_min=20,
                    gratis_desde=Decimal("15.00"), orden=1),
        ZonaEntrega(nombre="Extrarradio", descripcion="Zonas periféricas",
                    es_epicentro=False, activo=True,
                    precio_envio=Decimal("2.00"), tiempo_estimado_min=35,
                    gratis_desde=Decimal("25.00"), orden=2),
    ])
    db.session.commit()


def seed_comida():
    print("• Categorías COMIDA")
    entrantes = _cat("Entrantes", 1)
    principales = _cat("Principales", 2)
    bebidas = _cat("Bebidas", 3)
    postres = _cat("Postres", 4)

    print("• Productos COMIDA — uno por característica")

    # Simple ambas modalidades
    croquetas = _prod("Croquetas (6 uds)", 5.90, entrantes)

    # Solo delivery
    _prod("Bocadillo mañanero (solo domicilio)", 4.50, entrantes,
          modalidad_entrega="delivery")

    # Solo recogida
    _prod("Café en local (solo recoger)", 1.30, bebidas,
          modalidad_entrega="recogida")

    # Hipoalergénico (comida sin alérgenos)
    _prod("Ensalada verde (sin alérgenos)", 6.00, entrantes,
          es_hipoalergenico=True)

    # Con alérgenos EU
    _prod("Tabla queso curado", 9.50, entrantes,
          alergenos_json='["gluten","lacteos"]', canal_preparacion="almacen")

    # Tamaños (presentaciones)
    hamburguesa = _prod("Hamburguesa clásica", 8.90, principales)
    _pres(hamburguesa, [("pequeño", -1.50), ("mediano", 0.00), ("grande", 2.50)])
    _extra_group(hamburguesa, "Salsas", 0, 3, [
        ("Mayonesa", 0.30, 1), ("Ketchup", 0.30, 1),
        ("Bravas casera", 0.50, 1), ("BBQ", 0.60, 1),
    ])
    _extra_group(hamburguesa, "Extras", 0, 4, [
        ("Extra queso", 1.00, 2), ("Extra bacon", 1.20, 2),
        ("Doble carne", 2.50, 1), ("Huevo", 1.00, 1),
    ])

    pizza_marg = _prod("Pizza margarita", 8.50, principales)
    _pres(pizza_marg, [("pequeño", -2.00), ("mediano", 0.00), ("grande", 3.00)])

    pizza_4q = _prod("Pizza 4 quesos", 10.50, principales)
    _pres(pizza_4q, [("pequeño", -2.00), ("mediano", 0.00), ("grande", 3.50)])

    # Bebidas simples
    coca = _prod("Coca-Cola 33cl", 2.20, bebidas, canal_preparacion="almacen")
    cerveza = _prod("Cerveza tirada", 2.50, bebidas,
                    canal_preparacion="almacen", modalidad_entrega="recogida")
    _pres(cerveza, [("mediano", 0.00), ("grande", 1.50)])
    agua = _prod("Agua mineral 50cl", 1.20, bebidas, canal_preparacion="almacen")

    bravas = _prod("Bravas caseras", 4.50, entrantes)
    ensaladilla = _prod("Ensaladilla rusa", 5.00, entrantes)

    # Postre canjeable con puntos (SÍ tiene precio en euros)
    tarta = _prod("Tarta de queso", 4.50, postres,
                  canjeable_con_puntos=True, puntos_para_canje=100)

    # solo_canje (SOLO puntos, precio = 0)
    _prod("Café gratis con puntos", 0, postres,
          solo_canje=True, canjeable_con_puntos=True, puntos_para_canje=200,
          modalidad_entrega="recogida", canal_preparacion="cocina")
    _prod("Postre sorpresa (canje)", 0, postres,
          solo_canje=True, canjeable_con_puntos=True, puntos_para_canje=300)

    # Programado (encargo con fecha)
    _prod("Paella para 2 (encargo 24h)", 24.00, principales,
          tipo_entrega="programado",
          fecha_llegada=date.today() + timedelta(days=2))
    _prod("Tarta celebración (encargo 48h)", 22.00, postres,
          tipo_entrega="programado",
          fecha_llegada=date.today() + timedelta(days=3),
          canjeable_con_puntos=True, puntos_para_canje=350)

    # Con stock visible
    _prod("Botella vino crianza", 12.00, bebidas,
          canal_preparacion="almacen", stock_mostrar_en_web=True)

    # Con horario de visibilidad (solo mediodía)
    from datetime import time as _t
    _prod("Menú del día (solo mediodía)", 12.00, principales,
          hora_inicio_visibilidad=_t(12, 30),
          hora_fin_visibilidad=_t(16, 0))

    # Grupo de pedido (frío)
    _prod("Helado artesano", 3.50, postres,
          grupo_pedido="Cadena de frío", canal_preparacion="almacen")

    # ── COMBOS coherentes ────────────────────────────────────
    print("• Combos coherentes")

    # 1. Combo precio FIJO — todos fijos (sin selección)
    _combo("Trío de tapas (precio fijo)", entrantes, [
        (croquetas, False, True, None, 0),
        (bravas, False, True, None, 0),
        (ensaladilla, False, True, None, 0),
    ], precio_fijo=13.50)

    # 2. Combo con DESCUENTO % — todos fijos
    _combo("Menú pizza + refresco (15% dto)", principales, [
        (pizza_marg, False, True, None, 0),
        (coca, False, True, None, 0),
    ], descuento_pct=15)

    # 3. Combo con SELECCIONABLES — elige 1 principal + 1 bebida (precio fijo)
    _combo("Menú a elegir", principales, [
        (hamburguesa, True, True, "Elige tu principal", 0),
        (pizza_marg, True, False, "Elige tu principal", 0),
        (pizza_4q, True, False, "Elige tu principal", 2.00),  # opción con extra
        (coca, True, True, "Elige tu bebida", 0),
        (agua, True, False, "Elige tu bebida", -0.50),
        (cerveza, True, False, "Elige tu bebida", 0.80),
    ], precio_fijo=12.90)

    # 4. Combo MIXTO — fijos + seleccionables + descuento porcentual
    _combo("Menú familiar (20% dto)", principales, [
        (bravas, False, True, None, 0),           # fijo
        (croquetas, False, True, None, 0),        # fijo
        (pizza_marg, True, True, "Pizza", 0),     # seleccionable
        (pizza_4q, True, False, "Pizza", 2.00),
        (coca, True, True, "Bebida", 0),          # seleccionable
        (cerveza, True, False, "Bebida", 0.80),
    ], descuento_pct=20)


def main():
    app = create_app()
    with app.app_context():
        crear_zonas()
        # Aseguramos comida activa
        SiteConfig.set("TIPO_TIENDA", "comida")
        seed_comida()
        db.session.commit()
        n_prod = Product.query.count()
        n_combo = Product.query.filter_by(es_combo=True).count()
        n_pres = ProductPresentation.query.count()
        n_ext = ProductExtraGroup.query.count()
        n_canje = Product.query.filter_by(canjeable_con_puntos=True).count()
        n_solocanje = Product.query.filter_by(solo_canje=True).count()
        print()
        print("=" * 60)
        print(f"✓ Showcase completo:")
        print(f"  Productos: {n_prod} ({n_combo} combos)")
        print(f"  Con presentaciones/tamaños: {n_pres}")
        print(f"  Grupos de extras: {n_ext}")
        print(f"  Canjeables con puntos: {n_canje} ({n_solocanje} solo_canje)")
        print("=" * 60)


if __name__ == "__main__":
    main()
