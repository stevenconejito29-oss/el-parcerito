"""Acceso único a la configuración personalizable de cada tienda."""
from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


STORE_DEFAULTS = {
    "NOMBRE_NEGOCIO": "Mi tienda",
    "SLOGAN_NEGOCIO": "",
    "DESCRIPCION_NEGOCIO": "",
    "DIRECCION_NEGOCIO": "",
    "CIUDAD_NEGOCIO": "",
    "PROVINCIA_NEGOCIO": "",
    "PAIS_NEGOCIO": "",
    "PAIS_CODIGO_ISO": "",
    "TELEFONO_NEGOCIO": "",
    "EMAIL_CONTACTO": "",
    "WHATSAPP_COUNTRY_CODE": "",
    "BIZUM_TELEFONO": "",
    "BIZUM_HABILITADO": "1",
    "EFECTIVO_HABILITADO": "1",
    "MODO_TIENDA": "propia",
    "FEATURE_DELIVERY": "1",
    "FEATURE_RECOGIDA": "1",
    "FEATURE_PEDIDOS_PROGRAMADOS": "1",
    "FEATURE_PUNTOS": "1",
    "SERVICE_COMMISSION_PCT": "0",
    "LOGO_URL": "",
    "APP_ICON_URL": "",
    "HERO_IMAGE_URL": "",
    "COLOR_PRIMARIO": "#D9961A",
    "COLOR_SECUNDARIO": "#CE1126",
    "COLOR_ACENTO": "#003087",
    "HORARIO_APERTURA": "09:00",
    "HORARIO_CIERRE": "22:30",
    "TIENDA_FORZAR_CERRADA": "0",
    "TIENDA_MENSAJE_CIERRE": "",
    "CENTRO_LAT": "",
    "CENTRO_LON": "",
    "RADIO_ENTREGA_KM": "5",
}


def get_store_value(key: str, default: str | None = None) -> str:
    """SiteConfig es la autoridad; entorno y defaults solo sirven de bootstrap."""
    from models import SiteConfig

    fallback = STORE_DEFAULTS.get(key, "") if default is None else default
    env_value = os.environ.get(key, fallback)
    return SiteConfig.get(key, env_value) or fallback


def get_store_bool(key: str, default: str = "0") -> bool:
    value = get_store_value(key, default)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def get_store_features() -> dict:
    """Contrato de funcionalidades que debe respetar web, roles y chatbot."""
    modo = (get_store_value("MODO_TIENDA", "propia") or "propia").strip().lower()
    if modo not in {"propia", "bar_servicio"}:
        modo = "propia"
    delivery = get_store_bool("FEATURE_DELIVERY", "1")
    recogida = get_store_bool("FEATURE_RECOGIDA", "1")
    if not delivery and not recogida:
        recogida = True
    return {
        "modo_tienda": modo,
        "delivery": delivery,
        "recogida": recogida,
        "pedidos_programados": get_store_bool("FEATURE_PEDIDOS_PROGRAMADOS", "1"),
        "puntos": get_store_bool("FEATURE_PUNTOS", "1"),
        "proveedores": False,
    }


def is_service_mode() -> bool:
    """True cuando esta instalación es una tienda white-label para un solo bar."""
    return get_store_features()["modo_tienda"] == "bar_servicio"


def is_provider_flow_enabled() -> bool:
    """El flujo multi-proveedor/bar externo queda desactivado por diseño."""
    return False


def get_service_commission(total) -> dict:
    """Congela la comisión white-label correspondiente a una venta."""
    amount = Decimal(str(total or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if not is_service_mode():
        return {"pct": Decimal("0.00"), "amount": Decimal("0.00"), "merchant_net": amount}
    try:
        pct = Decimal(get_store_value("SERVICE_COMMISSION_PCT", "0"))
    except (InvalidOperation, TypeError):
        pct = Decimal("0")
    pct = min(Decimal("100"), max(Decimal("0"), pct)).quantize(Decimal("0.01"))
    commission = (amount * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"pct": pct, "amount": commission, "merchant_net": amount - commission}


def get_store_profile() -> dict:
    return {
        "nombre": get_store_value("NOMBRE_NEGOCIO"),
        "slogan": get_store_value("SLOGAN_NEGOCIO"),
        "descripcion": get_store_value("DESCRIPCION_NEGOCIO"),
        "direccion": get_store_value("DIRECCION_NEGOCIO"),
        "ciudad": get_store_value("CIUDAD_NEGOCIO"),
        "provincia": get_store_value("PROVINCIA_NEGOCIO"),
        "pais": get_store_value("PAIS_NEGOCIO"),
        "pais_codigo_iso": get_store_value("PAIS_CODIGO_ISO").lower(),
        "telefono": get_store_value("TELEFONO_NEGOCIO"),
        "email": get_store_value("EMAIL_CONTACTO"),
        "bizum_telefono": get_store_value("BIZUM_TELEFONO"),
        "bizum_habilitado": get_store_value("BIZUM_HABILITADO") == "1",
        "efectivo_habilitado": get_store_value("EFECTIVO_HABILITADO") == "1",
        "service_commission_pct": get_store_value("SERVICE_COMMISSION_PCT", "0"),
        **get_store_features(),
        "logo_url": get_store_value("LOGO_URL"),
        "app_icon_url": get_store_value("APP_ICON_URL"),
        "hero_image_url": get_store_value("HERO_IMAGE_URL"),
        "color_primario": get_store_value("COLOR_PRIMARIO"),
        "color_secundario": get_store_value("COLOR_SECUNDARIO"),
        "color_acento": get_store_value("COLOR_ACENTO"),
        "horario_apertura": get_store_value("HORARIO_APERTURA"),
        "horario_cierre": get_store_value("HORARIO_CIERRE"),
        "tienda_mensaje_cierre": get_store_value("TIENDA_MENSAJE_CIERRE"),
    }
