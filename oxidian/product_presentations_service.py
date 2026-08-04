"""Contrato compartido para presentaciones o tamaños de producto.

La selección se valida siempre contra filas activas del producto. Ningún canal
(web, POS o chatbot) debe confiar en el precio o la etiqueta enviados por el
cliente.
"""


def active_product_presentations(product):
    if not product or not hasattr(product, "presentaciones"):
        return []
    relation = product.presentaciones
    try:
        rows = relation.filter_by(activo=True).all()
    except AttributeError:
        rows = [row for row in relation if row.activo]
    return sorted(rows, key=lambda row: (int(row.orden or 0), row.id or 0))


def product_presentation_catalog_payload(product):
    """Serializa las opciones de tamaño que verá el cliente.

    Modelo mental: el producto SIEMPRE tiene un tamaño "Normal" al precio
    base — es lo que el admin definió como precio del producto. Cuando el
    admin activa variantes en admin, son ALTERNATIVAS al Normal, no lo
    reemplazan. Por eso, cuando hay ≥1 variante activa, prependemos un
    ítem sintético `id=0, tamaño="normal", label="Normal"` para que el
    cliente pueda elegir Normal explícitamente. Cuando no hay variantes,
    el payload queda vacío y el UI no muestra selector (el producto se
    vende directo al precio base — comportamiento actual).
    """
    from product_options_service import flavor_policy_for_presentation

    base_price = float(getattr(product, "precio_final", 0) or 0)
    payload = [
        {
            "id": row.id,
            "tamaño": row.tamaño,
            "label": row.label,
            "precio_extra": row.precio_extra_float,
            "precio_final": row.precio_final(base_price),
            "orden": int(row.orden or 0),
            "flavor_policy": flavor_policy_for_presentation(product, row),
        }
        for row in active_product_presentations(product)
    ]
    if payload:
        payload.insert(0, {
            "id": 0,
            "tamaño": "normal",
            "label": "Normal",
            "precio_extra": 0.0,
            "precio_final": base_price,
            "orden": -1,
            "flavor_policy": flavor_policy_for_presentation(product, None),
        })
    return payload


def validate_product_presentation_selection(product, raw_selection):
    """Devuelve ``(presentación, error)`` con pertenencia y actividad validadas.

    ``raw_selection`` puede ser el id numérico o el tamaño canónico. Cuando el
    producto no tiene presentaciones, una selección residual se ignora para
    conservar compatibilidad con sesiones antiguas.
    """
    rows = active_product_presentations(product)
    if not rows:
        return None, None

    raw = str(raw_selection or "").strip()
    if not raw:
        return None, f"Elige un tamaño para «{product.nombre}»."

    # Pseudo-opción "Normal" (id=0, tamaño="normal") = precio base del
    # producto, sin variante concreta. Emparejada con el prepend que hace
    # `product_presentation_catalog_payload` cuando hay ≥1 variante activa.
    if raw == "0" or raw.casefold() == "normal":
        return None, None

    selected = None
    try:
        selected_id = int(raw)
    except (TypeError, ValueError):
        selected_id = 0
    if selected_id > 0:
        selected = next((row for row in rows if row.id == selected_id), None)
    if selected is None:
        normalized = raw.casefold()
        selected = next(
            (row for row in rows if str(row.tamaño or "").strip().casefold() == normalized),
            None,
        )
    if selected is None:
        choices = ", ".join(row.label for row in rows)
        return None, f"El tamaño seleccionado no está disponible para «{product.nombre}». Elige: {choices}."
    return selected, None


def presentation_metadata(presentation):
    if not presentation:
        return None
    from product_options_service import flavor_policy_for_presentation

    policy = flavor_policy_for_presentation(presentation.producto, presentation)
    return {
        "id": presentation.id,
        "tamaño": presentation.tamaño,
        "label": presentation.label,
        "extra": presentation.precio_extra_float,
        "flavor_policy": {
            "min": policy["min"],
            "max": policy["max"],
            "custom": bool(presentation.flavor_rules_enabled),
        },
    }
