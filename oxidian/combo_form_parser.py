"""Parser tipado del formulario del builder de combos.

Fuente única de verdad para admin y socio_producto. Convierte los arrays
paralelos del ``request.form`` en objetos ``ComponenteInput`` validados o
levanta ``ComboParseError`` con el mensaje amigable del primer problema.

Reglas mantenidas idénticas al admin (`routes/admin.py:nuevo_combo`) para no
romper el contrato de UX ni tests que dependan de los mensajes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

from combo_validators import (
    ComboLimits,
    validate_component_quantity,
    validate_selections_per_group,
    validate_group_name,
    validate_parallel_arrays,
    validate_combo_structure,
)


class ComboParseError(ValueError):
    """Error de parseo/validación del form de combos con mensaje display-ready."""


# Sentinela distinguible de ``None`` (owner==None significa stock propio, que
# es un caso válido a enforce).
_UNSET = object()


@dataclass
class ComponenteInput:
    """Representación tipada de un componente del builder tras el parseo.

    Los campos ``_allowed_flavor_option_ids`` y ``_allowed_presentation_ids``
    son listas de IDs ya validadas contra el producto; el builder los usa para
    poblar las relaciones M2M ``allowed_flavor_options`` y
    ``allowed_presentations`` respectivamente.
    """

    producto_id: int
    producto: object                    # instancia Product resuelta
    cantidad: int
    es_seleccionable: bool
    grupo: Optional[str]
    max_selecciones: int
    es_default: bool = False
    precio_extra: Decimal = field(default_factory=lambda: Decimal("0.00"))
    nota_preparacion: str = ""
    presentation_mode: str = "fijo"      # "fijo" | "cliente_elige"
    presentation: object = None           # ProductPresentation resuelta o None
    presentation_id: Optional[int] = None
    allowed_presentation_ids: list = field(default_factory=list)
    flavor_mode: str = "sin_sabor"       # "sin_sabor" | "fijo" | "cliente_elige"
    fixed_flavor_option_id: Optional[int] = None
    allowed_flavor_option_ids: list = field(default_factory=list)
    group_uid: str = ""
    orden: int = 0

    # ── Snapshot de precio unitario para el pricing del combo ──
    precio_unit: float = 0.0


def _money(raw) -> Decimal:
    try:
        val = Decimal(str(raw or "0").replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ComboParseError("El suplemento no es un número válido.")
    if not val.is_finite() or val < 0:
        raise ComboParseError("El suplemento no puede ser negativo.")
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _to_int(raw, default=None):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError, AttributeError):
        return default


def _parse_id_list(raw) -> Optional[list]:
    """Convierte un valor JSON de array a lista de ints, o None si es inválido.

    Un string vacío devuelve ``[]``. Retorna ``None`` cuando el JSON no es lista
    o no parsea; el caller decide el mensaje concreto.
    """
    if raw is None or raw == "":
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    out = []
    for v in data:
        if isinstance(v, (int, str)) and str(v).lstrip("-").isdigit():
            try:
                out.append(int(v))
            except (TypeError, ValueError):
                return None
        else:
            return None
    return out


def parse_componentes(
    form,
    *,
    productos_permitidos: dict,
    group_defs: Optional[dict] = None,
    parent_vertical: Optional[str] = None,
    combo_id: Optional[int] = None,
    enforce_owner_id=_UNSET,
) -> list:
    """Convierte los arrays del form en ``ComponenteInput`` validados.

    ``productos_permitidos`` mapea ``producto_id -> Product`` y restringe el
    catálogo aceptado. ``group_defs`` viene de ``_combo_groups_payload_from_form``
    cuando el UI declara grupos con uid. ``parent_vertical`` alimenta la
    restricción de retail (bundle todo-fijo). ``enforce_owner_id`` obliga a que
    cada componente pertenezca a ese ``proveedor_despachador_id`` (None = stock
    propio) — usado por admin para blindar el origen del combo.

    En caso de error levanta ``ComboParseError`` con el mensaje del primer
    problema, con prefijo "Componente N: " cuando aplica (matching admin UX).
    """
    from extensions import db
    from models import ProductExtraGroup, ProductExtraOption, ProductPresentation
    from product_presentations_service import validate_product_presentation_selection
    from product_options_service import validate_combo_component_flavors

    group_defs = group_defs or {}

    prod_ids = form.getlist("comp_prod_id") or []
    cantidades = form.getlist("comp_cantidad") or []
    tipos = form.getlist("comp_tipo") or []
    grupos = form.getlist("comp_grupo") or []
    max_sels = form.getlist("comp_max_sel") or []
    extras = form.getlist("comp_precio_extra") or []
    defaults = form.getlist("comp_default") or []
    notas_prep = form.getlist("comp_notas_preparacion") or []
    presentation_ids = form.getlist("comp_presentation_id") or []
    permite_sabores = form.getlist("comp_permite_sabor") or []
    flavor_modes = form.getlist("comp_flavor_mode") or []
    fixed_flavor_ids = form.getlist("comp_fixed_flavor_id") or []
    allowed_flavor_ids_raw = form.getlist("comp_allowed_flavor_ids") or []
    presentation_modes = form.getlist("comp_presentation_mode") or []
    allowed_pres_ids_raw = form.getlist("comp_allowed_presentation_ids") or []
    group_uids = form.getlist("comp_group_uid") or []

    parallel_arrays = [prod_ids, cantidades, tipos, grupos, max_sels]
    if group_uids:
        parallel_arrays.append(group_uids)
    if extras:
        parallel_arrays.append(extras)
    if defaults:
        parallel_arrays.append(defaults)
    if notas_prep:
        parallel_arrays.append(notas_prep)
    if presentation_ids:
        parallel_arrays.append(presentation_ids)
    if permite_sabores:
        parallel_arrays.append(permite_sabores)
    if flavor_modes:
        parallel_arrays.append(flavor_modes)
    if fixed_flavor_ids:
        parallel_arrays.append(fixed_flavor_ids)
    if allowed_flavor_ids_raw:
        parallel_arrays.append(allowed_flavor_ids_raw)
    if presentation_modes:
        parallel_arrays.append(presentation_modes)
    if allowed_pres_ids_raw:
        parallel_arrays.append(allowed_pres_ids_raw)

    is_valid, error_msg = validate_parallel_arrays(*parallel_arrays)
    if not is_valid:
        raise ComboParseError(f"Error en validación: {error_msg}")

    result: list = []
    componentes_fijos: set = set()
    componentes_seleccionables: dict = {}
    limits = ComboLimits

    for i, raw_id in enumerate(prod_ids):
        prod_id = _to_int(raw_id)
        if prod_id is None:
            continue
        if prod_id not in productos_permitidos:
            raise ComboParseError(
                f"Componente {i + 1}: el producto seleccionado no está permitido."
            )
        producto = productos_permitidos[prod_id]

        if combo_id is not None and prod_id == combo_id:
            raise ComboParseError(
                f"Componente {i + 1}: un combo no puede contenerse a sí mismo."
            )

        if enforce_owner_id is not _UNSET:
            if getattr(producto, "proveedor_despachador_id", None) != enforce_owner_id:
                raise ComboParseError(
                    f"Componente {i + 1}: «{producto.nombre}» no pertenece al "
                    "mismo propietario de inventario que el combo."
                )

        # ── cantidad ──
        cant = _to_int(cantidades[i] if i < len(cantidades) else "", default=1) or 1

        # ── tipo / grupo / uid ──
        tipo = tipos[i] if i < len(tipos) else "fijo"
        es_sel = tipo in ("sel", "1", "true", "True")
        group_uid = (
            group_uids[i].strip() if i < len(group_uids) and group_uids[i] else ""
        )
        grupo_raw = (grupos[i].strip() if i < len(grupos) and grupos[i] else "") or None
        group_def = group_defs.get(group_uid) if group_uid else None
        if group_def:
            es_sel = group_def["tipo"] == "seleccion"
            grupo_raw = group_def["nombre"] if es_sel else None

        max_sel = _to_int(max_sels[i] if i < len(max_sels) else "", default=1) or 1
        if group_def:
            max_sel = int(group_def.get("max_selecciones") or max_sel or 1)

        # ── precio_extra ──
        try:
            precio_extra = _money(extras[i] if i < len(extras) and extras[i] else 0)
        except ComboParseError:
            raise ComboParseError(
                f"Componente {i + 1}: el suplemento no es un número válido."
            )
        if precio_extra < 0:
            raise ComboParseError(
                f"Componente {i + 1}: el suplemento no puede ser negativo."
            )

        es_default = (
            (defaults[i] if i < len(defaults) else "").strip().lower()
            in ("1", "true", "on", "si", "sí")
        )
        nota_prep = (
            (notas_prep[i].strip() if i < len(notas_prep) and notas_prep[i] else "")
        )[:300]

        # ── validaciones básicas ──
        ok, err = validate_component_quantity(cant, es_sel)
        if not ok:
            raise ComboParseError(f"Componente {i + 1}: {err}")
        if es_sel:
            ok, err = validate_selections_per_group(max_sel)
            if not ok:
                raise ComboParseError(f"Componente {i + 1}: {err}")
            ok, err = validate_group_name(grupo_raw, True)
            if not ok:
                raise ComboParseError(f"Componente {i + 1}: {err}")

        # ── producto base checks (activo / no combo / no solo_canje) ──
        if not getattr(producto, "activo", True):
            raise ComboParseError(
                f"Componente {i + 1}: el producto '{producto.nombre}' está inactivo."
            )
        if getattr(producto, "es_combo", False):
            raise ComboParseError(
                f"Componente {i + 1}: un combo no puede contener otro combo."
            )
        if getattr(producto, "solo_canje", False):
            raise ComboParseError(
                f"Componente {i + 1}: «{producto.nombre}» es de canje exclusivo por puntos."
            )

        # ── presentación ──
        presentation_raw = presentation_ids[i] if i < len(presentation_ids) else ""
        allowed_pres_ids_parsed: list = []
        pres_mode_raw = (
            presentation_modes[i] if i < len(presentation_modes) else "fijo"
        ) or "fijo"
        pres_mode = (
            pres_mode_raw.strip().lower()
            if isinstance(pres_mode_raw, str)
            else "fijo"
        )
        if pres_mode not in {"fijo", "cliente_elige"}:
            raise ComboParseError(
                f"Componente {i + 1}: el modo de tamaño no es válido."
            )

        presentation = None
        if pres_mode == "cliente_elige":
            raw_subset_p = (
                allowed_pres_ids_raw[i] if i < len(allowed_pres_ids_raw) else ""
            )
            candidate_p = _parse_id_list(raw_subset_p)
            if candidate_p is None:
                raise ComboParseError(
                    f"Componente {i + 1}: la lista de tamaños no es válida."
                )
            active_presentations = (
                ProductPresentation.query.filter(
                    ProductPresentation.producto_id == producto.id,
                    ProductPresentation.activo.is_(True),
                )
                .order_by(ProductPresentation.orden, ProductPresentation.id)
                .all()
            )
            valid_presentations = {row.id: row for row in active_presentations}
            requested_pres_ids = {int(v) for v in candidate_p}
            if not requested_pres_ids.issubset(valid_presentations):
                raise ComboParseError(
                    f"Componente {i + 1}: uno de los tamaños ya no pertenece "
                    f"a «{producto.nombre}»."
                )
            allowed_pres_ids_parsed = [
                row.id for row in active_presentations if row.id in requested_pres_ids
            ]
            if len(allowed_pres_ids_parsed) < 2:
                raise ComboParseError(
                    f"Componente {i + 1}: selecciona al menos dos tamaños válidos "
                    f"de «{producto.nombre}» o usa “Tamaño fijo”."
                )
            presentation = valid_presentations[allowed_pres_ids_parsed[0]]
        else:
            presentation, presentation_error = validate_product_presentation_selection(
                producto, presentation_raw
            )
            if presentation_error:
                raise ComboParseError(f"Componente {i + 1}: {presentation_error}")

        # ── sabor ──
        permite_sabor_raw = permite_sabores[i] if i < len(permite_sabores) else ""
        legacy_permite = str(permite_sabor_raw).strip().lower() in (
            "1",
            "on",
            "true",
            "yes",
        )
        flavor_mode = (
            (flavor_modes[i] if i < len(flavor_modes) else "")
            or ("cliente_elige" if legacy_permite else "sin_sabor")
        ).strip().lower()
        if flavor_mode not in {"sin_sabor", "fijo", "cliente_elige"}:
            raise ComboParseError(
                f"Componente {i + 1}: el modo de sabor no es válido."
            )
        permite_sabor = flavor_mode == "cliente_elige"
        fixed_flavor_id = None
        allowed_flavor_ids_parsed: list = []
        if flavor_mode in {"fijo", "cliente_elige"}:
            tiene_sabor = (
                ProductExtraGroup.query.filter_by(
                    producto_id=producto.id, tipo="sabor", activo=True
                ).first()
                is not None
            )
            if not tiene_sabor:
                raise ComboParseError(
                    f"Componente {i + 1}: «{producto.nombre}» no tiene sabores "
                    "activos. Configúralos en el producto o desactiva "
                    "“cliente elige sabor”."
                )
            if flavor_mode == "cliente_elige":
                raw_subset = (
                    allowed_flavor_ids_raw[i]
                    if i < len(allowed_flavor_ids_raw)
                    else ""
                )
                candidate = _parse_id_list(raw_subset)
                if candidate is None:
                    raise ComboParseError(
                        f"Componente {i + 1}: la lista de sabores no es válida."
                    )
                valid_ids = {
                    row.id
                    for row in ProductExtraOption.query.filter(
                        ProductExtraOption.activo.is_(True),
                        ProductExtraOption.grupo_id.in_(
                            db.session.query(ProductExtraGroup.id).filter(
                                ProductExtraGroup.producto_id == producto.id,
                                ProductExtraGroup.tipo == "sabor",
                                ProductExtraGroup.activo.is_(True),
                            )
                        ),
                    ).all()
                }
                requested_ids = {int(v) for v in candidate}
                if not requested_ids or not requested_ids.issubset(valid_ids):
                    raise ComboParseError(
                        f"Componente {i + 1}: selecciona al menos un sabor activo "
                        f"y válido de «{producto.nombre}»."
                    )
                allowed_flavor_ids_parsed = sorted(requested_ids)
            else:  # fijo
                raw_fixed_id = (
                    fixed_flavor_ids[i] if i < len(fixed_flavor_ids) else ""
                )
                fixed_flavor_id = _to_int(raw_fixed_id, default=0) or 0
                fixed_option = (
                    ProductExtraOption.query.join(ProductExtraGroup)
                    .filter(
                        ProductExtraOption.id == fixed_flavor_id,
                        ProductExtraOption.activo.is_(True),
                        ProductExtraGroup.producto_id == producto.id,
                        ProductExtraGroup.tipo == "sabor",
                        ProductExtraGroup.activo.is_(True),
                    )
                    .first()
                )
                if not fixed_option:
                    raise ComboParseError(
                        f"Componente {i + 1}: selecciona un sabor fijo activo "
                        f"de «{producto.nombre}»."
                    )

        # ── invariante compartida sabor↔tamaño ──
        presentation_rows = (
            ProductPresentation.query.filter(
                ProductPresentation.id.in_(allowed_pres_ids_parsed)
            )
            .order_by(ProductPresentation.orden, ProductPresentation.id)
            .all()
            if allowed_pres_ids_parsed
            else ([presentation] if presentation else [None])
        )
        flavor_config_error = validate_combo_component_flavors(
            producto,
            presentation_rows,
            flavor_mode=flavor_mode,
            allowed_flavor_ids=allowed_flavor_ids_parsed,
            fixed_flavor_id=fixed_flavor_id,
        )
        if flavor_config_error:
            raise ComboParseError(f"Componente {i + 1}: {flavor_config_error}")

        # ── detección de duplicados ──
        if es_sel:
            grupo_key = (grupo_raw or "").strip().lower()
            componentes_seleccionables.setdefault(grupo_key, set())
            if producto.id in componentes_seleccionables[grupo_key]:
                raise ComboParseError(
                    f"El producto '{producto.nombre}' está repetido dentro del "
                    f"grupo '{grupo_raw}'."
                )
            componentes_seleccionables[grupo_key].add(producto.id)
        else:
            if producto.id in componentes_fijos:
                raise ComboParseError(
                    f"El producto '{producto.nombre}' ya está como componente "
                    "fijo. Ajusta la cantidad en una sola línea."
                )
            componentes_fijos.add(producto.id)

        precio_unit = float(getattr(producto, "precio_final", 0) or 0) + (
            presentation.precio_extra_float if presentation else 0.0
        )

        result.append(
            ComponenteInput(
                producto_id=producto.id,
                producto=producto,
                cantidad=cant,
                es_seleccionable=es_sel,
                grupo=grupo_raw if es_sel else None,
                max_selecciones=max_sel if es_sel else 1,
                es_default=es_default if es_sel else False,
                precio_extra=precio_extra,
                nota_preparacion=nota_prep,
                presentation_mode=pres_mode,
                presentation=presentation,
                presentation_id=presentation.id if presentation else None,
                allowed_presentation_ids=allowed_pres_ids_parsed,
                flavor_mode=flavor_mode,
                fixed_flavor_option_id=fixed_flavor_id or None,
                allowed_flavor_option_ids=allowed_flavor_ids_parsed,
                group_uid=group_uid,
                orden=i,
                precio_unit=precio_unit,
            )
        )

    n_comp = len(result)
    if n_comp < limits.min_components():
        raise ComboParseError(
            f"Añade al menos {limits.min_components()} componente para crear un "
            "combo funcional."
        )
    if n_comp > limits.max_components():
        raise ComboParseError(
            f"No puedes añadir más de {limits.max_components()} componentes."
        )

    struct_payload = [
        {
            "prod_id": c.producto_id,
            "cantidad": c.cantidad,
            "es_sel": c.es_seleccionable,
            "grupo": c.grupo,
            "max_sel": c.max_selecciones,
            "es_predeterminado": c.es_default,
        }
        for c in result
    ]
    is_valid, error_msg = validate_combo_structure(
        struct_payload, combo_id, parent_vertical=parent_vertical
    )
    if not is_valid:
        raise ComboParseError(error_msg)

    return result
