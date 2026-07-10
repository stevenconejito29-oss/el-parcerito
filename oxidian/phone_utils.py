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
        configured_code = country_code
        if configured_code is None:
            configured_code = os.environ.get("WHATSAPP_COUNTRY_CODE", "")
            try:
                from flask import has_app_context
                if has_app_context():
                    from models import SiteConfig
                    configured_code = SiteConfig.get("WHATSAPP_COUNTRY_CODE", configured_code)
            except (ImportError, RuntimeError):
                pass
        prefix = re.sub(r"\D", "", configured_code or "")
        if prefix and len(digits) <= 10 and not digits.startswith(prefix):
            digits = f"{prefix}{digits}"
    # No truncar: convertir dos identidades largas distintas en el mismo valor
    # sería peligroso. `telefono_valido` rechazará lo que exceda E.164.
    return f"+{digits}"


def telefono_valido(value: str | None) -> bool:
    canonical = normalizar_telefono_cliente(value)
    # Mínimo operativo: 8 dígitos; máximo normativo E.164: 15.
    return bool(re.fullmatch(r"\+[1-9]\d{7,14}", canonical))


def telefono_local_ambiguo(value: str | None, country_code: str | None = None) -> bool:
    """Indica si un teléfono local no puede normalizarse sin prefijo de país."""
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not digits or raw.startswith("+") or raw.startswith("00"):
        return False
    configured_code = country_code
    if configured_code is None:
        configured_code = os.environ.get("WHATSAPP_COUNTRY_CODE", "")
        try:
            from flask import has_app_context
            if has_app_context():
                from models import SiteConfig
                configured_code = SiteConfig.get("WHATSAPP_COUNTRY_CODE", configured_code)
        except (ImportError, RuntimeError):
            pass
    return not re.sub(r"\D", "", configured_code or "")
