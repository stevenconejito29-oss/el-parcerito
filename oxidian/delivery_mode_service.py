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
