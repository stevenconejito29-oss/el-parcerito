# ════════════════════════════════════════════════════════════════════════════════
# Validadores robustos para combos — Sin hardcoding
# ════════════════════════════════════════════════════════════════════════════════

import os
from typing import Tuple, Optional, Dict, List, Set

try:
    from flask import has_app_context
except Exception:  # pragma: no cover - permite importar validadores fuera de Flask
    def has_app_context():
        return False


def _db_get(model, pk):
    """Wrapper de db.session.get() con guard: None si el pk es falsy o
    si no hay contexto Flask (para permitir tests unitarios que importan
    el módulo sin app). Reemplaza los Model.query.get() deprecados en
    SQLAlchemy 2.0."""
    if not pk:
        return None
    try:
        from extensions import db
        return db.session.get(model, pk)
    except Exception:  # pragma: no cover - fallback fuera de app-context
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN DE LÍMITES (sin hardcoding)
# ─────────────────────────────────────────────────────────────────────────────

class ComboLimits:
    """Configuración de límites para combos — gets valores de env / SiteConfig."""

    @staticmethod
    def _site_config_value(key: str):
        if not has_app_context():
            return None
        try:
            from models import SiteConfig
            return SiteConfig.get(key)
        except Exception:
            return None

    @staticmethod
    def max_qty_per_component() -> int:
        """Cantidad máxima de unidades por componente fijo."""
        val = ComboLimits._site_config_value("COMBO_MAX_QTY_COMPONENT")
        if val:
            return int(val)
        # Fallback a environment variable, luego default
        return int(os.environ.get("COMBO_MAX_QTY_COMPONENT", "50"))

    @staticmethod
    def max_selections_per_group() -> int:
        """Cantidad máxima de selecciones por grupo."""
        val = ComboLimits._site_config_value("COMBO_MAX_SELECTIONS_GROUP")
        if val:
            return int(val)
        return int(os.environ.get("COMBO_MAX_SELECTIONS_GROUP", "10"))

    @staticmethod
    def max_components() -> int:
        """Cantidad máxima de componentes en un combo."""
        val = ComboLimits._site_config_value("COMBO_MAX_COMPONENTS")
        if val:
            return int(val)
        return int(os.environ.get("COMBO_MAX_COMPONENTS", "30"))

    @staticmethod
    def min_components() -> int:
        """Cantidad mínima de componentes en un combo."""
        val = ComboLimits._site_config_value("COMBO_MIN_COMPONENTS")
        if val:
            return max(1, int(val))
        return max(1, int(os.environ.get("COMBO_MIN_COMPONENTS", "1")))

    @staticmethod
    def max_discount_percentage() -> float:
        """Porcentaje máximo de descuento permitido en combos."""
        val = ComboLimits._site_config_value("COMBO_MAX_DISCOUNT_PCT")
        if val:
            return float(val)
        return float(os.environ.get("COMBO_MAX_DISCOUNT_PCT", "50.0"))


# ─────────────────────────────────────────────────────────────────────────────
# VALIDADORES REUTILIZABLES
# ─────────────────────────────────────────────────────────────────────────────

def validate_component_quantity(
    quantity: int,
    is_selectable: bool = False,
    error_prefix: str = "Componente"
) -> Tuple[bool, Optional[str]]:
    """
    Valida que la cantidad de un componente sea válida.

    Args:
        quantity: Cantidad del componente
        is_selectable: Si es seleccionable (para custom validation)
        error_prefix: Prefijo del mensaje de error

    Returns:
        (is_valid, error_message)
    """
    if not isinstance(quantity, int) or quantity < 1:
        return False, f"{error_prefix}: la cantidad debe ser un número mayor a 0"

    max_qty = ComboLimits.max_qty_per_component()
    if quantity > max_qty:
        return False, f"{error_prefix}: la cantidad no puede exceder {max_qty}"

    return True, None


def validate_selections_per_group(
    max_selections: int,
    error_prefix: str = "Grupo"
) -> Tuple[bool, Optional[str]]:
    """
    Valida que el número de selecciones por grupo sea válido.

    Args:
        max_selections: Cantidad máxima de selecciones
        error_prefix: Prefijo del mensaje de error

    Returns:
        (is_valid, error_message)
    """
    if not isinstance(max_selections, int) or max_selections < 1:
        return False, f"{error_prefix}: el número de selecciones debe ser mayor a 0"

    max_allowed = ComboLimits.max_selections_per_group()
    if max_selections > max_allowed:
        return False, f"{error_prefix}: no puedes permitir más de {max_allowed} selecciones por grupo"

    return True, None


def validate_group_name(group_name: str, is_selectable: bool) -> Tuple[bool, Optional[str]]:
    """
    Valida que el nombre del grupo sea válido (si es seleccionable).

    Args:
        group_name: Nombre del grupo
        is_selectable: Si el componente es seleccionable

    Returns:
        (is_valid, error_message)
    """
    if not is_selectable:
        return True, None  # No se requiere grupo si es fijo

    if not group_name or not group_name.strip():
        return False, "Los componentes seleccionables requieren un nombre de grupo (ej: Bebida, Salsa)"

    if len(group_name.strip()) > 50:
        return False, "El nombre del grupo no puede exceder 50 caracteres"

    if not all(c.isalnum() or c in " -_" for c in group_name):
        return False, "El nombre del grupo solo puede contener letras, números, espacios, guiones y guiones bajos"

    return True, None


def validate_combo_structure(
    components: List[Dict],  # List of {prod_id, cantidad, es_sel, grupo, max_sel}
    combo_id: Optional[int] = None
) -> Tuple[bool, Optional[str]]:
    """
    Valida la estructura global de un combo.

    Args:
        components: Lista de componentes
        combo_id: ID del combo (para distinguir de None al crear)

    Returns:
        (is_valid, error_message)
    """
    if not components or len(components) < ComboLimits.min_components():
        return False, f"Un combo requiere al menos {ComboLimits.min_components()} componente"

    if len(components) > ComboLimits.max_components():
        return False, f"Un combo no puede tener más de {ComboLimits.max_components()} componentes"

    # Validar que no haya duplicados entre fijos
    fixed_product_ids: Set[int] = set()
    group_product_pairs: Set[Tuple[str, int]] = set()
    group_option_counts: Dict[str, int] = {}
    group_max_selections: Dict[str, int] = {}
    group_default_counts: Dict[str, int] = {}

    for comp in components:
        prod_id = comp.get("prod_id") or comp.get("producto_id")
        es_sel = comp.get("es_sel", comp.get("es_seleccionable", False))
        grupo = (comp.get("grupo") or comp.get("grupo_seleccion") or "").strip()
        max_sel = comp.get("max_sel", comp.get("max_selecciones", 1))
        cantidad = comp.get("cantidad", 1)

        # Validación individual del componente
        qty_valid, qty_err = validate_component_quantity(cantidad, es_sel)
        if not qty_valid:
            return False, qty_err

        if es_sel:
            sel_valid, sel_err = validate_selections_per_group(max_sel)
            if sel_err:
                return False, sel_err

            group_valid, group_err = validate_group_name(grupo, True)
            if group_err:
                return False, group_err

            key = (grupo.lower(), prod_id)
            if key in group_product_pairs:
                return False, f"No puedes repetir el mismo producto '{prod_id}' dentro del grupo '{grupo}'"
            group_product_pairs.add(key)
            group_key = grupo.lower()
            group_option_counts[group_key] = group_option_counts.get(group_key, 0) + 1
            if comp.get("es_predeterminado"):
                group_default_counts[group_key] = group_default_counts.get(group_key, 0) + 1
            if group_key in group_max_selections and group_max_selections[group_key] != max_sel:
                return False, f"El grupo '{grupo}' debe tener el mismo máximo de selecciones en todas sus opciones"
            group_max_selections[group_key] = max_sel
        else:
            # Componente fijo
            if prod_id in fixed_product_ids:
                return False, f"El producto '{prod_id}' ya existe como componente fijo. Ajusta la cantidad si deseas"
            fixed_product_ids.add(prod_id)

    for group_key, option_count in group_option_counts.items():
        max_sel = group_max_selections.get(group_key, 1)
        if max_sel > option_count:
            return False, f"El grupo '{group_key}' permite {max_sel} selecciones pero solo tiene {option_count} opción(es)"
        default_count = group_default_counts.get(group_key, 0)
        if default_count > max_sel:
            return False, f"El grupo '{group_key}' tiene {default_count} opciones recomendadas, pero solo permite {max_sel} selección(es)"

    return True, None


def validate_combo_pricing(
    precio: float,
    descuento_porcentaje: Optional[float] = None,
    error_prefix: str = "Precio"
) -> Tuple[bool, Optional[str]]:
    """
    Valida que el precio y descuento del combo sean válidos.

    Args:
        precio: Precio del combo (€)
        descuento_porcentaje: Porcentaje de descuento (0-100), None si no hay
        error_prefix: Prefijo del mensaje de error

    Returns:
        (is_valid, error_message)
    """
    if not isinstance(precio, (int, float)) or precio <= 0:
        return False, f"{error_prefix}: debe ser un número mayor a 0"

    if precio > 1000:
        return False, f"{error_prefix}: el precio no puede exceder 1000€"

    if descuento_porcentaje is not None:
        try:
            desc = float(descuento_porcentaje)
            if desc < 0 or desc > ComboLimits.max_discount_percentage():
                return False, f"Descuento: debe estar entre 0% y {ComboLimits.max_discount_percentage()}%"
        except (ValueError, TypeError):
            return False, "Descuento: debe ser un número válido"

    return True, None


def validate_component_product(
    combo_id: int,
    product_id: int,
    product_obj=None
) -> Tuple[bool, Optional[str]]:
    """
    Valida que un producto sea válido como componente del combo.

    Args:
        combo_id: ID del combo
        product_id: ID del producto a validar
        product_obj: Objeto Product (si ya lo tienes)

    Returns:
        (is_valid, error_message)
    """
    if product_id == combo_id:
        return False, "Un combo no puede contenerse a sí mismo"

    if product_obj is None:
        from models import Product
        product_obj = _db_get(Product, product_id)

    if not product_obj:
        return False, f"Producto {product_id} no existe"

    if not product_obj.activo:
        return False, f"El producto '{product_obj.nombre}' está inactivo"

    if product_obj.es_combo:
        return False, "Un combo no puede contener otro combo. Usa solo productos simples"

    # ── Bloqueo de productos exclusivos de canje ──────────────────────────
    # Un producto marcado como "solo canje por puntos" no puede formar parte
    # de un combo vendible: mezclaría dinero con puntos y rompería el pricing.
    if getattr(product_obj, "solo_canje", False):
        return False, (
            f"'{product_obj.nombre}' es de canje exclusivo por puntos y no puede "
            "formar parte de un combo. Retira el flag 'solo canje' o usa otro producto."
        )

    from models import ComboItem, Product

    # ── Modalidad de entrega compatible ──────────────────────────────────
    # Si un componente es solo-delivery y otro solo-recogida, el combo no
    # se puede armar (no existe modalidad válida para todos).
    def _modes(mode):
        m = (mode or "ambas").strip().lower()
        if m == "delivery":
            return {"delivery"}
        if m == "recogida":
            return {"recogida"}
        return {"delivery", "recogida"}

    combo = _db_get(Product, combo_id)
    if combo:
        allowed = _modes(getattr(combo, "modalidad_entrega", None))
        for it in ComboItem.query.filter_by(combo_id=combo_id).all():
            comp = it.componente
            if comp:
                allowed &= _modes(getattr(comp, "modalidad_entrega", None))
        allowed &= _modes(getattr(product_obj, "modalidad_entrega", None))
        if not allowed:
            return False, (
                f"'{product_obj.nombre}' es incompatible con el combo: "
                "los componentes actuales solo permiten una modalidad de entrega "
                "distinta (delivery vs recogida). Cambia la modalidad del producto "
                "o del combo antes de agregarlo."
            )

    # ── Tipo de entrega compatible (inmediato vs programado) ─────────────
    # Un combo no puede mezclar productos inmediatos con productos de fecha
    # fija: cocina, preparación y reparto no pueden coordinar dos flujos.
    if combo:
        combo_tipo = (getattr(combo, "tipo_entrega", "inmediato") or "inmediato").strip().lower()
        prod_tipo = (getattr(product_obj, "tipo_entrega", "inmediato") or "inmediato").strip().lower()
        if combo_tipo and prod_tipo and combo_tipo != prod_tipo:
            return False, (
                f"'{product_obj.nombre}' es de tipo '{prod_tipo}' pero el combo es "
                f"'{combo_tipo}'. No puedes mezclar productos inmediatos con productos "
                "de fecha programada."
            )
        for it in ComboItem.query.filter_by(combo_id=combo_id).all():
            comp = it.componente
            if not comp:
                continue
            comp_tipo = (getattr(comp, "tipo_entrega", "inmediato") or "inmediato").strip().lower()
            if comp_tipo and prod_tipo and comp_tipo != prod_tipo:
                return False, (
                    f"'{product_obj.nombre}' ({prod_tipo}) no comparte tipo de entrega "
                    f"con '{comp.nombre}' ({comp_tipo}). Todos los componentes de un "
                    "combo deben ser del mismo tipo."
                )

    # ── Origen (stock propio vs proveedor) ───────────────────────────────
    # Cada combo se arma con productos del MISMO origen (regla documentada
    # en CLAUDE.md). Refuerzo aquí para blindar la puerta de entrada.
    if combo:
        combo_prov = getattr(combo, "proveedor_despachador_id", None)
        prod_prov = getattr(product_obj, "proveedor_despachador_id", None)
        if combo_prov != prod_prov:
            origen_combo = "stock propio" if combo_prov is None else f"proveedor #{combo_prov}"
            origen_prod = "stock propio" if prod_prov is None else f"proveedor #{prod_prov}"
            return False, (
                f"'{product_obj.nombre}' es de {origen_prod} pero el combo es de "
                f"{origen_combo}. Todos los componentes deben compartir origen."
            )

    return True, None


# ─────────────────────────────────────────────────────────────────────────────
# VALIDACIÓN DE ZONA DE ENTREGA
# ─────────────────────────────────────────────────────────────────────────────

def validate_combo_delivery_zones(
    combo_id: int,
    zonas_permitidas: Optional[List[int]] = None
) -> Tuple[bool, Optional[str]]:
    """
    Valida que el combo esté disponible en al menos una zona de entrega.

    Args:
        combo_id: ID del combo
        zonas_permitidas: Lista de IDs de zona permitidas (None = todas)

    Returns:
        (is_valid, error_message)
    """
    from models import Product, ZonaEntrega

    combo = _db_get(Product, combo_id)
    if not combo or not combo.es_combo:
        return False, "Combo no existe o no es válido"

    # Si no hay restricción de zonas, es válido
    if zonas_permitidas is None or not zonas_permitidas:
        return True, None

    # Verificar que al menos una zona existe y está activa
    active_zones = ZonaEntrega.query.filter(
        ZonaEntrega.id.in_(zonas_permitidas),
        ZonaEntrega.activo == True
    ).count()

    if active_zones == 0:
        return False, "El combo no está asignado a ninguna zona de entrega activa"

    return True, None


def validate_delivery_zone_for_combo(
    combo_id: int,
    zone_id: int
) -> Tuple[bool, Optional[str]]:
    """
    Valida que una zona de entrega sea válida para un combo específico.

    Args:
        combo_id: ID del combo
        zone_id: ID de la zona

    Returns:
        (is_valid, error_message)
    """
    from models import Product, ZonaEntrega

    combo = _db_get(Product, combo_id)
    if not combo or not combo.es_combo:
        return False, "Combo no existe"

    zone = _db_get(ZonaEntrega, zone_id)
    if not zone:
        return False, "Zona de entrega no existe"

    if not zone.activo:
        return False, f"La zona '{zone.nombre}' está inactiva"

    return True, None


# ─────────────────────────────────────────────────────────────────────────────
# VALIDACIÓN DE ARRAYS PARALELOS (Desde form)
# ─────────────────────────────────────────────────────────────────────────────

def validate_parallel_arrays(
    *arrays
) -> Tuple[bool, Optional[str]]:
    """
    Valida que todos los arrays tengan la misma longitud.

    Args:
        *arrays: Arrays a validar

    Returns:
        (is_valid, error_message)
    """
    if not arrays:
        return False, "No hay arrays para validar"

    lengths = [len(arr) for arr in arrays]
    if len(set(lengths)) > 1:
        return False, f"Los arrays tienen longitudes inconsistentes: {lengths}"

    return True, None
