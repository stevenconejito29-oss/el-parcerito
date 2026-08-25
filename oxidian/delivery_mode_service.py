"""Política única para elegir cómo se despacha un pedido delivery.

Los canales nunca deciden por su cuenta si un pedido es inmediato o va por
franja: consultan esta pequeña capa. Así los toggles pueden activarse de forma
independiente sin dejar pedidos ambiguos entre checkout, web-chat y WhatsApp.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ModoDelivery(str, Enum):
    INMEDIATO = "inmediato"
    FRANJA = "franja"


@dataclass(frozen=True)
class PlanDelivery:
    modo: ModoDelivery
    slot_id: int | None = None


class ErrorPlanDelivery(ValueError):
    """Error presentable al cliente, común a todos los canales."""


MODE_CONFIG = {
    "inmediato": {"inmediato": True, "franjas": False},
    "franjas": {"inmediato": False, "franjas": True},
    "mixto": {"inmediato": True, "franjas": True},
}


def _enabled(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def modos_delivery_activos(reader=None) -> dict[str, bool]:
    """Estado de los dos módulos, leído una única vez desde SiteConfig."""
    if reader is None:
        from store_config import get_store_value
        reader = get_store_value
    return {
        "inmediato": _enabled(reader("delivery_inmediato_activo", "1")),
        "franjas": _enabled(reader("delivery_franjas_activo", "0")),
    }


def cambiar_modo_delivery(modo: str, *, actor_id: int, ip: str | None = None) -> dict[str, bool]:
    """Cambia la modalidad de forma atómica y protege trabajo en curso.

    Exige planificación futura antes de ofrecer franjas y evita apagar el
    módulo mientras cocina o reparto aún tienen pedidos programados activos.
    La autorización del actor se valida en la ruta; esta función concentra las
    invariantes compartidas y la auditoría.
    """
    from extensions import db
    from models import AuditLog, DeliverySlot, Order, SiteConfig
    from business_time import business_today

    selected = MODE_CONFIG.get(str(modo or "").strip().lower())
    if selected is None:
        raise ErrorPlanDelivery("Modo de reparto no válido.")
    if selected["franjas"]:
        future_slot = DeliverySlot.query.filter(
            DeliverySlot.activo.is_(True), DeliverySlot.fecha >= business_today(),
        ).first()
        if future_slot is None:
            raise ErrorPlanDelivery(
                "Crea al menos una franja futura activa antes de habilitar esta modalidad."
            )
    if not selected["franjas"]:
        active_scheduled = Order.query.filter(
            Order.slot_id.isnot(None),
            Order.estado.in_(("pendiente", "armando", "listo", "en_ruta")),
        ).count()
        if active_scheduled:
            raise ErrorPlanDelivery(
                f"No puedes apagar franjas: quedan {active_scheduled} pedidos programados activos."
            )
    SiteConfig.set("delivery_inmediato_activo", "1" if selected["inmediato"] else "0", actor_id)
    SiteConfig.set("delivery_franjas_activo", "1" if selected["franjas"] else "0", actor_id)
    AuditLog.registrar(
        actor_id, "cambiar_modo_delivery", "site_config",
        detalle=(
            f"modo={modo}; inmediato={int(selected['inmediato'])}; "
            f"franjas={int(selected['franjas'])}"
        ),
        ip=ip,
    )
    db.session.commit()
    return selected.copy()


def resolver_plan_delivery(slot_id_raw=None, modos=None) -> PlanDelivery:
    """Resuelve el modo de un pedido delivery y rechaza combinaciones inválidas.

    ``slot_id_raw`` es el dato recibido por HTTP. Si hay una franja, esta gana
    explícitamente; sin ella solo se permite inmediato cuando el módulo está
    activo. La disponibilidad/cupo concreto se valida atómicamente al reservar.
    """
    modos = modos or modos_delivery_activos()
    raw = str(slot_id_raw or "").strip()
    if raw:
        try:
            slot_id = int(raw)
        except (TypeError, ValueError) as exc:
            raise ErrorPlanDelivery("La franja de entrega no es válida.") from exc
        if slot_id < 1:
            raise ErrorPlanDelivery("La franja de entrega no es válida.")
        if not modos["franjas"]:
            raise ErrorPlanDelivery("El reparto por franjas no está disponible ahora.")
        return PlanDelivery(ModoDelivery.FRANJA, slot_id)
    if modos["inmediato"]:
        return PlanDelivery(ModoDelivery.INMEDIATO)
    if modos["franjas"]:
        raise ErrorPlanDelivery("Elige una franja de entrega para continuar.")
    raise ErrorPlanDelivery("El delivery no está disponible ahora mismo.")


def etiqueta_plan_delivery(pedido) -> str:
    """Etiqueta estable para ticket, chat y auditoría a partir del pedido."""
    return "DELIVERY · FRANJA" if getattr(pedido, "slot_id", None) else "DELIVERY · INMEDIATO"
