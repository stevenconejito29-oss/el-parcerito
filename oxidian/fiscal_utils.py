"""Utilidades fiscales España: validación de NIF/DNI/NIE/CIF, cálculo IVA.

Ligero — no pretende sustituir librerías completas. Suficiente para validar
formato al capturar el NIF opcional del cliente en checkout y para calcular
bases imponibles a partir de totales con IVA incluido.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP


_DNI_LETRAS = "TRWAGMYFPDXBNJZSQVHLCKE"
_RE_DNI = re.compile(r"^(\d{8})([A-Z])$")
_RE_NIE = re.compile(r"^([XYZ])(\d{7})([A-Z])$")
_RE_CIF = re.compile(r"^([ABCDEFGHJKLMNPQRSUVW])(\d{7})([0-9A-J])$")


def normalizar_nif(value: str | None) -> str:
    """Devuelve el NIF en mayúsculas, sin espacios ni guiones."""
    if not value:
        return ""
    return re.sub(r"[\s\-\.]", "", str(value).strip().upper())


def _letra_dni_valida(numero: int, letra: str) -> bool:
    return _DNI_LETRAS[numero % 23] == letra


def nif_valido(value: str | None) -> bool:
    """Valida formato de DNI, NIE o CIF español.

    No comprueba dígito de control del CIF de forma exhaustiva (reglas por
    letra de organización); acepta cualquier formato válido a nivel de patrón
    y letra permitida. Suficiente para checkout — el gestor detecta erratas.
    """
    nif = normalizar_nif(value)
    if not nif:
        return False
    m = _RE_DNI.match(nif)
    if m:
        return _letra_dni_valida(int(m.group(1)), m.group(2))
    m = _RE_NIE.match(nif)
    if m:
        prefijo = {"X": "0", "Y": "1", "Z": "2"}[m.group(1)]
        return _letra_dni_valida(int(prefijo + m.group(2)), m.group(3))
    m = _RE_CIF.match(nif)
    if m:
        return True
    return False


def base_e_iva_desde_total(total, iva_pct) -> tuple[Decimal, Decimal]:
    """Dado un total con IVA incluido y una tasa, devuelve (base_imponible, iva_importe).

    Ejemplo: total=11.00, iva_pct=10 → (10.00, 1.00).
    Cuantiza a 2 decimales con redondeo half-up (norma contable habitual).
    """
    total_d = Decimal(str(total or 0))
    tasa = Decimal(str(iva_pct or 0))
    if tasa <= 0:
        return total_d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), Decimal("0.00")
    base = (total_d / (Decimal("1") + tasa / Decimal("100")))
    base_q = base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    iva_q = (total_d - base_q).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return base_q, iva_q
