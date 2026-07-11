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
}


def sembrar_defaults() -> int:
    """Inserta en SiteConfig las claves definidas aquí que aún no existen.

    Devuelve el número de claves nuevas escritas. No hace commit — el llamador
    debe cerrar la transacción."""
    from models import SiteConfig
    nuevas = 0
    for clave, meta in DEFAULTS.items():
        if SiteConfig.query.filter_by(clave=clave).first():
            continue
        SiteConfig.set(clave, meta["default"], descripcion=meta["desc"])
        nuevas += 1
    return nuevas


def tipo_esperado(clave: str) -> str | None:
    meta = DEFAULTS.get(clave)
    return meta["type"] if meta else None
