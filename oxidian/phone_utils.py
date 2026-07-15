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
    # No truncar silenciosamente: dos números largos distintos podrían acabar
    # compartiendo identidad. `telefono_valido` se encarga de rechazarlos.
    return f"+{digits}"


def telefono_valido(value: str | None) -> bool:
    canonical = normalizar_telefono_cliente(value)
    # E.164 admite como máximo 15 dígitos. Exigimos al menos 8 para evitar
    # extensiones/locales demasiado cortos y el código de país no empieza en 0.
    return re.fullmatch(r"\+[1-9]\d{7,14}", canonical or "") is not None


def solo_digitos(value: str | None) -> str:
    """Extrae únicamente los dígitos del teléfono, sin normalizar prefijos.

    Usado para comparación entre números en formatos distintos
    (``+34633096706`` == ``34633096706`` == ``633096706 con +34 configurado``).
    Para persistencia canónica usar :func:`normalizar_telefono_cliente`.
    """
    if not value:
        return ""
    return "".join(c for c in str(value) if c.isdigit())


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
