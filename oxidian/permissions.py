"""Matriz de permisos unificada — fuente única para web y bot.

Diseño
======
Toda decisión de autorización de un actor (usuario web o número WhatsApp
identificado como admin/super_admin) sobre una acción del sistema pasa por
`allow(actor, action)`. El objetivo es evitar el drift entre:

- Decoradores web (`@admin_required`, `@super_admin_required`, feature gates).
- Guards del bot (`_bot_admin_actor_allowed`).
- Vistas condicionales en templates ("¿muestro este botón?").

Modelo de acción
----------------
Cada `action` es un identificador namespaced tipo `dominio.verbo[.subdominio]`:

    catalog.write            → crear/editar productos (feature productos)
    catalog.write.vertical   → cambiar catálogo (comida/producto/ambos)
    catalog.read             → listar/buscar productos
    store.read               → resumen operativo, salud, pedidos pendientes
    store.write              → cierre forzado / mensaje de tienda
    store.mode.toggle        → cambiar modo tienda (propia ↔ bar_servicio)
    store.modules.toggle     → activar/desactivar módulos (delivery, puntos…)
    config.write             → SiteConfig writes
    points.write             → mover puntos, gestionar canje, cupones
    marketing.write          → cupones, campañas
    whatsapp.send            → handoff, envíos manuales
    reports.read             → paneles IA, consultas analíticas
    finance.export           → exportar CSV Hacienda

Política por rol
----------------
- `super_admin` — siempre allow. Sin excepciones.
- `admin` — allow si la acción está mapeada a una `feature` que tiene
  concedida en `AdminFeature`, o si es una acción de lectura operativa.
- Cualquier otro rol — deny por defecto salvo overrides explícitos.

Añadir una acción
-----------------
1. Añade la constante en `ACTIONS`.
2. Registra su política en `_POLICY`.
3. Cambia la web y el bot para usar `allow(user, ACTION)`.

Nunca introduzcas guards con condiciones ad-hoc fuera de este módulo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ── Acciones (namespaced) ────────────────────────────────────────────────
class ACTIONS:
    CATALOG_READ            = "catalog.read"
    CATALOG_WRITE           = "catalog.write"
    CATALOG_WRITE_VERTICAL  = "catalog.write.vertical"
    STORE_READ              = "store.read"
    STORE_WRITE             = "store.write"
    STORE_MODE_TOGGLE       = "store.mode.toggle"
    STORE_MODULES_TOGGLE    = "store.modules.toggle"
    CONFIG_WRITE            = "config.write"
    POINTS_WRITE            = "points.write"
    MARKETING_WRITE         = "marketing.write"
    WHATSAPP_SEND           = "whatsapp.send"
    REPORTS_READ            = "reports.read"
    FINANCE_EXPORT          = "finance.export"
    ZONE_READ               = "zone.read"
    ZONE_TOGGLE             = "zone.toggle"     # admin puede togglear activo
    ZONE_WRITE              = "zone.write"      # solo super_admin
    STOCK_WRITE             = "stock.write"
    ORDER_TICKET_READ       = "order.ticket.read"


# ── Políticas ────────────────────────────────────────────────────────────
#   super_only  → solo super_admin
#   admin_read  → admin sin feature
#   feature:X   → admin si AdminFeature.tiene_acceso(user.id, X)
_POLICY = {
    ACTIONS.CATALOG_READ:            "admin_read",
    ACTIONS.CATALOG_WRITE:           "feature:productos",
    ACTIONS.CATALOG_WRITE_VERTICAL:  "super_only",
    ACTIONS.STORE_READ:              "admin_read",
    # Abrir/cerrar temporalmente forma parte de la operación diaria. El cambio
    # de nicho, módulos y configuración estructural permanece super_only.
    ACTIONS.STORE_WRITE:             "admin_read",
    ACTIONS.STORE_MODE_TOGGLE:       "super_only",
    ACTIONS.STORE_MODULES_TOGGLE:    "super_only",
    ACTIONS.CONFIG_WRITE:            "super_only",
    ACTIONS.POINTS_WRITE:            "feature:marketing",
    ACTIONS.MARKETING_WRITE:         "feature:marketing",
    ACTIONS.WHATSAPP_SEND:           "feature:whatsapp",
    ACTIONS.REPORTS_READ:            "feature:reportes",
    ACTIONS.FINANCE_EXPORT:          "super_only",
    ACTIONS.ZONE_READ:               "admin_read",
    ACTIONS.ZONE_TOGGLE:             "admin_read",
    ACTIONS.ZONE_WRITE:              "super_only",
    ACTIONS.STOCK_WRITE:             "feature:productos",
    # Lectura operativa con alcance por pedido en `can_read_order_ticket`.
    ACTIONS.ORDER_TICKET_READ:        "roles:admin,cocina,preparacion,repartidor",
}


@dataclass(frozen=True)
class Actor:
    """Sujeto de una decisión de permisos. `user_id` puede ser None si el
    actor solo se identificó por teléfono privilegiado (OWNER_NUMBER)."""
    rol: str
    user_id: Optional[int] = None
    privileged_by_env: bool = False  # OWNER_NUMBER / SUPERADMINS


def allow(actor: Optional[Actor], action: str) -> bool:
    """Autoriza una acción para un actor. Deny by default."""
    if actor is None:
        return False
    if actor.privileged_by_env or actor.rol == "super_admin":
        return True
    policy = _POLICY.get(action)
    if policy == "super_only":
        return False
    if policy == "admin_read":
        return actor.rol == "admin"
    if isinstance(policy, str) and policy.startswith("roles:"):
        roles = {rol.strip() for rol in policy.split(":", 1)[1].split(",")}
        return actor.rol in roles
    if isinstance(policy, str) and policy.startswith("feature:"):
        if actor.rol != "admin" or actor.user_id is None:
            return False
        slug = policy.split(":", 1)[1]
        # Import diferido para evitar ciclos y permitir tests con mocks.
        from models import AdminFeature
        return bool(AdminFeature.tiene_acceso(actor.user_id, slug))
    return False


def is_super_only(action: str) -> bool:
    """True si la acción está declarada `super_only` en la matriz.
    Útil para respuestas de error diferenciadas (SUPERADMIN_REQUIRED vs
    ADMIN_CAPABILITY_DENIED) sin exponer el dict interno."""
    return _POLICY.get(action) == "super_only"


def actor_from_user(user) -> Actor:
    """Construye Actor desde un flask_login `current_user`."""
    return Actor(
        rol=getattr(user, "rol", "") or "",
        user_id=getattr(user, "id", None),
        privileged_by_env=False,
    )


def can_read_order_ticket(user, pedido) -> bool:
    """Autoriza el ticket sin exponer pedidos de otros equipos o rutas.

    Admin y super_admin gestionan cualquier pedido. Los roles operativos solo
    acceden al pedido asignado o, mientras esté sin asignar, a su propia cola.
    Esta comprobación por recurso evita que cambiar el ID de la URL revele
    teléfono, dirección o notas de otro cliente.
    """
    actor = actor_from_user(user)
    if not allow(actor, ACTIONS.ORDER_TICKET_READ):
        return False
    if actor.rol in {"admin", "super_admin"}:
        return True
    if actor.rol == "repartidor":
        return getattr(pedido, "repartidor_id", None) == actor.user_id

    preparador_id = getattr(pedido, "preparador_id", None)
    if preparador_id is not None:
        return preparador_id == actor.user_id

    # Los pedidos preparados íntegramente por un proveedor externo nunca
    # pertenecen a las colas internas de cocina o preparación.
    from services import es_pedido_solo_bar
    if es_pedido_solo_bar(pedido):
        return False
    es_programado = bool(getattr(pedido, "es_programado", False))
    return (
        (actor.rol == "cocina" and not es_programado)
        or (actor.rol == "preparacion" and es_programado)
    )
