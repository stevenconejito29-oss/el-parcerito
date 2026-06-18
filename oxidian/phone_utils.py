"""Normalización canónica de teléfonos usados como identidad de cliente."""
from __future__ import annotations

import os
import re


def normalizar_telefono_cliente(value: str | None, country_code: str | None = None) -> str:
    """Devuelve un teléfono E.164 simplificado: ``+`` seguido solo de dígitos."""
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if not raw.startswith("+") and not raw.startswith("00"):
        prefix = re.sub(
            r"\D",
            "",
            country_code or os.environ.get("WHATSAPP_COUNTRY_CODE", "34"),
        )
        if prefix and len(digits) <= 10 and not digits.startswith(prefix):
            digits = f"{prefix}{digits}"
    return f"+{digits[:19]}"


def telefono_valido(value: str | None) -> bool:
    canonical = normalizar_telefono_cliente(value)
    return 8 <= len(canonical) <= 20
