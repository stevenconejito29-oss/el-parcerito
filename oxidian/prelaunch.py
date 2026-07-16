"""Reglas del escaparate de preapertura.

La cortina afecta únicamente a clientes anónimos. Salud, recursos estáticos,
integraciones y paneles operativos conservan sus rutas normales para que el
equipo pueda terminar la puesta a punto sin exponer el catálogo.
"""

OPERATIONAL_PREFIXES = (
    "/auth/",
    "/admin/",
    "/superadmin/",
    "/preparador/",
    "/repartidor/",
    "/pos/",
    "/staff/",
    "/marketing/",
    "/proveedor/",
    "/api/",
    "/static/",
    "/uploads/",
    "/health",
)

PUBLIC_INFRASTRUCTURE_PATHS = frozenset({
    "/favicon.ico",
    "/manifest.webmanifest",
    "/robots.txt",
    "/sw.js",
})


def es_ruta_exenta_preapertura(path: str) -> bool:
    """Indica si una ruta debe seguir disponible durante la preapertura."""
    normalized = path or "/"
    if normalized in PUBLIC_INFRASTRUCTURE_PATHS:
        return True
    return any(
        normalized == prefix.rstrip("/") or normalized.startswith(prefix)
        for prefix in OPERATIONAL_PREFIXES
    )
