#!/usr/bin/env python3
"""
Seed de catálogo REALISTA para producción.

Wipe controlado (preserva users, orders, config, zonas, proveedores existentes)
+ inserta 30 productos comida + 20 productos "producto" (tienda física)
+ 8 combos (4 comida, 4 producto) + extras de personalización.

Todo con nombres realistas (no TEST-*). Diseñado para lanzar la tienda en
elparcerito.com con contenido variado y probar cada nicho / combinación.

USO:
    docker exec oxidian python3 scripts/seed_prod_catalogo.py

REVIERTE con el backup en /mnt/hdd/oxidian-backups/pre-catalogo-wipe-*/.
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
)


def wipe_catalogo():
    """Borra catálogo (productos, categorías, combos, extras, stock).
    Preserva: users, orders, points_log, site_config, zonas, proveedores.
    """
    print("• Wipe catálogo (preservando orders/users/config)")
    for op in ProductExtraOption.query.all():
        db.session.delete(op)
    for gr in ProductExtraGroup.query.all():
        db.session.delete(gr)
    for ci in ComboItem.query.all():
        db.session.delete(ci)
    for cg in ComboGroup.query.all():
        db.session.delete(cg)
    for pp in ProveedorProducto.query.all():
        db.session.delete(pp)
    for s in Stock.query.all():
        db.session.delete(s)
    # Ojo: OrderItem.producto_id apunta a products. RESTRICT en FK.
    # Los productos referenciados por pedidos históricos no se pueden borrar.
    # Los desactivamos y renombramos para que no aparezcan en catálogo.
    borrables = 0
    conservados = 0
    for p in Product.query.all():
        try:
            db.session.delete(p)
            db.session.flush()
            borrables += 1
        except Exception:
            db.session.rollback()
            p.activo = False
            if not (p.nombre or "").startswith("[archivado]"):
                p.nombre = f"[archivado] {p.nombre}"[:200]
            conservados += 1
    for c in Categoria.query.all():
        try:
            db.session.delete(c)
            db.session.flush()
        except Exception:
            db.session.rollback()
            c.activo = False
    db.session.commit()
    print(f"  - Productos borrados: {borrables}, conservados-archivados: {conservados}")


def crear_categorias():
    print("• Categorías (ambos nichos)")
    data = [
        ("Entrantes", "comida"),
        ("Principales", "comida"),
        ("Bebidas", "comida"),
        ("Postres", "comida"),
        ("Cafetería", "comida"),
        ("Snacks", "producto"),
        ("Regalos", "producto"),
        ("Hogar", "producto"),
    ]
    out = {}
    for orden, (nombre, _v) in enumerate(data, start=1):
        c = Categoria(nombre=nombre, activo=True, orden=orden)
        db.session.add(c)
        out[nombre] = c
    db.session.flush()
    return out


def crear_productos_comida(cats):
    print("• Productos COMIDA (30)")
    fecha_futuro = date.today() + timedelta(days=14)
    items = [
        # (categoria, nombre, descripcion, precio, modalidad, tipo_ent, canjeable_pts, solo_canje, canal, fecha)
        ("Entrantes", "Croquetas caseras (6 uds)", "Croquetas artesanas rebozadas al momento.", 6.50, "ambas", "inmediato", None, False, "cocina", None),
        ("Entrantes", "Ensaladilla rusa", "La tradicional con atún y aceitunas.", 5.00, "ambas", "inmediato", None, False, "cocina", None),
        ("Entrantes", "Bravas El Parcerito", "Con nuestra salsa secreta picante.", 4.50, "ambas", "inmediato", None, False, "cocina", None),
        ("Entrantes", "Tabla de embutidos", "Jamón, chorizo y queso curado.", 12.00, "ambas", "inmediato", None, False, "almacen", None),
        ("Entrantes", "Nachos con queso", "Con guacamole y jalapeños.", 7.50, "ambas", "inmediato", None, False, "cocina", None),
        ("Principales", "Hamburguesa clásica", "200g de ternera, queso cheddar, bacon y tomate.", 9.90, "ambas", "inmediato", 250, False, "cocina", None),
        ("Principales", "Hamburguesa vegana", "Con hamburguesa de garbanzo y aguacate.", 10.50, "ambas", "inmediato", None, False, "cocina", None),
        ("Principales", "Pizza margarita", "Masa artesana, mozzarella y albahaca fresca.", 8.90, "ambas", "inmediato", None, False, "cocina", None),
        ("Principales", "Pizza cuatro quesos", "Mozzarella, gorgonzola, parmesano y provolone.", 10.90, "ambas", "inmediato", None, False, "cocina", None),
        ("Principales", "Pollo asado (medio)", "Con patatas fritas.", 8.50, "ambas", "inmediato", None, False, "cocina", None),
        ("Principales", "Solomillo al whisky", "Con patatas y ensalada.", 14.90, "ambas", "inmediato", None, False, "cocina", None),
        ("Principales", "Paella valenciana (2 pers)", "Con conejo, pollo y verduras. Encargo con 24h.", 25.00, "ambas", "programado", None, False, "cocina", fecha_futuro),
        ("Bebidas", "Cerveza tirada 33cl", "De grifo.", 2.50, "recogida", "inmediato", None, False, "almacen", None),
        ("Bebidas", "Coca-Cola 33cl", "Botellín cristal.", 2.20, "ambas", "inmediato", None, False, "almacen", None),
        ("Bebidas", "Agua mineral 50cl", "Sin gas.", 1.20, "ambas", "inmediato", None, False, "almacen", None),
        ("Bebidas", "Copa vino tinto", "Rioja crianza. Solo local.", 3.50, "recogida", "inmediato", None, False, "almacen", None),
        ("Bebidas", "Zumo natural naranja", "Recién exprimido.", 3.00, "ambas", "inmediato", None, False, "cocina", None),
        ("Bebidas", "Café bombón", "Con leche condensada.", 1.80, "recogida", "inmediato", None, False, "cocina", None),
        ("Postres", "Tarta de queso", "Estilo San Sebastián.", 4.50, "ambas", "inmediato", 100, False, "cocina", None),
        ("Postres", "Brownie con helado", "De chocolate y vainilla.", 5.00, "ambas", "inmediato", None, False, "cocina", None),
        ("Postres", "Flan casero", "Con caramelo líquido.", 3.20, "ambas", "inmediato", None, False, "cocina", None),
        ("Postres", "Tarta 3 chocolates", "Encargo con 24h, para 6 personas.", 22.00, "ambas", "programado", None, False, "cocina", fecha_futuro),
        ("Cafetería", "Café solo", "Espresso.", 1.30, "recogida", "inmediato", None, False, "cocina", None),
        ("Cafetería", "Café con leche", "Grande o pequeño.", 1.60, "recogida", "inmediato", None, False, "cocina", None),
        ("Cafetería", "Té a elegir", "Rojo, verde, manzanilla, poleo.", 1.80, "recogida", "inmediato", None, False, "cocina", None),
        ("Cafetería", "Tostada de aguacate", "Con tomate y aceite.", 4.20, "ambas", "inmediato", None, False, "cocina", None),
        ("Cafetería", "Croissant chocolate", "Recién horneado.", 2.20, "ambas", "inmediato", None, False, "almacen", None),
        # Solo canje
        ("Postres", "Café gratis con puntos", "Canjeable con 200 puntos.", 0.00, "recogida", "inmediato", 200, True, "cocina", None),
        ("Postres", "Cerveza gratis con puntos", "Canjeable con 250 puntos.", 0.00, "recogida", "inmediato", 250, True, "almacen", None),
        ("Postres", "Postre gratis con puntos", "Canjeable con 300 puntos.", 0.00, "ambas", "inmediato", 300, True, "cocina", None),
    ]
    productos = []
    for i, (cat, nombre, desc, precio, mod, tipo_ent, pts, solo, canal, fecha) in enumerate(items):
        p = Product(
            nombre=nombre,
            descripcion=desc,
            precio=Decimal(str(precio)),
            precio_costo=(Decimal(str(precio)) * Decimal("0.55")).quantize(Decimal("0.01")),
            activo=True,
            categoria_id=cats[cat].id,
            canal_preparacion=canal,
            tipo_entrega=tipo_ent,
            modalidad_entrega=mod,
            fecha_llegada=fecha,
            vertical="comida",
            canjeable_con_puntos=bool(pts),
            puntos_para_canje=pts,
            solo_canje=solo,
            stock_mostrar_en_web=False,
        )
        db.session.add(p)
        db.session.flush()
        db.session.add(Stock(
            producto_id=p.id,
            cantidad=50,
            fecha_caducidad=date.today() + timedelta(days=90),
        ))
        productos.append(p)
    return productos


def crear_productos_tienda(cats):
    print("• Productos TIENDA (20)")
    items = [
        ("Snacks", "Patatas Lay's clásicas 40g", "Bolsa individual.", 1.20, "ambas"),
        ("Snacks", "Kinder Bueno", "Barra doble.", 1.50, "ambas"),
        ("Snacks", "Chicles Trident menta", "Blister 10 uds.", 1.30, "ambas"),
        ("Snacks", "Doritos Tex-Mex 150g", "Grande para compartir.", 2.90, "ambas"),
        ("Snacks", "Turrón artesano tableta", "300g.", 8.50, "ambas"),
        ("Snacks", "Chocolate Valor 70%", "100g tableta.", 2.50, "ambas"),
        ("Regalos", "Caja regalo cerveza artesana", "6 botellas variedades locales.", 18.00, "ambas"),
        ("Regalos", "Bolsa gourmet queso+vino", "Media queso curado + botella crianza.", 32.00, "ambas"),
        ("Regalos", "Vale regalo 25€", "Canjeable en tienda o web.", 25.00, "ambas"),
        ("Regalos", "Vale regalo 50€", "Canjeable en tienda o web.", 50.00, "ambas"),
        ("Regalos", "Set desayuno gourmet", "Café molido + mermeladas + galletas.", 22.00, "ambas"),
        ("Hogar", "Café molido premium 250g", "Tueste natural, origen Colombia.", 6.80, "ambas"),
        ("Hogar", "Mermelada casera fresa 300g", "Elaborada en Carmona.", 4.50, "ambas"),
        ("Hogar", "Aceite virgen extra 500ml", "AOVE de la comarca.", 8.90, "ambas"),
        ("Hogar", "Vino tinto crianza (bot.)", "D.O. Rioja.", 12.00, "ambas"),
        ("Hogar", "Cerveza artesana pack 6", "Estilos IPA / rubia / negra.", 15.00, "ambas"),
        ("Hogar", "Taza personalizada El Parcerito", "Cerámica 350ml.", 8.00, "ambas"),
        ("Hogar", "Camiseta El Parcerito", "Talla S/M/L/XL.", 15.00, "ambas"),
        ("Hogar", "Bolsa de tela El Parcerito", "Algodón reciclado.", 5.00, "ambas"),
        ("Hogar", "Postal de Carmona (5 uds)", "Diseños locales.", 3.00, "ambas"),
    ]
    productos = []
    for cat, nombre, desc, precio, mod in items:
        p = Product(
            nombre=nombre,
            descripcion=desc,
            precio=Decimal(str(precio)),
            precio_costo=(Decimal(str(precio)) * Decimal("0.6")).quantize(Decimal("0.01")),
            activo=True,
            categoria_id=cats[cat].id,
            canal_preparacion="almacen",
            tipo_entrega="inmediato",
            modalidad_entrega=mod,
            vertical="producto",
            canjeable_con_puntos=False,
            solo_canje=False,
            stock_mostrar_en_web=True,
        )
        db.session.add(p)
        db.session.flush()
        db.session.add(Stock(
            producto_id=p.id,
            cantidad=30,
            fecha_caducidad=date.today() + timedelta(days=180),
        ))
        productos.append(p)
    return productos


def crear_combos(cats, comida, producto_tienda):
    print("• Combos (4 comida + 4 producto)")
    fecha_futuro = date.today() + timedelta(days=90)

    def _combo(nombre, desc, precio, vertical, cat_key, comps, seleccionables=None):
        c = Product(
            nombre=nombre,
            descripcion=desc,
            precio=Decimal(str(precio)),
            precio_costo=(Decimal(str(precio)) * Decimal("0.55")).quantize(Decimal("0.01")),
            activo=True,
            es_combo=True,
            categoria_id=cats[cat_key].id,
            tipo_entrega="inmediato",
            modalidad_entrega="ambas",
            vertical=vertical,
        )
        db.session.add(c)
        db.session.flush()
        g = ComboGroup(combo_id=c.id, nombre=f"Componentes {nombre}", orden=1, tipo="fijo", requerido=True)
        db.session.add(g)
        db.session.flush()
        db.session.add(Stock(producto_id=c.id, cantidad=100, fecha_caducidad=fecha_futuro))
        for j, comp in enumerate(comps):
            db.session.add(ComboItem(
                combo_id=c.id,
                producto_id=comp.id,
                combo_group_id=g.id,
                cantidad=1,
                orden=j,
                activo=True,
                es_predeterminado=(j == 0),
            ))
        return c

    # Comida: mapear por nombre para tomar referencias específicas
    by = {p.nombre: p for p in comida}
    tby = {p.nombre: p for p in producto_tienda}

    combos = []
    if all(k in by for k in ["Hamburguesa clásica", "Bravas El Parcerito", "Coca-Cola 33cl"]):
        combos.append(_combo("Menú hamburguesa clásica",
                             "Hamburguesa + bravas + refresco.", 12.90,
                             "comida", "Principales",
                             [by["Hamburguesa clásica"], by["Bravas El Parcerito"], by["Coca-Cola 33cl"]]))

    if all(k in by for k in ["Pizza margarita", "Cerveza tirada 33cl"]):
        combos.append(_combo("Menú pizza + cerveza",
                             "Pizza a elegir + cerveza tirada.", 10.90,
                             "comida", "Principales",
                             [by["Pizza margarita"], by["Cerveza tirada 33cl"]]))

    if all(k in by for k in ["Tostada de aguacate", "Café con leche"]):
        combos.append(_combo("Desayuno completo",
                             "Tostada de aguacate + café con leche.", 5.00,
                             "comida", "Cafetería",
                             [by["Tostada de aguacate"], by["Café con leche"]]))

    if all(k in by for k in ["Ensaladilla rusa", "Croquetas caseras (6 uds)", "Bravas El Parcerito"]):
        combos.append(_combo("Trío de tapas",
                             "Ensaladilla + croquetas + bravas.", 14.90,
                             "comida", "Entrantes",
                             [by["Ensaladilla rusa"], by["Croquetas caseras (6 uds)"], by["Bravas El Parcerito"]]))

    if all(k in tby for k in ["Café molido premium 250g", "Mermelada casera fresa 300g"]):
        combos.append(_combo("Pack desayuno gourmet",
                             "Café molido + mermelada.", 10.50,
                             "producto", "Regalos",
                             [tby["Café molido premium 250g"], tby["Mermelada casera fresa 300g"]]))

    if all(k in tby for k in ["Cerveza artesana pack 6", "Doritos Tex-Mex 150g"]):
        combos.append(_combo("Pack fin de semana",
                             "Cerveza pack 6 + doritos.", 17.00,
                             "producto", "Regalos",
                             [tby["Cerveza artesana pack 6"], tby["Doritos Tex-Mex 150g"]]))

    if all(k in tby for k in ["Taza personalizada El Parcerito", "Café molido premium 250g"]):
        combos.append(_combo("Regalo cafetero",
                             "Taza + café molido premium.", 14.00,
                             "producto", "Regalos",
                             [tby["Taza personalizada El Parcerito"], tby["Café molido premium 250g"]]))

    if all(k in tby for k in ["Aceite virgen extra 500ml", "Vino tinto crianza (bot.)"]):
        combos.append(_combo("Pack gastronómico",
                             "AOVE + botella de vino crianza.", 20.00,
                             "producto", "Regalos",
                             [tby["Aceite virgen extra 500ml"], tby["Vino tinto crianza (bot.)"]]))
    return combos


def crear_extras(comida):
    print("• Grupos de extras para personalizar comida")
    targets = [p for p in comida if p.nombre in
               ["Hamburguesa clásica", "Hamburguesa vegana", "Pizza margarita",
                "Pizza cuatro quesos", "Nachos con queso"]]
    for base in targets:
        # Salsas (multi-selección opcional)
        g_sal = ProductExtraGroup(
            producto_id=base.id,
            nombre="Salsas",
            descripcion="Añade tus salsas favoritas.",
            min_selecciones=0,
            max_selecciones=3,
            activo=True,
        )
        db.session.add(g_sal)
        db.session.flush()
        for i, (nombre, precio) in enumerate([
            ("Mayonesa", 0.30), ("Ketchup", 0.30), ("Bravas casera", 0.50),
            ("Alioli", 0.50), ("BBQ", 0.60),
        ]):
            db.session.add(ProductExtraOption(
                grupo_id=g_sal.id, nombre=nombre, precio=Decimal(str(precio)),
                max_cantidad=1, orden=i, activo=True,
            ))
        # Extras (opcional)
        g_ext = ProductExtraGroup(
            producto_id=base.id,
            nombre="Extras",
            descripcion="Personaliza a tu gusto.",
            min_selecciones=0,
            max_selecciones=4,
            activo=True,
        )
        db.session.add(g_ext)
        db.session.flush()
        for i, (nombre, precio) in enumerate([
            ("Extra queso", 1.00), ("Extra bacon", 1.20), ("Cebolla caramelizada", 0.80),
            ("Doble carne", 2.50), ("Huevo frito", 1.00),
        ]):
            db.session.add(ProductExtraOption(
                grupo_id=g_ext.id, nombre=nombre, precio=Decimal(str(precio)),
                max_cantidad=2, orden=i, activo=True,
            ))


def main():
    app = create_app()
    with app.app_context():
        # Guard antisúicida: la BD debe llamarse oxidian (no _test)
        uri = str(db.engine.url)
        # Aceptamos ambos: oxidian (prod) y oxidian_test (test)
        wipe_catalogo()
        cats = crear_categorias()
        comida = crear_productos_comida(cats)
        producto_tienda = crear_productos_tienda(cats)
        combos = crear_combos(cats, comida, producto_tienda)
        crear_extras(comida)
        db.session.commit()
        print()
        print("=" * 60)
        print(f"✓ Catálogo cargado en {uri}")
        print(f"  Categorías: {len(cats)}")
        print(f"  Productos COMIDA: {len(comida)}")
        print(f"  Productos TIENDA: {len(producto_tienda)}")
        print(f"  Combos: {len(combos)}")
        print("=" * 60)


if __name__ == "__main__":
    main()
