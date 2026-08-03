"""Fechas contables del negocio sobre timestamps UTC sin zona.

Los modelos persisten ``datetime`` UTC-naive. Las pantallas trabajan con días
locales; comparar directamente contra ``00:00`` local desplaza movimientos en
los cambios de día. Este módulo concentra la conversión para caja, P&L y
liquidaciones.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_BUSINESS_TIMEZONE = "Europe/Madrid"


def business_timezone() -> ZoneInfo:
    """Zona IANA configurada; usa el valor operativo por defecto si es inválida."""
    try:
        from store_config import get_store_value

        configured = (
            get_store_value("TIMEZONE_NEGOCIO", DEFAULT_BUSINESS_TIMEZONE)
            or DEFAULT_BUSINESS_TIMEZONE
        ).strip()
    except Exception:
        configured = DEFAULT_BUSINESS_TIMEZONE
    try:
        return ZoneInfo(configured)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_BUSINESS_TIMEZONE)


def business_today(now: datetime | None = None) -> date:
    """Fecha civil actual del negocio."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(business_timezone()).date()


def utc_naive_bounds(start_date: date, end_date: date | None = None) -> tuple[datetime, datetime]:
    """Devuelve ``[inicio, fin)`` UTC-naive para días civiles del negocio."""
    final_date = end_date or start_date
    if start_date > final_date:
        start_date, final_date = final_date, start_date
    tz = business_timezone()
    start_local = datetime.combine(start_date, time.min, tzinfo=tz)
    end_local = datetime.combine(final_date + timedelta(days=1), time.min, tzinfo=tz)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )
