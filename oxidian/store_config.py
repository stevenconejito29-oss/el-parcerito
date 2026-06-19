"""Acceso único a la configuración personalizable de cada tienda."""
from __future__ import annotations

import os


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
