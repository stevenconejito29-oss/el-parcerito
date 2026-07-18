"""Reglas compartidas para sabores y personalizaciones de producto.

Web, POS y chatbot deben validar contra los mismos grupos activos. El cliente
envía identificadores; el servidor resuelve nombre, tipo y precio y congela ese
snapshot en el pedido.
"""
from __future__ import annotations

from models import ProductExtraGroup


def product_option_catalog_payload(producto) -> list[dict]:
    groups = ProductExtraGroup.query.filter_by(
        producto_id=producto.id, activo=True
    ).order_by(ProductExtraGroup.orden, ProductExtraGroup.id).all()
    return [
        {
            "id": group.id,
            "tipo": group.tipo or "extra",
            "nombre": group.nombre,
            "descripcion": group.descripcion or "",
            "min": int(group.min_selecciones or 0),
            "max": int(group.max_selecciones or 1),
            "opciones": [
                {
                    "id": option.id,
                    "nombre": option.nombre,
                    "precio_extra": 0.0 if group.tipo == "sabor" else option.precio_float,
                    "max_cantidad": 1 if group.tipo == "sabor" else int(option.max_cantidad or 1),
                }
                for option in group.opciones.filter_by(activo=True).all()
            ],
        }
        for group in groups
        if group.opciones.filter_by(activo=True).first()
    ]


def validate_product_option_selection(producto, selected) -> tuple[dict, list[dict], float, str | None]:
    """Valida selección completa y devuelve datos normalizados y snapshot."""
    raw_selected = selected if isinstance(selected, dict) else {}
    normalized = {}
    for raw_id, raw_qty in raw_selected.items():
        try:
            option_id, qty = int(raw_id), int(raw_qty)
        except (TypeError, ValueError):
            return {}, [], 0.0, "La selección de personalización no tiene un formato válido."
        if option_id <= 0 or qty < 0:
            return {}, [], 0.0, "La selección de personalización no tiene un formato válido."
        if qty:
            normalized[str(option_id)] = qty

    groups = ProductExtraGroup.query.filter_by(
        producto_id=producto.id, activo=True
    ).order_by(ProductExtraGroup.orden, ProductExtraGroup.id).all()
    valid_ids = {
        option.id
        for group in groups
        for option in group.opciones.filter_by(activo=True).all()
    }
    unknown = {int(option_id) for option_id in normalized} - valid_ids
    if unknown:
        return {}, [], 0.0, "Una opción seleccionada ya no está disponible para este producto."

    rows = []
    total_price = 0.0
    for group in groups:
        group_total = 0
        for option in group.opciones.filter_by(activo=True).all():
            qty = int(normalized.get(str(option.id), 0) or 0)
            own_max = 1 if group.tipo == "sabor" else int(option.max_cantidad or 1)
            if qty < 0 or qty > own_max:
                return {}, [], 0.0, f"Cantidad inválida para «{option.nombre}»."
            if not qty:
                continue
            group_total += qty
            unit_price = 0.0 if group.tipo == "sabor" else option.precio_float
            amount = round(unit_price * qty, 2)
            total_price += amount
            rows.append({
                "id": option.id,
                "grupo": group.nombre,
                "tipo": group.tipo or "extra",
                "nombre": option.nombre,
                "cantidad": qty,
                "precio_unit": unit_price,
                "subtotal": amount,
            })
        if group_total < int(group.min_selecciones or 0):
            if group.tipo == "sabor":
                return {}, [], 0.0, f"Elige un sabor en «{group.nombre}»."
            return {}, [], 0.0, (
                f"Elige al menos {group.min_selecciones} opción(es) en «{group.nombre}»."
            )
        if group_total > int(group.max_selecciones or 1):
            return {}, [], 0.0, (
                f"Puedes elegir hasta {group.max_selecciones} opción(es) en «{group.nombre}»."
            )
    return normalized, rows, round(total_price, 2), None
