"""Reglas compartidas para sabores y personalizaciones de producto.

Web, POS y chatbot deben validar contra los mismos grupos activos. El cliente
envía identificadores; el servidor resuelve nombre, tipo y precio y congela ese
snapshot en el pedido.
"""
from __future__ import annotations

from models import ProductExtraGroup


def flavor_policy_for_presentation(producto, presentation=None) -> dict:
    """Resuelve una única política de sabores para cualquier canal de venta.

    Las reglas de la presentación prevalecen sobre las generales del grupo. La
    lista vacía con reglas activas significa que no hay sabores disponibles;
    sin reglas activas se heredan todas las opciones del producto.
    """
    flavor_groups = ProductExtraGroup.query.filter_by(
        producto_id=producto.id, tipo="sabor", activo=True
    ).order_by(ProductExtraGroup.orden, ProductExtraGroup.id).all()
    active_options = [
        option
        for group in flavor_groups
        for option in group.opciones.filter_by(activo=True).all()
    ]
    if not active_options:
        return {"enabled": False, "min": 0, "max": 0, "allowed_option_ids": []}

    default_min = sum(int(group.min_selecciones or 0) for group in flavor_groups)
    default_max = sum(int(group.max_selecciones or 1) for group in flavor_groups)
    if presentation and bool(presentation.flavor_rules_enabled):
        allowed = {
            option.id for option in presentation.allowed_flavor_options
            if option.activo and option.grupo and option.grupo.producto_id == producto.id
            and option.grupo.tipo == "sabor" and option.grupo.activo
        }
        minimum = max(0, int(presentation.flavor_min_selections or 0))
        maximum = max(0, int(presentation.flavor_max_selections or 0))
        return {
            "enabled": True,
            "min": min(minimum, maximum),
            "max": maximum,
            "allowed_option_ids": sorted(allowed),
        }
    return {
        "enabled": False,
        "min": max(0, default_min),
        "max": max(0, default_max),
        "allowed_option_ids": sorted(option.id for option in active_options),
    }


def product_option_catalog_payload(producto, presentation=None) -> list[dict]:
    flavor_policy = flavor_policy_for_presentation(producto, presentation)
    groups = ProductExtraGroup.query.filter_by(
        producto_id=producto.id, activo=True
    ).order_by(ProductExtraGroup.orden, ProductExtraGroup.id).all()
    return [
        {
            "id": group.id,
            "tipo": group.tipo or "extra",
            "nombre": group.nombre,
            "descripcion": group.descripcion or "",
            "min": (
                flavor_policy["min"] if group.tipo == "sabor" else int(group.min_selecciones or 0)
            ),
            "max": (
                flavor_policy["max"] if group.tipo == "sabor" else int(group.max_selecciones or 1)
            ),
            "opciones": [
                {
                    "id": option.id,
                    "nombre": option.nombre,
                    "precio_extra": 0.0 if group.tipo == "sabor" else option.precio_float,
                    "max_cantidad": (
                        flavor_policy["max"] if group.tipo == "sabor" else int(option.max_cantidad or 1)
                    ),
                }
                for option in group.opciones.filter_by(activo=True).all()
                if group.tipo != "sabor" or option.id in flavor_policy["allowed_option_ids"]
            ],
        }
        for group in groups
        if group.opciones.filter_by(activo=True).first()
    ]


def validate_product_option_selection(
    producto, selected, presentation=None
) -> tuple[dict, list[dict], float, str | None]:
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
    flavor_policy = flavor_policy_for_presentation(producto, presentation)
    valid_ids = {
        option.id
        for group in groups
        for option in group.opciones.filter_by(activo=True).all()
        if group.tipo != "sabor" or option.id in flavor_policy["allowed_option_ids"]
    }
    unknown = {int(option_id) for option_id in normalized} - valid_ids
    if unknown:
        return {}, [], 0.0, "Una opción seleccionada ya no está disponible para este producto."

    rows = []
    total_price = 0.0
    total_flavors = 0
    for group in groups:
        group_total = 0
        for option in group.opciones.filter_by(activo=True).all():
            qty = int(normalized.get(str(option.id), 0) or 0)
            if group.tipo == "sabor" and option.id not in flavor_policy["allowed_option_ids"]:
                continue
            own_max = flavor_policy["max"] if group.tipo == "sabor" else int(option.max_cantidad or 1)
            if qty < 0 or qty > own_max:
                return {}, [], 0.0, f"Cantidad inválida para «{option.nombre}»."
            if not qty:
                continue
            group_total += qty
            if group.tipo == "sabor":
                total_flavors += qty
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
        if group.tipo == "sabor":
            continue
        if group_total < int(group.min_selecciones or 0):
            return {}, [], 0.0, (
                f"Elige al menos {group.min_selecciones} opción(es) en «{group.nombre}»."
            )
        if group_total > int(group.max_selecciones or 1):
            return {}, [], 0.0, (
                f"Puedes elegir hasta {group.max_selecciones} opción(es) en «{group.nombre}»."
            )
    if total_flavors < flavor_policy["min"]:
        if flavor_policy["min"] == 1 and flavor_policy["max"] == 1:
            return {}, [], 0.0, "Elige un sabor para continuar."
        return {}, [], 0.0, (
            f"Distribuye {flavor_policy['min']} unidad(es) entre los sabores disponibles."
        )
    if total_flavors > flavor_policy["max"]:
        return {}, [], 0.0, (
            f"Puedes distribuir hasta {flavor_policy['max']} unidad(es) de sabor."
        )
    return normalized, rows, round(total_price, 2), None
