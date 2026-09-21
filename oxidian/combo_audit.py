"""Diagnóstico de composiciones guardadas; nunca modifica catálogo ni snapshots."""
from combo_validators import validate_combo_structure


def audit_combo(combo, items, groups):
    issues = []
    active = [item for item in items if item.activo]
    payload = [dict(prod_id=i.producto_id, cantidad=i.cantidad,
                    es_sel=i.es_seleccionable, grupo=i.grupo_seleccion,
                    max_sel=i.max_selecciones, es_predeterminado=i.es_predeterminado)
               for i in active]
    valid, error = validate_combo_structure(payload, combo.id, parent_vertical=combo.vertical)
    if not valid:
        issues.append(error)
    used_groups = set()
    for item in active:
        product = item.componente
        label = product.nombre if product else f'Componente #{item.id}'
        if not product or not product.activo or product.es_combo or product.solo_canje:
            issues.append(f'{label}: el producto base no está disponible como componente.')
        if product:
            if product.proveedor_despachador_id != combo.proveedor_despachador_id:
                issues.append(f'{label}: pertenece a otro origen de inventario.')
            if (product.tipo_entrega or 'inmediato') != (combo.tipo_entrega or 'inmediato'):
                issues.append(f'{label}: su tipo de entrega difiere del combo.')
            if (product.modalidad_entrega or 'ambas') not in {'ambas', combo.modalidad_entrega or 'ambas'}:
                issues.append(f'{label}: no admite todas las modalidades del combo.')
        group = item.grupo
        if not group or group.combo_id != combo.id:
            issues.append(f'{label}: falta un grupo propio del combo. Guarda la composición desde el editor completo.')
        else:
            used_groups.add(group.id)
            if (group.es_seleccion != bool(item.es_seleccionable)
                    or (item.es_seleccionable and (
                        group.nombre.strip().casefold() != (item.grupo_seleccion or '').strip().casefold()
                        or group.max_selecciones != item.max_selecciones))):
                issues.append(f'{label}: la opción y su grupo tienen reglas diferentes.')
        presentations = list(item.allowed_presentations)
        if item.presentacion:
            presentations.append(item.presentacion)
        if any(p.producto_id != item.producto_id or not p.activo for p in presentations):
            issues.append(f'{label}: contiene tamaños inactivos o de otro producto.')
        flavors = list(item.allowed_flavor_options)
        if item.fixed_flavor_option:
            flavors.append(item.fixed_flavor_option)
        if any(not f.activo or not f.grupo or f.grupo.producto_id != item.producto_id or not f.grupo.activo for f in flavors):
            issues.append(f'{label}: contiene sabores inactivos o de otro producto.')
    for group in groups:
        if group.id not in used_groups:
            issues.append(f'{group.nombre}: grupo sin componentes activos.')
    return list(dict.fromkeys(issues))
