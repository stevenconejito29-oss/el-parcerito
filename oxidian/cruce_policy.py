"""Reglas comerciales únicas de El Cruce para cliente, rider y administración."""
from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal, ROUND_UP

from store_config import get_store_value


ACTIVE_STATUSES = ("matched", "at_pickup", "picked_up", "in_transit")
CANCELLABLE_BY_CUSTOMER = ("open",) + ACTIVE_STATUSES
CANCELLABLE_BY_RIDER = ACTIVE_STATUSES


def _clean_reason(raw, minimum=4, maximum=240):
    text = (raw or "").strip()
    if len(text) < minimum:
        return None
    return text[:maximum]


def _number(key, default, minimum, maximum):
    try:
        value = Decimal(str(get_store_value(key, str(default))).replace(",", "."))
    except Exception:
        value = Decimal(str(default))
    return max(Decimal(str(minimum)), min(Decimal(str(maximum)), value))


def get_cruce_policy():
    return {
        "minimum": _number("CRUCE_PRECIO_MINIMO", 5, 3, 50),
        "per_km": _number("CRUCE_PRECIO_POR_KM", 1.25, .25, 10),
        "max_weight": _number("CRUCE_PESO_MAX_KG", 8, 1, 20),
        "max_value": _number("CRUCE_VALOR_MAX_EUR", 100, 10, 1000),
        "max_active": int(_number("CRUCE_MAX_ACTIVOS_CLIENTE", 3, 1, 5)),
    }


def distance_km(a_lat, a_lng, b_lat, b_lng):
    radius = 6371.0088
    lat1, lat2 = math.radians(float(a_lat)), math.radians(float(b_lat))
    dlat = lat2 - lat1
    dlng = math.radians(float(b_lng) - float(a_lng))
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def recommended_price(policy, kilometers):
    variable = Decimal(str(kilometers)) * policy["per_km"]
    raw = max(policy["minimum"], policy["minimum"] + variable)
    return (raw * 2).quantize(Decimal("1"), rounding=ROUND_UP) / 2


def open_timeout_minutes():
    """Minutos que un Cruce puede permanecer 'open' sin ofertas antes de expirar."""
    return int(_number("CRUCE_TIMEOUT_MIN", 60, 5, 720))


def registrar_evento_favor(request_row, actor_role, action, *, actor_id=None,
                           actor_label=None, amount=None, note=None):
    """Añade un evento a la sesión actual sin hacer commit.

    El caller es responsable del commit para mantener atomicidad con la
    transición de estado. `actor_label` sirve para riders/clientes sin `user_id`
    (visitor tokens, sistema, etc.).
    """
    from models import db, FavorEvent
    event = FavorEvent(
        request_id=request_row.id,
        actor_role=actor_role,
        actor_id=actor_id,
        actor_label=(actor_label or "")[:80] or None,
        action=action[:40],
        amount=amount,
        note=(note or "")[:240] or None,
    )
    db.session.add(event)
    return event


EXPIRE_BATCH_LIMIT = 200


def expire_stale_open_favors():
    """Marca como 'expired' los Cruces 'open' sin ofertas activas antiguos.

    Idempotente y acotada: procesa como máximo ``EXPIRE_BATCH_LIMIT`` filas
    por invocación para no bloquear el request cuando se llama desde el
    endpoint público (defensa contra DoS accidental si se acumula un lote).
    Devuelve la lista de FavorRequest expirados para notificar a los clientes
    tras el commit.
    """
    from datetime import datetime, timezone
    from models import db, FavorRequest
    minutes = open_timeout_minutes()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(minutes=minutes)
    stale = (
        FavorRequest.query
        .filter(FavorRequest.status == "open", FavorRequest.created_at < cutoff)
        .order_by(FavorRequest.created_at.asc())
        .limit(EXPIRE_BATCH_LIMIT)
        .all()
    )
    expired = []
    for row in stale:
        pending = [o for o in row.offers if o.status == "pending"]
        if pending:
            continue
        row.status = "expired"
        row.cancelled_at = now
        row.cancelled_by = "system"
        row.cancellation_reason = f"Sin ofertas tras {minutes} min."
        registrar_evento_favor(
            row, "system", "expired",
            note=f"Sin ofertas tras {minutes} min (corte {cutoff.isoformat()})",
            amount=row.offered_amount,
        )
        expired.append(row)
    if expired:
        db.session.commit()
    return expired


def rider_can_be_assigned(rider_id):
    """Comprueba estado y capacidad conjunta de pedidos + Cruces.

    ``capacidad_repartidor`` toma el advisory lock de carga de reparto y ya
    incluye Cruces activos en su cuenta (via ``carga_actual_repartidores``),
    por lo que basta con verificar que quede margen > 0.
    """
    from models import User
    from services import capacidad_repartidor

    rider = User.query.filter(User.id == rider_id, User.rol.in_(("repartidor", "admin", "super_admin")), User.activo.is_(True)).first()
    operational_admin = bool(rider and rider.rol in ("admin", "super_admin"))
    if not rider or (not operational_admin and not rider.disponible_para_pedidos) or not rider.acepta_cruces:
        return False, "El rider ya no está disponible para Cruces."
    if capacidad_repartidor(rider_id) <= 0:
        return False, "El rider alcanzó su capacidad de reparto."
    return True, ""
