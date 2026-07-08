from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_required, current_user
from functools import wraps
from datetime import datetime, date, timedelta, timezone
from calendar import monthrange
from sqlalchemy import func, or_
from urllib.parse import urlparse
import os
import re
import uuid

from extensions import db, get_or_404
from models import (User, Order, Caja, StaffPayment, Product,
                    ZonaEntrega, SiteConfig, AuditLog,
                    AdminFeature, ADMIN_FEATURES, PointsLog, utcnow)
from store_config import PUBLIC_THEME_DEFAULTS, PUBLIC_UI_DEFAULTS, get_store_features, get_store_value

superadmin_bp = Blueprint("superadmin", __name__)

def _eget(key, fallback=""):
    """Lee variable de entorno; igual que _env_default en app.py."""
    return os.environ.get(key, fallback)

CLAVES_DEFAULT = [
    ("PUNTOS_POR_EURO",        "1",    "Puntos que gana el cliente por cada euro gastado"),
    ("PUNTOS_CANJE_RATIO",     "100",  "Puntos necesarios para obtener 1€ de descuento"),
    ("ALERTA_CADUCIDAD_DIAS",  "7",    "Días antes de caducidad para mostrar alerta de stock"),
    ("NOMBRE_NEGOCIO",         _eget("NOMBRE_NEGOCIO"),            "Nombre del negocio"),
    ("SLOGAN_NEGOCIO",         "",                                  "Eslogan o tagline del negocio"),
    ("DESCRIPCION_NEGOCIO",    "",                                  "Descripción breve del negocio (SEO)"),
    ("HERO_IMAGE_URL",         "",                                  "Imagen de cabecera de la tienda (URL o ruta de subida)"),
    ("DIRECCION_NEGOCIO",      _eget("DIRECCION_NEGOCIO"),          "Dirección del local"),
    ("CIUDAD_NEGOCIO",         _eget("CIUDAD_NEGOCIO"),             "Ciudad (para geocodificación)"),
    ("PROVINCIA_NEGOCIO",      _eget("PROVINCIA_NEGOCIO"),          "Provincia o región"),
    ("PAIS_NEGOCIO",           _eget("PAIS_NEGOCIO"),               "País del negocio"),
    ("PAIS_CODIGO_ISO",        _eget("PAIS_CODIGO_ISO"),            "Código ISO de país para geocodificación"),
    ("TELEFONO_NEGOCIO",       _eget("TELEFONO_NEGOCIO"),           "Teléfono de contacto"),
    ("EMAIL_CONTACTO",         _eget("EMAIL_CONTACTO"),              "Correo público de contacto"),
    ("WHATSAPP_COUNTRY_CODE",  _eget("WHATSAPP_COUNTRY_CODE"),       "Prefijo telefónico internacional"),
    ("BIZUM_TELEFONO",         _eget("BIZUM_TELEFONO"),              "Número que recibe pagos Bizum"),
    ("BIZUM_HABILITADO",       "1",                                  "Permitir pagos mediante Bizum"),
    ("EFECTIVO_HABILITADO",    "1",                                  "Permitir pagos en efectivo"),
    ("TARJETA_HABILITADA",     "1",                                  "Permitir pagos con tarjeta al recoger o al repartidor"),
    ("MODO_TIENDA",            "propia",                             "Modo comercial de la instalación"),
    ("TIPO_TIENDA",            "comida",                             "Vertical del catálogo: comida o producto genérico (ropa, retail)"),
    ("FEATURE_DELIVERY",       "1",                                  "Permitir pedidos a domicilio"),
    ("FEATURE_RECOGIDA",       "1",                                  "Permitir pedidos para recoger"),
    ("FEATURE_PEDIDOS_PROGRAMADOS", "1",                              "Permitir productos/pedidos con fecha de entrega"),
    ("FEATURE_PUNTOS",         "1",                                  "Activar club de puntos y canjes"),
    ("SERVICE_COMMISSION_PCT", "0",                                  "Porcentaje ganado por venta en modo servicio"),
    ("BOT_API_KEY",            _eget("BOT_API_KEY"),                "Clave API para el bot de WhatsApp"),
    ("BOT_API_URL",            _eget("BOT_API_URL",    "http://127.0.0.1:3000"),  "URL interna del bot"),
    ("BOT_PANEL_KEY",          _eget("BOT_PANEL_KEY"),              "Clave de panel para administrar el bot desde Oxidian"),
    ("BOT_ADMIN_NUMBERS",      "",                                  "Números adicionales autorizados para administrar el chatbot"),
    ("BOT_OXIDIAN_URL",        _eget("BOT_OXIDIAN_URL", "http://127.0.0.1:5000"), "URL interna que usa el bot para llamar a Oxidian"),
    ("OXIDIAN_PUBLIC_URL",     _eget("OXIDIAN_PUBLIC_URL"),         "URL pública/local de Oxidian para clientes y enlaces"),
    ("EVOLUTION_API_URL",      _eget("EVOLUTION_API_URL", "http://evolution-api:8080"), "URL interna de Evolution API"),
    ("EVOLUTION_API_KEY",      _eget("EVOLUTION_API_KEY"),          "Clave API de Evolution"),
    ("EVOLUTION_INSTANCE",     _eget("EVOLUTION_INSTANCE", "oxidian"), "Instancia WhatsApp de Evolution"),
    ("WEBHOOK_SECRET",         _eget("WEBHOOK_SECRET"),             "Secreto del webhook Evolution -> Oxidian"),
    ("HORARIO_APERTURA",       "09:00", "Hora de apertura (HH:MM)"),
    ("HORARIO_CIERRE",         "22:30", "Hora de cierre (HH:MM)"),
    ("TIENDA_MENSAJE_CIERRE",  "",      "Mensaje que se muestra cuando la tienda está cerrada"),
    ("VALIDAR_RADIO_ENTREGA",  "1",     "Activar validación de distancia para checkout"),
    ("BLOQUEAR_DIRECCION_NO_VERIFICADA", "1", "Bloquear checkout si la dirección no se puede geocodificar"),
    ("RADIO_ENTREGA_KM",       "5",     "Radio máximo de entrega en km"),
    ("PEDIDO_MINIMO_EUR",      "0",     "Monto mínimo de pedido en euros (0 = sin mínimo)"),
    ("AUTO_DESTACADOS_ENABLED", "1",    "Mostrar recomendaciones automáticas si no hay destacados configurados"),
    ("CART_MAX_QTY",           "99",    "Cantidad máxima por producto en carrito"),
    ("COMBO_MIN_COMPONENTS",   "1",     "Mínimo de componentes requeridos para crear un combo"),
    ("COMBO_MAX_COMPONENTS",   "30",    "Máximo de componentes permitidos por combo"),
    ("COMBO_MAX_QTY_COMPONENT", "50",   "Cantidad máxima por componente dentro de un combo"),
    ("COMBO_MAX_SELECTIONS_GROUP", "10", "Máximo de selecciones permitidas por grupo elegible"),
    ("COMBO_MAX_DISCOUNT_PCT", "50",    "Descuento porcentual máximo permitido para combos"),
    ("TIENDA_URL",             _eget("TIENDA_URL"),                 "URL de la tienda para mostrar en WhatsApp"),
]
CLAVES_DEFAULT.extend(
    (key, value, "Token visual configurable de la tienda")
    for key, value in PUBLIC_THEME_DEFAULTS.items()
)
CLAVES_DEFAULT.extend(
    (key, value, "Texto público configurable de la tienda")
    for key, value in PUBLIC_UI_DEFAULTS.items()
)

PUBLIC_UI_FIELDS = [
    ("UI_CLOSE", "Acción general · cerrar"),
    ("UI_HEADER_MODE_BOTH", "Cabecera · delivery y recogida"),
    ("UI_HEADER_MODE_DELIVERY", "Cabecera · solo delivery"),
    ("UI_HEADER_MODE_PICKUP", "Cabecera · solo recogida"),
    ("UI_HEADER_OPEN", "Estado abierto"),
    ("UI_HEADER_CLOSED", "Estado cerrado"),
    ("UI_HEADER_INSTALL", "Acción instalar app"),
    ("UI_HEADER_CART_LABEL", "Etiqueta del pedido"),
    ("UI_HEADER_CART_ACTION", "Acción del carrito"),
    ("UI_HEADER_STAFF", "Acceso de empleados"),
    ("UI_CART_EYEBROW", "Carrito · antetítulo"),
    ("UI_CART_TITLE", "Carrito · título"),
    ("UI_CART_ITEM_ONE", "Carrito · producto singular"),
    ("UI_CART_ITEM_MANY", "Carrito · productos plural"),
    ("UI_CART_BACK", "Carrito · volver"),
    ("UI_CART_PROCESS", "Carrito · nombre del proceso"),
    ("UI_CART_STEP_CART", "Paso carrito"),
    ("UI_CART_STEP_FULFILLMENT", "Paso entrega"),
    ("UI_CART_STEP_PAYMENT", "Paso pago"),
    ("UI_CART_UPDATE", "Actualizar carrito"),
    ("UI_CART_COMBO_BADGE", "Combo · insignia"),
    ("UI_CART_COMBO_VIEW", "Combo · ver detalle"),
    ("UI_CART_COMBO_ITEMS", "Combo · unidad de ítems"),
    ("UI_CART_COMBO_BASE", "Combo · base incluida"),
    ("UI_CART_COMBO_INCLUDED", "Combo · incluido"),
    ("UI_CART_COMBO_SELECTION", "Combo · elección"),
    ("UI_CART_COMBO_KITCHEN", "Combo · contenido de cocina"),
    ("UI_CART_EXTRAS_LABEL", "Producto · extras"),
    ("UI_CART_ORDER_LABEL", "Producto · tipo de pedido"),
    ("UI_CART_NO_ALLERGENS", "Producto · sin alérgenos"),
    ("UI_CART_QUANTITY", "Producto · cantidad"),
    ("UI_CART_LESS", "Producto · reducir cantidad"),
    ("UI_CART_MORE", "Producto · aumentar cantidad"),
    ("UI_CART_REMOVE", "Producto · eliminar"),
    ("UI_CART_POINTS_TITLE", "Título de puntos"),
    ("UI_CART_POINTS_PREFIX", "Puntos · texto anterior al valor"),
    ("UI_CART_POINTS_UNIT", "Puntos · unidad"),
    ("UI_CART_POINTS_REMOVE_DISCOUNT", "Puntos · quitar descuento"),
    ("UI_CART_POINTS_AVAILABLE", "Puntos · disponibles"),
    ("UI_CART_POINTS_READY", "Puntos · mensaje listo"),
    ("UI_CART_POINTS_CLEAR", "Puntos · limpiar sesión"),
    ("UI_CART_POINTS_VERIFY_HELP", "Puntos · ayuda de verificación"),
    ("UI_CART_POINTS_SEND_CODE", "Puntos · enviar código"),
    ("UI_CART_POINTS_CODE_HELP", "Puntos · ayuda del código"),
    ("UI_CART_POINTS_VERIFY", "Puntos · verificar"),
    ("UI_CART_POINTS_CHANGE_PHONE", "Puntos · cambiar teléfono"),
    ("UI_CART_PHONE_PLACEHOLDER", "Puntos · ejemplo de teléfono"),
    ("UI_CART_CODE_PLACEHOLDER", "Puntos · ejemplo de código"),
    ("UI_CART_PHONE_REQUIRED", "Puntos · teléfono requerido"),
    ("UI_CART_CODE_REQUIRED", "Puntos · código requerido"),
    ("UI_CART_SENDING", "Puntos · enviando"),
    ("UI_CART_VERIFYING", "Puntos · verificando"),
    ("UI_CART_GENERIC_ERROR", "Puntos · error genérico"),
    ("UI_CART_NETWORK_ERROR", "Puntos · error de red"),
    ("UI_CART_INVALID_CODE", "Puntos · código incorrecto"),
    ("UI_CART_REDEEMABLE_TITLE", "Puntos · productos canjeables"),
    ("UI_CART_SELECTED", "Puntos · seleccionado"),
    ("UI_CART_AVAILABLE", "Puntos · disponible"),
    ("UI_CART_MISSING_PREFIX", "Puntos · faltan"),
    ("UI_CART_ALLERGEN_NOTICE", "Aviso de alérgenos"),
    ("UI_CART_TOTAL", "Etiqueta del total"),
    ("UI_CART_SUMMARY", "Resumen · título"),
    ("UI_CART_SUBTOTAL", "Resumen · subtotal"),
    ("UI_CART_SHIPPING", "Resumen · envío"),
    ("UI_CART_POINTS_DISCOUNT", "Resumen · descuento de puntos"),
    ("UI_CART_INCOMPATIBLE", "Resumen · incompatibilidades"),
    ("UI_CART_CHECKOUT_BOTH", "Continuar · ambas modalidades"),
    ("UI_CART_CHECKOUT_DELIVERY", "Continuar · delivery"),
    ("UI_CART_CHECKOUT_PICKUP", "Continuar · recogida"),
    ("UI_CART_CONTINUE", "Seguir comprando"),
    ("UI_CART_EMPTY_TITLE", "Carrito vacío · título"),
    ("UI_CART_EMPTY_TEXT", "Carrito vacío · descripción"),
    ("UI_CART_VIEW_MENU", "Carrito vacío · ver menú"),
    ("UI_PWA_DESCRIPTION", "PWA · descripción de instalación"),
    ("UI_PWA_IOS_INSTRUCTION", "PWA · instrucción para iOS"),
    ("UI_PWA_INSTALL", "PWA · instalar"),
    ("UI_PWA_IOS_ACTION", "PWA · acción en iOS"),
    ("UI_PWA_INAPP_INSTRUCTION", "PWA · instrucción en Instagram/WebView"),
    ("UI_PWA_INAPP_ACTION", "PWA · acción en Instagram/WebView"),
    ("UI_PWA_OFFLINE", "PWA · preparar offline"),
    ("UI_PWA_NOTIFICATIONS", "PWA · notificaciones"),
    ("UI_PWA_NOTIFICATIONS_ACTIVE", "PWA · notificaciones activas"),
    ("UI_PWA_PUSH_TITLE", "PWA · título de avisos"),
    ("UI_PWA_PUSH_TEXT", "PWA · descripción de avisos"),
    ("UI_PWA_PUSH_ACTION", "PWA · activar avisos"),
    ("UI_PWA_APP_SECTION", "PWA · título de herramientas"),
    ("UI_PWA_APP_SECTION_TEXT", "PWA · descripción de herramientas"),
    ("UI_PWA_STORAGE_PERSISTED", "PWA · almacenamiento persistente"),
    ("UI_PWA_STORAGE_AVAILABLE", "PWA · disponibilidad offline"),
    ("UI_PWA_STORAGE_MANAGED", "PWA · almacenamiento gestionado"),
    ("UI_INFO_HELP", "Acción de información y ayuda"),
    ("UI_NAV_HOME", "Navegación · inicio"),
    ("UI_NAV_SEARCH", "Navegación · buscar"),
    ("UI_NAV_INFO", "Navegación · información"),
    ("UI_NAV_CART", "Navegación · carrito"),
]

# Labels legibles para cada feature
FEATURE_LABELS = {
    "caja":        "💰 Caja y finanzas",
    "productos":   "📦 Productos y stock",
    "stock":       "🗃️ Gestión de stock",
    "cupones":     "🎟️ Cupones y descuentos",
    "staff_pagos": "👥 Pagos al staff",
    "reportes":    "📊 Reportes y analytics",
    "zonas":       "🗺️ Zonas de entrega",
    "auditoria":   "🔍 Log de auditoría",
    "marketing":   "📣 Marketing y promos",
    "pos":         "🖥️ Punto de venta (POS)",
    "whatsapp":    "💬 WhatsApp y campañas",
    "usuarios":    "👤 Usuarios y equipo",
}

_CONFIG_KEY_RE = re.compile(r"^[A-Z0-9_]{2,50}$")
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_PHONE_INPUT_RE = re.compile(r"^\+?[\d\s().-]+$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

CONFIG_SECTION_KEYS = {
    "tienda-identidad": {
        "NOMBRE_NEGOCIO", "SLOGAN_NEGOCIO", "DESCRIPCION_NEGOCIO",
        "TELEFONO_NEGOCIO", "EMAIL_CONTACTO", "BIZUM_TELEFONO",
        "WHATSAPP_COUNTRY_CODE",
    },
    "tienda-ubicacion": {
        "DIRECCION_NEGOCIO", "CIUDAD_NEGOCIO", "PROVINCIA_NEGOCIO",
        "PAIS_NEGOCIO", "PAIS_CODIGO_ISO",
    },
    "tienda-imagen": {
        "TIENDA_URL", "OXIDIAN_PUBLIC_URL", "LOGO_URL", "APP_ICON_URL",
        "HERO_IMAGE_URL",
    },
    "tienda-colores": {
        "COLOR_PRIMARIO", "COLOR_SECUNDARIO", "COLOR_ACENTO",
    },
    "tienda-tema": set(PUBLIC_THEME_DEFAULTS),
    "tienda-textos": set(PUBLIC_UI_DEFAULTS),
    "operacion-horario": {
        "HORARIO_APERTURA", "HORARIO_CIERRE", "TIENDA_FORZAR_CERRADA",
        "TIENDA_MENSAJE_CIERRE",
    },
    "operacion-pagos": {"EFECTIVO_HABILITADO", "BIZUM_HABILITADO", "TARJETA_HABILITADA"},
    "operacion-modo": {
        "MODO_TIENDA", "TIPO_TIENDA",
        "FEATURE_DELIVERY", "FEATURE_RECOGIDA",
        "FEATURE_PEDIDOS_PROGRAMADOS", "FEATURE_PUNTOS",
        "SERVICE_COMMISSION_PCT",
    },
    "entregas": {
        "VALIDAR_RADIO_ENTREGA", "BLOQUEAR_DIRECCION_NO_VERIFICADA",
        "RADIO_ENTREGA_KM", "CENTRO_LAT", "CENTRO_LON",
        "PEDIDO_MINIMO_EUR",
    },
    "puntos": {"PUNTOS_POR_EURO", "PUNTOS_CANJE_RATIO"},
    "integraciones": {
        "BOT_API_URL", "BOT_OXIDIAN_URL", "EVOLUTION_API_URL",
        "EVOLUTION_INSTANCE",
    },
    "avanzado": {
        "CART_MAX_QTY", "COMBO_MIN_COMPONENTS", "COMBO_MAX_COMPONENTS",
        "COMBO_MAX_QTY_COMPONENT", "COMBO_MAX_SELECTIONS_GROUP",
        "COMBO_MAX_DISCOUNT_PCT", "AUTO_DESTACADOS_ENABLED",
    },
}

CONFIG_SECTION_PARENT = {
    section: section.split("-", 1)[0]
    for section in CONFIG_SECTION_KEYS
}


def _valid_url(value, required=False, allow_internal=True):
    value = (value or "").strip().rstrip("/")
    if not value:
        return (not required), value
    if any(char.isspace() for char in value):
        return False, value
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return False, value
    if parsed.scheme not in ("http", "https") or not hostname:
        return False, value
    if parsed.username or parsed.password:
        return False, value
    if not allow_internal and "." not in hostname and hostname not in ("localhost",):
        return False, value
    return True, value


@superadmin_bp.route("/combos/nuevo")
@login_required
def nuevo_combo():
    """Acceso explicito del Super Admin al constructor unificado de combos."""
    if current_user.rol != "super_admin":
        flash("Solo el super admin puede acceder a este constructor.", "danger")
        return redirect(url_for("auth.login"))
    return redirect(url_for("admin.nuevo_combo"))


def _normalizar_telefono(value):
    raw = (value or "").strip()
    if not raw:
        return ""
    if not _PHONE_INPUT_RE.fullmatch(raw) or raw.count("+") > 1 or ("+" in raw and not raw.startswith("+")):
        return ""
    digits = re.sub(r"\D+", "", raw)
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 9 and digits[0] in "6789":
        digits = "34" + digits
    return digits


def _normalizar_lista_telefonos(value):
    numeros = []
    invalidos = []
    for raw in re.split(r"[\s,;\n]+", value or ""):
        raw = raw.strip()
        if not raw:
            continue
        numero = _normalizar_telefono(raw)
        if not re.fullmatch(r"\d{7,15}", numero):
            invalidos.append(raw)
        elif numero not in numeros:
            numeros.append(numero)
    return numeros, invalidos


def _validar_config_value(clave, valor):
    clave = (clave or "").strip().upper()
    valor = (valor or "").strip()
    if not _CONFIG_KEY_RE.match(clave):
        return False, clave, valor, "La clave debe usar solo MAYÚSCULAS, números y guion bajo."
    if len(valor) > 500:
        return False, clave, valor, "El valor no puede superar 500 caracteres."

    if clave in {
        "VALIDAR_RADIO_ENTREGA", "BLOQUEAR_DIRECCION_NO_VERIFICADA",
        "TIENDA_FORZAR_CERRADA", "BIZUM_HABILITADO", "EFECTIVO_HABILITADO", "TARJETA_HABILITADA",
        "FEATURE_DELIVERY", "FEATURE_RECOGIDA", "FEATURE_PEDIDOS_PROGRAMADOS",
        "FEATURE_PUNTOS",
    }:
        if valor not in {"0", "1"}:
            return False, clave, valor, "Este ajuste solo acepta 0 o 1."
        return True, clave, valor, None

    if clave == "MODO_TIENDA":
        if valor not in {"propia", "bar_servicio"}:
            return False, clave, valor, "Modo de tienda no válido."
        return True, clave, valor, None

    if clave in {"PUNTOS_POR_EURO", "PUNTOS_CANJE_RATIO", "ALERTA_CADUCIDAD_DIAS"}:
        try:
            numero = int(valor)
        except (TypeError, ValueError):
            return False, clave, valor, "Este ajuste debe ser un número entero."
        if numero <= 0 or numero > 100000:
            return False, clave, valor, "El número debe estar entre 1 y 100000."
        return True, clave, str(numero), None

    combo_int_limits = {
        "CART_MAX_QTY": (1, 999),
        "COMBO_MIN_COMPONENTS": (1, 100),
        "COMBO_MAX_COMPONENTS": (1, 200),
        "COMBO_MAX_QTY_COMPONENT": (1, 999),
        "COMBO_MAX_SELECTIONS_GROUP": (1, 100),
    }
    if clave in combo_int_limits:
        min_val, max_val = combo_int_limits[clave]
        try:
            numero = int(valor)
        except (TypeError, ValueError):
            return False, clave, valor, "Este ajuste debe ser un número entero."
        if numero < min_val or numero > max_val:
            return False, clave, valor, f"El número debe estar entre {min_val} y {max_val}."
        return True, clave, str(numero), None

    if clave in {"COMBO_MAX_DISCOUNT_PCT", "SERVICE_COMMISSION_PCT"}:
        try:
            numero = float(valor)
        except (TypeError, ValueError):
            label = "La comisión" if clave == "SERVICE_COMMISSION_PCT" else "El descuento máximo"
            return False, clave, valor, f"{label} debe ser numérico."
        if numero < 0 or numero > 100:
            label = "La comisión" if clave == "SERVICE_COMMISSION_PCT" else "El descuento máximo"
            return False, clave, valor, f"{label} debe estar entre 0 y 100."
        return True, clave, f"{numero:g}", None

    if clave == "RADIO_ENTREGA_KM":
        try:
            numero = float(valor)
        except (TypeError, ValueError):
            return False, clave, valor, "El radio debe ser un número."
        if numero < 0.5 or numero > 50:
            return False, clave, valor, "El radio debe estar entre 0.5 y 50 km."
        return True, clave, f"{numero:g}", None

    if clave == "CENTRO_LAT":
        if not valor:
            return True, clave, "", None
        try:
            numero = float(valor)
        except (TypeError, ValueError):
            return False, clave, valor, "La latitud debe ser numérica."
        if numero < -90 or numero > 90:
            return False, clave, valor, "La latitud debe estar entre -90 y 90."
        return True, clave, f"{numero:.6f}".rstrip("0").rstrip("."), None

    if clave == "CENTRO_LON":
        if not valor:
            return True, clave, "", None
        try:
            numero = float(valor)
        except (TypeError, ValueError):
            return False, clave, valor, "La longitud debe ser numérica."
        if numero < -180 or numero > 180:
            return False, clave, valor, "La longitud debe estar entre -180 y 180."
        return True, clave, f"{numero:.6f}".rstrip("0").rstrip("."), None

    if clave in {"HORARIO_APERTURA", "HORARIO_CIERRE"}:
        if not _TIME_RE.match(valor):
            return False, clave, valor, "El horario debe tener formato HH:MM."
        return True, clave, valor, None

    if clave in {"COLOR_PRIMARIO", "COLOR_SECUNDARIO", "COLOR_ACENTO", *PUBLIC_THEME_DEFAULTS}:
        if not _HEX_COLOR_RE.match(valor):
            return False, clave, valor, "El color debe tener formato hexadecimal, por ejemplo #CE1126."
        return True, clave, valor.upper(), None

    if clave in {
        "TIENDA_URL", "BOT_API_URL", "OXIDIAN_PUBLIC_URL", "LOGO_URL",
        "APP_ICON_URL", "HERO_IMAGE_URL", "EVOLUTION_API_URL", "BOT_OXIDIAN_URL",
    }:
        ok, normalized = _valid_url(valor, required=False)
        if not ok:
            return False, clave, valor, "La URL debe ser http:// o https://, sin espacios ni credenciales."
        return True, clave, normalized, None

    if clave in {"BOT_API_KEY", "BOT_PANEL_KEY"}:
        if valor and len(valor) < 8:
            return False, clave, valor, "La clave debe tener al menos 8 caracteres."
        return True, clave, valor, None

    if clave in {"TELEFONO_NEGOCIO", "BIZUM_TELEFONO"}:
        phone = _normalizar_telefono(valor)
        if valor and not re.fullmatch(r"\d{7,15}", phone):
            return False, clave, valor, "El teléfono debe tener entre 7 y 15 dígitos y solo signos telefónicos válidos."
        return True, clave, phone, None

    if clave == "WHATSAPP_COUNTRY_CODE":
        if valor and not re.fullmatch(r"\+?\d{1,4}", valor):
            return False, clave, valor, "El prefijo debe contener solo entre 1 y 4 dígitos."
        digits = re.sub(r"\D+", "", valor)
        return True, clave, digits, None

    if clave == "EMAIL_CONTACTO":
        if valor and (len(valor) > 254 or not _EMAIL_RE.fullmatch(valor)):
            return False, clave, valor, "El correo de contacto no tiene un formato válido."
        return True, clave, valor.lower(), None

    if clave == "PAIS_CODIGO_ISO":
        code = valor.lower()
        if code and not re.fullmatch(r"[a-z]{2}", code):
            return False, clave, valor, "Usa el código ISO de dos letras, por ejemplo es, co o mx."
        return True, clave, code, None

    if clave in PUBLIC_UI_DEFAULTS:
        if not valor:
            return False, clave, valor, "El texto no puede quedar vacío."
        if len(valor) > 240 or any(ord(char) < 32 and char not in "\t" for char in valor):
            return False, clave, valor, "El texto no puede superar 240 caracteres ni contener controles."
        return True, clave, valor, None

    if clave == "BOT_ADMIN_NUMBERS":
        numeros, invalidos = _normalizar_lista_telefonos(valor)
        if invalidos:
            return False, clave, valor, "Hay teléfonos administrativos inválidos."
        return True, clave, ",".join(numeros), None

    return True, clave, valor, None


def _config_section_submission(form):
    """Valida solo los campos declarados y presentes de una tarjeta."""
    section = (form.get("section") or "").strip()
    allowed = CONFIG_SECTION_KEYS.get(section)
    if allowed is None:
        return section, [], ["Sección de configuración desconocida."]

    requested = form.getlist("config_key")
    if len(requested) != len(set(requested)):
        return section, [], ["La solicitud contiene campos duplicados."]

    changes = []
    errors = []
    for key in requested:
        if key not in allowed:
            errors.append(f"{key}: no pertenece a esta sección.")
            continue
        if key not in form:
            errors.append(f"{key}: el campo no fue enviado.")
            continue
        ok, normalized_key, value, error = _validar_config_value(key, form.get(key))
        if ok:
            changes.append((normalized_key, value))
        else:
            errors.append(f"{key}: {error}")
    return section, changes, errors


def _parse_zona_form(form, zona=None):
    nombre = (form.get("nombre") or (zona.nombre if zona else "") or "").strip()
    descripcion = (form.get("descripcion") or "").strip()
    if not nombre:
        return None, "El nombre de la zona es obligatorio."
    if len(nombre) > 80:
        return None, "El nombre de la zona no puede superar 80 caracteres."
    if len(descripcion) > 200:
        return None, "La descripción de la zona no puede superar 200 caracteres."

    try:
        precio_envio = float(form.get("precio_envio", zona.precio_envio if zona else 0) or 0)
        tiempo_min = int(form.get("tiempo_estimado_min", zona.tiempo_estimado_min if zona else 30) or 30)
        orden = int(form.get("orden", zona.orden if zona else 0) or 0)
        gratis_raw = (form.get("gratis_desde") or "").strip()
        gratis_desde = float(gratis_raw) if gratis_raw else None
    except (ValueError, TypeError):
        return None, "Precio, tiempo, envío gratis u orden inválidos."

    # Geodata opcional para asignación automática de zona en checkout.
    # Si dejas los tres campos vacíos, la zona se sigue gestionando con el
    # comportamiento legacy (zonas[0]) y NO se valida cobertura geográfica.
    def _opt_float(name):
        raw = (form.get(name) or "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            raise ValueError(name)
    try:
        centro_lat = _opt_float("centro_lat")
        centro_lng = _opt_float("centro_lng")
        radio_km = _opt_float("radio_km")
    except ValueError as exc:
        return None, f"Campo geográfico inválido: {exc}"

    if centro_lat is not None and not (-90 <= centro_lat <= 90):
        return None, "centro_lat debe estar entre -90 y 90."
    if centro_lng is not None and not (-180 <= centro_lng <= 180):
        return None, "centro_lng debe estar entre -180 y 180."
    if radio_km is not None and not (0 < radio_km <= 200):
        return None, "radio_km debe estar entre 0 y 200."
    # Geodata es todo-o-nada: si pones uno, pon los tres.
    geo_count = sum(1 for v in (centro_lat, centro_lng, radio_km) if v is not None)
    if 0 < geo_count < 3:
        return None, "Para activar el match geográfico debes informar lat, lng y radio_km."

    if precio_envio < 0 or precio_envio > 100:
        return None, "El precio de envío debe estar entre 0 y 100€."
    if tiempo_min <= 0 or tiempo_min > 180:
        return None, "El tiempo estimado debe estar entre 1 y 180 minutos."
    if gratis_desde is not None and (gratis_desde < 0 or gratis_desde > 10000):
        return None, "El importe de envío gratis debe estar entre 0 y 10000€."

    return {
        "nombre": nombre,
        "descripcion": descripcion,
        "es_epicentro": form.get("es_epicentro", "1") == "1",
        "precio_envio": precio_envio,
        "tiempo_estimado_min": tiempo_min,
        "gratis_desde": gratis_desde,
        "orden": orden,
        "centro_lat": centro_lat,
        "centro_lng": centro_lng,
        "radio_km": radio_km,
    }, None


def superadmin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.rol != "super_admin":
            flash("Acceso exclusivo de super administrador.", "danger")
            return redirect(url_for("public.index"))
        return f(*args, **kwargs)
    return decorated


# ─── DASHBOARD ───────────────────────────────

@superadmin_bp.route("/dashboard")
@superadmin_required
def dashboard():
    features = get_store_features()
    hoy = date.today()
    ayer = hoy - timedelta(days=1)

    total_clientes = User.query.filter_by(rol="cliente", activo=True).count()
    total_staff = User.query.filter(
        User.rol.in_(["preparacion", "repartidor"]),
        User.activo == True
    ).count()
    total_pedidos = Order.query.count()

    ventas_hoy = db.session.query(func.sum(Caja.monto)).filter(
        db.func.date(Caja.fecha) == hoy, Caja.tipo == "ingreso"
    ).scalar() or 0
    ventas_ayer = db.session.query(func.sum(Caja.monto)).filter(
        db.func.date(Caja.fecha) == ayer, Caja.tipo == "ingreso"
    ).scalar() or 0

    ingresos_total = db.session.query(func.sum(Caja.monto)).filter(
        Caja.tipo == "ingreso"
    ).scalar() or 0
    egresos_total = db.session.query(func.sum(Caja.monto)).filter(
        Caja.tipo == "egreso"
    ).scalar() or 0

    pagos_pend = db.session.query(func.sum(StaffPayment.monto)).filter_by(pagado=False).scalar() or 0
    pedidos_sin_preparador = Order.query.filter(
        Order.estado.in_(["pendiente", "armando"]),
        Order.preparador_id == None,
    ).count()
    pedidos_sin_repartidor = 0
    if features["delivery"]:
        pedidos_sin_repartidor = Order.query.filter(
            Order.estado == "listo",
            Order.repartidor_id == None,
            Order.tipo_entrega_cliente == "delivery",
        ).count()
    pedidos_sin_asignar = pedidos_sin_preparador + pedidos_sin_repartidor

    # Puntos
    puntos_emitidos = db.session.query(func.sum(PointsLog.cantidad)).filter(
        PointsLog.tipo == "ganado"
    ).scalar() or 0
    puntos_canjeados = db.session.query(func.sum(PointsLog.cantidad)).filter(
        PointsLog.tipo == "canjeado"
    ).scalar() or 0
    clientes_con_puntos = User.query.filter(User.rol == "cliente", User.puntos > 0).count()

    # Chatbot status (rápido, sin bloquear mucho)
    bot_api_url = SiteConfig.get("BOT_API_URL", _eget("BOT_API_URL", "http://127.0.0.1:3000"))
    bot_status = _bot_get_status(bot_api_url)

    # Config de marca resumida
    brand_config = {
        "nombre":   SiteConfig.get("NOMBRE_NEGOCIO", ""),
        "telefono": SiteConfig.get("TELEFONO_NEGOCIO", ""),
        "color_primario":   SiteConfig.get("COLOR_PRIMARIO", "#FCD116"),
        "color_secundario": SiteConfig.get("COLOR_SECUNDARIO", "#CE1126"),
        "color_acento":     SiteConfig.get("COLOR_ACENTO", "#003087"),
        "logo_url":         SiteConfig.get("LOGO_URL", ""),
        "app_icon_url":     SiteConfig.get("APP_ICON_URL", ""),
    }

    # ── Modo tienda + comisión acumulada ──
    modo_tienda = features.get("modo_tienda", "propia")
    try:
        commission_pct = float(get_store_value("SERVICE_COMMISSION_PCT", "0") or 0)
    except (TypeError, ValueError):
        commission_pct = 0.0

    # Suma la comisión facturada por el hub este mes (bar_servicio)
    comision_mes = 0.0
    comision_hoy = 0.0
    try:
        cols = {c.name for c in Order.__table__.columns}
        if "service_commission_amount" in cols:
            inicio_mes = hoy.replace(day=1)
            comision_mes = float(
                db.session.query(func.coalesce(func.sum(Order.service_commission_amount), 0))
                .filter(Order.creado_en >= inicio_mes)
                .filter(Order.estado.in_(["entregado", "listo", "en_ruta"]))
                .scalar() or 0
            )
            comision_hoy = float(
                db.session.query(func.coalesce(func.sum(Order.service_commission_amount), 0))
                .filter(Order.creado_en >= hoy)
                .filter(Order.estado.in_(["entregado", "listo", "en_ruta"]))
                .scalar() or 0
            )
    except Exception:
        # Si la columna no existe (instalaciones legacy), la comisión queda a 0
        comision_mes = 0.0
        comision_hoy = 0.0

    tienda_context = {
        "modo": modo_tienda,
        "modo_label": "Modo servicio" if modo_tienda == "bar_servicio" else "Modo propio",
        "es_servicio": modo_tienda == "bar_servicio",
        "comision_pct": commission_pct,
        "comision_hoy": comision_hoy,
        "comision_mes": comision_mes,
    }

    return render_template("superadmin/dashboard.html",
                           total_clientes=total_clientes,
                           total_staff=total_staff,
                           total_pedidos=total_pedidos,
                           ventas_hoy=float(ventas_hoy),
                           ventas_ayer=float(ventas_ayer),
                           saldo_total=float(ingresos_total) - float(egresos_total),
                           pagos_pendientes=float(pagos_pend),
                           pedidos_sin_asignar=pedidos_sin_asignar,
                           pedidos_sin_preparador=pedidos_sin_preparador,
                           pedidos_sin_repartidor=pedidos_sin_repartidor,
                           puntos_emitidos=int(puntos_emitidos),
                           puntos_canjeados=int(abs(puntos_canjeados)),
                           clientes_con_puntos=clientes_con_puntos,
                           bot_status=bot_status,
                           brand_config=brand_config,
                           tienda_modo=tienda_context,
                           vertical_just_changed=session.pop("vertical_just_changed", None))


@superadmin_bp.route("/modulos/<modulo>/toggle", methods=["POST"])
@superadmin_required
def toggle_modulo(modulo):
    """Interruptor rápido; SiteConfig sigue siendo la única fuente de verdad."""
    claves = {
        "delivery": "FEATURE_DELIVERY",
        "recogida": "FEATURE_RECOGIDA",
        "programados": "FEATURE_PEDIDOS_PROGRAMADOS",
        "puntos": "FEATURE_PUNTOS",
    }
    clave = claves.get(modulo)
    if not clave:
        flash("Módulo desconocido.", "danger")
        return redirect(url_for("superadmin.dashboard"))
    activar = request.form.get("enabled") == "1"
    features = get_store_features()
    if not activar and (
        (modulo == "delivery" and not features["recogida"])
        or (modulo == "recogida" and not features["delivery"])
    ):
        flash("Debe quedar activo al menos delivery o recogida.", "danger")
        return redirect(url_for("superadmin.dashboard"))
    SiteConfig.set(
        clave,
        "1" if activar else "0",
        user_id=current_user.id,
        descripcion=f"Módulo {modulo}",
    )
    AuditLog.registrar(
        current_user.id,
        "toggle_modulo",
        "site_config",
        detalle=f"{clave}={'1' if activar else '0'}",
        ip=request.remote_addr,
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("No se pudo actualizar el módulo.", "danger")
        return redirect(url_for("superadmin.dashboard"))
    ok, msg = _sincronizar_chatbot_runtime()
    estado = "activado" if activar else "desactivado"
    flash(
        f"Módulo {modulo} {estado}." + ("" if ok else f" Bot pendiente: {msg}"),
        "success" if ok else "warning",
    )
    return redirect(url_for("superadmin.dashboard"))


@superadmin_bp.route("/toggle-vertical", methods=["POST"])
@superadmin_required
def toggle_vertical():
    """Cambia el vertical de la tienda (comida ↔ producto) en un clic.

    Solo altera SiteConfig['TIPO_TIENDA']. NO borra datos ni migra:
    alérgenos, categorías, presentaciones siguen guardados y se re-mostrarán
    si el admin vuelve al modo comida. Los emojis, etiquetas ("menú"→"catálogo"),
    manifest PWA, bot y push notifications se adaptan automáticamente en la
    siguiente request porque leen el flag en cada render.
    """
    actual = (SiteConfig.get("TIPO_TIENDA", "comida") or "comida").lower()
    destino = "producto" if actual == "comida" else "comida"
    # Permite forzar valor explícito por si el UI manda `destino`.
    # "mixto" añadido para exponer ambos verticales a la vez sin filtrar.
    override = (request.form.get("destino") or "").strip().lower()
    if override in ("comida", "producto", "mixto"):
        destino = override
    SiteConfig.set(
        "TIPO_TIENDA", destino,
        user_id=current_user.id,
        descripcion="Vertical del catálogo",
    )
    AuditLog.registrar(
        current_user.id,
        "toggle_vertical",
        "site_config",
        detalle=f"TIPO_TIENDA={actual}→{destino}",
        ip=request.remote_addr,
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("No se pudo cambiar el vertical de la tienda.", "danger")
        return redirect(url_for("superadmin.dashboard"))
    _sincronizar_chatbot_runtime()  # el bot re-lee /branding en su próximo sync
    label = "🍽️ Comida / gastronomía" if destino == "comida" else "🛍️ Producto / retail"
    # Bandera de "recién cambiado" para que el dashboard muestre un banner
    # con link a la vista cliente. Se limpia al primer render.
    session["vertical_just_changed"] = {
        "actual": actual,
        "destino": destino,
        "label": label,
        "at": int(datetime.now(timezone.utc).replace(tzinfo=None).timestamp()),
    }
    flash(
        f"✅ Vertical cambiado a {label}. "
        "Etiquetas, emojis y alérgenos se han adaptado automáticamente. "
        "Los productos, categorías y datos existentes se conservan.",
        "success",
    )
    return redirect(url_for("superadmin.dashboard"))


@superadmin_bp.route("/chatbot")
@superadmin_required
def chatbot():
    bot_api_key = SiteConfig.get("BOT_API_KEY", "")
    bot_api_url = SiteConfig.get("BOT_API_URL", _eget("BOT_API_URL", "http://127.0.0.1:3000"))
    bot_panel_key = SiteConfig.get("BOT_PANEL_KEY", "")
    tienda_url = SiteConfig.get("TIENDA_URL", "")
    oxidian_public_url = SiteConfig.get("OXIDIAN_PUBLIC_URL", request.url_root.rstrip("/"))
    evolution_api_url = SiteConfig.get("EVOLUTION_API_URL", _eget("EVOLUTION_API_URL", "http://evolution-api:8080"))
    evolution_api_key = SiteConfig.get("EVOLUTION_API_KEY", "")
    evolution_instance = SiteConfig.get("EVOLUTION_INSTANCE", "oxidian")
    webhook_secret = SiteConfig.get("WEBHOOK_SECRET", "")
    bot_admin_numbers = SiteConfig.get("BOT_ADMIN_NUMBERS", "")
    bot_ai_enabled = SiteConfig.get("BOT_AI_ENABLED", "0") or "0"
    bot_ai_provider = (SiteConfig.get("BOT_AI_PROVIDER", "") or "").strip().lower()
    bot_ai_model = SiteConfig.get("BOT_AI_MODEL", "") or ""
    bot_ai_rules = SiteConfig.get("BOT_AI_RULES", "") or ""
    bot_ai_daily_client = SiteConfig.get("BOT_AI_DAILY_CLIENT", "20") or "20"
    bot_ai_daily_global = SiteConfig.get("BOT_AI_DAILY_GLOBAL", "500") or "500"
    bot_ai_api_key_set = bool(SiteConfig.get("BOT_AI_API_KEY", ""))
    status = _bot_get_status(bot_api_url)
    evolution_status = _evolution_get_status(evolution_api_url, evolution_api_key)
    return render_template("superadmin/chatbot.html",
                           bot_api_key=bot_api_key,
                           bot_api_url=bot_api_url,
                           bot_panel_key=bot_panel_key,
                           tienda_url=tienda_url,
                           oxidian_public_url=oxidian_public_url,
                           evolution_api_url=evolution_api_url,
                           evolution_api_key=evolution_api_key,
                           evolution_instance=evolution_instance,
                           webhook_secret=webhook_secret,
                           bot_admin_numbers=bot_admin_numbers,
                           bot_ai_enabled=bot_ai_enabled,
                           bot_ai_provider=bot_ai_provider,
                           bot_ai_model=bot_ai_model,
                           bot_ai_rules=bot_ai_rules,
                           bot_ai_daily_client=bot_ai_daily_client,
                           bot_ai_daily_global=bot_ai_daily_global,
                           bot_ai_api_key_set=bot_ai_api_key_set,
                           evolution_status=evolution_status,
                           status=status)


def _bot_base_url():
    return (SiteConfig.get("BOT_API_URL", _eget("BOT_API_URL", "http://127.0.0.1:3000")) or "").rstrip("/")


def _bot_panel_key():
    return (
        SiteConfig.get("BOT_PANEL_KEY", "")
        or os.environ.get("BOT_PANEL_KEY", "").strip()
        or SiteConfig.get("BOT_API_KEY", "")
        or os.environ.get("BOT_API_KEY", "").strip()
    )


def _bot_get_status(bot_url=None):
    import requests
    url = (bot_url or _bot_base_url()).rstrip("/")
    if not url:
        return {"ok": False, "error": "BOT_API_URL no configurada"}
    try:
        key = _bot_panel_key()
        headers = {"X-Panel-Key": key} if key else {}
        resp = requests.get(f"{url}/api/status", headers=headers, timeout=3)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        data["ok"] = resp.ok
        data["http_status"] = resp.status_code
        return data
    except Exception as exc:
        msg = str(exc)
        if "Connection refused" in msg or "ConnectionRefusedError" in msg:
            msg = f"No se puede conectar con el bot en {url}. Verifica que el servicio esté en marcha."
        elif "timed out" in msg.lower():
            msg = f"Timeout al conectar con {url}. El bot no responde."
        elif len(msg) > 120:
            msg = msg[:120] + "…"
        return {"ok": False, "error": msg}


def _evolution_get_status(evolution_url=None, evolution_key=None):
    import requests
    url = (evolution_url or SiteConfig.get("EVOLUTION_API_URL", "") or "").rstrip("/")
    if not url:
        return {"ok": False, "error": "EVOLUTION_API_URL no configurada"}
    headers = {}
    key = evolution_key or SiteConfig.get("EVOLUTION_API_KEY", "")
    if key:
        headers["apikey"] = key
    try:
        resp = requests.get(f"{url}/", headers=headers, timeout=3)
        return {
            "ok": resp.ok,
            "http_status": resp.status_code,
            "url": url,
            "error": "" if resp.ok else f"HTTP {resp.status_code}",
        }
    except Exception as exc:
        msg = str(exc)
        if "Connection refused" in msg or "ConnectionRefusedError" in msg:
            msg = f"No se puede conectar con Evolution en {url}. Verifica que el servicio esté en marcha."
        elif "timed out" in msg.lower():
            msg = f"Timeout al conectar con {url}. Evolution no responde."
        elif len(msg) > 120:
            msg = msg[:120] + "..."
        return {"ok": False, "url": url, "error": msg}


def _bot_panel_post(path, payload=None, timeout=5, panel_key=None):
    import requests
    bot_url = _bot_base_url()
    key = panel_key or _bot_panel_key()
    if not bot_url:
        return False, "BOT_API_URL no configurada"
    if not key:
        return False, "BOT_PANEL_KEY o BOT_API_KEY no configurada"
    try:
        resp = requests.post(
            f"{bot_url}{path}",
            json=payload or {},
            headers={"X-Panel-Key": key},
            timeout=timeout,
        )
        try:
            data = resp.json()
        except Exception:
            data = {}
        if resp.ok and data.get("ok", True):
            return True, data.get("mensaje") or data.get("message") or "OK"
        return False, data.get("error") or data.get("mensaje") or f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)


def _sincronizar_chatbot_runtime(panel_key=None):
    bot_key = SiteConfig.get("BOT_API_KEY", "")
    bot_panel_key = SiteConfig.get("BOT_PANEL_KEY", "") or bot_key
    oxidian_url = (
        os.environ.get("BOT_OXIDIAN_URL")
        or os.environ.get("OXIDIAN_URL")
        or SiteConfig.get("BOT_OXIDIAN_URL", "")
        or "http://127.0.0.1:5000"
    ).rstrip("/")
    public_url = SiteConfig.get("OXIDIAN_PUBLIC_URL", "") or request.url_root.rstrip("/")
    tienda_url = SiteConfig.get("TIENDA_URL", "") or public_url
    evolution_url = SiteConfig.get("EVOLUTION_API_URL", "")
    evolution_key = SiteConfig.get("EVOLUTION_API_KEY", "")
    evolution_instance = SiteConfig.get("EVOLUTION_INSTANCE", "")
    webhook_secret = SiteConfig.get("WEBHOOK_SECRET", "")
    admin_numbers, invalid_admin_numbers = _normalizar_lista_telefonos(
        SiteConfig.get("BOT_ADMIN_NUMBERS", "")
    )
    if not bot_key:
        return False, "BOT_API_KEY no configurada"
    if invalid_admin_numbers:
        return False, "La lista de administradores del bot contiene teléfonos inválidos"

    ok_set, msg_set = _bot_panel_post(
        "/api/bot/set-key",
        {"key": bot_key, "panel_key": bot_panel_key},
        panel_key=panel_key,
    )
    if not ok_set:
        return False, f"No se pudo guardar la key para envíos WhatsApp: {msg_set}"

    # Desde este punto el bot ya exige la nueva clave de panel.
    active_panel_key = bot_panel_key or panel_key
    ok_ox, msg_ox = _bot_panel_post(
        "/api/oxidian/key",
        {"key": bot_key, "panel_key": bot_panel_key, "url": oxidian_url, "tienda_url": tienda_url},
        panel_key=active_panel_key,
    )
    if not ok_ox:
        return False, f"Key de envío guardada, pero falló la conexión bot→Oxidian: {msg_ox}"

    ok_evo, msg_evo = _bot_panel_post(
        "/api/evolution/config",
        {
            "evolution_url": evolution_url,
            "evolution_key": evolution_key,
            "evolution_instance": evolution_instance,
            "webhook_secret": webhook_secret,
        },
        panel_key=active_panel_key,
    )
    if not ok_evo:
        return False, f"Oxidian conectado, pero falló la configuración Evolution del bot: {msg_evo}"

    ok_admins, msg_admins = _bot_panel_post(
        "/api/admins/config",
        {"admins": admin_numbers},
        panel_key=active_panel_key,
    )
    if not ok_admins:
        return False, f"Integraciones conectadas, pero falló la lista de administradores: {msg_admins}"

    ok_sync, msg_sync = _bot_panel_post(
        "/api/oxidian/sync", {}, timeout=15, panel_key=active_panel_key
    )
    if not ok_sync:
        return False, f"Credenciales guardadas, pero falló la sincronización de módulos e IA: {msg_sync}"
    return True, "Credenciales, módulos, catálogo e IA sincronizados con el bot."


@superadmin_bp.route("/chatbot/guardar", methods=["POST"])
@superadmin_required
def guardar_chatbot():
    previous_panel_key = _bot_panel_key()
    bot_url = request.form.get("bot_api_url", "").strip().rstrip("/")
    bot_key = request.form.get("bot_api_key", "").strip() or SiteConfig.get("BOT_API_KEY", "")
    panel_key = request.form.get("bot_panel_key", "").strip() or SiteConfig.get("BOT_PANEL_KEY", "")
    oxidian_url = request.form.get("oxidian_public_url", "").strip().rstrip("/")
    tienda_url = request.form.get("tienda_url", "").strip().rstrip("/")
    evolution_url = request.form.get("evolution_api_url", "").strip().rstrip("/")
    evolution_key = request.form.get("evolution_api_key", "").strip() or SiteConfig.get("EVOLUTION_API_KEY", "")
    evolution_instance = request.form.get("evolution_instance", "").strip()
    webhook_secret = request.form.get("webhook_secret", "").strip() or SiteConfig.get("WEBHOOK_SECRET", "")
    admin_numbers_raw = request.form.get("bot_admin_numbers", "").strip()
    admin_numbers, invalid_admin_numbers = _normalizar_lista_telefonos(admin_numbers_raw)
    env_bot_key = os.environ.get("BOT_API_KEY", "").strip()
    env_panel_key = os.environ.get("BOT_PANEL_KEY", "").strip()

    ok_url, bot_url = _valid_url(bot_url, required=True)
    ok_ox, oxidian_url = _valid_url(oxidian_url, required=False)
    ok_store, tienda_url = _valid_url(tienda_url, required=False)
    ok_evo, evolution_url = _valid_url(evolution_url, required=False)
    if not ok_url:
        flash("La URL del bot debe empezar por http:// o https://.", "danger")
        return redirect(url_for("superadmin.chatbot"))
    if not ok_ox or not ok_store or not ok_evo:
        flash("Las URLs de Oxidian, tienda y Evolution deben empezar por http:// o https:// si se informan.", "danger")
        return redirect(url_for("superadmin.chatbot"))
    if not bot_key:
        bot_key = str(uuid.uuid4())
    if env_bot_key and bot_key != env_bot_key:
        flash("BOT_API_KEY está fijada por el entorno. Rótala en Cosmos/.env y reinicia la pila.", "danger")
        return redirect(url_for("superadmin.chatbot"))
    if env_panel_key and panel_key and panel_key != env_panel_key:
        flash("BOT_PANEL_KEY está fijada por el entorno. Rótala en Cosmos/.env y reinicia la pila.", "danger")
        return redirect(url_for("superadmin.chatbot"))
    if len(bot_key) < 16 or (panel_key and len(panel_key) < 16):
        flash("Las claves del bot deben tener al menos 16 caracteres.", "danger")
        return redirect(url_for("superadmin.chatbot"))
    if evolution_key and len(evolution_key) < 16:
        flash("EVOLUTION_API_KEY debe tener al menos 16 caracteres.", "danger")
        return redirect(url_for("superadmin.chatbot"))
    if evolution_instance and not re.fullmatch(r"[A-Za-z0-9_.-]{2,80}", evolution_instance):
        flash("La instancia de Evolution solo puede usar letras, números, punto, guion y guion bajo.", "danger")
        return redirect(url_for("superadmin.chatbot"))
    if webhook_secret and len(webhook_secret) < 32:
        flash("WEBHOOK_SECRET debe tener al menos 32 caracteres.", "danger")
        return redirect(url_for("superadmin.chatbot"))
    if invalid_admin_numbers:
        flash("Revisa los números administradores del chatbot.", "danger")
        return redirect(url_for("superadmin.chatbot"))

    SiteConfig.set("BOT_API_URL", bot_url, user_id=current_user.id,
                   descripcion="URL del bot WhatsApp")
    SiteConfig.set("BOT_API_KEY", bot_key, user_id=current_user.id,
                   descripcion="Clave compartida Oxidian ↔ Bot")
    SiteConfig.set("BOT_PANEL_KEY", panel_key, user_id=current_user.id,
                   descripcion="Clave de panel para administrar el bot")
    SiteConfig.set("OXIDIAN_PUBLIC_URL", oxidian_url, user_id=current_user.id,
                   descripcion="URL pública/local de Oxidian para clientes y enlaces")
    SiteConfig.set("TIENDA_URL", tienda_url, user_id=current_user.id,
                   descripcion="URL de la tienda para WhatsApp")
    SiteConfig.set("EVOLUTION_API_URL", evolution_url, user_id=current_user.id,
                   descripcion="URL interna de Evolution API")
    SiteConfig.set("EVOLUTION_API_KEY", evolution_key, user_id=current_user.id,
                   descripcion="Clave API de Evolution")
    SiteConfig.set("EVOLUTION_INSTANCE", evolution_instance, user_id=current_user.id,
                   descripcion="Instancia WhatsApp de Evolution")
    SiteConfig.set("WEBHOOK_SECRET", webhook_secret, user_id=current_user.id,
                   descripcion="Secreto webhook Evolution -> Oxidian")
    SiteConfig.set(
        "BOT_ADMIN_NUMBERS",
        ",".join(admin_numbers),
        user_id=current_user.id,
        descripcion="Números adicionales autorizados para administrar el chatbot",
    )

    # ── Configuración del Asistente IA del bot ──
    ai_enabled = "1" if request.form.get("bot_ai_enabled") == "1" else "0"
    ai_provider = (request.form.get("bot_ai_provider") or "").strip().lower()
    if ai_provider not in {"", "groq", "openai"}:
        ai_provider = ""
    ai_model = (request.form.get("bot_ai_model") or "").strip()[:80]
    ai_rules = (request.form.get("bot_ai_rules") or "").strip()[:1500]
    try:
        ai_daily_client = int(request.form.get("bot_ai_daily_client") or 20)
        ai_daily_global = int(request.form.get("bot_ai_daily_global") or 500)
    except (TypeError, ValueError):
        flash("Los límites diarios de IA deben ser números enteros.", "danger")
        return redirect(url_for("superadmin.chatbot"))
    if not (1 <= ai_daily_client <= 1000 and 1 <= ai_daily_global <= 10000):
        flash("Límites IA fuera de rango: cliente 1-1000 y global 1-10000.", "danger")
        return redirect(url_for("superadmin.chatbot"))
    ai_api_key_new = (request.form.get("bot_ai_api_key") or "").strip()
    ai_api_key = ai_api_key_new or SiteConfig.get("BOT_AI_API_KEY", "")
    if ai_enabled == "1" and (not ai_provider or not ai_model or not ai_api_key):
        flash("Para activar la IA debes indicar proveedor, modelo y API key.", "danger")
        return redirect(url_for("superadmin.chatbot"))
    SiteConfig.set("BOT_AI_ENABLED", ai_enabled, user_id=current_user.id,
                   descripcion="Activa/desactiva el fallback IA del bot")
    SiteConfig.set("BOT_AI_PROVIDER", ai_provider, user_id=current_user.id,
                   descripcion="Proveedor IA (groq | openai)")
    SiteConfig.set("BOT_AI_MODEL", ai_model, user_id=current_user.id,
                   descripcion="Modelo IA usado por el bot")
    SiteConfig.set("BOT_AI_RULES", ai_rules, user_id=current_user.id,
                   descripcion="Reglas extra del system prompt del bot")
    SiteConfig.set("BOT_AI_DAILY_CLIENT", ai_daily_client, user_id=current_user.id,
                   descripcion="Máximo de consultas IA diarias por cliente")
    SiteConfig.set("BOT_AI_DAILY_GLOBAL", ai_daily_global, user_id=current_user.id,
                   descripcion="Máximo de consultas IA diarias globales")
    if ai_api_key_new:
        SiteConfig.set("BOT_AI_API_KEY", ai_api_key_new, user_id=current_user.id,
                       descripcion="API key del proveedor IA del bot")

    AuditLog.registrar(current_user.id, "guardar_chatbot", "site_config",
                       detalle=bot_url, ip=request.remote_addr)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al guardar chatbot: {exc}", "danger")
        return redirect(url_for("superadmin.chatbot"))

    ok, msg = _sincronizar_chatbot_runtime(panel_key=previous_panel_key)
    flash(("Configuración guardada. " if ok else "Configuración guardada, pero ") + msg,
          "success" if ok else "warning")
    return redirect(url_for("superadmin.chatbot"))


@superadmin_bp.route("/chatbot/sincronizar", methods=["POST"])
@superadmin_required
def sincronizar_chatbot():
    ok, msg = _sincronizar_chatbot_runtime()
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("superadmin.chatbot"))


@superadmin_bp.route("/chatbot/sincronizar-catalogo", methods=["POST"])
@superadmin_required
def sincronizar_chatbot_catalogo():
    ok, msg = _bot_panel_post("/api/oxidian/sync", {}, timeout=12)
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("superadmin.chatbot"))


@superadmin_bp.route("/chatbot/test-ai", methods=["POST"])
@superadmin_required
def chatbot_test_ai():
    ok, msg = _bot_panel_post("/api/ai/test", {}, timeout=20)
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("superadmin.chatbot"))


@superadmin_bp.route("/chatbot/power", methods=["POST"])
@superadmin_required
def chatbot_power():
    enabled = request.form.get("enabled") == "1"
    ok, msg = _bot_panel_post("/api/bot/power", {"enabled": enabled})
    flash(("Bot activado." if enabled else "Bot pausado.") if ok else msg,
          "success" if ok else "danger")
    return redirect(url_for("superadmin.chatbot"))


@superadmin_bp.route("/chatbot/reset", methods=["POST"])
@superadmin_required
def chatbot_reset():
    full = request.form.get("full") == "1"
    ok, msg = _bot_panel_post("/api/bot/reset", {"full": full}, timeout=15)
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("superadmin.chatbot"))


@superadmin_bp.route("/chatbot/test-whatsapp", methods=["POST"])
@superadmin_required
def chatbot_test_whatsapp():
    from services import enviar_whatsapp_generico
    telefono = _normalizar_telefono(request.form.get("telefono", ""))
    mensaje = (request.form.get("mensaje", "").strip() or "Mensaje de prueba desde Oxidian.")[:1000]
    if not re.fullmatch(r"\+?\d{7,20}", telefono):
        flash("Indica un teléfono válido para enviar la prueba.", "danger")
        return redirect(url_for("superadmin.chatbot"))
    ok = enviar_whatsapp_generico(
        telefono,
        mensaje,
        evento="superadmin_test",
        user_id=current_user.id,
    )
    if ok:
        db.session.commit()
    else:
        db.session.rollback()
    flash("Mensaje de prueba enviado por WhatsApp." if ok else "No se pudo enviar. Revisa URL, key y estado conectado del bot.",
          "success" if ok else "danger")
    return redirect(url_for("superadmin.chatbot"))


# ─── CONFIGURACIÓN DEL SISTEMA ───────────────

# Claves "soberanas": SOLO super_admin puede cambiarlas, incluso si el admin
# tiene acceso a la pantalla /superadmin/config. Definen el modelo comercial,
# comisiones, features del producto (delivery/recogida/puntos/programados),
# integraciones y bot. El admin operativo NUNCA puede tocarlas.
#
# Filosofía: en modo servicio el admin gestiona SU tienda (marca, contacto,
# horarios, pagos, zonas, textos, imágenes) pero jamás puede quitarle el
# control al super_admin sobre lo estratégico.
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
    "BOT_AI_DAILY_CLIENT", "BOT_AI_DAILY_GLOBAL", "BOT_ALLOW_ORDER_CREATE",
    "BOT_EMAIL_DOMAIN",
    "EVOLUTION_API_URL", "EVOLUTION_INSTANCE",
    # Sistema
    "OXIDIAN_PUBLIC_URL", "TIENDA_URL", "ALLOW_DEMO_RESET",
    # Reset masivo de puntos (afecta a todos los clientes)
    "POINTS_RESET_PERIOD_DAYS", "POINTS_LAST_RESET_AT",
})


def _user_puede_modificar_clave(user, clave):
    """El super_admin puede tocar cualquier clave. El admin no puede tocar
    las LOCKED_CONFIG_KEYS. Fuente única de la verdad para la UI y el save."""
    if getattr(user, "rol", None) == "super_admin":
        return True
    return clave not in LOCKED_CONFIG_KEYS


@superadmin_bp.route("/config")
@login_required
def config():
    """Centro de configuración. Accesible por super_admin y admin.
    El admin ve las mismas secciones pero las claves soberanas aparecen como
    solo-lectura con etiqueta 'Controla el super admin' — nunca puede
    modificarlas por más que intente."""
    if current_user.rol not in ("super_admin", "admin"):
        return redirect(url_for("public.index"))
    entradas = SiteConfig.query.order_by(SiteConfig.clave).all()
    zonas = ZonaEntrega.query.order_by(ZonaEntrega.orden, ZonaEntrega.nombre).all()
    config_map = {e.clave: e.valor for e in entradas}
    for key, value in {**PUBLIC_THEME_DEFAULTS, **PUBLIC_UI_DEFAULTS}.items():
        config_map.setdefault(key, value)
    public_ui_groups = [
        ("Cabecera", [(key, label) for key, label in PUBLIC_UI_FIELDS if key == "UI_CLOSE" or key.startswith("UI_HEADER_")]),
        ("Carrito", [(key, label) for key, label in PUBLIC_UI_FIELDS if key.startswith("UI_CART_")]),
        ("PWA y navegación", [
            (key, label) for key, label in PUBLIC_UI_FIELDS
            if key.startswith(("UI_PWA_", "UI_NAV_", "UI_INFO_"))
        ]),
    ]
    return render_template("superadmin/config.html", entradas=entradas,
                           config_map=config_map, zonas=zonas,
                           public_theme_defaults=PUBLIC_THEME_DEFAULTS,
                           public_ui_fields=PUBLIC_UI_FIELDS,
                           public_ui_groups=public_ui_groups,
                           locked_keys=LOCKED_CONFIG_KEYS,
                           es_super_admin=(current_user.rol == "super_admin"))


@superadmin_bp.route("/config/guardar", methods=["POST"])
@login_required
def guardar_config():
    if current_user.rol not in ("super_admin", "admin"):
        return "Sin permiso", 403
    clave = request.form.get("clave", "").strip()
    valor = request.form.get("valor", "").strip()
    descripcion = request.form.get("descripcion", "").strip() or None
    # Blindaje: si el admin envía una clave soberana, rechazamos con flash
    # explícito. Evita cualquier intento de escalada por edición del form.
    if not _user_puede_modificar_clave(current_user, clave):
        flash(f"Solo el super admin puede cambiar {clave}.", "warning")
        return redirect(url_for("superadmin.config"))
    ok, clave, valor, error = _validar_config_value(clave, valor)
    if not ok:
        flash(error, "danger")
        return redirect(url_for("superadmin.config"))
    if clave in {"FEATURE_DELIVERY", "FEATURE_RECOGIDA"} and valor == "0":
        otra = "FEATURE_RECOGIDA" if clave == "FEATURE_DELIVERY" else "FEATURE_DELIVERY"
        if SiteConfig.get(otra, "1") == "0":
            flash("Debe quedar habilitado delivery o recogida.", "danger")
            return redirect(url_for("superadmin.config"))
    SiteConfig.set(clave, valor, user_id=current_user.id, descripcion=descripcion)
    es_secreto = any(token in clave for token in ("KEY", "SECRET", "PASSWORD", "TOKEN"))
    valor_auditado = "<redacted>" if es_secreto else valor
    AuditLog.registrar(current_user.id, "config_update", "site_config",
                       detalle=f"{clave}={valor_auditado}", ip=request.remote_addr)
    try:
        db.session.commit()
        flash(f"Configuración '{clave}' guardada.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al guardar configuración: {exc}", "danger")
    return redirect(url_for("superadmin.config"))


@superadmin_bp.route("/config/guardar-seccion", methods=["POST"])
@login_required
def guardar_config_seccion():
    """Guarda únicamente los campos presentes de una tarjeta.

    Permisos:
    - super_admin: puede tocar cualquier clave (incluidas las soberanas).
    - admin: puede tocar solo claves NO bloqueadas (ver LOCKED_CONFIG_KEYS).
      Las tocaduras a claves bloqueadas se ignoran silenciosamente con un
      flash informativo — nunca se lanzan 500, ni el admin puede escalar.
    """
    if current_user.rol not in ("super_admin", "admin"):
        return "Sin permiso", 403
    defaults = {clave: desc for clave, _, desc in CLAVES_DEFAULT}
    default_values = {clave: valor for clave, valor, _ in CLAVES_DEFAULT}
    section, cambios, errores = _config_section_submission(request.form)
    # Bloqueo por claves soberanas. Si el admin intenta cambiar una clave
    # bloqueada, la retiramos del dict antes de guardar y avisamos que el
    # super_admin es quien controla eso.
    if current_user.rol != "super_admin":
        cambios_originales = list(cambios)
        cambios = [(k, v) for k, v in cambios if k not in LOCKED_CONFIG_KEYS]
        bloqueadas = [k for k, _ in cambios_originales if k in LOCKED_CONFIG_KEYS]
        if bloqueadas:
            flash(
                f"Algunos ajustes solo puede cambiarlos el super admin ({', '.join(bloqueadas)}).",
                "warning",
            )
    parent_section = CONFIG_SECTION_PARENT.get(section, "tienda")
    if errores:
        flash("No se guardó esta tarjeta: " + " ".join(errores), "danger")
        return redirect(url_for("superadmin.config", section=parent_section))

    raw_actuales = {
        key: SiteConfig.get(key)
        for key in CONFIG_SECTION_KEYS.get(section, ())
    }
    actuales = {
        key: value if value is not None else default_values.get(key, "")
        for key, value in raw_actuales.items()
    }
    propuestos = actuales | dict(cambios)

    if (
        section == "operacion-pagos"
        and propuestos.get("EFECTIVO_HABILITADO", "1") == "0"
        and propuestos.get("BIZUM_HABILITADO", "1") == "0"
    ):
        flash("Debe quedar habilitado al menos un método de pago.", "danger")
        return redirect(url_for("superadmin.config", section=parent_section))
    if (
        section == "operacion-modo"
        and propuestos.get("FEATURE_DELIVERY", "1") == "0"
        and propuestos.get("FEATURE_RECOGIDA", "1") == "0"
    ):
        flash("Debe quedar habilitado delivery o recogida.", "danger")
        return redirect(url_for("superadmin.config", section=parent_section))
    if (
        section == "operacion-horario"
        and propuestos.get("HORARIO_APERTURA")
        and propuestos.get("HORARIO_APERTURA") == propuestos.get("HORARIO_CIERRE")
    ):
        flash("La hora de apertura y la de cierre no pueden ser iguales.", "danger")
        return redirect(url_for("superadmin.config", section=parent_section))
    if not cambios:
        flash("Esta tarjeta no contenía campos para guardar.", "info")
        return redirect(url_for("superadmin.config", section=parent_section))

    cambios = [
        (clave, valor)
        for clave, valor in cambios
        if raw_actuales.get(clave) != valor
    ]
    if not cambios:
        flash("Sin cambios: la configuración ya estaba actualizada.", "info")
        return redirect(url_for("superadmin.config", section=parent_section))

    try:
        for clave, valor in cambios:
            SiteConfig.set(
                clave, valor, user_id=current_user.id,
                descripcion=defaults.get(clave),
            )
        AuditLog.registrar(
            current_user.id, "config_section_update", "site_config",
            detalle=", ".join(clave for clave, _ in cambios),
            ip=request.remote_addr,
        )
        db.session.commit()
        nombres = ", ".join(clave for clave, _ in cambios)
        flash(f"Tarjeta guardada ({len(cambios)}): {nombres}.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"No se pudo guardar esta tarjeta: {exc}", "danger")
    return redirect(url_for("superadmin.config", section=parent_section))


@superadmin_bp.route("/config/seed", methods=["POST"])
@superadmin_required
def seed_config():
    creados = 0
    for clave, valor_default, desc in CLAVES_DEFAULT:
        if not SiteConfig.query.filter_by(clave=clave).first():
            v = str(uuid.uuid4()) if clave == "BOT_API_KEY" and not valor_default else valor_default
            SiteConfig.set(clave, v, user_id=current_user.id, descripcion=desc)
            creados += 1
    try:
        db.session.commit()
        flash(f"Config por defecto aplicada ({creados} entradas nuevas).", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error: {exc}", "danger")
    return redirect(url_for("superadmin.config"))


@superadmin_bp.route("/config/regenerar-api-key", methods=["POST"])
@superadmin_required
def regenerar_api_key():
    if os.environ.get("BOT_API_KEY", "").strip():
        flash("BOT_API_KEY está fijada por el entorno. Rótala en Cosmos/.env y reinicia la pila.", "danger")
        return redirect(url_for("superadmin.chatbot"))
    previous_panel_key = _bot_panel_key()
    nueva_key = str(uuid.uuid4())
    SiteConfig.set("BOT_API_KEY", nueva_key, user_id=current_user.id)
    AuditLog.registrar(current_user.id, "regenerar_bot_api_key", ip=request.remote_addr)
    try:
        db.session.commit()
        ok, msg = _sincronizar_chatbot_runtime(panel_key=previous_panel_key)
        flash("Nueva API key generada. " + msg,
              "success" if ok else "warning")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al regenerar API key: {exc}", "danger")
    return redirect(url_for("superadmin.chatbot"))


# ─── GESTIÓN DE ADMINS ────────────────────────

@superadmin_bp.route("/admins")
@superadmin_required
def admins():
    users = User.query.filter(
        User.rol.in_(["admin", "super_admin"])
    ).order_by(User.rol, User.nombre).all()
    return render_template("superadmin/admins.html", users=users)


@superadmin_bp.route("/admins/crear", methods=["POST"])
@superadmin_required
def crear_admin():
    email = request.form.get("email", "").strip().lower()
    nombre = request.form.get("nombre", "").strip()
    password = request.form.get("password", "").strip()
    rol = request.form.get("rol", "admin")

    if not email or not nombre:
        flash("Nombre y email son obligatorios.", "danger")
        return redirect(url_for("superadmin.admins"))
    if len(password) < 12:
        flash("La contraseña debe tener al menos 12 caracteres.", "danger")
        return redirect(url_for("superadmin.admins"))
    if rol not in ("admin", "super_admin"):
        rol = "admin"
    if User.query.filter_by(email=email).first():
        flash("Email ya registrado.", "warning")
        return redirect(url_for("superadmin.admins"))

    u = User(nombre=nombre, email=email, rol=rol)
    u.set_password(password)
    db.session.add(u)
    db.session.flush()
    if rol == "admin":
        # Preset operacional: admin nuevo puede gestionar productos, combos,
        # cupones, afiliados, POS, stock, reportes, staff y zonas por defecto.
        # Las 3 sensibles (auditoria, usuarios, whatsapp) quedan apagadas hasta
        # que el super_admin las active caso a caso.
        AdminFeature.inicializar_para_admin(u.id, preset="operacional")
    AuditLog.registrar(current_user.id, "crear_admin", "user",
                       detalle=f"{email} [{rol}]", ip=request.remote_addr)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al crear admin: {exc}", "danger")
        return redirect(url_for("superadmin.admins"))
    flash(f"Admin '{u.nombre}' creado. Configura sus features →", "success")
    return redirect(url_for("superadmin.admin_features", user_id=u.id))


@superadmin_bp.route("/admins/<int:user_id>/editar", methods=["GET", "POST"])
@superadmin_required
def editar_admin(user_id):
    u = get_or_404(User, user_id)
    if request.method == "GET":
        return render_template("superadmin/admin_editar.html", admin_user=u)

    u.nombre = request.form.get("nombre", u.nombre).strip()
    nuevo_rol = request.form.get("rol")
    if nuevo_rol in ("admin", "super_admin") and u.id != current_user.id:
        u.rol = nuevo_rol
    nueva_pw = request.form.get("nueva_password", request.form.get("password", "")).strip()
    if nueva_pw:
        if len(nueva_pw) < 12:
            flash("La contraseña debe tener al menos 12 caracteres.", "danger")
            return redirect(url_for("superadmin.admins"))
        u.set_password(nueva_pw)
    AuditLog.registrar(current_user.id, "editar_admin", "user",
                       u.id, ip=request.remote_addr)
    try:
        db.session.commit()
        flash(f"Admin '{u.nombre}' actualizado.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al actualizar admin: {exc}", "danger")
    return redirect(url_for("superadmin.admins"))


@superadmin_bp.route("/admins/<int:user_id>/toggle", methods=["POST"])
@superadmin_required
def toggle_admin(user_id):
    u = get_or_404(User, user_id)
    if u.id == current_user.id:
        flash("No puedes desactivarte a ti mismo.", "warning")
        return redirect(url_for("superadmin.admins"))
    u.activo = not u.activo
    AuditLog.registrar(current_user.id, "toggle_admin", "user",
                       u.id, detalle=f"activo={u.activo}", ip=request.remote_addr)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(f"Error: {exc}", "danger")
    return redirect(url_for("superadmin.admins"))


@superadmin_bp.route("/admins/<int:user_id>/features", methods=["GET"])
@superadmin_required
def admin_features(user_id):
    u = get_or_404(User, user_id)
    if u.rol not in ("admin", "super_admin"):
        flash("Solo se pueden gestionar features de admins.", "warning")
        return redirect(url_for("superadmin.admins"))
    # Inicializar features si no existen aún
    AdminFeature.inicializar_para_admin(u.id)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    features = {af.feature: af for af in AdminFeature.query.filter_by(user_id=u.id).all()}
    from models import ADMIN_FEATURES_OPERACIONALES, ADMIN_FEATURES_SENSIBLES
    return render_template("superadmin/admin_features.html",
                           admin_user=u, features=features,
                           all_features=ADMIN_FEATURES,
                           features_sensibles=set(ADMIN_FEATURES_SENSIBLES),
                           features_operacionales=set(ADMIN_FEATURES_OPERACIONALES),
                           feature_labels=FEATURE_LABELS)


@superadmin_bp.route("/admins/<int:user_id>/features/guardar", methods=["POST"])
@superadmin_required
def guardar_features(user_id):
    u = get_or_404(User, user_id)
    if u.rol not in ("admin", "super_admin"):
        flash("Solo se pueden gestionar features de admins.", "warning")
        return redirect(url_for("superadmin.admins"))

    AdminFeature.inicializar_para_admin(u.id)
    db.session.flush()

    for feat in ADMIN_FEATURES:
        af = AdminFeature.query.filter_by(user_id=u.id, feature=feat).first()
        if af:
            nuevo = feat in request.form.getlist("features")
            if af.activo != nuevo:
                af.activo = nuevo
                af.actualizado_por = current_user.id
                af.actualizado_en = utcnow()

    AuditLog.registrar(current_user.id, "actualizar_features_admin", "user",
                       u.id, detalle=str(request.form.getlist("features")),
                       ip=request.remote_addr)
    try:
        db.session.commit()
        flash(f"Features de '{u.nombre}' actualizados.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al guardar features: {exc}", "danger")
    return redirect(url_for("superadmin.admin_features", user_id=u.id))


@superadmin_bp.route("/admins/<int:user_id>/features/activar-todos", methods=["POST"])
@superadmin_required
def activar_todos_features(user_id):
    u = get_or_404(User, user_id)
    AdminFeature.inicializar_para_admin(u.id, activar_todos=True)
    db.session.flush()
    for af in AdminFeature.query.filter_by(user_id=u.id).all():
        af.activo = True
        af.actualizado_por = current_user.id
        af.actualizado_en = utcnow()
    try:
        db.session.commit()
        flash(f"Todos los features activados para '{u.nombre}'.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al activar features: {exc}", "danger")
    return redirect(url_for("superadmin.admin_features", user_id=u.id))


@superadmin_bp.route("/admins/<int:user_id>/features/preset-operacional", methods=["POST"])
@superadmin_required
def preset_operacional_features(user_id):
    """Aplica el preset operacional: enciende operacionales, apaga sensibles."""
    from models import ADMIN_FEATURES_OPERACIONALES
    u = get_or_404(User, user_id)
    AdminFeature.inicializar_para_admin(u.id, preset="ninguno")  # asegura filas
    db.session.flush()
    operacionales = set(ADMIN_FEATURES_OPERACIONALES)
    for af in AdminFeature.query.filter_by(user_id=u.id).all():
        af.activo = af.feature in operacionales
        af.actualizado_por = current_user.id
        af.actualizado_en = utcnow()
    try:
        db.session.commit()
        flash(f"Preset operacional aplicado a '{u.nombre}': {len(operacionales)} módulos activos, sensibles desactivados.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al aplicar preset: {exc}", "danger")
    return redirect(url_for("superadmin.admin_features", user_id=u.id))


# ─── RESET DE PUNTOS PERIÓDICO ─────────────────────────

@superadmin_bp.route("/modo-tienda/toggle", methods=["POST"])
@superadmin_required
def toggle_modo_tienda():
    """Alterna entre modo 'propia' y 'bar_servicio'. Endpoint directo,
    sin depender de la sección de config. Adapta el sistema al vuelo:
    comisiones, vistas de comisión en dashboard, cálculos en checkout."""
    features = get_store_features()
    actual = features.get("modo_tienda", "propia")
    nuevo = "bar_servicio" if actual == "propia" else "propia"
    SiteConfig.set(
        "MODO_TIENDA",
        nuevo,
        user_id=current_user.id,
        descripcion="Modo comercial de la tienda",
    )
    AuditLog.registrar(
        current_user.id,
        "toggle_modo_tienda",
        "site_config",
        detalle=f"MODO_TIENDA {actual}→{nuevo}",
        ip=request.remote_addr,
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("No se pudo cambiar el modo.", "danger")
        return redirect(url_for("superadmin.dashboard"))
    _sincronizar_chatbot_runtime()
    label = "servicio (comisión)" if nuevo == "bar_servicio" else "propio (ingresos íntegros)"
    flash(f"Modo tienda cambiado a: {label}.", "success")
    return redirect(url_for("superadmin.dashboard"))


@superadmin_bp.route("/puntos/reset-manual", methods=["POST"])
@superadmin_required
def puntos_reset_manual():
    """Ejecuta un reset inmediato de puntos de todos los clientes.
    Solo super_admin. Guarda log en PointsLog con motivo 'manual'."""
    from datetime import datetime
    from loyalty_service import reset_periodico_si_toca
    from models import User, PointsLog, SiteConfig
    afectados = 0
    for u in User.query.filter(User.rol == "cliente", User.puntos > 0).all():
        previo = int(u.puntos or 0)
        u.puntos = 0
        db.session.add(PointsLog(cliente_id=u.id, tipo="reset", cantidad=-previo,
                                 descripcion=f"Reset manual por {current_user.email}"))
        afectados += 1
    SiteConfig.set("POINTS_LAST_RESET_AT", datetime.utcnow().isoformat(),
                   descripcion="Timestamp del último reset de puntos")
    AuditLog.registrar(current_user.id, "puntos_reset_manual", "site_config",
                       detalle=f"{afectados} clientes reseteados", ip=request.remote_addr)
    db.session.commit()
    flash(f"Puntos reseteados a 0 en {afectados} clientes.", "success")
    return redirect(url_for("superadmin.config"))


# ─── ZONAS DE ENTREGA ─────────────────────────

def _exigir_delivery_para_zonas():
    """Las zonas de entrega solo aplican si delivery está activo. Si no, devolvemos
    404 para evitar que se gestionen configs huérfanas."""
    from store_config import get_store_features
    if not get_store_features()["delivery"]:
        from flask import abort
        abort(404)


@superadmin_bp.route("/zonas")
@superadmin_required
def zonas():
    _exigir_delivery_para_zonas()
    zonas = ZonaEntrega.query.order_by(ZonaEntrega.orden, ZonaEntrega.nombre).all()
    return render_template("superadmin/zonas.html", zonas=zonas)


@superadmin_bp.route("/zonas/crear", methods=["POST"])
@superadmin_required
def crear_zona():
    _exigir_delivery_para_zonas()
    data, error = _parse_zona_form(request.form)
    if error:
        flash(error, "danger")
        return redirect(url_for("superadmin.zonas"))
    zona = ZonaEntrega(**data)
    db.session.add(zona)
    AuditLog.registrar(current_user.id, "crear_zona", "zona_entrega",
                       detalle=zona.nombre, ip=request.remote_addr)
    try:
        db.session.commit()
        flash(f"Zona '{zona.nombre}' creada.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al crear zona: {exc}", "danger")
    return redirect(url_for("superadmin.zonas"))


@superadmin_bp.route("/zonas/<int:zona_id>/editar", methods=["GET", "POST"])
@superadmin_required
def editar_zona(zona_id):
    _exigir_delivery_para_zonas()
    zona = get_or_404(ZonaEntrega, zona_id)
    if request.method == "GET":
        return render_template("superadmin/zona_editar.html", zona=zona)

    data, error = _parse_zona_form(request.form, zona=zona)
    if error:
        flash(error, "danger")
        return redirect(url_for("superadmin.zonas"))
    for campo, valor in data.items():
        setattr(zona, campo, valor)
    AuditLog.registrar(current_user.id, "editar_zona", "zona_entrega",
                       zona.id, ip=request.remote_addr)
    try:
        db.session.commit()
        flash("Zona actualizada.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al actualizar zona: {exc}", "danger")
    return redirect(url_for("superadmin.zonas"))


@superadmin_bp.route("/zonas/<int:zona_id>/toggle", methods=["POST"])
@superadmin_required
def toggle_zona(zona_id):
    _exigir_delivery_para_zonas()
    zona = get_or_404(ZonaEntrega, zona_id)
    if zona.activo:
        activas_restantes = ZonaEntrega.query.filter(
            ZonaEntrega.activo == True,
            ZonaEntrega.id != zona.id
        ).count()
        if activas_restantes == 0:
            flash("Debe quedar al menos una zona activa para no romper el checkout.", "warning")
            return redirect(url_for("superadmin.zonas"))
    zona.activo = not zona.activo
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(f"Error al cambiar estado de zona: {exc}", "danger")
    return redirect(url_for("superadmin.zonas"))


# ─── P&L COMPLETO ─────────────────────────────

@superadmin_bp.route("/pl")
@superadmin_required
def pl():
    hoy = date.today()
    primer_dia = hoy.replace(day=1)
    ultimo_dia = hoy.replace(day=monthrange(hoy.year, hoy.month)[1])

    fecha_ini = request.args.get("fecha_ini", primer_dia.isoformat())
    fecha_fin = request.args.get("fecha_fin", ultimo_dia.isoformat())
    try:
        fi = date.fromisoformat(fecha_ini)
        ff = date.fromisoformat(fecha_fin)
    except (ValueError, TypeError):
        fi, ff = primer_dia, ultimo_dia
        fecha_ini, fecha_fin = fi.isoformat(), ff.isoformat()

    from collections import defaultdict
    from services import calcular_pl

    datos = calcular_pl(fi, ff)
    fi_dt = datetime(fi.year, fi.month, fi.day, 0, 0, 0)
    ff_dt = datetime(ff.year, ff.month, ff.day, 23, 59, 59)
    movimientos = Caja.query.filter(Caja.fecha.between(fi_dt, ff_dt)).all()
    por_categoria = defaultdict(lambda: {"ingreso": 0.0, "egreso": 0.0})
    for m in movimientos:
        cat = (m.categoria or "sin_categoria").lower()
        por_categoria[cat][m.tipo] += float(m.monto)

    pagos_pendientes_equipo = StaffPayment.query.filter_by(pagado=False)\
        .order_by(StaffPayment.creado_en.asc()).all()

    return render_template("superadmin/pl.html",
                           ventas_brutas=datos["ventas_brutas"],
                           descuentos=datos["descuentos_concedidos"],
                           ingresos_netos=datos["ingresos_netos"],
                           cogs=datos["cogs"],
                           margen_bruto=datos["margen_bruto"],
                           margen_bruto_pct=datos["margen_bruto_pct"],
                           nominas=datos["nominas"],
                           comisiones=datos["comisiones_repartidor"],
                           service_commission=datos["service_commission"],
                           merchant_net=datos["merchant_net"],
                           gastos_caja=datos["gastos_caja"],
                           otros_ingresos_caja=datos["otros_ingresos_caja"],
                           resultado=datos["resultado"],
                           resultado_pct=datos["resultado_pct"],
                           ticket_medio=datos["ticket_medio"],
                           ventas_online=datos["ventas_online"],
                           ventas_presencial=datos["ventas_presencial"],
                           ventas_whatsapp=datos["ventas_whatsapp"],
                           total_pedidos=datos["total_pedidos"],
                           por_categoria=dict(por_categoria),
                           pagos_pendientes_equipo=pagos_pendientes_equipo,
                           fecha_ini=fecha_ini, fecha_fin=fecha_fin)


# ─── RESET DATOS DEMO ─────────────────────────

@superadmin_bp.route("/reset-demo", methods=["POST"])
@superadmin_required
def reset_demo():
    """Elimina productos/stock/cupones sin pedidos y re-inserta datos demo."""
    if os.environ.get("ALLOW_DEMO_RESET", "").strip().lower() not in ("1", "true", "yes", "si", "sí"):
        flash("El reset demo está deshabilitado en este entorno.", "warning")
        return redirect(url_for("superadmin.dashboard"))

    confirmacion = request.form.get("confirmacion", "").strip()
    if confirmacion != "RESET":
        flash("Escribe RESET en el campo de confirmación para continuar.", "danger")
        return redirect(url_for("superadmin.dashboard"))

    from models import Product, Stock, Coupon, ComboItem

    AuditLog.registrar(current_user.id, "reset_demo", "system",
                       detalle="Limpieza y re-seed de datos demo", ip=request.remote_addr)
    try:
        prods_sin_pedidos = Product.query.filter(~Product.order_items.any()).all()
        for p in prods_sin_pedidos:
            Stock.query.filter_by(producto_id=p.id).delete()
            ComboItem.query.filter_by(combo_id=p.id).delete()
            ComboItem.query.filter_by(producto_id=p.id).delete()
            db.session.delete(p)

        Coupon.query.filter(~Coupon.pedidos.any()).delete(synchronize_session=False)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(f"Error durante el reset: {exc}", "danger")
        return redirect(url_for("superadmin.dashboard"))

    from app import _seed_demo_data
    _seed_demo_data()

    flash("Datos demo reiniciados correctamente.", "success")
    return redirect(url_for("superadmin.dashboard"))


# ─── LOG DE AUDITORÍA ─────────────────────────

@superadmin_bp.route("/audit")
@superadmin_required
def audit():
    page = request.args.get("page", 1, type=int)
    user_id_f = request.args.get("user_id", type=int)
    recurso_f = request.args.get("recurso", "").strip()
    accion_f = request.args.get("accion", "").strip()
    buscar_f = request.args.get("q", "").strip()[:100]
    periodo_f = request.args.get("periodo", "7").strip()
    periodos_validos = {"1": 1, "7": 7, "30": 30, "90": 90, "all": None}
    dias = periodos_validos.get(periodo_f, 7)
    periodo_f = periodo_f if periodo_f in periodos_validos else "7"

    q = AuditLog.query.order_by(AuditLog.creado_en.desc())
    if dias:
        q = q.filter(AuditLog.creado_en >= utcnow() - timedelta(days=dias))
    if user_id_f:
        q = q.filter_by(user_id=user_id_f)
    if recurso_f:
        q = q.filter(AuditLog.recurso == recurso_f)
    if accion_f:
        q = q.filter(AuditLog.accion == accion_f)
    if buscar_f:
        patron = f"%{buscar_f}%"
        q = q.filter(or_(
            AuditLog.detalle.ilike(patron),
            AuditLog.accion.ilike(patron),
            AuditLog.recurso.ilike(patron),
            AuditLog.ip.ilike(patron),
        ))

    logs = q.paginate(page=page, per_page=50, error_out=False)
    staff_users = User.query.filter(
        User.rol.in_(["super_admin", "admin",
                      "preparacion", "repartidor"])
    ).order_by(User.nombre).all()
    acciones = [
        row[0] for row in db.session.query(AuditLog.accion)
        .filter(AuditLog.accion.isnot(None))
        .distinct().order_by(AuditLog.accion.asc()).all()
    ]
    recursos = [
        row[0] for row in db.session.query(AuditLog.recurso)
        .filter(AuditLog.recurso.isnot(None))
        .distinct().order_by(AuditLog.recurso.asc()).all()
    ]
    desde_24h = utcnow() - timedelta(hours=24)
    resumen_24h = {
        "total": AuditLog.query.filter(AuditLog.creado_en >= desde_24h).count(),
        "usuarios": db.session.query(func.count(func.distinct(AuditLog.user_id)))
        .filter(AuditLog.creado_en >= desde_24h, AuditLog.user_id.isnot(None)).scalar() or 0,
        "seguridad": AuditLog.query.filter(
            AuditLog.creado_en >= desde_24h,
            or_(
                AuditLog.accion.ilike("%login%"),
                AuditLog.accion.ilike("%toggle%"),
                AuditLog.accion.ilike("%admin%"),
                AuditLog.accion.ilike("%config%"),
            ),
        ).count(),
    }

    return render_template("superadmin/audit.html",
                           logs=logs,
                           staff_users=staff_users,
                           user_id_f=user_id_f,
                           recurso_f=recurso_f,
                           accion_f=accion_f,
                           buscar_f=buscar_f,
                           periodo_f=periodo_f,
                           acciones=acciones,
                           recursos=recursos,
                           resumen_24h=resumen_24h)
