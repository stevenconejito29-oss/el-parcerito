#!/usr/bin/env python3
"""
Seed de matriz de productos para el entorno de TEST.

Genera 96 productos cubriendo la matriz ortogonal de:
- origen: propio, proveedor
- tipo_entrega: inmediato, programado
- modalidad_entrega: delivery, recogida, ambas
- puntos: none, canjeable, solo_canje
- vertical: comida, producto, ambos
- stock_mostrar_en_web: sí, no

Más 6 combos con distintas combinaciones de origen/modalidad.
Más 4 categorías, 2 proveedores (bares), 1 usuario cliente, extras de ejemplo.

Todo se etiqueta con nombre `TEST-*` para poder auditarlo y limpiarlo con:
    from models import Product, Categoria, Proveedor, User
    Product.query.filter(Product.nombre.like('TEST-%')).delete()
    ...

NO ejecutar contra la BD productiva. Este script asume oxidian_test.
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from itertools import product as iproduct

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db
from models import (
    Product,
    Categoria,
    Proveedor,
    ProveedorProducto,
    Stock,
    User,
    ComboGroup,
    ComboItem,
    ProductExtraGroup,
    ProductExtraOption,
)

ORIGENES = ["propio", "proveedor"]
TIPOS_ENTREGA = ["inmediato", "programado"]
MODALIDADES = ["delivery", "recogida", "ambas"]
PUNTOS = ["none", "canjeable", "solo_canje"]
VERTICALES = ["comida", "producto", "ambos"]
STOCK_VISIBLE = [True, False]


def guard_no_es_prod():
    """Sanity check: el nombre de la BD debe contener 'test'."""
    uri = str(db.engine.url)
    if "test" not in uri.lower():
        raise SystemExit(
            f"REHUSO: la URL de BD ({uri!r}) no parece de test. "
            "Este script solo debe correr contra oxidian_test."
        )


def wipe_previous():
    """Borra todo lo etiquetado TEST-*, respetando FKs."""
    print("• Wipe previo TEST-*")
    # Orden: dependencias primero
    for op in ProductExtraOption.query.filter(
        ProductExtraOption.nombre.like("TEST-%")
    ).all():
        db.session.delete(op)
    for gr in ProductExtraGroup.query.filter(
        ProductExtraGroup.nombre.like("TEST-%")
    ).all():
        db.session.delete(gr)
    for ci in ComboItem.query.all():
        if ci.combo and ci.combo.nombre.startswith("TEST-"):
            db.session.delete(ci)
    for cg in ComboGroup.query.filter(ComboGroup.nombre.like("TEST-%")).all():
        db.session.delete(cg)
    for pp in ProveedorProducto.query.all():
        p = db.session.get(Product, pp.producto_id) if pp.producto_id else None
        if p and (p.nombre or "").startswith("TEST-"):
            db.session.delete(pp)
    for s in Stock.query.all():
        p = db.session.get(Product, s.producto_id) if s.producto_id else None
        if p and (p.nombre or "").startswith("TEST-"):
            db.session.delete(s)
    for prod in Product.query.filter(Product.nombre.like("TEST-%")).all():
        db.session.delete(prod)
    for cat in Categoria.query.filter(Categoria.nombre.like("TEST-%")).all():
        db.session.delete(cat)
    for prov in Proveedor.query.filter(Proveedor.nombre.like("TEST-%")).all():
        db.session.delete(prov)
    for u in User.query.filter(User.email.like("test-%@test.local")).all():
        db.session.delete(u)
    db.session.commit()


def crear_categorias():
    print("• 4 categorías")
    cats = {}
    for name in ["TEST-Cocina", "TEST-Bar", "TEST-Almacén", "TEST-Programados"]:
        cat = Categoria(nombre=name, activo=True)
        db.session.add(cat)
        cats[name] = cat
    db.session.flush()
    return cats


def crear_proveedores():
    print("• 2 proveedores (bares)")
    provs = []
    for i, nombre in enumerate(["TEST-Bar-Uno", "TEST-Bar-Dos"], start=1):
        p = Proveedor(
            nombre=nombre,
            activo=True,
            telefono=f"3460000000{i}",
            direccion=f"Calle Test {i}",
            modelo_acuerdo="stock_proveedor",
            comision_pct=Decimal("15.00"),
        )
        db.session.add(p)
        provs.append(p)
    db.session.flush()
    return provs


def crear_cliente_test():
    print("• 1 cliente test")
    u = User(
        nombre="Cliente Test",
        email="test-cliente@test.local",
        telefono="34611111111",
        telefono_normalizado="34611111111",
        rol="cliente",
        activo=True,
        puntos=500,
    )
    u.set_password("test1234")
    db.session.add(u)
    db.session.flush()
    return u


def crear_matriz(cats, provs):
    print("• 96 productos sintéticos (matriz 2x2x3x3x3x2 podada a ortogonal 96)")
    productos = []
    tag = 0

    # Recorrido controlado — evitamos combinatoria total: seleccionamos 96
    # combinaciones representativas cubriendo cada eje al menos 2 veces.
    combos = list(iproduct(ORIGENES, TIPOS_ENTREGA, MODALIDADES, PUNTOS, VERTICALES, STOCK_VISIBLE))
    # 2 * 2 * 3 * 3 * 3 * 2 = 216 → tomamos 96 mezclando pares
    combos = combos[::216 // 96][:96]

    fecha_futuro = date.today() + timedelta(days=7)

    for (origen, tipo_ent, modalidad, pts, vertical, stock_vis) in combos:
        tag += 1
        nombre = (
            f"TEST-P{tag:02d}-{origen[:3]}-{tipo_ent[:3]}-"
            f"{modalidad[:3]}-{pts[:3]}-{vertical[:3]}-"
            f"{'sv' if stock_vis else 'no'}"
        )

        # solo_canje IMPLICA precio=0 y canjeable_con_puntos=True
        if pts == "solo_canje":
            precio = Decimal("0.00")
            canjeable = True
            solo_canje = True
            puntos_canje = 150
        elif pts == "canjeable":
            precio = Decimal("4.50")
            canjeable = True
            solo_canje = False
            puntos_canje = 100
        else:
            precio = Decimal("3.00") + Decimal(str(tag % 5))
            canjeable = False
            solo_canje = False
            puntos_canje = None

        cat = cats["TEST-Programados"] if tipo_ent == "programado" else (
            cats["TEST-Cocina"] if modalidad != "recogida" else cats["TEST-Bar"]
        )

        # Producto de proveedor: DEBE tener proveedor_despachador_id set,
        # o el sistema lo trata como propio-sin-stock y bloquea.
        prov = provs[tag % len(provs)] if origen == "proveedor" else None

        prod = Product(
            nombre=nombre,
            descripcion=f"Sintético {tag}: origen={origen}, entrega={tipo_ent}, "
                        f"mod={modalidad}, pts={pts}, vertical={vertical}",
            precio=precio,
            precio_costo=(precio * Decimal("0.6")).quantize(Decimal("0.01")),
            activo=True,
            canal_preparacion="cocina" if cat.nombre == "TEST-Cocina" else "almacen",
            tipo_entrega=tipo_ent,
            modalidad_entrega=modalidad,
            fecha_llegada=fecha_futuro if tipo_ent == "programado" else None,
            categoria_id=cat.id,
            vertical=vertical,
            canjeable_con_puntos=canjeable,
            puntos_para_canje=puntos_canje,
            solo_canje=solo_canje,
            stock_mostrar_en_web=stock_vis,
            proveedor_despachador_id=prov.id if prov else None,
        )
        db.session.add(prod)
        db.session.flush()

        # Stock propio siempre (para que aparezca en catálogo público).
        # El `proveedor_despachador_id` decide quién prepara/despacha, pero
        # el flujo web actual (`is_provider_flow_enabled()`=False) filtra por
        # Stock propio en `pertenece_a_origen("propio")`.
        db.session.add(Stock(
            producto_id=prod.id,
            cantidad=25 + (tag % 15),
            fecha_caducidad=fecha_futuro,
        ))
        if origen == "proveedor":
            db.session.add(ProveedorProducto(
                proveedor_id=prov.id,
                producto_id=prod.id,
                stock=25 + (tag % 15),
                precio_costo=(precio * Decimal("0.55")).quantize(Decimal("0.01")),
                activo=True,
            ))

        productos.append(prod)

    return productos


def crear_combos(cats, productos):
    print("• 6 combos representativos")
    # Elegimos productos propios/inmediatos/comida como componentes seguros
    componentes = [
        p for p in productos
        if p.tipo_entrega == "inmediato"
        and not p.solo_canje
        and p.vertical in ("comida", "ambos")
    ][:10]
    if len(componentes) < 3:
        print("  ⚠ pocos componentes seguros; salto combos")
        return []

    combos = []
    for i in range(1, 7):
        combo = Product(
            nombre=f"TEST-COMBO-{i:02d}",
            descripcion=f"Combo sintético {i}",
            precio=Decimal("9.90"),
            activo=True,
            es_combo=True,
            tipo_entrega="inmediato",
            modalidad_entrega="ambas",
            categoria_id=cats["TEST-Cocina"].id,
            vertical="comida",
        )
        db.session.add(combo)
        db.session.flush()

        grupo = ComboGroup(
            combo_id=combo.id,
            nombre=f"TEST-Grupo-{i}",
            orden=1,
        )
        db.session.add(grupo)
        db.session.flush()

        for j, comp in enumerate(componentes[:3]):
            db.session.add(ComboItem(
                combo_id=combo.id,
                producto_id=comp.id,
                combo_group_id=grupo.id,
                cantidad=1,
                orden=j,
                activo=True,
                es_predeterminado=(j == 0),
            ))
        combos.append(combo)
    return combos


def crear_extras(productos):
    print("• 2 grupos de extras (bebidas y salsas) aplicables a productos comida")
    targets = [
        p for p in productos
        if p.vertical in ("comida", "ambos")
        and p.tipo_entrega == "inmediato"
        and not p.solo_canje
    ][:5]
    if not targets:
        return
    for base in targets:
        for grupo_nombre, opciones in [
            ("TEST-Bebidas", [("Agua", 1.50), ("Refresco", 2.50), ("Cerveza", 3.00)]),
            ("TEST-Salsas", [("Mayonesa", 0.30), ("Bravas", 0.30), ("Alioli", 0.50)]),
        ]:
            grupo = ProductExtraGroup(
                producto_id=base.id,
                nombre=grupo_nombre,
                min_selecciones=0,
                max_selecciones=2,
                activo=True,
            )
            db.session.add(grupo)
            db.session.flush()
            for op_nombre, precio in opciones:
                db.session.add(ProductExtraOption(
                    grupo_id=grupo.id,
                    nombre=f"{grupo_nombre}-{op_nombre}",
                    precio=Decimal(str(precio)),
                    activo=True,
                    max_cantidad=2,
                ))


def main():
    app = create_app()
    with app.app_context():
        guard_no_es_prod()
        wipe_previous()
        cats = crear_categorias()
        provs = crear_proveedores()
        crear_cliente_test()
        productos = crear_matriz(cats, provs)
        combos = crear_combos(cats, productos)
        crear_extras(productos)
        db.session.commit()
        print()
        print("=" * 60)
        print(f"✓ Seed OK: {len(productos)} productos + {len(combos)} combos")
        print("  Cliente: test-cliente@test.local / test1234")
        print("  Categorías TEST-*: 4")
        print("  Proveedores TEST-*: 2")
        print("=" * 60)


if __name__ == "__main__":
    main()
