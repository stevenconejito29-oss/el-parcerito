"""Horario semanal de la tienda.

La configuración se guarda como JSON en SiteConfig.HORARIO_SEMANAL_JSON:

    {"0": [["09:00", "14:00"], ["18:00", "22:00"]], ..., "6": []}

Las claves siguen ``datetime.weekday`` (0=lunes, 6=domingo). Una franja cuyo
fin es menor que el inicio cruza medianoche. El horario único histórico se usa
solo como compatibilidad cuando todavía no existe programación semanal.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import re


DAY_NAMES = (
    "Lunes", "Martes", "Miércoles", "Jueves",
    "Viernes", "Sábado", "Domingo",
)
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _minutes(value: str) -> int:
    hours, minutes = value.split(":", 1)
    return int(hours) * 60 + int(minutes)


def normalize_weekly_schedule(value) -> dict[str, list[list[str]]]:
    """Valida y normaliza un horario semanal serializable.

    Rechaza franjas vacías, duplicadas, solapadas o de 24 horas implícitas.
    Un día cerrado se representa con una lista vacía.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("El horario semanal no contiene JSON válido.") from exc
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("El horario semanal debe agrupar las franjas por día.")

    normalized: dict[str, list[list[str]]] = {str(day): [] for day in range(7)}
    for raw_day, raw_windows in value.items():
        try:
            day = int(raw_day)
        except (TypeError, ValueError) as exc:
            raise ValueError("Cada día del horario debe estar entre lunes y domingo.") from exc
        if day not in range(7):
            raise ValueError("Cada día del horario debe estar entre lunes y domingo.")
        if raw_windows is None:
            raw_windows = []
        if not isinstance(raw_windows, list):
            raise ValueError(f"Las franjas de {DAY_NAMES[day]} no son una lista.")

        expanded: list[tuple[int, int, list[str]]] = []
        for raw_window in raw_windows:
            if not isinstance(raw_window, (list, tuple)) or len(raw_window) != 2:
                raise ValueError(f"Una franja de {DAY_NAMES[day]} está incompleta.")
            start, end = (str(raw_window[0]).strip(), str(raw_window[1]).strip())
            if not _TIME_RE.fullmatch(start) or not _TIME_RE.fullmatch(end):
                raise ValueError(f"Revisa las horas de {DAY_NAMES[day]} (formato HH:MM).")
            if start == end:
                raise ValueError(
                    f"En {DAY_NAMES[day]} la apertura y el cierre no pueden ser iguales."
                )
            start_min = _minutes(start)
            end_min = _minutes(end)
            if end_min <= start_min:
                end_min += 24 * 60
            expanded.append((start_min, end_min, [start, end]))

        expanded.sort(key=lambda item: item[0])
        for previous, current in zip(expanded, expanded[1:]):
            if current[0] < previous[1]:
                raise ValueError(f"Hay franjas solapadas en {DAY_NAMES[day]}.")
        normalized[str(day)] = [item[2] for item in expanded]

    # Una franja que cruza medianoche también ocupa el inicio del día
    # siguiente. Validamos el ciclo semanal completo para impedir que dos
    # franjas distintas declaren abierto el mismo minuto.
    absolute: list[tuple[int, int, int]] = []
    week_minutes = 7 * 24 * 60
    for day in range(7):
        for start, end in normalized[str(day)]:
            start_abs = day * 1440 + _minutes(start)
            end_abs = day * 1440 + _minutes(end)
            if end_abs <= start_abs:
                end_abs += 1440
            absolute.append((start_abs, end_abs, day))
            # Duplicado desplazado para detectar el cruce domingo→lunes.
            absolute.append((start_abs + week_minutes, end_abs + week_minutes, day))
    absolute.sort(key=lambda item: item[0])
    for previous, current in zip(absolute, absolute[1:]):
        if current[0] < previous[1] and current[2] != previous[2]:
            raise ValueError(
                f"Una franja de {DAY_NAMES[current[2]]} se solapa con el día anterior."
            )

    return normalized


def schedule_json(value) -> str:
    return json.dumps(
        normalize_weekly_schedule(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def franja_cabe_en_horario(fecha, hora_inicio, hora_fin, schedule=None) -> tuple[bool, str]:
    """Valida que una franja de reparto quepa DENTRO del horario semanal.

    ``fecha`` (date) determina el weekday; ``hora_inicio``/``hora_fin`` (time)
    definen el intervalo de la franja. ``schedule`` es opcional — si es None
    se lee de SiteConfig (fuente única).

    Devuelve ``(ok, mensaje)``:
      - ok=True: la franja está completamente contenida en al menos una
        ventana del horario del día.
      - ok=False: mensaje operativo describiendo el conflicto.

    Backward-compat: si no hay horario configurado (ni JSON semanal ni legacy),
    devuelve ok=True. Esto permite instalaciones aún sin horarios definidos.
    Cruce de medianoche: si la ventana termina antes de empezar (ej 22:00-02:00)
    se admite como válida y se comprueba de forma explícita.
    """
    if schedule is None:
        schedule = configured_schedule()
    normalized = normalize_weekly_schedule(schedule) if schedule else {}
    if not normalized:
        return True, "sin horario semanal configurado (validación relajada)"

    weekday = fecha.weekday()
    inicio_min = hora_inicio.hour * 60 + hora_inicio.minute
    fin_min = hora_fin.hour * 60 + hora_fin.minute

    # Las franjas de reparto no cruzan medianoche. Una apertura nocturna sí
    # puede continuar desde el día anterior, por ejemplo una ruta de martes
    # 00:30–01:30 dentro de la apertura del lunes 20:00–02:00.
    ventanas_hoy = normalized.get(str(weekday), [])
    for v_ini, v_fin in ventanas_hoy:
        vi = _minutes(v_ini)
        vf = _minutes(v_fin)
        if vf > vi:
            # Ventana normal dentro del día.
            if vi <= inicio_min and fin_min <= vf:
                return True, "dentro del horario"
        else:
            # Ventana que cruza medianoche (ej 22:00-02:00). La franja debe
            # caber en [vi..24:00] o en [00:00..vf]. No aceptamos franjas
            # que crucen medianoche (por diseño: franjas son intra-día).
            if vi <= inicio_min and fin_min <= 24 * 60:
                return True, "dentro del horario (nocturno)"

    ayer = (weekday - 1) % 7
    for v_ini, v_fin in normalized.get(str(ayer), []):
        vi = _minutes(v_ini)
        vf = _minutes(v_fin)
        if vf < vi and 0 <= inicio_min and fin_min <= vf:
            return True, "dentro de la apertura nocturna del día anterior"

    horario_txt = ", ".join(f"{i}-{f}" for i, f in ventanas_hoy) or "cerrado"
    return False, (
        f"La franja {hora_inicio.strftime('%H:%M')}–{hora_fin.strftime('%H:%M')} "
        f"queda fuera del horario del {DAY_NAMES[weekday].lower()} ({horario_txt}). "
        f"Amplía primero el horario semanal o ajusta la franja."
    )


def schedule_is_open(schedule, when: datetime | None = None) -> bool:
    """Evalúa franjas normales y las que continúan desde el día anterior."""
    normalized = normalize_weekly_schedule(schedule)
    if not normalized:
        return False
    when = when or datetime.now()
    minute = when.hour * 60 + when.minute
    day = when.weekday()

    for start, end in normalized[str(day)]:
        start_min, end_min = _minutes(start), _minutes(end)
        if end_min > start_min and start_min <= minute <= end_min:
            return True
        if end_min < start_min and minute >= start_min:
            return True

    previous = (day - 1) % 7
    for start, end in normalized[str(previous)]:
        if _minutes(end) < _minutes(start) and minute <= _minutes(end):
            return True
    return False


def day_schedule_text(schedule, day: int | None = None) -> str:
    normalized = normalize_weekly_schedule(schedule)
    day = datetime.now().weekday() if day is None else int(day)
    windows = normalized.get(str(day), [])
    if not windows:
        return f"{DAY_NAMES[day]}: cerrado"
    return f"{DAY_NAMES[day]}: " + " y ".join(f"{start}–{end}" for start, end in windows)


def weekly_schedule_text(schedule) -> str:
    """Resumen completo, agrupando días consecutivos con las mismas franjas."""
    normalized = normalize_weekly_schedule(schedule)
    if not normalized:
        return "Horario no configurado"
    groups: list[tuple[int, int, tuple[tuple[str, str], ...]]] = []
    for day in range(7):
        windows = tuple(tuple(window) for window in normalized[str(day)])
        if groups and groups[-1][2] == windows:
            start, _, previous_windows = groups[-1]
            groups[-1] = (start, day, previous_windows)
        else:
            groups.append((day, day, windows))
    parts = []
    for start_day, end_day, windows in groups:
        label = DAY_NAMES[start_day]
        if end_day != start_day:
            label += f"–{DAY_NAMES[end_day]}"
        hours = "cerrado" if not windows else " y ".join(
            f"{start}–{end}" for start, end in windows
        )
        parts.append(f"{label}: {hours}")
    return " · ".join(parts)


def next_opening_text(schedule, when: datetime | None = None) -> str | None:
    normalized = normalize_weekly_schedule(schedule)
    when = when or datetime.now()
    for day_offset in range(8):
        candidate = when + timedelta(days=day_offset)
        for start, _end in normalized[str(candidate.weekday())]:
            start_min = _minutes(start)
            if day_offset == 0 and start_min <= when.hour * 60 + when.minute:
                continue
            day_label = "hoy" if day_offset == 0 else (
                "mañana" if day_offset == 1 else DAY_NAMES[candidate.weekday()].lower()
            )
            return f"{day_label} a las {start}"
    return None


def legacy_schedule(apertura: str, cierre: str) -> dict[str, list[list[str]]]:
    start = (apertura or "00:00").strip()
    end = (cierre or "23:59").strip()
    if not _TIME_RE.fullmatch(start) or not _TIME_RE.fullmatch(end) or start == end:
        start, end = "00:00", "23:59"
    return {str(day): [[start, end]] for day in range(7)}


def configured_schedule() -> dict[str, list[list[str]]]:
    """Lee una sola fuente de verdad y conserva instalaciones sin migrar."""
    from models import SiteConfig
    raw = SiteConfig.get("HORARIO_SEMANAL_JSON", "")
    if raw:
        try:
            schedule = normalize_weekly_schedule(raw)
            if schedule:
                return schedule
        except ValueError:
            pass
    return legacy_schedule(
        SiteConfig.get("HORARIO_APERTURA", "09:00"),
        SiteConfig.get("HORARIO_CIERRE", "22:30"),
    )


def configured_schedule_context(when: datetime | None = None) -> dict:
    schedule = configured_schedule()
    when = when or datetime.now()
    return {
        "schedule": schedule,
        "is_open": schedule_is_open(schedule, when),
        "today": day_schedule_text(schedule, when.weekday()),
        "weekly": weekly_schedule_text(schedule),
        "next_opening": next_opening_text(schedule, when),
    }
