"""Servicio de reparto por franjas horarias con capacidad.

Módulo opcional (toggle `delivery_franjas_activo` en SiteConfig, apagado por
defecto). Cuando está activo, un pedido puede asociarse a una `DeliverySlot`
que impone tope de pedidos y cierre configurable.

Reglas invariantes (verificadas por tests):

- Solo pedidos con ``estado != 'cancelado'`` cuentan hacia la capacidad de la
  franja. Cancelar libera cupo inmediatamente al recalcular el conteo.
- La reserva usa ``SELECT ... FOR UPDATE`` sobre la franja para evitar dos
  clientes tomando el último hueco simultáneamente.
- El cierre por franja hereda del default global de SiteConfig cuando
  ``cierre_modo`` es NULL.
- Un repartidor no puede tener dos asignaciones activas simultáneas a la
  misma franja (índice único parcial).
- Repartidor no puede tomar franjas cerradas o inactivas.
- Toda franja planificada debe caber dentro del horario de apertura vigente.

Reglas de negocio en este módulo; las rutas HTTP solo adaptan I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
import json
from typing import Iterable

from extensions import db
from models import (
    DeliverySlot,
    ESTADOS_ACTIVOS,
    NotificationOutbox,
    Order,
    SlotRepartidor,
    User,
    utcnow,
)


# ─── Constantes ───────────────────────────────────────────────────────────

CIERRE_MODOS = ("al_iniciar_siguiente", "minutos_antes", "hora_fija")
MAX_SALIDAS_DIARIAS = 4
_ESTADO_CANCELADO = "cancelado"
NOTIF_CANAL = "whatsapp"
NOTIF_EVENTO_EN_PUERTA = "delivery_en_puerta"


def _validar_cierre(modo: str | None, valor: str | None) -> None:
    if modo is None:
        return
    if modo not in CIERRE_MODOS:
        raise ValueError(f"cierre_modo inválido: {modo}")
    if modo == "minutos_antes":
        try:
            minutos = int(valor or "")
        except (TypeError, ValueError) as exc:
            raise ValueError("Indica los minutos de cierre antes de la franja") from exc
        if not 0 <= minutos <= 24 * 60:
            raise ValueError("Los minutos de cierre deben estar entre 0 y 1440")
    if modo == "hora_fija":
        try:
            hh, mm = (int(part) for part in str(valor or "").split(":", 1))
            time(hh, mm)
        except (TypeError, ValueError) as exc:
            raise ValueError("La hora fija debe tener formato HH:MM") from exc


def ahora_local_negocio() -> datetime:
    from business_time import business_timezone
    return datetime.now(business_timezone()).replace(tzinfo=None)


def pedidos_por_salida() -> int:
    from store_config import get_store_value
    try:
        return max(1, min(10, int(get_store_value("delivery_franjas_pedidos_por_salida", "3") or 3)))
    except (TypeError, ValueError):
        return 3


def peso_maximo_salida_gramos() -> int:
    from store_config import get_store_value
    try:
        kg = float(get_store_value("delivery_franjas_peso_max_salida_kg", "12") or 12)
    except (TypeError, ValueError):
        kg = 12
    return int(max(1, min(50, kg)) * 1000)


def peso_estimado_pedido_gramos(pedido: Order) -> int:
    """Peso trazable desde catálogo; usa un default configurable por unidad."""
    from store_config import get_store_value
    try:
        por_defecto = max(1, int(get_store_value(
            "delivery_producto_peso_default_gramos", "500"
        ) or 500))
    except (TypeError, ValueError):
        por_defecto = 500
    total = 0
    for item in pedido.items:
        peso = getattr(item.producto, "peso_gramos", None) or por_defecto
        total += int(peso) * int(item.cantidad or 0)
    return total


def validar_tanda_seleccionada(pedidos: list[Order]) -> tuple[bool, str, int]:
    """Contrato único de cantidad y peso usado por cualquier interfaz rider."""
    if not pedidos:
        return False, "Selecciona al menos un pedido.", 0
    if len(pedidos) > pedidos_por_salida():
        return False, f"Puedes llevar hasta {pedidos_por_salida()} pedidos por salida.", 0
    peso = sum(peso_estimado_pedido_gramos(p) for p in pedidos)
    if peso > peso_maximo_salida_gramos():
        return False, "La selección supera el peso máximo configurado para la mochila.", peso
    return True, "", peso


def estado_operativo(slot: DeliverySlot, ahora: datetime | None = None) -> dict:
    ahora = ahora or ahora_local_negocio()
    inicio = datetime.combine(slot.fecha, slot.hora_inicio)
    fin = datetime.combine(slot.fecha, slot.hora_fin)
    if ahora < inicio:
        return {"estado": "proxima", "segundos": int((inicio - ahora).total_seconds())}
    if ahora >= fin:
        return {"estado": "finalizada", "segundos": 0}
    return {"estado": "activa", "segundos": int((fin - ahora).total_seconds())}


# ─── Resultado tipado de reserva (evita excepciones para flujos esperados) ─

class ResultadoReserva(Enum):
    RESERVADA = "reservada"
    LLENA = "llena"
    CERRADA = "cerrada"
    INACTIVA = "inactiva"
    NO_EXISTE = "no_existe"


@dataclass(frozen=True)
class Reserva:
    tipo: ResultadoReserva
    slot: DeliverySlot | None = None
    detalle: str = ""


# ─── Cierre de franja (función pura, unit-testeable sin DB) ───────────────

def franja_esta_cerrada(
    slot: DeliverySlot,
    ahora: datetime,
    franja_siguiente: DeliverySlot | None = None,
    cierre_modo_default: str = "al_iniciar_siguiente",
    cierre_valor_default: str = "",
) -> bool:
    """Aplica la política de cierre efectiva a la franja.

    ``ahora`` debe estar en la misma referencia temporal que ``slot.fecha`` +
    ``slot.hora_*`` (UTC naive según convención del modelo).

    Herencia: si ``slot.cierre_modo`` es NULL, se aplican los defaults
    globales (``cierre_modo_default`` / ``cierre_valor_default``).
    """
    inicio_dt = datetime.combine(slot.fecha, slot.hora_inicio)
    fin_dt = datetime.combine(slot.fecha, slot.hora_fin)

    # Una franja ya iniciada NO acepta nuevas reservas; ya empezó su ejecución.
    if ahora >= inicio_dt:
        return True

    modo = slot.cierre_modo or cierre_modo_default
    valor = (slot.cierre_valor if slot.cierre_modo else cierre_valor_default) or ""

    if modo == "al_iniciar_siguiente":
        return False  # sigue abierta hasta que llegue hora_inicio
    if modo == "minutos_antes":
        try:
            minutos = int(valor)
        except (TypeError, ValueError):
            return False
        return ahora >= (inicio_dt - timedelta(minutes=minutos))
    if modo == "hora_fija":
        try:
            hh, mm = (int(x) for x in valor.split(":", 1))
        except (TypeError, ValueError):
            return False
        cierre_dt = datetime.combine(slot.fecha, time(hh, mm))
        return ahora >= cierre_dt
    # Modo desconocido: falla segura (no cierra), operativa lo detectará.
    return False


# ─── Conteos y helpers de capacidad ───────────────────────────────────────

def _conteo_pedidos_activos(slot_id: int) -> int:
    """Nº de pedidos que ocupan cupo en la franja (todos menos cancelados)."""
    return (
        db.session.query(db.func.count(Order.id))
        .filter(Order.slot_id == slot_id, Order.estado != _ESTADO_CANCELADO)
        .scalar()
        or 0
    )


def _repartidores_activos(slot_id: int) -> int:
    return (
        db.session.query(db.func.count(SlotRepartidor.id))
        .filter(
            SlotRepartidor.slot_id == slot_id,
            SlotRepartidor.liberado_en.is_(None),
        )
        .scalar()
        or 0
    )


def _cierre_defaults() -> tuple[str, str]:
    """Lee defaults de cierre desde SiteConfig (import diferido evita ciclo)."""
    from store_config import get_store_value  # local, evita import circular

    modo = get_store_value("delivery_franjas_cierre_modo_default", "al_iniciar_siguiente") or "al_iniciar_siguiente"
    valor = get_store_value("delivery_franjas_cierre_valor_default", "") or ""
    return str(modo), str(valor)


# ─── Listado admin y cliente ──────────────────────────────────────────────

def listar_franjas_admin(desde: date, hasta: date) -> list[DeliverySlot]:
    """Franjas dentro del rango, ordenadas por fecha y hora de inicio."""
    return (
        DeliverySlot.query
        .filter(DeliverySlot.fecha >= desde, DeliverySlot.fecha <= hasta)
        .order_by(DeliverySlot.fecha, DeliverySlot.hora_inicio)
        .all()
    )


def listar_franjas_cliente(
    hoy: date,
    horizonte_dias: int = 7,
    ahora: datetime | None = None,
) -> list[dict]:
    """Franjas visibles para el cliente en checkout.

    Devuelve lista de dicts con los campos necesarios para la UI:
    ``id, fecha, hora_inicio, hora_fin, capacidad_max, ocupados,
    disponible, cerrada, sugerida``. Marca ``sugerida=True`` en la primera
    franja disponible cronológicamente. Excluye inactivas.
    """
    if ahora is None:
        ahora = ahora_local_negocio()
    hasta = hoy + timedelta(days=horizonte_dias - 1)
    slots = (
        DeliverySlot.query
        .filter(
            DeliverySlot.fecha >= hoy,
            DeliverySlot.fecha <= hasta,
            DeliverySlot.activo.is_(True),
        )
        .order_by(DeliverySlot.fecha, DeliverySlot.hora_inicio)
        .all()
    )
    modo_def, valor_def = _cierre_defaults()

    # Precomputa "franja siguiente" para el modo al_iniciar_siguiente por si
    # en el futuro se usa esa lógica (hoy no la aplicamos aquí porque el
    # cierre "al_iniciar_siguiente" equivale a ahora >= inicio, ya cubierto).
    resultado: list[dict] = []
    sugerida_marcada = False
    for slot in slots:
        ocupados = _conteo_pedidos_activos(slot.id)
        cerrada = franja_esta_cerrada(
            slot, ahora,
            cierre_modo_default=modo_def,
            cierre_valor_default=valor_def,
        )
        llena = ocupados >= slot.capacidad_max
        disponible = not cerrada and not llena
        sugerida = disponible and not sugerida_marcada
        if sugerida:
            sugerida_marcada = True
        resultado.append({
            "id": slot.id,
            "fecha": slot.fecha.isoformat(),
            "hora_inicio": slot.hora_inicio.strftime("%H:%M"),
            "hora_fin": slot.hora_fin.strftime("%H:%M"),
            "capacidad_max": slot.capacidad_max,
            "ocupados": ocupados,
            "cerrada": cerrada,
            "llena": llena,
            "disponible": disponible,
            "sugerida": sugerida,
        })
    return resultado


# ─── CRUD admin ───────────────────────────────────────────────────────────

def crear_franja(
    fecha: date,
    hora_inicio: time,
    hora_fin: time,
    capacidad_max: int,
    max_repartidores: int | None = None,
    cierre_modo: str | None = None,
    cierre_valor: str | None = None,
    notas_admin: str | None = None,
) -> DeliverySlot:
    from business_time import business_today
    if fecha < business_today():
        raise ValueError("No puedes crear una franja en una fecha pasada")
    if capacidad_max < 1:
        raise ValueError("capacidad_max debe ser >= 1")
    if max_repartidores is not None and max_repartidores < 1:
        raise ValueError("max_repartidores debe ser >= 1")
    if hora_fin <= hora_inicio:
        raise ValueError("hora_fin debe ser > hora_inicio")
    from schedule_service import franja_cabe_en_horario
    cabe, motivo = franja_cabe_en_horario(fecha, hora_inicio, hora_fin)
    if not cabe:
        raise ValueError(motivo)
    salidas_activas = (
        db.session.query(db.func.count(DeliverySlot.id))
        .filter(DeliverySlot.fecha == fecha, DeliverySlot.activo.is_(True))
        .scalar()
        or 0
    )
    if salidas_activas >= MAX_SALIDAS_DIARIAS:
        raise ValueError(
            f"Ya hay {MAX_SALIDAS_DIARIAS} salidas activas el {fecha.isoformat()}. "
            "Desactiva una franja o ajusta una de las existentes."
        )
    _validar_cierre(cierre_modo, cierre_valor)
    if notas_admin is not None and len(str(notas_admin)) > 500:
        raise ValueError("La nota interna no puede superar 500 caracteres")
    slot = DeliverySlot(
        fecha=fecha,
        hora_inicio=hora_inicio,
        hora_fin=hora_fin,
        capacidad_max=capacidad_max,
        max_repartidores=max_repartidores or 1,
        cierre_modo=cierre_modo,
        cierre_valor=cierre_valor,
        notas_admin=notas_admin,
    )
    db.session.add(slot)
    db.session.flush()
    return slot


def actualizar_franja(slot: DeliverySlot, **campos) -> DeliverySlot:
    """Actualiza una franja conservando cupos, horario y asignaciones válidas."""
    slot = db.session.query(DeliverySlot).filter(
        DeliverySlot.id == slot.id,
    ).with_for_update().one()
    permitidos = {
        "fecha", "hora_inicio", "hora_fin", "capacidad_max", "max_repartidores", "cierre_modo",
        "cierre_valor", "activo", "notas_admin",
    }
    desconocidos = set(campos) - permitidos
    if desconocidos:
        raise ValueError(f"campos no permitidos: {', '.join(sorted(desconocidos))}")
    fecha = campos.get("fecha", slot.fecha)
    hora_inicio = campos.get("hora_inicio", slot.hora_inicio)
    hora_fin = campos.get("hora_fin", slot.hora_fin)
    capacidad = campos.get("capacidad_max", slot.capacidad_max)
    max_repartidores = campos.get("max_repartidores", slot.max_repartidores)
    activo = campos.get("activo", slot.activo)
    from business_time import business_today
    if fecha < business_today():
        raise ValueError("No puedes mover una franja a una fecha pasada")
    if hora_fin <= hora_inicio:
        raise ValueError("hora_fin debe ser > hora_inicio")
    if capacidad < 1 or max_repartidores < 1:
        raise ValueError("La capacidad y los repartidores deben ser mayores que cero")
    pedidos_asignados = db.session.query(db.func.count(Order.id)).filter(
        Order.slot_id == slot.id, Order.estado != _ESTADO_CANCELADO,
    ).scalar() or 0
    if capacidad < pedidos_asignados:
        raise ValueError(f"La franja ya tiene {pedidos_asignados} pedidos y no puede reducirse por debajo de ese cupo")
    asignaciones_activas = slot.repartidores.filter_by(liberado_en=None).count()
    if max_repartidores < asignaciones_activas:
        raise ValueError(f"La franja ya tiene {asignaciones_activas} riders asignados")
    if slot.activo and activo is False:
        pedidos_vivos = db.session.query(db.func.count(Order.id)).filter(
            Order.slot_id == slot.id, Order.estado.in_(ESTADOS_ACTIVOS),
        ).scalar() or 0
        if pedidos_vivos:
            raise ValueError(
                f"No puedes desactivar la franja: todavía tiene {pedidos_vivos} pedido(s) activos"
            )
    cierre_modo = campos.get("cierre_modo", slot.cierre_modo)
    _validar_cierre(cierre_modo, campos.get("cierre_valor", slot.cierre_valor))
    if "notas_admin" in campos and campos["notas_admin"] is not None and len(str(campos["notas_admin"])) > 500:
        raise ValueError("La nota interna no puede superar 500 caracteres")
    from schedule_service import franja_cabe_en_horario
    cabe, motivo = franja_cabe_en_horario(fecha, hora_inicio, hora_fin)
    if not cabe:
        raise ValueError(motivo)
    if activo and (not slot.activo or fecha != slot.fecha):
        salidas_activas = (
            db.session.query(db.func.count(DeliverySlot.id))
            .filter(
                DeliverySlot.id != slot.id,
                DeliverySlot.fecha == fecha,
                DeliverySlot.activo.is_(True),
            )
            .scalar()
            or 0
        )
        if salidas_activas >= MAX_SALIDAS_DIARIAS:
            raise ValueError(f"Ya hay {MAX_SALIDAS_DIARIAS} salidas activas el {fecha.isoformat()}")
    for key, value in campos.items():
        setattr(slot, key, value)
    db.session.flush()
    return slot


def eliminar_franja(slot: DeliverySlot) -> str:
    """Elimina la franja. Si tiene pedidos, hace soft delete (activo=False).

    Devuelve 'hard' o 'soft' según lo aplicado.
    """
    slot = db.session.query(DeliverySlot).filter(
        DeliverySlot.id == slot.id,
    ).with_for_update().one()
    pedidos_vivos = db.session.query(Order.id).filter(
        Order.slot_id == slot.id, Order.estado.in_(ESTADOS_ACTIVOS),
    ).first()
    if pedidos_vivos:
        raise ValueError("No puedes eliminar una franja con pedidos activos")
    tiene_pedidos = db.session.query(Order.id).filter(Order.slot_id == slot.id).first()
    if tiene_pedidos:
        slot.activo = False
        db.session.flush()
        return "soft"
    db.session.delete(slot)
    db.session.flush()
    return "hard"


def clonar_semana(semana_origen_lunes: date, semana_destino_lunes: date) -> int:
    """Duplica franjas de una semana (lunes-domingo) a otra.

    No pisa franjas existentes en destino (UNIQUE(fecha, hora_inicio, hora_fin)).
    Retorna cantidad de franjas creadas.
    """
    if semana_origen_lunes.weekday() != 0 or semana_destino_lunes.weekday() != 0:
        raise ValueError("Las fechas deben ser lunes")
    delta = semana_destino_lunes - semana_origen_lunes
    origen_fin = semana_origen_lunes + timedelta(days=6)
    fuente = (
        DeliverySlot.query
        .filter(DeliverySlot.fecha >= semana_origen_lunes,
                DeliverySlot.fecha <= origen_fin)
        .all()
    )
    from schedule_service import franja_cabe_en_horario
    # Validamos todo antes de insertar para que una semana no quede a medias
    # si el horario de apertura cambió desde que se creó la semana de origen.
    for src in fuente:
        nueva_fecha = src.fecha + delta
        cabe, motivo = franja_cabe_en_horario(
            nueva_fecha, src.hora_inicio, src.hora_fin,
        )
        if not cabe:
            raise ValueError(
                f"No se puede clonar la franja {src.hora_inicio.strftime('%H:%M')}–"
                f"{src.hora_fin.strftime('%H:%M')} del {nueva_fecha.isoformat()}: {motivo}"
            )
    # Una franja es una salida agrupada: el negocio trabaja con un máximo de
    # cuatro salidas diarias. Contamos solo las que se crearán (no duplicados).
    candidatas = []
    for src in fuente:
        nueva_fecha = src.fecha + delta
        existe = (
            db.session.query(DeliverySlot.id)
            .filter(
                DeliverySlot.fecha == nueva_fecha,
                DeliverySlot.hora_inicio == src.hora_inicio,
                DeliverySlot.hora_fin == src.hora_fin,
            )
            .first()
        )
        if existe:
            continue
        candidatas.append((src, nueva_fecha))
    por_fecha = {}
    for src, nueva_fecha in candidatas:
        if not src.activo:
            continue
        por_fecha[nueva_fecha] = por_fecha.get(nueva_fecha, 0) + 1
    for nueva_fecha, nuevas_activas in por_fecha.items():
        existentes_activas = (
            db.session.query(db.func.count(DeliverySlot.id))
            .filter(DeliverySlot.fecha == nueva_fecha, DeliverySlot.activo.is_(True))
            .scalar()
            or 0
        )
        if existentes_activas + nuevas_activas > MAX_SALIDAS_DIARIAS:
            raise ValueError(
                f"El {nueva_fecha.isoformat()} superaría las {MAX_SALIDAS_DIARIAS} "
                "salidas diarias permitidas."
            )

    creadas = 0
    for src, nueva_fecha in candidatas:
        db.session.add(DeliverySlot(
            fecha=nueva_fecha,
            hora_inicio=src.hora_inicio,
            hora_fin=src.hora_fin,
            capacidad_max=src.capacidad_max,
            max_repartidores=src.max_repartidores,
            cierre_modo=src.cierre_modo,
            cierre_valor=src.cierre_valor,
            activo=src.activo,
            notas_admin=src.notas_admin,
        ))
        creadas += 1
    if creadas:
        db.session.flush()
    return creadas


# ─── Reserva (checkout) con locking ───────────────────────────────────────

def reservar_franja(slot_id: int, pedido: Order, ahora: datetime | None = None) -> Reserva:
    """Asocia el pedido a la franja de forma atómica.

    Usa SELECT ... FOR UPDATE sobre delivery_slots para serializar dos
    reservas simultáneas del último cupo. En SQLite (tests) FOR UPDATE es
    ignorado silenciosamente por SQLAlchemy, pero la lógica sigue siendo
    correcta bajo el modelo de bloqueo de SQLite (una escritura a la vez).
    """
    if ahora is None:
        ahora = ahora_local_negocio()
    slot = (
        db.session.query(DeliverySlot)
        .filter(DeliverySlot.id == slot_id)
        .with_for_update()
        .one_or_none()
    )
    if slot is None:
        return Reserva(ResultadoReserva.NO_EXISTE)
    if not slot.activo:
        return Reserva(ResultadoReserva.INACTIVA, slot=slot)
    modo_def, valor_def = _cierre_defaults()
    if franja_esta_cerrada(slot, ahora, cierre_modo_default=modo_def, cierre_valor_default=valor_def):
        return Reserva(ResultadoReserva.CERRADA, slot=slot)
    if _conteo_pedidos_activos(slot.id) >= slot.capacidad_max:
        return Reserva(ResultadoReserva.LLENA, slot=slot)
    # Si el pedido ya tenía otra franja, esta reasignación libera la anterior
    # implícitamente (el conteo la excluye al no coincidir slot_id).
    pedido.slot_id = slot.id
    db.session.flush()
    return Reserva(ResultadoReserva.RESERVADA, slot=slot)


def liberar_franja_por_pedido(pedido: Order) -> None:
    """Hook llamado desde cancelar_pedido_operativo. Idempotente.

    No hace falta cambiar contadores: el conteo activo se calcula en vivo
    excluyendo estado='cancelado'. Se conserva pedido.slot_id para trazabilidad
    del hueco que había ocupado.
    """
    # Explícitamente no borramos slot_id: histórico + trazabilidad.
    # El cupo queda libre porque el pedido pasa a estado 'cancelado'.
    return None


# ─── Self-assign de repartidor ────────────────────────────────────────────

class ResultadoRepartidor(Enum):
    TOMADA = "tomada"
    YA_TOMADA_POR_TI = "ya_tomada"
    LLENA_DE_REPARTIDORES = "llena_repartidores"
    CONFLICTO_HORARIO = "conflicto_horario"
    CERRADA = "cerrada"
    INACTIVA = "inactiva"
    NO_EXISTE = "no_existe"


@dataclass(frozen=True)
class AsignacionRepartidor:
    tipo: ResultadoRepartidor
    asignacion: SlotRepartidor | None = None


def tomar_franja_repartidor(
    slot_id: int, repartidor_id: int, ahora: datetime | None = None,
) -> AsignacionRepartidor:
    if ahora is None:
        ahora = ahora_local_negocio()
    # Serializa las decisiones del mismo rider: tomar dos franjas desde dos
    # pestañas no puede saltarse la comprobación de solapamiento.
    db.session.query(User).filter(User.id == repartidor_id).with_for_update().one()
    slot = (
        db.session.query(DeliverySlot)
        .filter(DeliverySlot.id == slot_id)
        .with_for_update()
        .one_or_none()
    )
    if slot is None:
        return AsignacionRepartidor(ResultadoRepartidor.NO_EXISTE)
    if not slot.activo:
        return AsignacionRepartidor(ResultadoRepartidor.INACTIVA)
    modo_def, valor_def = _cierre_defaults()
    if franja_esta_cerrada(slot, ahora, cierre_modo_default=modo_def, cierre_valor_default=valor_def):
        return AsignacionRepartidor(ResultadoRepartidor.CERRADA)
    ya = (
        db.session.query(SlotRepartidor)
        .filter(
            SlotRepartidor.slot_id == slot.id,
            SlotRepartidor.repartidor_id == repartidor_id,
            SlotRepartidor.liberado_en.is_(None),
        )
        .one_or_none()
    )
    if ya:
        return AsignacionRepartidor(ResultadoRepartidor.YA_TOMADA_POR_TI, asignacion=ya)
    solapada = (
        db.session.query(SlotRepartidor.id)
        .join(DeliverySlot, DeliverySlot.id == SlotRepartidor.slot_id)
        .filter(
            SlotRepartidor.repartidor_id == repartidor_id,
            SlotRepartidor.liberado_en.is_(None),
            SlotRepartidor.slot_id != slot.id,
            DeliverySlot.fecha == slot.fecha,
            DeliverySlot.hora_inicio < slot.hora_fin,
            DeliverySlot.hora_fin > slot.hora_inicio,
        )
        .first()
    )
    if solapada:
        return AsignacionRepartidor(ResultadoRepartidor.CONFLICTO_HORARIO)
    if _repartidores_activos(slot.id) >= slot.max_repartidores:
        return AsignacionRepartidor(ResultadoRepartidor.LLENA_DE_REPARTIDORES)
    asignacion = SlotRepartidor(
        slot_id=slot.id,
        repartidor_id=repartidor_id,
    )
    db.session.add(asignacion)
    db.session.flush()
    return AsignacionRepartidor(ResultadoRepartidor.TOMADA, asignacion=asignacion)


def liberar_franja_repartidor(slot_id: int, repartidor_id: int) -> bool:
    """Marca la asignación activa como liberada. Devuelve True si liberó algo."""
    db.session.query(User).filter(User.id == repartidor_id).with_for_update().one()
    activa = (
        db.session.query(SlotRepartidor)
        .filter(
            SlotRepartidor.slot_id == slot_id,
            SlotRepartidor.repartidor_id == repartidor_id,
            SlotRepartidor.liberado_en.is_(None),
        )
        .with_for_update()
        .one_or_none()
    )
    if not activa:
        return False
    if db.session.query(Order.id).filter(
        Order.slot_id == slot_id,
        Order.repartidor_id == repartidor_id,
        Order.estado.in_(("listo", "en_ruta")),
    ).first():
        raise ValueError("La franja tiene pedidos activos a tu cargo.")
    activa.liberado_en = utcnow()
    db.session.flush()
    return True


# ─── Notificación "en la puerta" ──────────────────────────────────────────

def notificar_en_la_puerta(pedido: Order, actor_id: int | None = None) -> NotificationOutbox | None:
    """Emite (una sola vez) la notificación WhatsApp de "repartidor en la puerta".

    Reutiliza la columna existente ``Order.en_punto_encuentro`` + timestamp
    como marca operativa; encola en NotificationOutbox el mensaje al cliente.
    Idempotente: si el pedido ya fue marcado, no vuelve a encolar.
    """
    from phone_utils import normalizar_telefono_cliente  # import diferido

    # Serializa doble toque/reintento y vuelve a leer el estado actual antes
    # de crear el outbox. Así la idempotencia no depende del navegador.
    pedido = db.session.query(Order).filter(Order.id == pedido.id).with_for_update().one()
    if pedido.estado != "en_ruta" or pedido.tipo_entrega_cliente != "delivery":
        raise ValueError("Solo se puede avisar al llegar durante una entrega activa")
    if actor_id is not None and pedido.repartidor_id != actor_id:
        raise PermissionError("El pedido pertenece a otro repartidor")

    if pedido.en_punto_encuentro:
        return None
    ya_encolada = (
        db.session.query(NotificationOutbox.id)
        .filter(
            NotificationOutbox.pedido_id == pedido.id,
            NotificationOutbox.evento == NOTIF_EVENTO_EN_PUERTA,
        )
        .first()
    )
    if ya_encolada:
        return None

    cliente = db.session.get(User, pedido.cliente_id)
    telefono = normalizar_telefono_cliente(getattr(cliente, "telefono", "") or "") if cliente else ""
    if not telefono:
        # Sin teléfono normalizable no hay destino; se salta silenciosamente
        # y el subestado igual se marca para consumo interno.
        pedido.en_punto_encuentro = True
        pedido.en_punto_encuentro_en = utcnow()
        db.session.flush()
        return None

    from store_config import get_store_value

    plantilla = (
        get_store_value(
            "delivery_franjas_notificar_puerta_texto",
            "Tu repartidor está en la puerta.",
        )
        or "Tu repartidor está en la puerta."
    )

    # El procesador WhatsApp (services.procesar_notificaciones_pendientes)
    # espera payload con las claves 'telefono' y 'mensaje'. Respetamos ese
    # contrato compartido para que el worker despache sin cambios.
    outbox = NotificationOutbox(
        canal=NOTIF_CANAL,
        evento=NOTIF_EVENTO_EN_PUERTA,
        destinatario=telefono,
        payload_json=json.dumps({
            "telefono": telefono,
            "mensaje": plantilla,
            "pedido_id": pedido.id,
            "numero_pedido": pedido.numero_pedido,
        }, ensure_ascii=False),
        pedido_id=pedido.id,
        user_id=pedido.cliente_id,
    )
    db.session.add(outbox)
    pedido.en_punto_encuentro = True
    pedido.en_punto_encuentro_en = utcnow()
    db.session.flush()
    return outbox
