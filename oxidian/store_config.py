"""Acceso único a la configuración personalizable de cada tienda."""
from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


PUBLIC_THEME_DEFAULTS = {
    "COLOR_FONDO_APP": "#F7F3EA",
    "COLOR_SUPERFICIE": "#FFFDF8",
    "COLOR_SUPERFICIE_ALT": "#F1EBDD",
    "COLOR_TEXTO": "#1B1A16",
    "COLOR_TEXTO_SUAVE": "#68635B",
    "COLOR_CABECERA_FONDO": "#171714",
    "COLOR_CABECERA_TEXTO": "#FFFFFF",
    "COLOR_EXITO": "#087A48",
    "COLOR_ALERTA": "#B42318",
}


PUBLIC_UI_DEFAULTS = {
    "UI_CLOSE": "Cerrar",
    "UI_HEADER_MODE_BOTH": "Delivery · Recogida",
    "UI_HEADER_MODE_DELIVERY": "Delivery",
    "UI_HEADER_MODE_PICKUP": "Recogida",
    "UI_HEADER_OPEN": "Abierto",
    "UI_HEADER_CLOSED": "Cerrado",
    "UI_HEADER_INSTALL": "Añadir app",
    "UI_HEADER_CART_LABEL": "Tu pedido",
    "UI_HEADER_CART_ACTION": "Ver carrito",
    "UI_HEADER_STAFF": "Empleados",
    "UI_CART_EYEBROW": "Revisa tu pedido",
    "UI_CART_TITLE": "Tu carrito",
    "UI_CART_ITEM_ONE": "producto",
    "UI_CART_ITEM_MANY": "productos",
    "UI_CART_BACK": "Volver",
    "UI_CART_PROCESS": "Proceso de compra",
    "UI_CART_STEP_CART": "Carrito",
    "UI_CART_STEP_FULFILLMENT": "Entrega",
    "UI_CART_STEP_PAYMENT": "Pago",
    "UI_CART_UPDATE": "Actualizar carrito",
    "UI_CART_COMBO_BADGE": "Combo",
    "UI_CART_COMBO_VIEW": "Ver combo",
    "UI_CART_COMBO_ITEMS": "ítems",
    "UI_CART_COMBO_BASE": "Base incluida",
    "UI_CART_COMBO_INCLUDED": "Incluido",
    "UI_CART_COMBO_SELECTION": "Tu elección",
    "UI_CART_COMBO_KITCHEN": "Contenido definido en cocina",
    "UI_CART_EXTRAS_LABEL": "Extras",
    "UI_CART_ORDER_LABEL": "Pedido",
    "UI_CART_NO_ALLERGENS": "Sin alérgenos",
    "UI_CART_QUANTITY": "Cantidad",
    "UI_CART_LESS": "Menos",
    "UI_CART_MORE": "Más",
    "UI_CART_REMOVE": "Eliminar",
    "UI_CART_POINTS_TITLE": "Puntos y recompensas",
    "UI_CART_POINTS_PREFIX": "Este pedido suma aprox.",
    "UI_CART_POINTS_UNIT": "puntos",
    "UI_CART_POINTS_REMOVE_DISCOUNT": "Quitar descuento",
    "UI_CART_POINTS_AVAILABLE": "puntos disponibles",
    "UI_CART_POINTS_READY": "Tus puntos están listos. El descuento se aplica en el paso de entrega.",
    "UI_CART_POINTS_CLEAR": "Quitar puntos de la sesión",
    "UI_CART_POINTS_VERIFY_HELP": "¿Tienes puntos acumulados? Consulta y canjea con tu WhatsApp.",
    "UI_CART_POINTS_SEND_CODE": "Enviar código",
    "UI_CART_POINTS_CODE_HELP": "Introduce el código recibido por WhatsApp:",
    "UI_CART_POINTS_VERIFY": "Verificar",
    "UI_CART_POINTS_CHANGE_PHONE": "Cambiar número",
    "UI_CART_PHONE_PLACEHOLDER": "+34 600 000 000",
    "UI_CART_CODE_PLACEHOLDER": "000000",
    "UI_CART_PHONE_REQUIRED": "Introduce tu número",
    "UI_CART_CODE_REQUIRED": "Introduce el código",
    "UI_CART_SENDING": "Enviando...",
    "UI_CART_VERIFYING": "Verificando...",
    "UI_CART_GENERIC_ERROR": "Error",
    "UI_CART_NETWORK_ERROR": "Error de red",
    "UI_CART_INVALID_CODE": "Código incorrecto",
    "UI_CART_REDEEMABLE_TITLE": "Productos canjeables con tus puntos",
    "UI_CART_SELECTED": "Seleccionado",
    "UI_CART_AVAILABLE": "Disponible",
    "UI_CART_MISSING_PREFIX": "Faltan",
    "UI_CART_ALLERGEN_NOTICE": "Revisa los alérgenos. Si tienes intolerancias o alergias indícalo en las notas al confirmar el pedido.",
    "UI_CART_SUMMARY": "Resumen",
    "UI_CART_SUBTOTAL": "Subtotal",
    "UI_CART_SHIPPING": "Envío",
    "UI_CART_POINTS_DISCOUNT": "Descuento puntos",
    "UI_CART_INCOMPATIBLE": "Revisa los productos incompatibles",
    "UI_CART_TOTAL": "Total estimado",
    "UI_CART_CHECKOUT_BOTH": "Elegir entrega o recogida",
    "UI_CART_CHECKOUT_DELIVERY": "Continuar con entrega",
    "UI_CART_CHECKOUT_PICKUP": "Continuar para recoger",
    "UI_CART_CONTINUE": "Seguir comprando",
    "UI_CART_EMPTY_TITLE": "Tu carrito está vacío",
    "UI_CART_EMPTY_TEXT": "Explora el menú y encuentra tu favorito",
    "UI_CART_VIEW_MENU": "Ver menú completo",
    "UI_PWA_DESCRIPTION": "Añade la tienda a tu pantalla de inicio para acceder sin abrir el navegador.",
    "UI_PWA_IOS_INSTRUCTION": "En Safari, toca Compartir y luego Añadir a pantalla de inicio.",
    "UI_PWA_INSTALL": "Instalar",
    "UI_PWA_IOS_ACTION": "Ver cómo",
    "UI_PWA_INAPP_INSTRUCTION": "Abre este sitio en Safari o Chrome para instalar la aplicación y activar todas sus funciones.",
    "UI_PWA_INAPP_ACTION": "Cómo instalar",
    "UI_PWA_OFFLINE": "Preparar offline",
    "UI_PWA_NOTIFICATIONS": "Notificaciones",
    "UI_PWA_NOTIFICATIONS_ACTIVE": "Activas",
    "UI_PWA_PUSH_TITLE": "Seguimiento en tiempo real",
    "UI_PWA_PUSH_TEXT": "Activa avisos sobre el estado de tus pedidos.",
    "UI_PWA_PUSH_ACTION": "Activar",
    "UI_PWA_APP_SECTION": "Experiencia de aplicación",
    "UI_PWA_APP_SECTION_TEXT": "Instala la tienda y conserva recursos esenciales para abrirla más rápido.",
    "UI_PWA_STORAGE_PERSISTED": "Almacenamiento persistente activado.",
    "UI_PWA_STORAGE_AVAILABLE": "La aplicación puede funcionar sin conexión; el navegador gestionará el espacio.",
    "UI_PWA_STORAGE_MANAGED": "El navegador gestionará el almacenamiento automáticamente.",
    "UI_INFO_HELP": "Info y ayuda",
    "UI_NAV_HOME": "Inicio",
    "UI_NAV_SEARCH": "Buscar",
    "UI_NAV_INFO": "Info",
    "UI_NAV_CART": "Carrito",
}


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
    **PUBLIC_THEME_DEFAULTS,
    **PUBLIC_UI_DEFAULTS,
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
    profile = {
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
    profile["theme"] = {
        key.removeprefix("COLOR_").lower(): get_store_value(key, default)
        for key, default in PUBLIC_THEME_DEFAULTS.items()
    }
    profile["ui"] = {
        key.removeprefix("UI_").lower(): get_store_value(key, default)
        for key, default in PUBLIC_UI_DEFAULTS.items()
    }
    return profile
