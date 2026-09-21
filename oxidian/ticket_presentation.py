"""Detalle compartido de combos impreso desde el snapshot del pedido."""


def combo_ticket_lines(item):
    combo = (item.get_metadata() or {}).get('combo') or {}
    lines = []

    def component_lines(component, label):
        quantity = component.get('cantidad', component.get('qty', 1))
        lines.append(f"{label}: {quantity}x {component.get('nombre', 'Componente')}")
        presentation = component.get('presentacion') or {}
        if presentation:
            lines.append(f"  Tamaño: {presentation.get('label') or presentation.get('tamaño') or ''}")
        for unit in component.get('unidades_cliente') or []:
            size = unit.get('presentacion') or {}
            flavor = unit.get('sabor') or {}
            details = [size.get('label') or size.get('tamaño'), flavor.get('nombre')]
            lines.append(f"  Unidad {unit.get('unidad', '')}: " + ' · '.join(str(d) for d in details if d))
        flavors = component.get('sabor_cliente') or []
        if flavors:
            lines.append('  Sabores: ' + ', '.join(f"{f.get('cantidad', 1)}x {f.get('nombre', '')}" for f in flavors))
        fixed = component.get('sabor_fijo') or component.get('fixed_flavor')
        if isinstance(fixed, dict) and fixed.get('nombre'):
            lines.append('  Sabor: ' + fixed['nombre'])
        notes = component.get('notas_preparacion')
        if notes:
            lines.append('  Preparación: ' + str(notes))
        allergens = component.get('alergenos') or []
        if allergens:
            lines.append('  Alérgenos: ' + ', '.join(str(a.get('label') or a.get('code') or '') if isinstance(a, dict) else str(a) for a in allergens))

    for component in combo.get('componentes') or []:
        component_lines(component, 'Incluido')
    for group in combo.get('selecciones') or []:
        for component in group.get('opciones') or []:
            component_lines(component, group.get('grupo') or 'Elección')
    return lines
