"""Acceso único a la configuración personalizable de cada tienda."""
from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from ipaddress import ip_address
from urllib.parse import urlparse


BRAND_COLOR_DEFAULTS = {
    "COLOR_PRIMARIO": "#F4C542",
    "COLOR_SECUNDARIO": "#DA4D40",
    "COLOR_ACENTO": "#245A9A",
}


PUBLIC_THEME_DEFAULTS = {
    "COLOR_FONDO_APP": "#FFF9E8",
    "COLOR_SUPERFICIE": "#FFFFFF",
    "COLOR_SUPERFICIE_ALT": "#FFE7A3",
    "COLOR_TEXTO": "#2B2118",
    "COLOR_TEXTO_SUAVE": "#6B5B4D",
    "COLOR_CABECERA_FONDO": "#162A46",
    "COLOR_CABECERA_TEXTO": "#FFF9E8",
    "COLOR_EXITO": "#27875D",
    "COLOR_ALERTA": "#C63E3E",
    "COLOR_INFORMATIVO": "#326FA8",
    "COLOR_ADVERTENCIA": "#B66A00",
    "COLOR_PROMOCION": "#DA4D40",
    "COLOR_DESTACADO": "#245A9A",
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
    "UI_CART_ALLERGEN_NOTICE": "Revisa los alérgenos. Si tienes intolerancias o alergias indícalo en las notas al confirmar el pedido.",
    "UI_CART_SUMMARY": "Resumen",
    "UI_CART_SUBTOTAL": "Subtotal",
    "UI_CART_SHIPPING": "Envío",
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
    "TARJETA_HABILITADA": "1",
    "MODO_TIENDA": "propia",
    "FEATURE_DELIVERY": "1",
    "FEATURE_RECOGIDA": "1",
    "FEATURE_PEDIDOS_PROGRAMADOS": "1",
    "FEATURE_PUNTOS": "1",
    "SERVICE_COMMISSION_PCT": "0",
    "LOGO_URL": "",
    "APP_ICON_URL": "",
    "HERO_IMAGE_URL": "",
    **BRAND_COLOR_DEFAULTS,
    "BRAND_FALLBACK_EMOJI": "🥟",
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


def _absolute_http_url(value: str | None) -> str:
    url = str(value or "").strip().rstrip("/")
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url


def _is_private_url(value: str | None) -> bool:
    url = _absolute_http_url(value)
    if not url:
        return False
    host = (urlparse(url).hostname or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return True
    try:
        parsed_ip = ip_address(host)
    except ValueError:
        return False
    return (
        parsed_ip.is_private
        or parsed_ip.is_loopback
        or parsed_ip.is_link_local
        or parsed_ip.is_reserved
    )


def get_public_store_url(request_root: str | None = None) -> str:
    """URL pública para enlaces de cliente, evitando filtrar hosts internos.

    La configuración guardada sigue siendo editable desde superadmin, pero si
    una instalación pública conserva una URL privada antigua, se prefiere un
    valor público disponible o el host real de la petición.
    """
    request_url = _absolute_http_url(request_root)
    request_is_public = bool(request_url and not _is_private_url(request_url))
    fallback_private = ""

    for candidate in (
        get_store_value("TIENDA_URL", ""),
        get_store_value("OXIDIAN_PUBLIC_URL", ""),
        request_url,
    ):
        url = _absolute_http_url(candidate)
        if not url:
            continue
        if _is_private_url(url):
            fallback_private = fallback_private or url
            if request_is_public:
                continue
        return url
    return request_url or fallback_private


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
        "tarjeta_habilitada": get_store_value("TARJETA_HABILITADA") == "1",
        "service_commission_pct": get_store_value("SERVICE_COMMISSION_PCT", "0"),
        **get_store_features(),
        "logo_url": get_store_value("LOGO_URL"),
        "app_icon_url": get_store_value("APP_ICON_URL"),
        "hero_image_url": get_store_value("HERO_IMAGE_URL"),
        "color_primario": get_store_value("COLOR_PRIMARIO"),
        "color_secundario": get_store_value("COLOR_SECUNDARIO"),
        "color_acento": get_store_value("COLOR_ACENTO"),
        "fallback_emoji": get_store_value("BRAND_FALLBACK_EMOJI", "🥟") or "🥟",
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


# ─── Autoridad sobre SiteConfig ────────────────────────────────────────
# Claves cuyo control queda reservado al super_admin. El admin operativo
# puede ver estas claves pero jamás modificarlas — el UI las muestra como
# solo-lectura con etiqueta "Controla el super admin".
#
# Cubren: modo comercial, comisiones, toggles de features, integraciones
# del bot, credenciales de IA y sistema. Cualquier ruta o endpoint que
# ofrezca cambiar SiteConfig debe consultar este set antes de aceptar el
# cambio bajo un rol admin.
LOCKED_CONFIG_KEYS = frozenset({
    # Modo comercial y comisiones
    "MODO_TIENDA",
    "SERVICE_COMMISSION_PCT",
    # Toggles de features del producto (super_admin decide qué contrata)
    "FEATURE_DELIVERY", "FEATURE_RECOGIDA",
    "FEATURE_PEDIDOS_PROGRAMADOS", "FEATURE_PUNTOS",
    # Integraciones y bot (super_admin gestiona el WhatsApp central)
    "BOT_API_URL", "BOT_OXIDIAN_URL", "BOT_API_KEY", "BOT_PANEL_KEY",
    "BOT_ADMIN_NUMBERS", "BOT_AI_ENABLED", "BOT_AI_API_KEY",
    "BOT_AI_PROVIDER", "BOT_AI_MODEL", "BOT_AI_RULES",
    "BOT_AI_DAILY_CLIENT", "BOT_AI_DAILY_GLOBAL",
    "BOT_EMAIL_DOMAIN",
    "EVOLUTION_API_URL", "EVOLUTION_INSTANCE",
    # Sistema
    "OXIDIAN_PUBLIC_URL", "TIENDA_URL", "ALLOW_DEMO_RESET",
    # Reset masivo de puntos (afecta a todos los clientes)
    "POINTS_RESET_PERIOD_DAYS", "POINTS_LAST_RESET_AT",
})


def user_puede_modificar_clave(user, clave):
    """El super_admin puede tocar cualquier clave. Cualquier otro rol no
    puede tocar las `LOCKED_CONFIG_KEYS`. Fuente única para UI y save."""
    if getattr(user, "rol", None) == "super_admin":
        return True
    return clave not in LOCKED_CONFIG_KEYS


# ─── Claves que exigen refrescar el bot de WhatsApp ────────────────────
# Cuando un admin cambia CUALQUIERA de estas claves, el bot Node debe
# resincronizar su caché local (branding, feature flags, textos) para
# reflejar el cambio inmediatamente en la conversación con clientes en
# curso. El resto de claves (colores UI, defaults técnicos, etc) se
# refrescan pasivamente en el ciclo de 10 min — no hace falta pushear.
#
# Ampliar aquí cuando se detecte que otra clave requiere latencia < 10min.
CLAVES_QUE_REFRESCAN_BOT = frozenset({
    # Modo comercial → determina qué comandos del bar se exponen.
    "MODO_TIENDA",
    # Rubro / etiqueta del catálogo → cambia "Menú" ↔ "Catálogo" en menús.
    "TIPO_TIENDA", "VERTICAL_LABEL",
    # Identidad visible al cliente en cada saludo.
    "NOMBRE_NEGOCIO", "TELEFONO_NEGOCIO", "TIENDA_URL", "OXIDIAN_PUBLIC_URL",
    # Toggles de features → controlan qué opciones muestra el menú (3/4).
    "FEATURE_DELIVERY", "FEATURE_RECOGIDA",
    "FEATURE_PEDIDOS_PROGRAMADOS", "FEATURE_PUNTOS",
    # Horario y forzado de cierre → el bot debe reflejarlos al instante
    # para no aceptar pedidos fuera de ventana.
    "HORARIO_APERTURA", "HORARIO_CIERRE",
    "TIENDA_FORZAR_CERRADA", "MENSAJE_CIERRE",
    # Números administrativos y IA → cambios raros pero críticos.
    "BOT_ADMIN_NUMBERS",
    "BOT_AI_ENABLED", "BOT_AI_PROVIDER", "BOT_AI_MODEL", "BOT_AI_RULES",
})


def alguna_clave_refresca_bot(claves) -> bool:
    """True si al menos una clave de la iterable dispara refresh del bot."""
    for c in claves or ():
        if c in CLAVES_QUE_REFRESCAN_BOT:
            return True
    return False
