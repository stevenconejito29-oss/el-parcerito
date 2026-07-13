"""Fuente única de defaults para claves de SiteConfig gestionadas por Oxidian.

Cada entrada documenta:
  - `default`  : valor sensato para arrancar sin config manual.
  - `type`     : tipo esperado (str|int|float|bool).
  - `desc`     : descripción legible — se persiste en SiteConfig.descripcion.

Este módulo NO reemplaza la seed histórica en `app._seed_admin` (esa cubre
tokens de marca, colores, integraciones y otros valores por-env). Aquí solo
viven las claves nuevas introducidas para eliminar hardcoding en el runtime.

Al arrancar la app (`app.create_app`) se llama a `sembrar_defaults()` que
inserta las claves que aún no existen en SiteConfig. Es idempotente.
"""
from __future__ import annotations

DEFAULTS: dict[str, dict] = {
    # ── Fiscal (España) ────────────────────────────────────────────────
    "IVA_DEFAULT_COMIDA": {
        "default": "10.00",
        "type": "float",
        "desc": "IVA por defecto para productos vertical=comida (España: 10%).",
    },
    "IVA_DEFAULT_RETAIL": {
        "default": "21.00",
        "type": "float",
        "desc": "IVA por defecto para productos retail/servicios (España: 21%).",
    },
    "NOMBRE_FISCAL": {
        "default": "",
        "type": "str",
        "desc": "Razón social o nombre fiscal para facturas. Cae a NOMBRE_NEGOCIO si vacío.",
    },
    "NIF_NEGOCIO": {
        "default": "",
        "type": "str",
        "desc": "NIF/CIF del negocio. Requerido en facturas fiscales españolas.",
    },
    "DIRECCION_FISCAL": {
        "default": "",
        "type": "str",
        "desc": "Domicilio fiscal completo (para exportación al gestor).",
    },

    # ── Confirmación de entrega / OTP ──────────────────────────────────
    "DELIVERY_CODE_MAX_INTENTOS": {
        "default": "3",
        "type": "int",
        "desc": "Intentos permitidos al validar el código de entrega (repartidor).",
    },
    "COD_PUNTOS_MAX_INTENTOS": {
        "default": "5",
        "type": "int",
        "desc": "Intentos permitidos al canjear puntos con OTP WhatsApp.",
    },
    "COD_PUNTOS_TTL_MINUTOS": {
        "default": "10",
        "type": "int",
        "desc": "Vigencia (minutos) del OTP para canje de puntos.",
    },

    # ── Retención / poda de tablas de crecimiento continuo ─────────────
    # Consumidas por `services.purgar_registros_antiguos()`, invocada por el
    # worker de outbox cada `OUTBOX_PURGE_EVERY_SECONDS` (default 1h).
    "NOTIFICATION_OUTBOX_RETENTION_DAYS": {
        "default": "30",
        "type": "int",
        "desc": (
            "Días que se conservan las notificaciones ya enviadas o fallidas "
            "en notification_outbox. Solo purga estado in (sent, failed). "
            "Cap defensivo interno 7-365."
        ),
    },
    "IDEMPOTENCY_PURGE_ENABLED": {
        "default": "1",
        "type": "bool",
        "desc": (
            "Habilita la purga automática de idempotency_keys expiradas junto "
            "con la de notification_outbox. Poner a 0 solo para diagnóstico."
        ),
    },
    "OTP_MIN_RESEND_SECONDS": {
        "default": "60",
        "type": "int",
        "desc": (
            "Ventana mínima (segundos) entre 2 solicitudes de OTP de puntos "
            "del mismo cliente. Anti-flood — consumida por loyalty_service."
        ),
    },
    "ADMIN_CLIENTES_PAGE_SIZE": {
        "default": "40",
        "type": "int",
        "desc": (
            "Tamaño de página del listado /admin/clientes (cap 10-200)."
        ),
    },

    # ── Workload balancing (no sobrecargar empleados) ────────────────
    # Consumidas por `services.distribuir_pedido` / `distribuir_repartidor`
    # como tope de pedidos activos concurrentes por empleado. Si TODOS los
    # candidatos superan el tope, se asigna al menos cargado igualmente
    # (log warning) — evita dejar pedidos huérfanos.
    "MAX_PEDIDOS_POR_PREPARADOR": {
        "default": "8",
        "type": "int",
        "desc": (
            "Máximo pedidos activos (pendiente+armando) por preparador antes "
            "de repartir a otros. Cap defensivo 1-100. Ajusta según capacidad "
            "real de tu cocina."
        ),
    },
    "MAX_PEDIDOS_POR_REPARTIDOR": {
        "default": "5",
        "type": "int",
        "desc": (
            "Máximo pedidos activos (listo+en_ruta) por repartidor antes de "
            "repartir a otros. Cap defensivo 1-50. En moto/coche subir; en "
            "bici bajar."
        ),
    },

    # ── Inventario / lotes ────────────────────────────────────────────
    # Consumida por rutas admin al crear entradas de Stock cuando el form
    # no incluye un valor explícito. Cap defensivo interno 1-90.
    "STOCK_ALERTA_DIAS_DEFAULT": {
        "default": "7",
        "type": "int",
        "desc": (
            "Días de anticipación por defecto para alertar próximo vencimiento "
            "de lotes de Stock. Cada lote puede sobrescribirlo en su ficha."
        ),
    },

    # ── Antifraude / verificación pasiva de pedidos ───────────────────
    # Consumidas por `services.evaluate_order_risk`. Un pedido cuyo total
    # supera el umbral, o de un cliente sin historial de entregas previas,
    # se marca `confirmacion_estado='pending'`. Riesgo bajo pasa sin
    # fricción para no ralentizar el flujo del cliente en la web.
    "CONFIRMACION_MONTO_UMBRAL_EUR": {
        "default": "50",
        "type": "float",
        "desc": (
            "Umbral en euros a partir del cual el sistema marca el pedido "
            "como MEDIUM/HIGH y solicita verificación pasiva vía WhatsApp. "
            "Ajusta según ticket medio de tu negocio. Cap defensivo 1-9999."
        ),
    },
    "CONFIRMACION_HABILITADA": {
        "default": "1",
        "type": "bool",
        "desc": (
            "Interruptor global de la verificación anti-fantasma. "
            "0 desactiva completamente el marcado — usa solo durante debug "
            "o si el negocio prefiere revisión 100% manual."
        ),
    },
    "CONFIRMACION_TTL_HIGH_MINUTES": {
        "default": "120",
        "type": "int",
        "desc": (
            "Minutos que un HIGH puede estar pending sin respuesta antes de "
            "auto-cancelar. Solo HIGH — MEDIUM queda para revisión manual. "
            "Cap 15-1440. 0 desactiva."
        ),
    },

    # ── Cobertura geográfica ─────────────────────────────────────────
    # Fuente única para el radio de entrega y el centro del negocio.
    # Consumidas por `services.validar_radio_entrega` y por el fallback
    # global en `asignar_zona_por_coordenadas`. Configurar SIEMPRE en
    # producción — sin CENTRO_LAT/LON el servicio queda fail-closed.
    "CENTRO_LAT": {
        "default": "37.4736",
        "type": "float",
        "desc": (
            "Latitud del centro del negocio. Default apunta a Carmona "
            "(37.4736, -5.6438). Se usa como origen para calcular distancia "
            "en km y validar cobertura de delivery."
        ),
    },
    "CENTRO_LON": {
        "default": "-5.6438",
        "type": "float",
        "desc": (
            "Longitud del centro del negocio (Carmona por defecto). "
            "Emparejar siempre con CENTRO_LAT."
        ),
    },
    "RADIO_ENTREGA_KM": {
        "default": "3",
        "type": "float",
        "desc": (
            "Radio máximo de entrega en kilómetros. Carmona centro cabe en "
            "~1.5km; 3km cubre urbanizaciones cercanas sin alcanzar Sevilla. "
            "Cap 0.5-25. Ajustar según capacidad de reparto real."
        ),
    },
    "CIUDAD_NEGOCIO": {
        "default": "Carmona",
        "type": "str",
        "desc": (
            "Ciudad para desambiguar direcciones al geocodificar. Se envía "
            "a Nominatim como bias para que 'Calle Mayor 5' no acabe "
            "resolviendo en otra provincia."
        ),
    },

    # ── Combos (fuente única de límites) ─────────────────────────────
    # Consumidas por `combo_validators.ComboLimits`. Sin estas claves, el
    # sistema caía a env → default hardcodeado. Ahora hay una fuente
    # documentada en /superadmin/config que evita drift.
    "COMBO_MAX_PRICE_EUR": {
        "default": "1000",
        "type": "float",
        "desc": (
            "Precio máximo permitido para un combo, en euros. Cap "
            "defensivo interno 1-100000. Antes hardcoded a 1000€."
        ),
    },
    "COMBO_MAX_DISCOUNT_PCT": {
        "default": "50",
        "type": "float",
        "desc": (
            "Porcentaje máximo de descuento sobre precio compuesto de un "
            "combo. 50% típico permite ofertas agresivas sin regalar "
            "productos."
        ),
    },
    "COMBO_MAX_COMPONENTS": {
        "default": "30",
        "type": "int",
        "desc": (
            "Máximo de componentes distintos en un combo. Más = pesada "
            "vista, más lento el checkout y difícil comunicar al cliente."
        ),
    },
    "COMBO_MIN_COMPONENTS": {
        "default": "1",
        "type": "int",
        "desc": "Mínimo de componentes para considerar un producto como combo.",
    },
    "COMBO_MAX_QTY_COMPONENT": {
        "default": "50",
        "type": "int",
        "desc": "Cantidad máxima de unidades de un mismo componente en un combo.",
    },
    "COMBO_MAX_SELECTIONS_GROUP": {
        "default": "10",
        "type": "int",
        "desc": (
            "Máximo de opciones seleccionables por grupo dentro de un combo. "
            "Ej: grupo 'Bebida' con hasta 10 sabores para elegir."
        ),
    },

    # ── Bot admin — límites operativos ─────────────────────────────
    # Antes: `!precio 12 4.50` rechazaba precios sobre 1000€ hardcode,
    # `!puntos 34XXX +5000` rechazaba magnitudes sobre 10000. Sin
    # fuente única el admin no podía ajustarlos sin redeploy.
    "BOT_MAX_PRICE_EUR": {
        "default": "9999",
        "type": "float",
        "desc": (
            "Precio máximo permitido en cambios de producto/SKU desde el "
            "bot WhatsApp (`!precio ID EUROS`). Cap defensivo interno "
            "1-100000. Sirve tanto para productos propios como para "
            "SKUs del bar en modo bar_servicio."
        ),
    },
    "BOT_MAX_POINTS_ADJUST": {
        "default": "10000",
        "type": "int",
        "desc": (
            "Cantidad máxima de puntos ajustables en una sola operación "
            "desde el bot (`!puntos +/-N` o menú admin). Evita typos "
            "que agreguen 100000 en vez de 100. Cap 1-1000000."
        ),
    },
}


# `SiteConfig.descripcion` es VARCHAR(200) en Postgres. Truncar aquí
# defiende el arranque de la app: una descripción demasiado larga en un
# default lanzaría StringDataRightTruncation y el contenedor no llega a
# healthy, provocando rollback en despliegue. Preferimos verdad parcial
# a caída dura.
_DESC_MAX_LEN = 200


def sembrar_defaults() -> int:
    """Inserta en SiteConfig las claves definidas aquí que aún no existen.

    Devuelve el número de claves nuevas escritas. No hace commit — el llamador
    debe cerrar la transacción."""
    from models import SiteConfig
    nuevas = 0
    for clave, meta in DEFAULTS.items():
        if SiteConfig.query.filter_by(clave=clave).first():
            continue
        desc = str(meta.get("desc") or "")
        if len(desc) > _DESC_MAX_LEN:
            desc = desc[: _DESC_MAX_LEN - 1] + "…"
        SiteConfig.set(clave, meta["default"], descripcion=desc)
        nuevas += 1
    return nuevas


def tipo_esperado(clave: str) -> str | None:
    meta = DEFAULTS.get(clave)
    return meta["type"] if meta else None
