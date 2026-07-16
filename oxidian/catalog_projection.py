"""Proyección de solo lectura para renderizar el catálogo público.

Las relaciones históricas de :class:`Product` son ``lazy="dynamic"`` porque
los paneles administrativos necesitan poder filtrarlas. Consultarlas desde
cada tarjeta pública, sin embargo, generaba cientos de consultas N+1. Este
módulo reúne en bloque toda la información visual e inventariable que necesita
el menú sin cambiar el modelo transaccional usado por carrito y checkout.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from extensions import db
from models import (
    ComboItem,
    Product,
    ProductExtraGroup,
    ProductPresentation,
    Proveedor,
    ProveedorProducto,
    Review,
    Stock,
)


@dataclass(slots=True)
class CatalogProductView:
    """Datos ya resueltos de una tarjeta, indexados por ``Product.id``."""

    stock: int = 0
    available: bool = False
    has_extras: bool = False
    presentations: list = field(default_factory=list)
    combo_items: list = field(default_factory=list)
    rating: float = 0.0


def _origin_parts(origin):
    raw = str(origin or "propio").strip().lower()
    if raw == "propio":
        return "propio", None
    if raw.startswith("proveedor:"):
        try:
            provider_id = int(raw.split(":", 1)[1])
        except (TypeError, ValueError):
            return "", None
        if provider_id > 0:
            return f"proveedor:{provider_id}", provider_id
    return "", None


def build_catalog_projection(products, origin="propio"):
    """Carga en consultas acotadas todo lo necesario para el menú público.

    La disponibilidad replica las reglas de ``Product.disponible_para_venta``:
    origen, proveedor activo, productos programados, stock opcional y capacidad
    de componentes fijos/seleccionables de combos.
    """

    products = list(products or [])
    if not products:
        return {}

    origin_key, provider_id = _origin_parts(origin)
    product_ids = {product.id for product in products if product.id is not None}
    combo_ids = {product.id for product in products if product.id and product.es_combo}

    combo_rows = []
    if combo_ids:
        combo_rows = (
            ComboItem.query.options(
                joinedload(ComboItem.componente),
                joinedload(ComboItem.grupo),
            )
            .filter(ComboItem.combo_id.in_(combo_ids))
            .order_by(ComboItem.combo_id, ComboItem.orden, ComboItem.id)
            .all()
        )
    combo_items = defaultdict(list)
    component_ids = set()
    for item in combo_rows:
        combo_items[item.combo_id].append(item)
        if item.producto_id:
            component_ids.add(item.producto_id)

    inventory_ids = product_ids | component_ids
    stock_totals = {}
    stock_row_ids = set()
    if inventory_ids:
        stock_totals = {
            product_id: int(total or 0)
            for product_id, total in (
                db.session.query(Stock.producto_id, func.coalesce(func.sum(Stock.cantidad), 0))
                .filter(
                    Stock.producto_id.in_(inventory_ids),
                    or_(Stock.fecha_caducidad.is_(None), Stock.fecha_caducidad >= date.today()),
                )
                .group_by(Stock.producto_id)
                .all()
            )
        }
        stock_row_ids = {
            row[0]
            for row in db.session.query(Stock.producto_id)
            .filter(Stock.producto_id.in_(inventory_ids))
            .distinct()
            .all()
        }

    provider_rows = []
    if inventory_ids:
        provider_rows = (
            ProveedorProducto.query.filter(
                ProveedorProducto.producto_id.in_(inventory_ids),
                ProveedorProducto.activo.is_(True),
            ).all()
        )
    products_with_provider = {row.producto_id for row in provider_rows}
    selected_provider_stock = {
        row.producto_id: int(row.stock or 0)
        for row in provider_rows
        if provider_id is not None and row.proveedor_id == provider_id
    }
    provider_active = True
    if provider_id is not None:
        provider = db.session.get(Proveedor, provider_id)
        provider_active = bool(provider and provider.activo)

    extras_ids = {
        row[0]
        for row in db.session.query(ProductExtraGroup.producto_id)
        .filter(
            ProductExtraGroup.producto_id.in_(product_ids),
            ProductExtraGroup.activo.is_(True),
        )
        .distinct()
        .all()
    }
    presentations = defaultdict(list)
    for presentation in (
        ProductPresentation.query.filter(
            ProductPresentation.producto_id.in_(product_ids),
            ProductPresentation.activo.is_(True),
        )
        .order_by(ProductPresentation.producto_id, ProductPresentation.orden, ProductPresentation.id)
        .all()
    ):
        presentations[presentation.producto_id].append(presentation)

    ratings = {
        product_id: round(float(average), 1)
        for product_id, average in (
            db.session.query(Review.producto_id, func.avg(Review.calificacion))
            .filter(Review.producto_id.in_(product_ids), Review.aprobada.is_(True))
            .group_by(Review.producto_id)
            .all()
        )
    }

    def stock_for(product):
        if provider_id is not None:
            return selected_provider_stock.get(product.id, 0)
        return stock_totals.get(product.id, 0)

    def belongs(product):
        if not origin_key:
            return False
        if product.es_combo:
            return origin_key == product.origen_operativo_key
        if provider_id is not None:
            return product.id in selected_provider_stock
        return product.id in stock_row_ids or product.id not in products_with_provider

    def component_available(component):
        if not component or not component.activo:
            return False
        if (component.tipo_entrega or "inmediato") != "inmediato":
            return True
        if not bool(component.stock_mostrar_en_web):
            return True
        return stock_for(component) >= 1

    def combo_available(product, items):
        active_items = [item for item in items if item.activo]
        if not active_items:
            return False
        if (product.tipo_entrega or "inmediato") != "inmediato":
            return True
        selectable = defaultdict(list)
        for item in active_items:
            if item.es_seleccionable:
                selectable[item.grupo_seleccion or "Seleccion"].append(item)
            elif not component_available(item.componente):
                return False
        return all(
            any(component_available(option.componente) for option in options)
            for options in selectable.values()
        )

    def component_capacity(item):
        component = item.componente
        if not component or not component.activo or int(item.cantidad or 0) <= 0:
            return 0
        if (component.tipo_entrega or "inmediato") != "inmediato":
            return None
        available_stock = stock_for(component)
        if not bool(component.stock_mostrar_en_web):
            if provider_id is None or available_stock <= 0:
                return 999999
        return available_stock // max(1, int(item.cantidad or 1))

    def combo_stock(product, items):
        if not belongs(product):
            return 0
        active_items = [item for item in items if item.activo]
        if not active_items:
            return 0
        capacities = []
        selectable = defaultdict(list)
        for item in active_items:
            if item.es_seleccionable:
                selectable[item.grupo_seleccion or "Seleccion"].append(item)
                continue
            capacity = component_capacity(item)
            if capacity is not None:
                capacities.append(capacity)
        for options in selectable.values():
            max_selections = max(1, int(options[0].max_selecciones or 1))
            option_capacities = [component_capacity(item) for item in options]
            if any(capacity is None for capacity in option_capacities):
                continue
            group_capacity = sum(
                capacity for capacity in option_capacities
                if isinstance(capacity, int) and capacity > 0
            )
            capacities.append(group_capacity // max_selections if group_capacity > 0 else 0)
        return min(capacities) if capacities else 999999

    projection = {}
    for product in products:
        items = combo_items.get(product.id, [])
        stock = combo_stock(product, items) if product.es_combo else stock_for(product)
        available = bool(product.activo)
        available = available and bool(product.visible_ahora) and belongs(product) and provider_active
        if available:
            if product.es_combo:
                available = combo_available(product, items)
            elif (product.tipo_entrega or "inmediato") == "inmediato":
                if not bool(product.stock_mostrar_en_web):
                    available = not (provider_id is None and product.id in stock_row_ids) or stock >= 1
                else:
                    available = stock >= 1
        projection[product.id] = CatalogProductView(
            stock=stock,
            available=available,
            has_extras=product.id in extras_ids,
            presentations=presentations.get(product.id, []),
            combo_items=items,
            rating=ratings.get(product.id, 0.0),
        )
    return projection
