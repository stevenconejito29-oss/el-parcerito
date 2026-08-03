"""Servicio de líneas de carrito.

Modelo antiguo (legacy): `session["carrito"] = {str(producto_id): cantidad}`.
Todas las selecciones (presentación, sabores, extras, variante, combo, notas)
se guardaban en dicts paralelos también con `str(producto_id)` como clave. Eso
impedía tener el mismo producto en dos líneas con distinta selección — el
sistema sumaba cantidades.

Modelo nuevo: la clave del carrito es una firma de línea (`line_key`) que
incorpora la selección completa. Mismas selecciones → misma clave → se fusiona
(comportamiento previo). Selecciones distintas → claves distintas → líneas
separadas. El `producto_id` va embebido al inicio (`"{pid}#{hash}"`) para poder
extraerlo sin lookup.

Este módulo NO habla con Flask ni con la BD: sólo transforma datos. Toda la
integración con `session` vive en `routes/public.py`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


LINE_SEPARATOR = "#"


def _canon_extras(extras: Optional[Mapping[Any, Any]]) -> List[Tuple[int, int]]:
    """Normaliza dict de extras/sabores a lista ordenada [(option_id, qty)]."""
    if not extras:
        return []
    out: List[Tuple[int, int]] = []
    for k, v in extras.items():
        try:
            oid = int(k)
            qty = int(v)
        except (TypeError, ValueError):
            continue
        if qty > 0:
            out.append((oid, qty))
    out.sort()
    return out


def _canon_combo_value(value: Any) -> Any:
    """Normaliza recursivamente snapshots de combo sin perder pares por unidad."""
    if isinstance(value, Mapping):
        return [
            (str(key), _canon_combo_value(nested))
            for key, nested in sorted(value.items(), key=lambda row: str(row[0]))
        ]
    if isinstance(value, (list, tuple)):
        return [_canon_combo_value(nested) for nested in value]
    if isinstance(value, set):
        normalized = [_canon_combo_value(nested) for nested in value]
        return sorted(normalized, key=lambda nested: json.dumps(nested, sort_keys=True))
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _canon_combo(combo_seleccion: Optional[Mapping[Any, Any]]) -> List[Tuple[str, Any]]:
    """Firma estable de una selección de combo.

    `combo_seleccion` es un dict `{grupo_id: [item_ids...]}` o similar. Lo
    serializamos deterministamente para que orden de teclado no invalide fusión.
    """
    if not combo_seleccion:
        return []
    normalized = _canon_combo_value(combo_seleccion)
    return normalized if isinstance(normalized, list) else []


def line_signature(
    producto_id: int,
    presentation_size: str = "",
    sabores: Optional[Mapping[Any, Any]] = None,
    extras: Optional[Mapping[Any, Any]] = None,
    variant_id: Optional[int] = None,
    combo_seleccion: Optional[Mapping[Any, Any]] = None,
    notas: str = "",
) -> str:
    """Devuelve la `line_key` estable para una selección.

    Si no hay ninguna variación (producto simple sin opciones), devuelve
    `str(producto_id)` para preservar compat con carritos legacy y URLs
    antiguas del enlace "eliminar".
    """
    payload = {
        "s": (presentation_size or "").strip().lower(),
        "sb": _canon_extras(sabores),
        "ex": _canon_extras(extras),
        "v": int(variant_id) if variant_id else 0,
        "c": _canon_combo(combo_seleccion),
        "n": (notas or "").strip(),
    }
    # Producto simple sin opciones: clave legacy (str del producto_id).
    if not any(payload.values()):
        return str(int(producto_id))
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.blake2b(blob.encode("utf-8"), digest_size=6).hexdigest()
    return f"{int(producto_id)}{LINE_SEPARATOR}{digest}"


def producto_id_from_line_key(line_key: str) -> Optional[int]:
    """Extrae el `producto_id` de una `line_key` (legacy o nueva)."""
    if line_key is None:
        return None
    s = str(line_key)
    head = s.split(LINE_SEPARATOR, 1)[0]
    try:
        return int(head)
    except (TypeError, ValueError):
        return None


def is_line_key(line_key: str) -> bool:
    return producto_id_from_line_key(line_key) is not None


def iter_producto_ids(carrito: Optional[Mapping[str, Any]]) -> List[int]:
    """Lista de producto_ids únicos en un carrito (respetando orden de inserción)."""
    seen: Dict[int, None] = {}
    for k in (carrito or {}).keys():
        pid = producto_id_from_line_key(k)
        if pid is not None and pid not in seen:
            seen[pid] = None
    return list(seen.keys())


def migrate_legacy_session(session_dict) -> None:
    """Migración one-shot de una sesión legacy a formato nuevo.

    Idempotente: si ya está migrada, no hace nada. No mutamos claves que ya
    son válidas — sólo garantizamos que el estado quede consistente.
    """
    carrito = session_dict.get("carrito") or {}
    if not carrito:
        return
    # Ya está en formato nuevo si alguna clave tiene el separador.
    if any(LINE_SEPARATOR in str(k) for k in carrito.keys()):
        return
    # Formato legacy: {str(producto_id): cantidad}. Reconstruimos cada línea
    # con su firma completa usando los dicts paralelos ligados por producto_id.
    presentaciones = session_dict.get("presentaciones_carrito") or {}
    extras_sel = session_dict.get("extras_selecciones") or {}
    variantes = session_dict.get("variantes_carrito") or {}
    combos = session_dict.get("combo_selecciones") or {}
    notas = session_dict.get("notas_combo") or {}

    new_carrito: Dict[str, int] = {}
    new_pres: Dict[str, str] = {}
    new_extras: Dict[str, Any] = {}
    new_vars: Dict[str, int] = {}
    new_combos: Dict[str, Any] = {}
    new_notas: Dict[str, str] = {}

    for pid_str, qty in carrito.items():
        try:
            pid_int = int(pid_str)
            qty_int = int(qty)
        except (TypeError, ValueError):
            continue
        if qty_int <= 0:
            continue
        pres_size = presentaciones.get(pid_str) or ""
        sel_extras = extras_sel.get(pid_str) or {}
        # Legacy juntaba sabores y extras en el mismo dict.
        sabores_sub = sel_extras if isinstance(sel_extras, dict) else {}
        variant_id = variantes.get(pid_str)
        combo_sel = combos.get(pid_str) or {}
        nota = notas.get(pid_str) or ""
        key = line_signature(
            pid_int,
            presentation_size=pres_size,
            sabores=sabores_sub,  # legacy: mezclados; el hash absorbe todo
            extras=None,
            variant_id=variant_id,
            combo_seleccion=combo_sel,
            notas=nota,
        )
        new_carrito[key] = new_carrito.get(key, 0) + qty_int
        if pres_size:
            new_pres[key] = pres_size
        if sabores_sub:
            new_extras[key] = dict(sabores_sub)
        if variant_id:
            new_vars[key] = int(variant_id)
        if combo_sel:
            new_combos[key] = combo_sel
        if nota:
            new_notas[key] = nota

    session_dict["carrito"] = new_carrito
    session_dict["presentaciones_carrito"] = new_pres
    session_dict["extras_selecciones"] = new_extras
    session_dict["variantes_carrito"] = new_vars
    session_dict["combo_selecciones"] = new_combos
    session_dict["notas_combo"] = new_notas


__all__ = [
    "line_signature",
    "producto_id_from_line_key",
    "is_line_key",
    "iter_producto_ids",
    "migrate_legacy_session",
    "LINE_SEPARATOR",
]
