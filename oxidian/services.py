"""
Lógica de negocio central:
  - Distribución automática de pedidos entre staff conectado
  - Generación de comisiones para repartidores
  - Registro de movimientos de caja
  - Validación de radio de entrega (geocodificación OSM Nominatim + Haversine)
"""
import math
import os
import time
import threading
import urllib.error
import urllib.request
import logging
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import and_, or_, case
from extensions import db
from models import (
    Caja,
    ESTADOS_ACTIVOS,
    ESTADOS_EN_PREPARACION,
    ESTADOS_EN_REPARTO,
    NotificationOutbox,
    Order,
    OrderEvent,
    StaffPayment,
    User,
)
from store_config import get_loyalty_terms, get_store_features

logger = logging.getLogger(__name__)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def minutos_anticipacion_pedido_programado() -> int:
    """Ventana configurable para iniciar un pedido con fecha de entrega."""
    from models import SiteConfig

    raw = SiteConfig.get(
        "PREP_BUFFER_PROGRAMADO_MIN",
        os.environ.get("PREP_BUFFER_PROGRAMADO_MIN", "60"),
    )
    try:
        minutos = int(str(raw).strip())
    except (TypeError, ValueError):
        minutos = 60
    return max(5, min(24 * 60, minutos))


def pedido_programado_disponible_para_preparar(
    pedido: Order,
    ahora: datetime | None = None,
) -> bool:
    """Aplica en todos los canales la ventana de un pedido programado.

    Una fecha ausente o contradictoria se cierra de forma segura para evitar
    preparar el pedido en un día incorrecto.
    """
    if not pedido.es_programado:
        return True
    fecha = pedido.fecha_entrega_programada
    if not fecha:
        return False
    referencia = ahora or utcnow()
    corte = (
        referencia + timedelta(minutes=minutos_anticipacion_pedido_programado())
    ).date()
    return fecha <= corte


def _json_payload(payload: dict | None) -> str:
    return json.dumps(payload or {}, ensure_ascii=False, default=str)


def registrar_evento_pedido(
    pedido: Order,
    tipo: str,
    actor_id: int | None = None,
    estado_anterior: str | None = None,
    estado_nuevo: str | None = None,
    canal: str | None = None,
    detalle: str | None = None,
    metadata: dict | None = None,
) -> OrderEvent | None:
    """Añade una entrada auditable al timeline del pedido sin hacer commit."""
    if not pedido or not pedido.id:
        return None
    evento = OrderEvent(
        pedido_id=pedido.id,
        tipo=tipo,
        actor_id=actor_id,
        estado_anterior=estado_anterior,
        estado_nuevo=estado_nuevo,
        canal=canal,
        detalle=detalle,
        metadata_json=_json_payload(metadata) if metadata else None,
    )
    db.session.add(evento)
    return evento


def registrar_pedido_creado(
    pedido: Order,
    actor_id: int | None = None,
    canal: str | None = None,
    detalle: str | None = None,
    metadata: dict | None = None,
) -> OrderEvent | None:
    # Antifraude: marca el pedido para verificación pasiva ANTES de escribir
    # el evento — si el motor de riesgo hoy dice "pending", el evento queda
    # con esa señal metadata para que la timeline lo refleje.
    marcar_confirmacion_si_procede(pedido)
    return registrar_evento_pedido(
        pedido,
        "pedido_creado",
        actor_id=actor_id,
        estado_nuevo=pedido.estado,
        canal=canal or pedido.origen,
        detalle=detalle,
        metadata=metadata,
    )


# ─────────────────────────────────────────────
# ANTIFRAUDE — verificación pasiva del pedido
# ─────────────────────────────────────────────

def _config_confirmacion_habilitada() -> bool:
    """Interruptor global. Config decide si aplicamos scoring de riesgo."""
    from models import SiteConfig as _SC
    raw = _SC.get("CONFIRMACION_HABILITADA", "1")
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _config_umbral_monto() -> float:
    """Umbral en euros por encima del cual el pedido es MEDIUM/HIGH."""
    from models import SiteConfig as _SC
    try:
        raw = _SC.get("CONFIRMACION_MONTO_UMBRAL_EUR", "50")
        umbral = float(raw or 50)
    except (TypeError, ValueError):
        umbral = 50.0
    return max(1.0, min(9999.0, umbral))


def _cliente_tiene_identidad_verificada(cliente_id: int, pedido_id_actual: int | None = None) -> bool:
    """True si el WhatsApp del cliente ya fue validado en una compra previa.

    Una respuesta afirmativa guardada como ``confirmacion_estado=confirmed``
    demuestra que controla ese WhatsApp sin obligarle a esperar a la entrega.
    Conservamos ``entregado`` como prueba para pedidos históricos anteriores a
    esta verificación. Un pedido cancelado o nunca confirmado no valida identidad.
    """
    if not cliente_id:
        return False
    q = Order.query.filter(
        Order.cliente_id == cliente_id,
        or_(
            Order.confirmacion_estado == "confirmed",
            Order.estado == "entregado",
        ),
    )
    if pedido_id_actual is not None:
        q = q.filter(Order.id != pedido_id_actual)
    return db.session.query(q.exists()).scalar() is True


def evaluate_order_risk(pedido: Order) -> dict:
    """Puntúa el pedido para verificación pasiva antifraude.

    Devuelve un dict con:
      - level:   'LOW' | 'MEDIUM' | 'HIGH'
      - reasons: lista de motivos legibles (útil para logs y auditoría)

    Regla v2 (2026-07-13) — deliberadamente simple:
      - HIGH   → cliente sin ningún pedido entregado previo (primera vez).
                 SIEMPRE se pide confirmación, independiente del monto.
      - MEDIUM → cliente con historial pero monto EXTREMO (>= 3x el umbral
                 configurado). Puede indicar cuenta comprometida, pedido
                 accidental o cambio de comportamiento — señal para el
                 equipo, no bloqueo.
      - LOW    → cliente con historial y monto normal. Cero fricción para
                 clientes conocidos, alineado con la política del negocio.

    La rama MEDIUM es un guardrail defensivo opcional: si el umbral se
    configura muy alto (o el negocio no maneja tickets extremos), en la
    práctica nunca dispara. La rama HIGH cubre el 99% de pedidos fantasma
    (siempre vienen de identidades nuevas).

    Ampliar heurísticas aquí es seguro: solo altera el `level` — no muta
    el pedido ni escribe en BD. El caller decide qué hacer con el level.
    """
    reasons: list[str] = []
    umbral = _config_umbral_monto()

    monto = float(pedido.total or 0)
    tiene_historial = _cliente_tiene_identidad_verificada(
        pedido.cliente_id, pedido_id_actual=pedido.id
    )

    if not tiene_historial:
        reasons.append("cliente_sin_historial")
        return {"level": "HIGH", "reasons": reasons}

    # Cliente conocido con historial de al menos 1 entrega.
    umbral_extremo = umbral * 3
    if monto >= umbral_extremo:
        reasons.append(f"monto_extremo>={umbral_extremo:.0f}")
        return {"level": "MEDIUM", "reasons": reasons}

    return {"level": "LOW", "reasons": reasons}


def marcar_confirmacion_si_procede(pedido: Order) -> str | None:
    """Aplica scoring de riesgo al pedido y setea `confirmacion_estado`.

    Devuelve el nivel de riesgo evaluado (para logs / tests), o None si el
    feature está desactivado en config o si el pedido ya tenía un valor.

    LOW    → no toca (pedido queda con confirmacion_estado=NULL, sin fricción).
    MEDIUM → no muta el pedido ni pide otra confirmación a un cliente conocido.
    HIGH   → marca 'pending'.

    No lanza ninguna excepción: el guard antifraude es best-effort. Si algo
    en la evaluación falla (ej: SiteConfig inaccesible), se loguea y el
    pedido sigue su flujo normal — nunca bloqueamos por un error del scorer.
    """
    if not _config_confirmacion_habilitada():
        return None
    if pedido.confirmacion_estado:
        return None
    try:
        result = evaluate_order_risk(pedido)
    except Exception:
        logger.exception(
            "evaluate_order_risk falló pedido=%s — sigue flujo normal",
            getattr(pedido, "id", None),
        )
        return None
    level = result["level"]
    # La confirmación por WhatsApp se solicita exclusivamente la primera vez.
    # Un importe extremo puede evaluarse como MEDIUM en el caller, pero nunca
    # debe reabrir la fricción de identidad ni dejar el pedido pendiente.
    if level == "HIGH":
        pedido.confirmacion_estado = "pending"
        pedido.confirmacion_nivel = level
        logger.info(
            "confirmacion pending pedido=%s level=%s reasons=%s",
            pedido.numero_pedido, level, ",".join(result["reasons"]),
        )
    return level


def _config_ttl_high_minutes() -> int:
    """Ventana de espera para HIGH pending antes de auto-cancelar."""
    from models import SiteConfig as _SC
    try:
        raw = _SC.get("CONFIRMACION_TTL_HIGH_MINUTES", "120")
        minutos = int(raw or 120)
    except (TypeError, ValueError):
        minutos = 120
    # 0 desactiva la auto-cancelación; para valores positivos aplicamos
    # el cap 15-1440 (15 min a 24 h). Un TTL muy corto genera falsos
    # positivos (cliente que tardó en ver el WhatsApp); muy largo pierde
    # protección.
    if minutos <= 0:
        return 0
    return max(15, min(1440, minutos))


def autocancelar_confirmaciones_expiradas() -> dict:
    """Cancela pedidos HIGH que llevan pending más de `CONFIRMACION_TTL_HIGH_MINUTES`.

    Diseño (deliberadamente conservador):
      - Solo cancela HIGH — MEDIUM queda para revisión manual.
      - Solo cancela pedidos aún en estado `pendiente` (no `armando` — si
        el equipo ya empezó a preparar, no interferimos).
      - Usa `cancelar_pedido_operativo` con detalle específico para que
        `metricas_antifraude` pueda distinguir del rechazo directo del
        cliente.
      - Best-effort por pedido: un fallo en uno no bloquea el resto.
      - Interruptor: TTL=0 desactiva completamente.

    Devuelve dict `{procesados, cancelados, errores}`.
    """
    resultado = {"procesados": 0, "cancelados": 0, "errores": 0}

    ttl_min = _config_ttl_high_minutes()
    if ttl_min <= 0:
        return resultado

    from datetime import timedelta
    corte = utcnow() - timedelta(minutes=ttl_min)

    try:
        pedidos = (
            Order.query
            .filter(
                Order.confirmacion_estado == "pending",
                Order.confirmacion_nivel == "HIGH",
                Order.estado == "pendiente",
                Order.creado_en <= corte,
            )
            .with_for_update(skip_locked=True)
            .all()
        )
    except Exception:
        logger.exception("autocancelar_confirmaciones_expiradas: query falló")
        return resultado

    for pedido in pedidos:
        resultado["procesados"] += 1
        try:
            cancelar_pedido_operativo(
                pedido,
                canal="antifraude",
                detalle=(
                    f"auto-cancelado por verificación pasiva expirada "
                    f"(HIGH, TTL {ttl_min}min sin respuesta)"
                ),
            )
            db.session.commit()
            resultado["cancelados"] += 1
            logger.info(
                "autocancel HIGH: pedido=%s creado_en=%s",
                pedido.numero_pedido, pedido.creado_en.isoformat(),
            )
        except Exception:
            db.session.rollback()
            resultado["errores"] += 1
            logger.exception(
                "autocancel HIGH falló pedido=%s — sigue con el resto",
                getattr(pedido, "id", None),
            )
    return resultado


def marcar_pedido_confirmado(pedido: Order) -> bool:
    """Registra la confirmación del cliente sobre un pedido `pending`.

    Idempotente — si el pedido no estaba en pending o ya fue confirmado
    devuelve False sin tocar la fila (para que el caller decida qué
    responder al bot). El commit lo hace el caller.
    """
    if pedido.confirmacion_estado != "pending":
        return False
    pedido.confirmacion_estado = "confirmed"
    pedido.confirmacion_en = utcnow()
    # La primera compra permanece fuera de la operación hasta verificar el
    # WhatsApp. Al confirmarla activamos en la misma transacción la asignación
    # y las notificaciones que se omitieron de forma deliberada al crearla.
    try:
        distribuir_pedido(pedido)
        encolar_notificaciones_proveedores_pedido(pedido)
    except Exception:
        # La identidad ya quedó verificada; una incidencia de asignación no
        # debe obligar al cliente a confirmar otra vez. La cola operativa puede
        # recuperar el pedido sin asignar y el fallo queda trazado.
        logger.exception(
            "No se pudo activar completamente el pedido confirmado %s",
            getattr(pedido, "id", None),
        )
    logger.info("confirmacion confirmed pedido=%s", pedido.numero_pedido)
    return True


def _coalesce_proveedor_id(snapshot: dict, item) -> int | None:
    """Resuelve el proveedor despachador de un item (suelto o combo).

    Prioridad: snapshot nuevo (`proveedor_despachador_id`) → producto vivo
    (`Product.proveedor_despachador_id`). Aplica tanto a SKUs sueltos como a
    combos: si el campo está informado, el bar es quien despacha el item.
    Devuelve None para pedidos legacy con snapshot sin equivalente."""
    snapshot_tiene_origen = (
        isinstance(snapshot, dict)
        and "proveedor_despachador_id" in snapshot
    )
    raw = snapshot.get("proveedor_despachador_id") if snapshot_tiene_origen else None
    if not snapshot_tiene_origen and item is not None and item.producto:
        raw = item.producto.proveedor_despachador_id
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _combo_componentes_snapshot(snapshot: dict) -> list[dict]:
    combo = snapshot.get("combo") if isinstance(snapshot, dict) else None
    if not isinstance(combo, dict):
        return []
    componentes = list(combo.get("componentes") or [])
    for grupo in combo.get("selecciones") or []:
        if isinstance(grupo, dict):
            componentes.extend(grupo.get("opciones") or [])
    return [c for c in componentes if isinstance(c, dict)]


def _metadata_pedido_item(item) -> dict:
    try:
        return item.get_metadata() or {}
    except Exception:
        return {}


def _snapshot_producto_item(item) -> dict:
    metadata = _metadata_pedido_item(item)
    snapshot = metadata.get("producto") if isinstance(metadata, dict) else None
    if isinstance(snapshot, dict):
        return snapshot
    return item.producto_snapshot or {}


def sincronizar_proveedores_pedido(pedido: Order) -> int:
    """Garantiza un OrderProviderStatus por cada proveedor que despacha items."""
    from models import OrderProviderStatus

    if not pedido or not pedido.id:
        return 0

    proveedor_ids: set[int] = set()
    for item in pedido.items:
        snapshot = _snapshot_producto_item(item)
        prov_id = _coalesce_proveedor_id(snapshot, item)
        if prov_id:
            proveedor_ids.add(prov_id)

    existentes = {
        estado.proveedor_id
        for estado in OrderProviderStatus.query.filter_by(pedido_id=pedido.id).all()
    }
    creados = 0
    for proveedor_id in proveedor_ids:
        if proveedor_id not in existentes:
            db.session.add(OrderProviderStatus(
                pedido_id=pedido.id,
                proveedor_id=proveedor_id,
            ))
            creados += 1
    return creados


def lineas_proveedor_pedido(pedido: Order, proveedor_id: int | None = None) -> list[dict]:
    """Líneas que un proveedor debe preparar.

    Devuelve una entrada por cada producto o combo cuyo despachador es el
    proveedor indicado. En combos incluye componentes como sub-detalle."""
    if not pedido:
        return []

    try:
        proveedor_id = int(proveedor_id) if proveedor_id is not None else None
    except (TypeError, ValueError):
        proveedor_id = None

    lineas = []
    for item in pedido.items:
        metadata = _metadata_pedido_item(item)
        snapshot = _snapshot_producto_item(item)
        item_prov = _coalesce_proveedor_id(snapshot, item)
        if not item_prov:
            continue
        if proveedor_id is not None and proveedor_id != item_prov:
            continue
        proveedor_nombre = snapshot.get("proveedor_despachador_nombre")
        if not proveedor_nombre and item.producto and item.producto.proveedor_despachador:
            proveedor_nombre = item.producto.proveedor_despachador.nombre

        componentes_resumen = []
        for componente in _combo_componentes_snapshot(metadata):
            componentes_resumen.append({
                "nombre": componente.get("nombre") or "Componente",
                "cantidad": max(1, int(componente.get("cantidad") or 1)) * max(1, int(item.cantidad or 1)),
                "notas": componente.get("notas_preparacion") or "",
            })

        lineas.append({
            "tipo": "item",
            "item": item,
            "cantidad": item.cantidad,
            "nombre": item.display_nombre,
            "notas": item.notas,
            "sabores": item.selected_flavor_names,
            "proveedor_id": item_prov,
            "proveedor_nombre": proveedor_nombre,
            "combo_nombre": None,
            "es_combo_completo": bool(item.display_es_combo),
            "componentes": componentes_resumen,
        })
    return lineas


def encolar_notificaciones_proveedores_pedido(pedido: Order) -> int:
    """Avisa a cada operador WhatsApp solo sobre las líneas de su bar."""
    from models import NotificationOutbox, OrderProviderStatus, User

    if not pedido or not pedido.id:
        return 0
    creadas = 0
    estados = OrderProviderStatus.query.filter_by(pedido_id=pedido.id).all()
    for estado in estados:
        lineas = lineas_proveedor_pedido(pedido, estado.proveedor_id)
        if not lineas:
            continue
        detalle = "\n".join(
            "• {}× {}{}".format(
                linea["cantidad"],
                linea["nombre"],
                f" · Sabor: {' · '.join(linea['sabores'])}"
                if linea.get("sabores") else "",
            )
            for linea in lineas
        )
        operadores = (
            User.query
            .filter_by(rol="proveedor", proveedor_id=estado.proveedor_id, activo=True)
            .filter(User.telefono_normalizado.isnot(None))
            .all()
        )
        for operador in operadores:
            evento = f"provider_order_{estado.proveedor_id}"
            ya_existe = NotificationOutbox.query.filter_by(
                canal="whatsapp",
                evento=evento,
                pedido_id=pedido.id,
                user_id=operador.id,
            ).first()
            if ya_existe:
                continue
            mensaje = (
                f"🏪 Nuevo pedido {pedido.numero_pedido} para tu bar.\n\n"
                f"{detalle}\n\n"
                "Responde *menu* para abrir el panel del bar."
            )
            if encolar_whatsapp_generico(
                operador.telefono_normalizado,
                mensaje,
                evento=evento,
                pedido_id=pedido.id,
                user_id=operador.id,
            ):
                creadas += 1
    return creadas


def avanzar_estado_pedido(
    pedido: Order,
    actor_id: int | None = None,
    canal: str | None = None,
    detalle: str | None = None,
    validar_operativa: bool = False,
) -> str:
    """Avanza el pedido usando el modelo y registra el cambio de estado."""
    if pedido.estado == "pendiente" and pedido.confirmacion_estado == "pending":
        raise ValueError(
            "El primer pedido aún no fue confirmado por WhatsApp. "
            "El cliente debe responder SI antes de iniciar la preparación."
        )
    if validar_operativa:
        validar_avance_operativo(pedido)
    estado_anterior = pedido.estado
    pedido.avanzar_estado()
    registrar_evento_pedido(
        pedido,
        "estado_cambiado",
        actor_id=actor_id,
        estado_anterior=estado_anterior,
        estado_nuevo=pedido.estado,
        canal=canal,
        detalle=detalle,
    )
    # Cuando un pedido con reserva (producto de fecha fija) queda listo,
    # avisamos al cliente que ya puede recoger o que su reparto está
    # programado. `enviar_whatsapp_estado` ya se encarga del canal WhatsApp
    # y respeta la config de notificaciones; aquí lo disparamos solo para
    # transiciones a "listo" que involucran productos programados.
    if estado_anterior != pedido.estado and pedido.estado == "listo":
        try:
            tiene_reserva = any(
                (getattr(item, "display_tipo_entrega", None) or "").lower() in ("programado", "encargo")
                for item in pedido.items
            )
        except Exception:
            tiene_reserva = False
        if tiene_reserva:
            try:
                enviar_whatsapp_estado(pedido)
            except Exception:
                logger.exception(
                    "No se pudo notificar al cliente del pedido %s la reserva lista",
                    getattr(pedido, "id", None),
                )
    return pedido.estado


def es_pedido_solo_bar(pedido: Order) -> bool:
    """True si TODOS los items del pedido tienen proveedor_despachador_id.

    En ese caso el pedido no necesita preparador interno: el bar (o bares) lo
    preparan, nuestra cocina/almacén no participa. Repartidor y avance los
    sigue gestionando Oxidian normalmente."""
    if not pedido or not pedido.items.count():
        return False
    for item in pedido.items:
        snapshot = _snapshot_producto_item(item)
        if not _coalesce_proveedor_id(snapshot, item):
            return False
    return True


def lineas_preparacion_interna(pedido: Order) -> list:
    """Items que realmente corresponden a cocina/almacén propios."""
    if not pedido:
        return []
    lineas = []
    for item in pedido.items:
        snapshot = _snapshot_producto_item(item)
        if not _coalesce_proveedor_id(snapshot, item):
            lineas.append(item)
    return lineas


def validar_avance_operativo(pedido: Order) -> None:
    """Aplica las barreras operativas que un avance administrativo no puede omitir."""
    solo_bar = es_pedido_solo_bar(pedido)

    if pedido.estado == "pendiente" and not pedido.preparador_id and not solo_bar:
        raise ValueError("Asigna un responsable antes de iniciar la preparación.")

    if pedido.estado != "armando":
        return
    if not pedido.preparador_id and not solo_bar:
        raise ValueError("El pedido no puede marcarse listo sin responsable de preparación.")

    sincronizar_proveedores_pedido(pedido)
    db.session.flush()
    db.session.expire(pedido, ["estados_proveedor"])
    if pedido.proveedores_pendientes:
        nombres = ", ".join(
            estado.proveedor.nombre if estado.proveedor else f"Proveedor #{estado.proveedor_id}"
            for estado in pedido.proveedores_pendientes
        )
        raise ValueError(f"Falta confirmación de proveedor: {nombres}.")

    # NOTA: el concepto "almacén" fue retirado — el negocio opera como un
    # único punto físico donde se prepara y despacha. No existe distinción
    # entre canal cocina y canal almacén. La validación previa se conserva
    # como no-op para no romper llamadas históricas.
    return


def reasignar_responsable_pedido(
    pedido: Order,
    campo: str,
    user_id: int | None,
    actor_id: int | None = None,
    canal: str | None = None,
) -> tuple[int | None, int | None]:
    """Valida y registra el cambio de responsable sin hacer commit."""
    reglas = {
        "preparador_id": ("pendiente", "preparación"),
        "repartidor_id": ("listo", "reparto"),
    }
    if campo not in reglas:
        raise ValueError("Campo de responsable inválido.")
    if campo == "repartidor_id" and not get_store_features()["delivery"]:
        raise ValueError("El módulo de delivery está desactivado.")
    if campo == "repartidor_id" and not pedido.requiere_reparto:
        raise ValueError("Los pedidos para recoger no admiten repartidor.")

    anterior_id = getattr(pedido, campo)
    nuevo_id = user_id or None
    if anterior_id == nuevo_id:
        return anterior_id, nuevo_id

    estado_permitido, etiqueta = reglas[campo]
    if pedido.estado != estado_permitido:
        raise ValueError(
            f"Solo se puede reasignar {etiqueta} cuando el pedido está "
            f"en estado '{estado_permitido}'."
        )

    if nuevo_id:
        asignado = db.session.get(User, nuevo_id)
        if not asignado or not asignado.activo:
            raise ValueError("Usuario no encontrado o inactivo.")
        if campo == "preparador_id":
            roles_permitidos = {"preparacion"} if _canal_pedido(pedido) == "almacen" else {"cocina", "preparacion"}
            if asignado.rol not in roles_permitidos:
                destino = (
                    "preparación o empaque/almacén"
                    if _canal_pedido(pedido) == "almacen"
                    else "cocina o preparación"
                )
                raise ValueError(f"Este pedido debe asignarse al equipo de {destino}.")
        elif asignado.rol != "repartidor":
            raise ValueError("El usuario seleccionado no tiene rol de repartidor.")

    setattr(pedido, campo, nuevo_id)
    registrar_evento_pedido(
        pedido,
        "responsable_reasignado",
        actor_id=actor_id,
        estado_anterior=pedido.estado,
        estado_nuevo=pedido.estado,
        canal=canal,
        detalle=f"{campo}: {anterior_id or 'sin asignar'} -> {nuevo_id or 'sin asignar'}",
        metadata={
            "campo": campo,
            "responsable_anterior_id": anterior_id,
            "responsable_nuevo_id": nuevo_id,
        },
    )
    return anterior_id, nuevo_id


def _restaurar_stock_pedido(pedido: Order) -> None:
    """Devuelve al inventario los ítems del pedido que corresponden.

    Reglas:
    - POS descuenta stock de TODOS los productos al vender.
    - Web/bot solo descuentan los productos ``inmediato`` (los ``programado``
      se descuentan al armar el pedido).
    Por tanto, solo se restaura si el ítem era inmediato O el pedido fue
    presencial (POS). El destino del stock (origen propio vs bar) se lee
    del snapshot congelado en ``OrderItem.metadata_json`` para preservar
    la trazabilidad histórica.
    """
    for item in pedido.items:
        producto = item.producto
        if not producto:
            continue
        restaurar = (
            pedido.origen == "presencial"
            or (item.display_tipo_entrega or "inmediato") == "inmediato"
        )
        if not restaurar:
            continue
        producto.restaurar_stock_pedido(item.cantidad, item.get_metadata())


def _liberar_tandas_pedido(pedido: Order) -> None:
    """Devuelve al `ProductBatch` las tandas reservadas por este pedido.

    Cada `OrderItem.metadata_json` de un encargo por lote guarda
    ``batch_id`` y ``tandas_reservadas`` en el momento del checkout.
    Al cancelar liberamos ese cupo para que otro cliente pueda comprar.
    Idempotente: si el batch no existe o el metadata no está, no rompe.
    """
    from models import ProductBatch

    for item in pedido.items:
        meta = item.get_metadata() or {}
        batch_id = meta.get("batch_id")
        tandas = int(meta.get("tandas_reservadas") or 0)
        if not batch_id or tandas <= 0:
            continue
        batch = ProductBatch.query.filter_by(id=batch_id).with_for_update().first()
        if batch is None:
            continue
        batch.liberar_tandas(tandas)


def _revertir_puntos_pedido(pedido: Order) -> None:
    """Ajusta el saldo de puntos del cliente al cancelar un pedido.

    - Puntos ``ganados``: solo se restan si realmente hubo un ``PointsLog`` de
      tipo ``ganado`` para este pedido (protege ante cancelaciones antes de
      la entrega, cuando aún no se han otorgado).
    - Puntos ``usados``: se devuelven íntegros. Cliente los canjeó como
      descuento y el pedido no llegó a fin.

    Se toma un lock sobre el ``User`` para evitar que dos cancelaciones
    concurrentes del mismo cliente pisen el saldo.
    """
    from models import PointsLog

    if not pedido.cliente_id:
        return
    cliente = (
        User.query.filter_by(id=pedido.cliente_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not cliente:
        return
    if pedido.puntos_ganados:
        log_ganado = PointsLog.query.filter_by(
            cliente_id=pedido.cliente_id,
            pedido_id=pedido.id,
            tipo="ganado",
        ).first()
        if log_ganado:
            puntos_a_quitar = min(pedido.puntos_ganados, cliente.puntos)
            if puntos_a_quitar > 0:
                cliente.puntos -= puntos_a_quitar
                db.session.add(PointsLog(
                    cliente_id=pedido.cliente_id,
                    pedido_id=pedido.id,
                    tipo="cancelado",
                    cantidad=-puntos_a_quitar,
                    descripcion=f"Puntos ganados revertidos — cancelación {pedido.numero_pedido}",
                ))
    if pedido.puntos_usados:
        cliente.puntos += pedido.puntos_usados
        db.session.add(PointsLog(
            cliente_id=pedido.cliente_id,
            pedido_id=pedido.id,
            tipo="devuelto",
            cantidad=pedido.puntos_usados,
            descripcion=f"Puntos de canje devueltos — cancelación {pedido.numero_pedido}",
        ))


def _revertir_comisiones_pedido(pedido: Order) -> None:
    """Desliga usos de afiliado del pedido y elimina StaffPayments no pagados.

    PostgreSQL no permite eliminar el StaffPayment mientras el AffiliateUse
    conserve la FK, por eso primero se anula ``staff_payment_id`` en cada
    uso y solo después se borra el StaffPayment de tipo comisión pendiente.
    """
    from models import AffiliateUse, StaffPayment

    for uso in AffiliateUse.query.filter_by(
        pedido_id=pedido.id,
        comision_pagada=False,
    ).all():
        uso.comision_generada = 0
        uso.staff_payment_id = None
    for pago in StaffPayment.query.filter_by(
        pedido_id=pedido.id, tipo="comision", pagado=False,
    ).all():
        db.session.delete(pago)


def _ejecutar_cancelacion_pedido(
    pedido: Order,
    forzar_desde_entregado: bool = False,
) -> None:
    """Aplica todos los efectos de negocio al cancelar un pedido.

    Restaura stock, revierte puntos, libera cupones/afiliados y anula
    comisiones no pagadas. NO escribe evento de auditoría — de eso se
    encarga el llamador (``cancelar_pedido_operativo``) para tener una
    única fuente de logging.
    """
    if pedido.estado == "cancelado":
        raise ValueError("El pedido ya está cancelado")
    if pedido.estado == "entregado" and not forzar_desde_entregado:
        raise ValueError("No se puede cancelar un pedido ya entregado")
    _restaurar_stock_pedido(pedido)
    _liberar_tandas_pedido(pedido)
    _revertir_puntos_pedido(pedido)
    if pedido.cupon:
        pedido.cupon.revertir_uso()
    if pedido.afiliado_codigo_rel:
        from models import AffiliateUse
        uso_pagado = AffiliateUse.query.filter_by(
            pedido_id=pedido.id,
            comision_pagada=True,
        ).first()
        # Si ya hubo una liquidación irreversible conservamos el uso y su
        # contador como evidencia financiera. Las cancelaciones normales nunca
        # llegan aquí porque no se permite pagar antes de entregar.
        if not uso_pagado:
            pedido.afiliado_codigo_rel.revertir_uso()
    _revertir_comisiones_pedido(pedido)
    # Un pedido terminal no debe conservar secretos de entrega. Esto también
    # cubre cancelaciones administrativas excepcionales desde reparto.
    pedido.codigo_confirmacion = None
    pedido.codigo_confirmacion_expira_en = None
    pedido.intentos_codigo = 0
    pedido.estado = "cancelado"


def cancelar_pedido_operativo(
    pedido: Order,
    actor_id: int | None = None,
    canal: str | None = None,
    detalle: str | None = None,
    forzar_desde_entregado: bool = False,
) -> None:
    """Cancela un pedido registrando el evento de auditoría.

    Entrada única para cancelaciones desde routes (admin, POS, api_bot,
    repartidor, proveedor). Toda la lógica de reversión vive en helpers
    privados de este módulo — el método ``Order.cancelar`` fue eliminado
    para que no exista un segundo camino que salte esta traza.
    """
    estado_anterior = pedido.estado
    _ejecutar_cancelacion_pedido(pedido, forzar_desde_entregado=forzar_desde_entregado)
    registrar_reversion_caja_pedido(
        pedido,
        motivo=detalle or canal or "cancelación",
        registrado_por=actor_id,
    )
    registrar_evento_pedido(
        pedido,
        "pedido_cancelado",
        actor_id=actor_id,
        estado_anterior=estado_anterior,
        estado_nuevo=pedido.estado,
        canal=canal,
        detalle=detalle,
    )


def registrar_reversion_caja_pedido(
    pedido: Order,
    motivo: str,
    registrado_por: int | None = None,
) -> Caja | None:
    """Revierte de forma idempotente el ingreso cobrado de un pedido cancelado.

    La cancelación puede originarse en admin, POS, bot, proveedor o reparto;
    por eso la reversión pertenece al servicio de dominio y no a una ruta.
    Solo se descuentan devoluciones previas: otros egresos relacionados con el
    pedido (si los hubiera) no reducen el dinero que corresponde devolver.
    """
    ingresos = db.session.query(db.func.coalesce(db.func.sum(Caja.monto), 0)).filter(
        Caja.pedido_id == pedido.id,
        Caja.tipo == "ingreso",
    ).scalar() or 0
    devoluciones = db.session.query(db.func.coalesce(db.func.sum(Caja.monto), 0)).filter(
        Caja.pedido_id == pedido.id,
        Caja.tipo == "egreso",
        Caja.categoria == "devolucion",
    ).scalar() or 0
    monto = Decimal(str(ingresos)) - Decimal(str(devoluciones))
    if monto <= 0:
        return None
    return registrar_egreso(
        monto.quantize(Decimal("0.01")),
        f"Reversión {pedido.numero_pedido} — {motivo}"[:200],
        categoria="devolucion",
        pedido_id=pedido.id,
        registrado_por=registrado_por,
    )


def registrar_pago_pedido(
    pedido: Order,
    actor_id: int | None = None,
    canal: str | None = None,
    detalle: str | None = None,
) -> OrderEvent | None:
    pedido.pago_confirmado = True
    pedido.pago_confirmado_por = actor_id
    pedido.pago_confirmado_en = utcnow()
    return registrar_evento_pedido(
        pedido,
        "pago_confirmado",
        actor_id=actor_id,
        estado_anterior=pedido.estado,
        estado_nuevo=pedido.estado,
        canal=canal,
        detalle=detalle or pedido.metodo_pago,
        metadata={
            "metodo_pago": pedido.metodo_pago,
            "total": float(pedido.total or 0),
            "pago_confirmado_en": pedido.pago_confirmado_en,
        },
    )


# ─────────────────────────────────────────────
# CONFIGURACIÓN DE PUNTOS — fuente única (BD)
# ─────────────────────────────────────────────

def get_puntos_config() -> dict:
    """
    Lee PUNTOS_POR_EURO y PUNTOS_CANJE_RATIO siempre desde SiteConfig (BD).
    Único punto de verdad para todos los canales (web, bot, POS).
    Devuelve {'por_euro': int, 'ratio': int}.
    """
    from models import SiteConfig
    def _int_config(clave, default):
        raw = SiteConfig.get(clave, default)
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning("Config de puntos inválida para %s=%r; usando %s", clave, raw, default)
            return default
    return {
        "por_euro": max(0, _int_config("PUNTOS_POR_EURO", 1)),
        "ratio":    max(1, _int_config("PUNTOS_CANJE_RATIO", 100)),
    }


def calcular_puntos_ganados(total) -> int:
    """Calcula la acumulación de una compra, independientemente del canje.

    ``FEATURE_PUNTOS`` controla si el cliente puede consultar y canjear su
    saldo, pero nunca debe detener la acumulación interna. Mantener el cálculo
    aquí evita reglas distintas entre web, WhatsApp y POS.
    """
    try:
        importe = Decimal(str(total or 0))
    except (TypeError, ValueError, ArithmeticError):
        logger.warning("Total inválido para calcular puntos: %r", total)
        return 0
    if not importe.is_finite() or importe <= 0:
        return 0
    return max(0, int(importe * Decimal(get_puntos_config()["por_euro"])))


def get_pedido_minimo() -> float:
    """
    Monto mínimo global de pedido (euros). 0 = sin mínimo.
    Se configura desde `/superadmin/config` como clave PEDIDO_MINIMO_EUR.
    """
    from models import SiteConfig
    raw = SiteConfig.get("PEDIDO_MINIMO_EUR", "0")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        logger.warning("PEDIDO_MINIMO_EUR inválido (%r); usando 0", raw)
        return 0.0


# ─────────────────────────────────────────────
# GEO-VALIDACIÓN DE RADIO DE ENTREGA
# ─────────────────────────────────────────────

def _haversine_km(lat1, lon1, lat2, lon2):
    """Distancia en km entre dos coordenadas usando fórmula de Haversine."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# Caché en memoria para geocodificación: {clave: (coords, timestamp)}
_geocode_cache: dict = {}
_GEOCODE_TTL = 3600  # 1 hora — direcciones locales no cambian frecuentemente


def _tiene_calle_nominatim(hit: dict) -> bool:
    """Devuelve True si el resultado de Nominatim contiene una calle real (no solo ciudad)."""
    address = hit.get("address", {})
    return bool(
        address.get("road") or
        address.get("pedestrian") or
        address.get("footway") or
        address.get("house_number")
    )


def _bbox_cobertura_activa():
    """Calcula el rectángulo de búsqueda a partir de las zonas realmente activas."""
    from models import ZonaEntrega
    from zone_geometry import limites_cobertura

    bounds = []
    for zona in ZonaEntrega.query.filter_by(activo=True).all():
        if zona.tiene_poligono:
            limit = limites_cobertura(zona.cobertura_geojson)
            if limit:
                bounds.append(limit)
        elif zona.tiene_radio:
            lat_delta = float(zona.radio_km) / 111.0
            lon_delta = float(zona.radio_km) / (
                111.0 * max(0.1, math.cos(math.radians(zona.centro_lat)))
            )
            bounds.append((
                zona.centro_lat - lat_delta, zona.centro_lng - lon_delta,
                zona.centro_lat + lat_delta, zona.centro_lng + lon_delta,
            ))
    if not bounds:
        return None
    return (
        min(item[0] for item in bounds), min(item[1] for item in bounds),
        max(item[2] for item in bounds), max(item[3] for item in bounds),
    )


def geocodificar_direccion(direccion: str, ciudad: str = "") -> tuple[float, float] | None:
    """
    Geocodifica una dirección dentro del área del negocio. Estrategia de dos pasos:

    1. Búsqueda estructurada (street + city configurada): precisa, no puede ser manipulada
       por el cliente escribiendo otra ciudad.
    2. Si no encuentra, búsqueda libre con viewbox+bounded=1 restringido al radio de
       entrega: cubre calles que OSM no tiene indexadas en la búsqueda estructurada,
       pero sigue siendo imposible devolver resultados fuera del área configurada.

    Ambos pasos exigen que el resultado contenga una calle real (no solo ciudad).
    """
    cache_key = None
    try:
        import requests as _req
        from models import SiteConfig

        ciudad = (ciudad or SiteConfig.get("CIUDAD_NEGOCIO", "")).strip()
        direccion = (direccion or "").strip()
        provincia = SiteConfig.get("PROVINCIA_NEGOCIO", "")
        pais = SiteConfig.get("PAIS_NEGOCIO", "")
        pais_iso = SiteConfig.get("PAIS_CODIGO_ISO", "").lower()
        nombre_neg = SiteConfig.get("NOMBRE_NEGOCIO", "Mi tienda")
        user_agent = f"{nombre_neg.replace(' ', '')}/1.0"

        try:
            centro_lat = float(SiteConfig.get("CENTRO_LAT", ""))
            centro_lon = float(SiteConfig.get("CENTRO_LON", ""))
            radio_km = float(SiteConfig.get("RADIO_ENTREGA_KM", "5"))
        except (ValueError, TypeError):
            centro_lat = centro_lon = None
            radio_km = 5.0

        # Extraer solo el segmento de calle — descartar cualquier ciudad que el cliente
        # haya escrito después de la primera coma (ej: "Calle Real 5, Madrid").
        # Excepción: si el segundo segmento es un número, se considera parte de la calle
        # (formato "Calle Mayor, 5").
        if "," in direccion:
            partes = [p.strip() for p in direccion.split(",")]
            segundo = partes[1].replace("º", "").replace("ª", "").replace("°", "").strip()
            calle = f"{partes[0]}, {partes[1]}" if segundo.isdigit() else partes[0]
        else:
            calle = direccion.strip()

        calle = (calle or "").strip()
        cache_key = f"v2:{calle.lower()}|{ciudad.lower()}"
        cached = _geocode_cache.get(cache_key)
        if cached:
            coords, ts = cached
            if time.time() - ts < _GEOCODE_TTL:
                logger.debug("Geocoding cache hit '%s'", direccion)
                return coords

        def _get(params):
            query_params = {**params, "format": "json", "limit": 1, "addressdetails": 1}
            if pais_iso:
                query_params["countrycodes"] = pais_iso
            resp = _req.get(
                "https://nominatim.openstreetmap.org/search",
                params=query_params,
                headers={"User-Agent": user_agent},
                timeout=5,
            )
            time.sleep(1.1)
            return resp.json() if resp.ok else []

        # ── Paso 1: búsqueda estructurada (más precisa) ──────────────────────
        structured = {"street": calle}
        if ciudad:
            structured["city"] = ciudad
        if provincia:
            structured["state"] = provincia
        if pais:
            structured["country"] = pais
        hits = _get(structured)
        if hits and _tiene_calle_nominatim(hits[0]):
            coords = float(hits[0]["lat"]), float(hits[0]["lon"])
            _geocode_cache[cache_key] = (coords, time.time())
            logger.debug("Geocoding struct '%s' → %.4f,%.4f", direccion, *coords)
            return coords

        # ── Paso 2: búsqueda libre ACOTADA al bbox del radio de entrega ──────
        # bounded=1 impide que Nominatim devuelva resultados fuera del viewbox,
        # por lo que no importa si el cliente escribió "Madrid" en la dirección.
        if centro_lat is None or centro_lon is None:
            return None
        margen = geocode_bbox_margin()
        detailed_bbox = _bbox_cobertura_activa()
        if detailed_bbox:
            min_lat, min_lon, max_lat, max_lon = detailed_bbox
            lat_padding = max((max_lat - min_lat) * (margen - 1) / 2, 0.002)
            lon_padding = max((max_lon - min_lon) * (margen - 1) / 2, 0.002)
            viewbox = (
                f"{min_lon - lon_padding:.6f},{min_lat - lat_padding:.6f},"
                f"{max_lon + lon_padding:.6f},{max_lat + lat_padding:.6f}"
            )
        else:
            deg_lat = radio_km / 111.0 * margen
            deg_lon = radio_km / (111.0 * math.cos(math.radians(centro_lat))) * margen
            viewbox = (
                f"{centro_lon - deg_lon:.6f},{centro_lat - deg_lat:.6f},"
                f"{centro_lon + deg_lon:.6f},{centro_lat + deg_lat:.6f}"
            )
        hits2 = _get({"q": calle, "viewbox": viewbox, "bounded": 1})
        if hits2 and _tiene_calle_nominatim(hits2[0]):
            coords = float(hits2[0]["lat"]), float(hits2[0]["lon"])
            _geocode_cache[cache_key] = (coords, time.time())
            logger.debug("Geocoding bounded '%s' → %.4f,%.4f", direccion, *coords)
            return coords

    except Exception as e:
        logger.warning("Geocodificación fallida para '%s': %s", direccion, e)

    if cache_key:
        _geocode_cache[cache_key] = (None, time.time())
    return None


def asignar_zona_por_direccion(direccion: str, zonas):
    """Devuelve la ZonaEntrega que mejor se ajusta a la dirección del cliente.

    Diseño **fail-closed** (2026-07-13): ante ausencia de geodata o dirección
    no geocodificable, devolvemos None en vez de aceptar el pedido con
    fallback ciego. El fallback legacy (primera zona activa sin verificar
    geografía) solo se activa si `ALLOW_LEGACY_ZONE_FALLBACK` está a 1 en
    SiteConfig — por defecto está desactivado.

    Reglas:
    - Si HAY zonas con geodata: match por distancia dentro del radio, o
      None si no encaja / no se puede geocodificar.
    - Si NO hay zonas con geodata: intenta el radio global del negocio
      (CENTRO_LAT/LON/RADIO_ENTREGA_KM). Si tampoco, cae al legacy solo
      cuando el flag lo permite explícitamente.
    """
    if not zonas:
        return None
    from models import SiteConfig
    geo_zonas = [z for z in zonas if z.activo and z.tiene_geo]
    ciudad = SiteConfig.get("CIUDAD_NEGOCIO", "")

    if geo_zonas:
        coords = geocodificar_direccion(direccion or "", ciudad=ciudad) if direccion else None
        if coords is None:
            return None
        zona, _ = _resolver_zona_por_coordenadas(coords[0], coords[1], zonas)
        return zona

    # Sin zonas con geodata — intento con el radio global del negocio.
    activas = [z for z in zonas if z.activo]
    if not activas:
        return None
    validacion = validar_radio_entrega(direccion)
    if validacion["ok"] and validacion.get("distancia_km") is not None:
        activas.sort(key=lambda z: (z.orden or 0, float(z.precio_envio or 0)))
        return activas[0]

    # Último recurso: fallback legacy sin verificación geográfica. Deshabilitado
    # por defecto — solo lo activa un admin con conocimiento de causa cuando
    # todavía no ha configurado geodata pero necesita seguir vendiendo.
    if _to_bool_service(SiteConfig.get("ALLOW_LEGACY_ZONE_FALLBACK", "0")):
        logger.warning(
            "asignar_zona_por_direccion: fallback LEGACY activo, aceptando %r sin geocode",
            (direccion or "")[:60],
        )
        return activas[0]
    return None


def asignar_zona_por_coordenadas(lat, lon, zonas):
    """Resuelve zona y distancia usando coordenadas concedidas por el navegador.

    Estrategia (por orden):
    1. Si hay zonas con centro+radio: matchea contra la más cercana dentro
       del radio.
    2. Si NO hay zonas con geo pero SÍ hay coordenadas del NEGOCIO
       (SiteConfig.DIRECCION_NEGOCIO_LAT/LNG + RADIO_ENTREGA_KM), valida
       contra el centro del negocio con ese radio.
    3. Fallback final: primera zona activa (compat con zonas legacy sin geo)."""
    return _resolver_zona_por_coordenadas(lat, lon, zonas)


def _resolver_zona_por_coordenadas(lat, lon, zonas):
    """Motor único de cobertura usado por web, PWA, checkout y chatbot.

    Los polígonos prevalecen sobre círculos legacy. En solapes del mismo tipo,
    ``orden`` decide de forma explícita y estable.
    """
    if not zonas:
        return None, None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None

    geo_zonas = [zona for zona in zonas if zona.activo and zona.tiene_geo]
    if geo_zonas:
        from zone_geometry import contiene_punto

        candidatos = []
        negocio_lat, negocio_lng, _ = _leer_geo_negocio()
        for zona in geo_zonas:
            if zona.tiene_poligono:
                contiene = contiene_punto(zona.cobertura_geojson, lat, lon)
                precision = 0
            else:
                contiene = (
                    _haversine_km(zona.centro_lat, zona.centro_lng, lat, lon)
                    <= float(zona.radio_km)
                )
                precision = 1
            if not contiene:
                continue
            ref_lat = zona.centro_lat if zona.centro_lat is not None else negocio_lat
            ref_lng = zona.centro_lng if zona.centro_lng is not None else negocio_lng
            distancia = (
                _haversine_km(ref_lat, ref_lng, lat, lon)
                if ref_lat is not None and ref_lng is not None else None
            )
            candidatos.append((
                precision, zona.orden or 0,
                distancia if distancia is not None else float("inf"), zona.id, zona,
            ))
        if not candidatos:
            return None, None
        candidatos.sort(key=lambda row: row[:4])
        distancia, zona = candidatos[0][2], candidatos[0][4]
        return zona, None if not math.isfinite(distancia) else round(distancia, 2)

    # 2) Sin zonas geo: usa el centro global del negocio como fallback si está
    # configurado. Este es el camino usado por comercios que aún no han creado
    # zonas individuales — la validación de radio global se aplica igual.
    neg_lat, neg_lng, neg_radio = _leer_geo_negocio()
    if neg_lat is not None:
        distancia = _haversine_km(neg_lat, neg_lng, lat, lon)
        activas = [zona for zona in zonas if zona.activo]
        if distancia <= neg_radio and activas:
            activas.sort(key=lambda z: (z.orden or 0, float(z.precio_envio or 0), z.id))
            return activas[0], round(distancia, 2)
        return None, round(distancia, 2)

    # 3) Sin geodata en zonas ni en negocio: fail-closed. El fallback legacy
    # sin verificar geografía solo se activa si el admin lo pidió
    # explícitamente vía `ALLOW_LEGACY_ZONE_FALLBACK`.
    from models import SiteConfig
    if _to_bool_service(SiteConfig.get("ALLOW_LEGACY_ZONE_FALLBACK", "0")):
        activas = [zona for zona in zonas if zona.activo]
        if activas:
            logger.warning(
                "asignar_zona_por_coordenadas: fallback LEGACY activo (%.4f, %.4f)",
                lat, lon,
            )
            return activas[0], None
    return None, None


def aplicar_snapshot_zona_pedido(pedido, zona, costo_envio=0) -> None:
    """Congela la decisión geográfica y financiera aplicada al pedido.

    El FK mantiene la relación operativa para reparto; estos campos preservan
    el recibo histórico aunque la zona se renombre, archive o cambie de precio.
    """
    pedido.costo_envio_snapshot = max(0, Decimal(str(costo_envio or 0)))
    if zona is None:
        pedido.zona_nombre_snapshot = None
        pedido.zona_precio_envio_snapshot = None
        pedido.zona_tiempo_estimado_min_snapshot = None
        pedido.zona_tipo_cobertura_snapshot = None
        return
    pedido.zona_nombre_snapshot = zona.nombre
    pedido.zona_precio_envio_snapshot = zona.precio_envio
    pedido.zona_tiempo_estimado_min_snapshot = zona.tiempo_estimado_min
    pedido.zona_tipo_cobertura_snapshot = zona.tipo_cobertura


def _leer_geo_negocio() -> tuple[float | None, float | None, float | None]:
    """Lee (lat, lon, radio_km) del centro del negocio desde SiteConfig.

    Devuelve (None, None, None) si cualquier valor está ausente o no se puede
    parsear. El cap defensivo del radio protege contra configuraciones que
    dispararían el radio a valores absurdos (>25km cubre casi toda una
    provincia y hace inviable el negocio de proximidad).
    """
    from models import SiteConfig

    def _f(clave):
        raw = SiteConfig.get(clave, "")
        if raw is None or str(raw).strip() == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    lat = _f("CENTRO_LAT")
    lon = _f("CENTRO_LON")
    radio = _f("RADIO_ENTREGA_KM")
    if lat is None or lon is None or radio is None:
        return None, None, None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None, None, None
    # Cap defensivo: 0.5 km mínimo (evita configuraciones que rechazan todo),
    # 25 km máximo (delivery de proximidad, ya no local).
    radio = max(0.5, min(25.0, radio))
    return lat, lon, radio


def validar_radio_entrega(direccion: str) -> dict:
    """Valida una dirección contra zonas detalladas o el radio global compatible.

    Devuelve dict {"ok": bool, "distancia_km": float|None, "mensaje": str}.
    Diseño **fail-closed**: cualquier ambigüedad devuelve `ok=False` con
    mensaje operativo. Nunca acepta silenciosamente con advertencia — el
    daño de un pedido a 30km es mayor que la fricción de re-teclear.

    Escenarios cubiertos:
      - Validación desactivada (`VALIDAR_RADIO_ENTREGA=0`) → acepta.
      - Dirección demasiado corta (<6 chars) → rechaza con instrucción.
      - Sin config de centro/radio → rechaza pidiendo configurar.
      - Dirección no geocodificable → rechaza con instrucción, salvo que
        el admin haya bajado el flag `BLOQUEAR_DIRECCION_NO_VERIFICADA=0`.
      - Fuera de todas las zonas → rechaza y ofrece recogida si está disponible.
    """
    from models import SiteConfig, ZonaEntrega

    if not _to_bool_service(SiteConfig.get("VALIDAR_RADIO_ENTREGA", "1")):
        return {
            "ok": True, "distancia_km": None, "mensaje": "",
            "validacion_desactivada": True,
        }

    if not direccion or len(direccion.strip()) < 6:
        return {
            "ok": False,
            "distancia_km": None,
            "mensaje": "Escribe la dirección completa con calle y número.",
        }

    zonas = ZonaEntrega.query.filter_by(activo=True).all()
    hay_cobertura_detallada = any(zona.tiene_geo for zona in zonas)
    centro_lat, centro_lon, radio_km = _leer_geo_negocio()
    if centro_lat is None and not hay_cobertura_detallada:
        # Fail-closed: sin config no aceptamos pedidos aunque el flag esté
        # activo. Mejor no vender un pedido que enviarlo a Sevilla y perder
        # dinero + reputación.
        return {
            "ok": False,
            "distancia_km": None,
            "mensaje": "La cobertura todavía no está configurada. Contacta con el negocio.",
        }

    ciudad = SiteConfig.get("CIUDAD_NEGOCIO", "")
    coords = geocodificar_direccion(direccion, ciudad=ciudad)
    if coords is None:
        if hay_cobertura_detallada or _to_bool_service(SiteConfig.get("BLOQUEAR_DIRECCION_NO_VERIFICADA", "1")):
            return {
                "ok": False,
                "distancia_km": None,
                "mensaje": (
                    f"No encontramos esa dirección{f' en {ciudad}' if ciudad else ''}. "
                    "Escribe la calle y número tal como aparece en el callejero, "
                    "por ejemplo «Calle Mayor 5»."
                ),
            }
        logger.warning("No se pudo geocodificar '%s'. Pedido aceptado con advertencia.", direccion)
        return {"ok": True, "distancia_km": None, "mensaje": "No se pudo verificar la ubicación"}

    lat, lon = coords
    if hay_cobertura_detallada:
        zona, distancia = _resolver_zona_por_coordenadas(lat, lon, zonas)
        if zona is None:
            if centro_lat is not None:
                distancia = round(_haversine_km(centro_lat, centro_lon, lat, lon), 2)
            return {
                "ok": False,
                "distancia_km": distancia,
                "mensaje": (
                    f"Lo sentimos, esa dirección queda fuera de las zonas de reparto"
                    f"{f' de {ciudad}' if ciudad else ''}. "
                    "Prueba otra dirección o selecciona recogida en el local."
                ),
            }
        return {
            "ok": True,
            "distancia_km": distancia,
            "mensaje": "",
            "zona_id": zona.id,
            "zona_nombre": zona.nombre,
            "metodo_cobertura": zona.tipo_cobertura,
        }

    distancia = _haversine_km(centro_lat, centro_lon, lat, lon)

    if distancia > radio_km:
        return {
            "ok": False,
            "distancia_km": round(distancia, 2),
            "mensaje": (
                f"Lo sentimos, tu dirección queda fuera de nuestra zona de reparto"
                f"{f' en {ciudad}' if ciudad else ''} "
                f"({distancia:.1f} km del centro). Solo entregamos dentro de {radio_km:.1f} km."
            ),
        }

    return {"ok": True, "distancia_km": round(distancia, 2), "mensaje": ""}


def _to_bool_service(val):
    return str(val).strip().lower() in ("1", "true", "si", "sí", "on", "yes")


def tienda_abierta_en_horario(
    apertura: str,
    cierre: str,
    ahora: str | None = None,
    forzada_cerrada: bool = False,
    forzada_abierta: bool = False,
) -> bool:
    """Evalua estado de tienda combinando overrides manuales + horario.

    Precedencia:
        1. `forzada_cerrada` gana sobre todo (emergencia / cierre urgente).
        2. `forzada_abierta` ignora el horario (admin quiere vender fuera de
           franja — p. ej. evento especial, servicio extraordinario). Sin
           este flag no había forma de aceptar pedidos fuera del horario
           configurado aunque el admin lo pidiera desde WhatsApp.
        3. Sin overrides → respeta la franja HH:MM (soporta ventanas
           nocturnas tipo 20:00-02:00).
    """
    if forzada_cerrada:
        return False
    if forzada_abierta:
        return True
    ahora = ahora or datetime.now().strftime("%H:%M")
    apertura = (apertura or "00:00").strip()
    cierre = (cierre or "23:59").strip()
    if not all(len(v) == 5 and v[2] == ":" for v in (apertura, cierre, ahora)):
        return True
    if apertura <= cierre:
        return apertura <= ahora <= cierre
    return ahora >= apertura or ahora <= cierre


# ─────────────────────────────────────────────
# COLA DE DISTRIBUCIÓN
# ─────────────────────────────────────────────

def _candidatos_disponibles(usuarios):
    """
    Devuelve candidatos que activaron disponibilidad manual y siguen conectados.
    """
    return [u for u in usuarios if getattr(u, "disponible_para_pedidos", False)]


def _tipo_pedido(pedido: Order) -> str:
    """
    Determina el tipo dominante de un pedido según sus items:
    - 'programado' si algún item es programado
    - 'inmediato' si todos son inmediatos
    Usado para dirigir el pedido al rol correcto de preparación.
    """
    try:
        for item in pedido.items:
            if item.display_tipo_entrega in ("encargo", "programado"):
                return "programado"
    except Exception:
        logger.exception("No se pudo determinar tipo de pedido %s", getattr(pedido, "id", None))
    return "inmediato"


def _canal_pedido(pedido: Order) -> str:
    """Canal operativo estable; prioriza el snapshot guardado en cada línea."""
    canales = set()
    for item in pedido.items:
        canal = item.display_canal_preparacion
        canales.add((canal or "cocina").strip().lower())
    return "almacen" if canales == {"almacen"} else "cocina"


# ═══════════════════════════════════════════════════════════════════════
# WORKLOAD BALANCING — carga de empleados y topes concurrentes
#
# Guarda contra sobrecarga: un empleado que se queda como único online no
# debe recibir infinitos pedidos. Config vía SiteConfig con env fallback.
#
# `carga_actual_preparadores()` y `carga_actual_repartidores()` computan la
# carga de TODOS los usuarios candidatos en 1 sola query — reemplaza los
# N accesos a `pedidos_activos_como_*()` en el sort.
# ═══════════════════════════════════════════════════════════════════════

def _cfg_int(clave: str, default: int, minimo: int = 1, maximo: int = 999) -> int:
    """Lee SiteConfig con cap defensivo. Fallback silencioso al default."""
    try:
        from models import SiteConfig as _SC
        v = int(_SC.get(clave, str(default)) or default)
        return max(minimo, min(v, maximo))
    except Exception:
        return default


def _cfg_float(clave: str, default: float, minimo: float = 0.0, maximo: float = 1e9) -> float:
    """Análogo a `_cfg_int` para valores decimales (timeouts, márgenes)."""
    try:
        from models import SiteConfig as _SC
        raw = _SC.get(clave, str(default))
        v = float(str(raw).strip().replace(",", ".")) if raw is not None else default
        return max(minimo, min(v, maximo))
    except Exception:
        return default


def bot_health_errors_24h_max() -> int:
    return _cfg_int("BOT_HEALTH_ERRORS_24H_MAX", 200, minimo=10, maximo=10000)


def bot_health_handoffs_undelivered_max() -> int:
    return _cfg_int("BOT_HEALTH_HANDOFFS_UNDELIVERED_MAX", 50, minimo=1, maximo=10000)


def bot_status_check_timeout_sec() -> float:
    return _cfg_float("BOT_STATUS_CHECK_TIMEOUT_SEC", 2.5, minimo=0.5, maximo=30.0)


def notification_retry_backoff_max_min() -> int:
    return _cfg_int("NOTIFICATION_RETRY_BACKOFF_MAX_MIN", 60, minimo=1, maximo=1440)


def geocode_bbox_margin() -> float:
    return _cfg_float("GEOCODE_BBOX_MARGIN", 1.5, minimo=1.0, maximo=5.0)


def max_pedidos_por_preparador() -> int:
    """Máximo pedidos activos (pendiente+armando) por preparador antes de
    considerar desbordado. Default 8. Se puede subir/bajar sin redeploy."""
    return _cfg_int("MAX_PEDIDOS_POR_PREPARADOR", 8, minimo=1, maximo=100)


def max_pedidos_por_repartidor() -> int:
    """Máximo pedidos activos (listo+en_ruta) por repartidor. Default 5."""
    return _cfg_int("MAX_PEDIDOS_POR_REPARTIDOR", 5, minimo=1, maximo=50)


def carga_actual_preparadores(user_ids: list[int]) -> dict[int, int]:
    """Devuelve {user_id: pedidos_activos_como_preparador} para user_ids
    dados, con UNA sola query agregada. Evita el N+1 del sort."""
    if not user_ids:
        return {}
    from sqlalchemy import func
    rows = (
        db.session.query(Order.preparador_id, func.count(Order.id))
        .filter(
            Order.preparador_id.in_(user_ids),
            Order.estado.in_(ESTADOS_EN_PREPARACION),
        )
        .group_by(Order.preparador_id)
        .all()
    )
    return {uid: 0 for uid in user_ids} | {uid: n for uid, n in rows}


def carga_actual_repartidores(user_ids: list[int]) -> dict[int, int]:
    """Devuelve {user_id: pedidos_activos_como_repartidor} en 1 query.
    Cuenta pedidos en estado listo o en_ruta asignados al repartidor."""
    if not user_ids:
        return {}
    from sqlalchemy import func
    rows = (
        db.session.query(Order.repartidor_id, func.count(Order.id))
        .filter(
            Order.repartidor_id.in_(user_ids),
            Order.estado.in_(ESTADOS_EN_REPARTO),
            Order.tipo_entrega_cliente == "delivery",
        )
        .group_by(Order.repartidor_id)
        .all()
    )
    return {uid: 0 for uid in user_ids} | {uid: n for uid, n in rows}


def _elegir_menos_cargado(candidatos: list, cargas: dict[int, int],
                          tope: int) -> tuple | None:
    """Elige el candidato menos cargado que aún tenga margen bajo el tope.

    Si TODOS los candidatos están al tope o por encima, devuelve el menos
    cargado igualmente (política pragmática: mejor asignar tarde que dejar
    huérfano). Retorna (usuario, carga_actual, overloaded_flag).
    """
    if not candidatos:
        return None
    ordenados = sorted(candidatos, key=lambda u: (cargas.get(u.id, 0), u.id))
    con_margen = [u for u in ordenados if cargas.get(u.id, 0) < tope]
    if con_margen:
        elegido = con_margen[0]
        return (elegido, cargas.get(elegido.id, 0), False)
    elegido = ordenados[0]
    logger.warning(
        "workload: TODOS los candidatos (%d) están al tope (%d). Asigno al "
        "menos cargado igualmente: %s con %d pedidos activos.",
        len(candidatos), tope, elegido.nombre, cargas.get(elegido.id, 0),
    )
    return (elegido, cargas.get(elegido.id, 0), True)


def rebalancear_pedidos_huerfanos() -> dict:
    """Reasigna trabajo no iniciado cuyo responsable está offline o inactivo.

    Nunca mueve pedidos ``armando`` ni ``en_ruta``: en esos estados el
    responsable puede tener físicamente el producto y una reasignación
    automática duplicaría preparación o entrega. Esos incidentes se resuelven
    explícitamente desde operación.

    Ejecutable desde el worker (cron) o desde admin como acción one-shot.
    Devuelve dict con conteos por rol.
    """
    resultado = {"preparador": 0, "repartidor": 0}

    # ── Preparador huérfano
    pedidos_prep = (
        Order.query
        .filter(
            Order.estado == "pendiente",
            Order.preparador_id.isnot(None),
        )
        .join(User, Order.preparador_id == User.id)
        .filter(db.or_(User.activo.is_(False), User.en_linea.is_(False)))
        .with_for_update(skip_locked=True)
        .all()
    )
    for pedido in pedidos_prep:
        try:
            preparador_anterior_id = pedido.preparador_id
            pedido.preparador_id = None
            db.session.flush()
            nuevo = distribuir_pedido(pedido)
            if nuevo and nuevo.id != preparador_anterior_id:
                resultado["preparador"] += 1
                logger.info(
                    "rebalanceo: pedido %s reasignado de user %s → %s (huérfano)",
                    pedido.numero_pedido, preparador_anterior_id, nuevo.nombre,
                )
            elif not nuevo:
                # Nadie disponible: dejar sin asignar para que cola lo recoja.
                pass
        except Exception:
            logger.exception("rebalanceo preparador: fallo pedido %s", pedido.id)

    # ── Repartidor huérfano
    pedidos_rep = (
        Order.query
        .filter(
            Order.estado == "listo",
            Order.repartidor_id.isnot(None),
            Order.tipo_entrega_cliente == "delivery",
        )
        .join(User, Order.repartidor_id == User.id)
        .filter(db.or_(User.activo.is_(False), User.en_linea.is_(False)))
        .with_for_update(skip_locked=True)
        .all()
    )
    for pedido in pedidos_rep:
        try:
            rep_anterior_id = pedido.repartidor_id
            pedido.repartidor_id = None
            db.session.flush()
            nuevo = distribuir_repartidor(pedido)
            if nuevo and nuevo.id != rep_anterior_id:
                resultado["repartidor"] += 1
                logger.info(
                    "rebalanceo: pedido %s reasignado de rep %s → %s (huérfano)",
                    pedido.numero_pedido, rep_anterior_id, nuevo.nombre,
                )
        except Exception:
            logger.exception("rebalanceo repartidor: fallo pedido %s", pedido.id)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("rebalancear_pedidos_huerfanos: commit falló")

    if resultado["preparador"] or resultado["repartidor"]:
        logger.info("rebalanceo: %s", resultado)
    return resultado


def redistribuir_pendientes_sin_asignar() -> int:
    """
    Asigna a preparadores online los pedidos 'pendiente' sin preparador.
    Se llama cuando un preparador se pone online — los pedidos que esperaban
    se reparten de forma equitativa de inmediato.

    Hace flush() tras cada asignación para que pedidos_activos_como_preparador()
    refleje la carga real al distribuir el siguiente pedido.

    Devuelve el número de pedidos asignados.
    """
    pedidos = (
        Order.query
        .filter_by(estado="pendiente", preparador_id=None)
        .filter(or_(
            Order.confirmacion_estado.is_(None),
            Order.confirmacion_estado != "pending",
        ))
        .order_by(Order.creado_en)
        .with_for_update(skip_locked=True)
        .all()
    )

    asignados = 0
    for pedido in pedidos:
        try:
            with db.session.begin_nested():
                responsable = distribuir_pedido(pedido)
                if responsable:
                    db.session.flush()
                    asignados += 1
                    logger.info(
                        "redistribuir: pedido %s → %s",
                        pedido.numero_pedido, responsable.nombre,
                    )
        except Exception as exc:
            logger.warning("No se pudo asignar pedido %s: %s", pedido.id, exc, exc_info=True)
            continue
    return asignados


def distribuir_pedido(pedido: Order) -> User | None:
    """
    Asigna el pedido al preparador disponible con menos carga.
    Considera el tipo de entrega del pedido:
    - Inmediato → prioriza cocina
    - Programado → prioriza preparacion
    Prioridades de candidatos:
      1. Rol correcto + en_linea + conectado
      2. Rol alternativo + en_linea + conectado
      3. Admin disponible como comodín
      4. Si nadie está online, el pedido queda sin asignar para la cola/admin

    Si el pedido es 100% del bar (todos sus items tienen
    proveedor_despachador_id), no se asigna preparador interno: el bar lo
    prepara y nuestro personal solo gestiona el reparto.
    """
    if pedido.confirmacion_estado == "pending":
        logger.info(
            "distribuir_pedido: pedido %s espera confirmación de WhatsApp.",
            pedido.numero_pedido,
        )
        pedido.preparador_id = None
        return None

    if es_pedido_solo_bar(pedido):
        logger.info(
            "distribuir_pedido: pedido %s es 100%% del bar, no se asigna preparador interno.",
            pedido.numero_pedido,
        )
        pedido.preparador_id = None
        return None

    tope = max_pedidos_por_preparador()
    canal = _canal_pedido(pedido)
    if canal == "almacen":
        candidatos = _candidatos_disponibles(
            User.query.filter(
                User.rol == "preparacion",
                User.activo.is_(True),
            ).all()
        )
        if not candidatos:
            admins = User.query.filter_by(rol="admin", activo=True).all()
            candidatos = _candidatos_disponibles(admins)
        if not candidatos:
            logger.warning(
                "distribuir_pedido: sin staff online. Pedido %s queda sin asignar.",
                pedido.numero_pedido,
            )
            return None
        cargas = carga_actual_preparadores([u.id for u in candidatos])
        resultado = _elegir_menos_cargado(candidatos, cargas, tope)
        if not resultado:
            return None
        asignado, _, _ = resultado
        pedido.preparador_id = asignado.id
        return asignado

    tipo = _tipo_pedido(pedido)
    rol_preferido  = "cocina" if tipo == "inmediato" else "preparacion"
    # Un inmediato SÍ puede caer a 'preparacion' como alternativa (staff de
    # encargos puede echar mano si la cocina está saturada). Un programado NO
    # cae a 'cocina': antes lo hacía y los encargos aparecían en el dashboard
    # de cocina, invisibles para el rol correcto. Preferimos dejarlo sin
    # asignar (visible en cola de admin) a esconder el problema.
    rol_alternativo = "preparacion" if tipo == "inmediato" else None

    # 1. Intentar con el rol preferido
    candidatos_pref = User.query.filter_by(rol=rol_preferido, activo=True).all()
    candidatos = _candidatos_disponibles(candidatos_pref)

    # 2. Alternativa: el otro rol (solo para inmediatos)
    if not candidatos and rol_alternativo:
        candidatos_alt = User.query.filter_by(rol=rol_alternativo, activo=True).all()
        candidatos = _candidatos_disponibles(candidatos_alt)
        if candidatos:
            logger.info("distribuir_pedido: usando rol %s como alternativa.", rol_alternativo)

    # 3. Admin como comodín
    if not candidatos:
        admins = User.query.filter_by(rol="admin", activo=True).all()
        candidatos = _candidatos_disponibles(admins)

    if not candidatos:
        logger.warning(
            "distribuir_pedido: sin preparadores online. Pedido %s queda sin asignar.",
            pedido.numero_pedido
        )
        return None

    cargas = carga_actual_preparadores([u.id for u in candidatos])
    resultado = _elegir_menos_cargado(candidatos, cargas, tope)
    if not resultado:
        return None
    asignado, _, _ = resultado
    pedido.preparador_id = asignado.id
    return asignado


def distribuir_repartidor(pedido: Order) -> User | None:
    """
    Asigna al repartidor disponible con menos carga cuando el pedido pasa a 'listo'.
    Solo usa repartidores con disponibilidad manual activa y presencia reciente.

    Zona (coherente con PR #5): si el pedido tiene `zona_id`, se prefieren
    repartidores asignados a esa zona. Si no hay ninguno online, cae al pool
    global (sin zona o cualquier zona) para no bloquear entregas.
    """
    if not get_store_features()["delivery"]:
        return None
    if not getattr(pedido, "requiere_reparto", True):
        return None
    if pedido.repartidor_id:
        return db.session.get(User, pedido.repartidor_id)

    repartidores = User.query.filter_by(rol="repartidor", activo=True).all()
    candidatos = _candidatos_disponibles(repartidores)

    if not candidatos:
        logger.warning(
            "distribuir_repartidor: no hay repartidores online. Pedido %s queda sin repartidor.",
            pedido.numero_pedido
        )
        return None

    # Preferencia por zona: especialistas primero, comodines después, pool
    # completo solo si no hay ninguno de los dos (evita entregas huérfanas).
    zona_pedido = getattr(pedido, "zona_id", None)
    if zona_pedido is not None:
        de_zona = [u for u in candidatos
                   if getattr(u, "zona_repartidor_id", None) == zona_pedido]
        sin_zona = [u for u in candidatos
                    if getattr(u, "zona_repartidor_id", None) is None]
        pool = de_zona or sin_zona or candidatos
    else:
        pool = candidatos

    # Workload balancing: menor carga primero, respeta tope configurable.
    # Bulk query en vez de N queries del sort key.
    tope = max_pedidos_por_repartidor()
    cargas = carga_actual_repartidores([u.id for u in pool])
    resultado = _elegir_menos_cargado(pool, cargas, tope)
    if not resultado:
        return None
    asignado, _, _ = resultado
    pedido.repartidor_id = asignado.id
    return asignado


def redistribuir_listos_sin_repartidor() -> int:
    """Asigna pedidos listos cuando un repartidor vuelve a ponerse disponible."""
    if not get_store_features()["delivery"]:
        return 0
    pedidos = Order.query.filter_by(
        estado="listo",
        repartidor_id=None,
        tipo_entrega_cliente="delivery",
    ).order_by(Order.creado_en).with_for_update(skip_locked=True).all()
    asignados = 0
    for pedido in pedidos:
        responsable = distribuir_repartidor(pedido)
        if responsable:
            db.session.flush()
            asignados += 1
            logger.info(
                "redistribuir delivery: pedido %s → %s",
                pedido.numero_pedido,
                responsable.nombre,
            )
    return asignados


def pedidos_delivery_sin_repartidor_query():
    """Fuente única de pedidos realmente repartibles que esperan conductor."""
    return Order.query.filter(
        Order.estado == "listo",
        Order.repartidor_id.is_(None),
        Order.tipo_entrega_cliente == "delivery",
    )


def estado_cola() -> dict:
    """Snapshot del estado actual de la cola por rol.
    Usa conteos agregados en una sola query para evitar N+1 bajo carga."""
    from sqlalchemy import func
    from models import Order as _Order

    # Un solo SELECT para contar carga de preparadores activos
    carga_prep = {
        row.preparador_id: row.n
        for row in db.session.query(
            _Order.preparador_id, func.count(_Order.id).label("n")
        ).filter(
            _Order.estado.in_(ESTADOS_EN_PREPARACION),
            _Order.preparador_id.isnot(None),
        ).group_by(_Order.preparador_id).all()
    }
    # Un solo SELECT para contar carga de repartidores activos
    carga_rep = {
        row.repartidor_id: row.n
        for row in db.session.query(
            _Order.repartidor_id, func.count(_Order.id).label("n")
        ).filter(
            _Order.estado.in_(ESTADOS_EN_REPARTO),
            _Order.repartidor_id.isnot(None),
            _Order.tipo_entrega_cliente == "delivery",
        ).group_by(_Order.repartidor_id).all()
    }

    features = get_store_features()
    roles = ["cocina", "admin"]
    if features["pedidos_programados"]:
        roles.append("preparacion")
    if features["delivery"]:
        roles.append("repartidor")
    resultado = {}
    _roles_prep = {"cocina", "preparacion", "admin"}
    _roles_rep  = {"repartidor", "admin"}
    for rol in roles:
        usuarios = User.query.filter_by(rol=rol, activo=True).all()
        resultado[rol] = [
            {
                "id": u.id,
                "nombre": u.nombre,
                "en_linea": getattr(u, "en_linea", False),
                "conectado": u.esta_conectado,
                "disponible": getattr(u, "disponible_para_pedidos", u.esta_conectado),
                "minutos_inactivo": u.minutos_inactivo,
                "carga_preparador": carga_prep.get(u.id, 0) if rol in _roles_prep else None,
                "carga_repartidor": carga_rep.get(u.id, 0) if rol in _roles_rep else None,
            }
            for u in usuarios
        ]
    return resultado


# ─────────────────────────────────────────────
# CAJA — helpers
# ─────────────────────────────────────────────

def registrar_ingreso(monto, concepto, categoria="general",
                      pedido_id=None, registrado_por=None):
    entry = Caja(tipo="ingreso", categoria=categoria,
                 monto=monto, concepto=concepto,
                 pedido_id=pedido_id, registrado_por=registrado_por)
    db.session.add(entry)
    return entry


def registrar_ingreso_pedido(pedido: Order, registrado_por=None):
    """Registra el cobro una sola vez, en el momento real de confirmarlo.

    Defensa: si el pedido es Bizum y `pago_confirmado` sigue en False, NO
    registra ingreso ni siquiera con `force` — el repartidor o el endpoint
    callente debe haber llamado primero a `registrar_pago_pedido()`.
    """
    existente = Caja.query.filter_by(pedido_id=pedido.id, tipo="ingreso").first()
    if existente:
        return existente
    if (pedido.metodo_pago or "").lower() == "bizum" and not pedido.pago_confirmado:
        logger.warning(
            "registrar_ingreso_pedido bloqueado: pedido %s es Bizum sin pago_confirmado",
            pedido.numero_pedido,
        )
        return None
    categoria = {
        "online": "venta_online",
        "web": "venta_online",
        "whatsapp": "venta_whatsapp",
        "presencial": "venta_presencial",
        "pos": "venta_presencial",
    }.get(pedido.origen, "venta")
    return registrar_ingreso(
        pedido.total,
        f"Pedido {pedido.numero_pedido}",
        categoria=categoria,
        pedido_id=pedido.id,
        registrado_por=registrado_por,
    )


def registrar_egreso(monto, concepto, categoria="general",
                     staff_payment_id=None, pedido_id=None, registrado_por=None):
    entry = Caja(tipo="egreso", categoria=categoria,
                 monto=monto, concepto=concepto,
                 pedido_id=pedido_id,
                 staff_payment_id=staff_payment_id,
                 registrado_por=registrado_por)
    db.session.add(entry)
    return entry


# ─────────────────────────────────────────────
# PUNTOS: OTORGAR AL ENTREGAR
# ─────────────────────────────────────────────

def award_points_on_delivery(pedido: Order) -> int:
    """
    Otorga al cliente los puntos calculados en pedido.puntos_ganados.
    Recalcula pedidos pendientes antiguos que se guardaron con cero por tener
    el canje desactivado en el momento de la compra.
    Se llama SOLO cuando el estado cambia a 'entregado'.
    Idempotente: si ya existe un PointsLog tipo='ganado' para este pedido, no hace nada.
    Retorna la cantidad de puntos otorgados (0 si ya se habían otorgado o no corresponde).
    """
    if not pedido.cliente_id:
        return 0
    from models import PointsLog
    ya_otorgados = PointsLog.query.filter_by(
        cliente_id=pedido.cliente_id,
        pedido_id=pedido.id,
        tipo="ganado",
    ).first()
    if ya_otorgados:
        return 0
    cliente = pedido.cliente
    if not cliente:
        return 0
    puntos = int(pedido.puntos_ganados or 0)
    if puntos <= 0:
        puntos = calcular_puntos_ganados(pedido.total)
        pedido.puntos_ganados = puntos
    if puntos <= 0:
        return 0
    cliente.sumar_puntos(
        puntos,
        pedido_id=pedido.id,
        descripcion=f"Pedido {pedido.numero_pedido} entregado",
    )
    logger.info("Puntos otorgados: %d pts → cliente %s (pedido %s)",
                puntos, pedido.cliente_id, pedido.numero_pedido)
    return puntos


# ─────────────────────────────────────────────
# COMISIONES AUTOMÁTICAS
# ─────────────────────────────────────────────

def generar_comision_entrega(pedido: Order) -> StaffPayment | None:
    """
    Crea un registro de comisión para el repartidor cuando entrega un pedido.
    Usa la tarifa configurada del repartidor. Si no existe, usa el coste de
    entrega cobrado como compatibilidad con instalaciones anteriores.
    """
    if not pedido.repartidor_id or not pedido.requiere_reparto:
        return None
    existente = StaffPayment.query.filter_by(
        user_id=pedido.repartidor_id,
        tipo="comision",
        origen="delivery",
        pedido_id=pedido.id,
    ).first()
    if existente:
        return existente
    repartidor = db.session.get(User, pedido.repartidor_id)
    if not repartidor:
        return None
    monto = Decimal(str(repartidor.tarifa_entrega or 0))
    if monto <= 0:
        monto = pedido.costo_envio
    if monto <= 0:
        return None

    pago = StaffPayment(
        user_id=repartidor.id,
        tipo="comision",
        origen="delivery",
        monto=monto,
        concepto=f"Reparto cobrado en {pedido.numero_pedido}",
        pedido_id=pedido.id,
        pagado=False,
    )
    db.session.add(pago)
    return pago


# ─────────────────────────────────────────────
# RESUMEN FINANCIERO
# ─────────────────────────────────────────────

def resumen_caja_hoy():
    from datetime import date
    from sqlalchemy import func
    hoy = date.today()
    ingresos = db.session.query(func.sum(Caja.monto)).filter(
        db.func.date(Caja.fecha) == hoy, Caja.tipo == "ingreso"
    ).scalar() or 0
    egresos = db.session.query(func.sum(Caja.monto)).filter(
        db.func.date(Caja.fecha) == hoy, Caja.tipo == "egreso"
    ).scalar() or 0
    return float(ingresos), float(egresos)


def pagos_pendientes_staff():
    """Devuelve obligaciones pendientes que producirán una salida de caja."""
    from sqlalchemy import func
    total = db.session.query(func.sum(StaffPayment.monto)).filter(
        StaffPayment.pagado.is_(False),
        StaffPayment.tipo != "descuento",
    ).scalar() or 0
    return float(total)


# ─────────────────────────────────────────────
# AFILIADOS
# ─────────────────────────────────────────────

def notificar_bot_sync():
    """Dispara una petición asíncrona al bot para que resincronice catálogo.

    Best-effort — silencioso si el bot no está accesible. Corre en un hilo
    daemon para no bloquear la respuesta al cliente HTTP. La clave se lee
    de SiteConfig con fallback a env por bootstrap.
    """
    from models import SiteConfig as _SC

    bot_url = _SC.get("BOT_API_URL", os.environ.get("BOT_API_URL", "http://127.0.0.1:3000"))
    panel_key = _SC.get("BOT_PANEL_KEY", "") or _SC.get("BOT_API_KEY", "")
    if not bot_url or not panel_key:
        return

    def _fire():
        try:
            req = urllib.request.Request(
                f"{bot_url.rstrip('/')}/api/oxidian/sync",
                method="POST",
                headers={"Content-Type": "application/json", "X-Panel-Key": panel_key},
                data=b"{}",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Bot no disponible para sincronizacion en %s: %s", bot_url, exc)
        except Exception:
            logger.exception("Error inesperado notificando sync al bot")

    threading.Thread(target=_fire, daemon=True).start()


def consultar_estado_bot(timeout: float | None = None) -> dict:
    """Consulta el `/api/status` del bot Node y devuelve un resumen listo
    para renderizar en el panel admin.

    Best-effort — nunca lanza. Ante fallo devuelve un dict con `salud="down"`
    para que el widget muestre estado rojo. Timeout corto (default 2.5s)
    para no ralentizar la carga del dashboard cuando el bot no responde.

    Salud clasificada:
      - `up`        → HTTP 200 + connected=true + errores_24h razonables.
      - `degraded`  → responde pero WhatsApp desconectado O errores altos.
      - `down`      → no responde, timeout o HTTP error.

    Devuelve un dict con:
      salud, connected, evolution_state, errores_24h, sessions_client,
      handoffs_pending, handoffs_undelivered, latency_ms, mensaje (para UI).
    """
    from models import SiteConfig as _SC
    import requests

    if timeout is None:
        timeout = bot_status_check_timeout_sec()
    bot_url = (_SC.get("BOT_API_URL", os.environ.get("BOT_API_URL", "http://chat:3000")) or "").rstrip("/")
    panel_key = _SC.get("BOT_PANEL_KEY", "") or _SC.get("BOT_API_KEY", "")
    if not bot_url or not panel_key:
        return {
            "salud": "unknown",
            "mensaje": "BOT_API_URL o BOT_PANEL_KEY no configurados.",
            "connected": False,
            "evolution_state": None,
            "errores_24h": None,
            "sessions_client": None,
            "handoffs_pending": None,
            "handoffs_undelivered": None,
            "latency_ms": None,
        }

    inicio = time.monotonic()
    try:
        resp = requests.get(
            f"{bot_url}/api/status",
            headers={"X-Panel-Key": panel_key, "X-API-Key": panel_key},
            timeout=timeout,
        )
        latency_ms = int((time.monotonic() - inicio) * 1000)
        if not resp.ok:
            return {
                "salud": "down",
                "mensaje": f"HTTP {resp.status_code}",
                "connected": False,
                "evolution_state": None,
                "errores_24h": None,
                "sessions_client": None,
                "handoffs_pending": None,
                "handoffs_undelivered": None,
                "latency_ms": latency_ms,
            }
        d = resp.json() if resp.content else {}
    except Exception as exc:
        logger.warning("consultar_estado_bot fallo: %s", exc)
        return {
            "salud": "down",
            "mensaje": "Bot no responde",
            "connected": False,
            "evolution_state": None,
            "errores_24h": None,
            "sessions_client": None,
            "handoffs_pending": None,
            "handoffs_undelivered": None,
            "latency_ms": None,
        }

    connected = bool(d.get("connected"))
    evolution_state = d.get("evolution_state") or "unknown"
    errores_24h = int(d.get("errores_24h") or 0)
    handoffs = d.get("handoffs") or {}
    handoffs_pending = int(handoffs.get("pending") or 0)
    handoffs_undelivered = int(handoffs.get("undelivered_messages") or 0)
    sessions = d.get("sessions") or {}
    sessions_client = int(sessions.get("client") or 0)

    # Clasificación de salud. Umbrales configurables vía SiteConfig
    # (BOT_HEALTH_ERRORS_24H_MAX / BOT_HEALTH_HANDOFFS_UNDELIVERED_MAX).
    err_max = bot_health_errors_24h_max()
    hnd_max = bot_health_handoffs_undelivered_max()
    if not connected:
        salud = "degraded"
        mensaje = f"WhatsApp desconectado (estado: {evolution_state})."
    elif errores_24h > err_max or handoffs_undelivered > hnd_max:
        salud = "degraded"
        mensaje = f"Errores 24h: {errores_24h}. Handoffs sin entregar: {handoffs_undelivered}."
    else:
        salud = "up"
        mensaje = f"Conectado. {sessions_client} clientes en sesión."

    return {
        "salud": salud,
        "mensaje": mensaje,
        "connected": connected,
        "evolution_state": evolution_state,
        "errores_24h": errores_24h,
        "sessions_client": sessions_client,
        "handoffs_pending": handoffs_pending,
        "handoffs_undelivered": handoffs_undelivered,
        "latency_ms": latency_ms,
    }


def refrescar_bot_si_claves_relevantes(claves) -> bool:
    """Dispara `notificar_bot_sync` solo si alguna de `claves` es sensible
    para el bot (según `store_config.CLAVES_QUE_REFRESCAN_BOT`).

    Es la puerta única que las rutas admin llaman tras guardar SiteConfig
    para forzar refresco inmediato del cliente Node — sin este helper el
    bot no ve el cambio hasta el próximo ciclo pasivo de 10 minutos, lo
    que genera confusión cuando un operador cambia el modo tienda o un
    feature flag desde el panel.

    Devuelve True si se disparó el sync, False si ninguna clave era
    relevante o si la clave era `None`. Best-effort — nunca lanza.
    """
    from store_config import alguna_clave_refresca_bot

    try:
        if not alguna_clave_refresca_bot(claves):
            return False
        notificar_bot_sync()
        return True
    except Exception:
        logger.exception("refrescar_bot_si_claves_relevantes fallo — claves=%s", list(claves or ()))
        return False


def buscar_cliente_por_telefono(raw):
    """Localiza al usuario que compra bajo este teléfono.

    Prioriza rol='cliente' para no confundir con cuentas operativas, pero
    cae al match sin filtro de rol si no existe cliente puro. Necesario
    porque el UNIQUE es global sobre ``telefono_normalizado`` y un
    operador que a la vez compra debe recuperar puntos y checkout con la
    misma cuenta.

    Devuelve ``(cliente_or_none, telefono_normalizado)``.
    """
    from phone_utils import normalizar_telefono_cliente, telefono_valido

    telefono = normalizar_telefono_cliente(raw)
    if not telefono_valido(telefono):
        return None, telefono
    q = User.query.filter_by(telefono_normalizado=telefono)
    cliente = q.filter_by(rol="cliente").first() or q.first()

    # Compatibilidad reparadora para datos legacy creados cuando faltaba el
    # prefijo de país: +632… frente al JID real +34632…. Sólo se intenta si
    # existe un prefijo explícitamente configurado y el match local es único;
    # nunca adivinamos países ni hacemos búsquedas laxas por sufijo.
    from phone_utils import solo_digitos
    try:
        from models import SiteConfig
        prefijo = solo_digitos(SiteConfig.get("WHATSAPP_COUNTRY_CODE", ""))
    except (ImportError, RuntimeError):
        prefijo = solo_digitos(os.environ.get("WHATSAPP_COUNTRY_CODE", ""))
    digits = solo_digitos(telefono)
    if prefijo and digits.startswith(prefijo) and len(digits) > len(prefijo):
        legacy = f"+{digits[len(prefijo):]}"
        legacy_rows = User.query.filter_by(telefono_normalizado=legacy).limit(2).all()
        if len(legacy_rows) == 1:
            legacy_user = legacy_rows[0]
            if not cliente:
                return legacy_user, telefono
            # Una cuenta operativa canónica puede coexistir con un cliente
            # histórico creado sin prefijo. Si el historial de compra vive
            # inequívocamente en el registro legacy, éste es la identidad de
            # cliente hasta que ambos perfiles se consoliden.
            if cliente.rol != "cliente" and legacy_user.rol == "cliente":
                exact_has_orders = Order.query.filter_by(cliente_id=cliente.id).first() is not None
                legacy_has_orders = Order.query.filter_by(cliente_id=legacy_user.id).first() is not None
                if legacy_has_orders and not exact_has_orders:
                    logger.warning(
                        "Identidad cliente legacy seleccionada: canonical_user=%s legacy_user=%s",
                        cliente.id,
                        legacy_user.id,
                    )
                    return legacy_user, telefono
    return cliente, telefono


def _cliente_es_referido_nuevo(cliente, pedido_actual) -> bool:
    """Determina si `cliente` cuenta como REFERIDO NUEVO para efectos de
    comisión de afiliado.

    Regla: el pedido actual debe ser el primero no-cancelado del cliente.
    Antes: se otorgaba comisión cada vez que se usaba el código, aunque
    fuera un cliente veterano — un vector de fraude claro (staff podía
    aplicarse su propio código en cada pedido de clientes recurrentes).

    - Se excluye el propio `pedido_actual.id` del recuento (ya está en BD
      cuando llamamos).
    - Se excluye `estado='cancelado'` para no penalizar cancelaciones.
    """
    from models import Order
    if not cliente or not cliente.id:
        return False
    q = (db.session.query(db.func.count(Order.id))
         .filter(Order.cliente_id == cliente.id,
                 Order.estado != "cancelado"))
    if getattr(pedido_actual, "id", None):
        q = q.filter(Order.id != pedido_actual.id)
    previos = int(q.scalar() or 0)
    return previos == 0


def registrar_uso_afiliado(codigo, pedido, cliente, descuento_aplicado):
    """Registra el uso de un código de afiliado.

    El descuento al cliente ya se aplicó antes en `calcular_precio` y
    afecta `Order.descuento`. Aquí registramos la traza en `AffiliateUse`
    y — SOLO si el cliente es referido nuevo — generamos el StaffPayment
    de comisión al afiliado. Comisiones sobre clientes recurrentes se
    marcan con `comision_generada = 0` y se registran para trazabilidad
    con `detalle_no_comision` en el motivo del uso (sin StaffPayment).
    """
    from models import AffiliateUse, StaffPayment

    existente = AffiliateUse.query.filter_by(
        codigo_id=codigo.id,
        pedido_id=pedido.id,
    ).first()
    if existente:
        return existente

    es_nuevo = _cliente_es_referido_nuevo(cliente, pedido)
    comision = float(codigo.calcular_comision(float(pedido.total))) if es_nuevo else 0.0

    uso = AffiliateUse(
        codigo_id=codigo.id,
        pedido_id=pedido.id,
        cliente_id=cliente.id,
        descuento_aplicado=descuento_aplicado,
        comision_generada=comision,
    )
    db.session.add(uso)
    codigo.registrar_uso()

    if codigo.user_id and comision > 0:
        pago = StaffPayment(
            user_id=codigo.user_id,
            tipo="comision",
            origen="affiliate",
            monto=comision,
            concepto=f"Comisión afiliado {codigo.codigo} — {pedido.numero_pedido}",
            pedido_id=pedido.id,
        )
        db.session.add(pago)
        db.session.flush()
        uso.staff_payment_id = pago.id
    elif not es_nuevo:
        logger.info(
            "registrar_uso_afiliado: cliente %s NO es referido nuevo — "
            "sin comisión para %s (pedido %s)",
            cliente.id, codigo.codigo, getattr(pedido, "numero_pedido", pedido.id),
        )

    return uso


# ─────────────────────────────────────────────
# ANALYTICS / P&L
# ─────────────────────────────────────────────

def metricas_antifraude(dias: int = 30) -> dict:
    """Resumen operativo de la verificación pasiva antifraude en la ventana.

    Devuelve un dict con:
      - `evaluados`: pedidos creados en la ventana con confirmacion_estado
        no NULL (LOW no cuenta — no se puntúa).
      - `confirmados`: dentro de esos, cuántos el cliente confirmó por
        WhatsApp.
      - `pending_vigentes`: pending que aún NO ha vencido ni sido
        resuelto (útil para saber la cola actual de riesgo).
      - `cancelados_por_bot`: pedidos que el cliente rechazó respondiendo
        NO en la verificación — se identifican por evento con detalle
        específico. Diferentes de cancelaciones normales del cliente.
      - `tasa_confirmacion`: confirmados / (confirmados + cancelados_por_bot)
        expresada como float 0-1 (o None si no hay resoluciones aún).
        Es la métrica más útil para tunear `CONFIRMACION_MONTO_UMBRAL_EUR`
        — si sube al 90%+ probablemente puedes bajar el umbral (menos
        pedidos evaluados innecesariamente), si baja al 50%- probablemente
        el umbral debería subir (evitas alienar a clientes legítimos).

    Best-effort: cualquier fallo devuelve un dict de ceros para no romper
    el dashboard.
    """
    from datetime import timedelta

    try:
        dias = max(1, min(365, int(dias or 30)))
    except (TypeError, ValueError):
        dias = 30
    desde = utcnow() - timedelta(days=dias)

    try:
        evaluados = db.session.query(db.func.count(Order.id)).filter(
            Order.creado_en >= desde,
            Order.confirmacion_estado.isnot(None),
        ).scalar() or 0
        confirmados = db.session.query(db.func.count(Order.id)).filter(
            Order.creado_en >= desde,
            Order.confirmacion_estado == "confirmed",
        ).scalar() or 0
        pending_vigentes = db.session.query(db.func.count(Order.id)).filter(
            Order.confirmacion_estado == "pending",
            Order.estado.notin_(["cancelado", "entregado"]),
        ).scalar() or 0
        cancelados_por_bot = db.session.query(db.func.count(OrderEvent.id)).filter(
            OrderEvent.creado_en >= desde,
            OrderEvent.tipo == "pedido_cancelado",
            OrderEvent.detalle.ilike("%verificación pasiva%"),
        ).scalar() or 0
        # Desglose por nivel de riesgo — cuenta evaluados en ventana por nivel.
        # NULL nivel = pedidos que quedaron pending pero fueron creados antes
        # de introducir la columna (legacy), o donde la migración falló.
        por_nivel = dict(
            db.session.query(Order.confirmacion_nivel, db.func.count(Order.id))
            .filter(
                Order.creado_en >= desde,
                Order.confirmacion_estado.isnot(None),
            )
            .group_by(Order.confirmacion_nivel)
            .all()
        )
    except Exception:
        logger.exception("metricas_antifraude: query falló")
        return {
            "dias": dias,
            "evaluados": 0,
            "confirmados": 0,
            "pending_vigentes": 0,
            "cancelados_por_bot": 0,
            "tasa_confirmacion": None,
            "por_nivel": {"MEDIUM": 0, "HIGH": 0},
        }

    resueltos = confirmados + cancelados_por_bot
    tasa = round(confirmados / resueltos, 3) if resueltos else None
    return {
        "dias": dias,
        "evaluados": int(evaluados),
        "confirmados": int(confirmados),
        "pending_vigentes": int(pending_vigentes),
        "cancelados_por_bot": int(cancelados_por_bot),
        "tasa_confirmacion": tasa,
        "por_nivel": {
            "MEDIUM": int(por_nivel.get("MEDIUM", 0) or 0),
            "HIGH": int(por_nivel.get("HIGH", 0) or 0),
        },
    }


def calcular_pl(fecha_ini, fecha_fin):
    """P&L entre dos date objects. Devuelve dict con la cascada financiera completa."""
    from sqlalchemy import func
    from models import Caja, Order, OrderItem, StaffPayment
    from datetime import timedelta as _td

    fi = datetime(fecha_ini.year, fecha_ini.month, fecha_ini.day, 0, 0, 0)
    ff = datetime(fecha_fin.year, fecha_fin.month, fecha_fin.day, 0, 0, 0) + _td(days=1)

    # ── Ventas (fuente: pedidos entregados, fecha de entrega) ──────────
    ventas_online = db.session.query(func.sum(Order.total)).filter(
        Order.entregado_en >= fi, Order.entregado_en < ff,
        Order.estado == "entregado", Order.origen == "online"
    ).scalar() or 0
    ventas_presencial = db.session.query(func.sum(Order.total)).filter(
        Order.entregado_en >= fi, Order.entregado_en < ff,
        Order.estado == "entregado", Order.origen == "presencial"
    ).scalar() or 0
    ventas_whatsapp = db.session.query(func.sum(Order.total)).filter(
        Order.entregado_en >= fi, Order.entregado_en < ff,
        Order.estado == "entregado", Order.origen == "whatsapp"
    ).scalar() or 0

    ventas_epicentro = db.session.query(func.sum(Order.total)).filter(
        Order.entregado_en >= fi, Order.entregado_en < ff,
        Order.estado == "entregado", Order.es_entrega_epicentro.is_(True),
    ).scalar() or 0
    ventas_fuera_epicentro = db.session.query(func.sum(Order.total)).filter(
        Order.entregado_en >= fi, Order.entregado_en < ff,
        Order.estado == "entregado", Order.es_entrega_epicentro.is_(False),
    ).scalar() or 0

    total_pedidos = Order.query.filter(
        Order.entregado_en >= fi, Order.entregado_en < ff,
        Order.estado == "entregado"
    ).count()
    pedidos_cancelados = Order.query.filter(
        Order.creado_en >= fi, Order.creado_en < ff, Order.estado == "cancelado"
    ).count()
    descuentos = db.session.query(func.sum(Order.descuento)).filter(
        Order.entregado_en >= fi, Order.entregado_en < ff, Order.estado == "entregado"
    ).scalar() or 0
    service_commission = db.session.query(func.sum(Order.service_commission_amount)).filter(
        Order.entregado_en >= fi, Order.entregado_en < ff, Order.estado == "entregado"
    ).scalar() or 0
    merchant_net = db.session.query(func.sum(Order.merchant_net_amount)).filter(
        Order.entregado_en >= fi, Order.entregado_en < ff, Order.estado == "entregado"
    ).scalar() or 0

    # Order.total ya contiene descuentos y envío: es el importe realmente
    # cobrado. Los descuentos se muestran aparte, sin restarlos dos veces.
    ingresos_netos = float(ventas_online) + float(ventas_presencial) + float(ventas_whatsapp)
    ventas_brutas = ingresos_netos + float(descuentos)

    # ── COGS — coste de productos vendidos (snapshot del precio de costo) ──
    cogs = 0.0
    ids_entregados = [
        row[0] for row in db.session.query(Order.id).filter(
            Order.entregado_en >= fi, Order.entregado_en < ff, Order.estado == "entregado"
        ).all()
    ]
    if ids_entregados:
        for item in OrderItem.query.filter(OrderItem.pedido_id.in_(ids_entregados)).all():
            costo_u = (item.producto_snapshot or {}).get("precio_costo") or 0
            cogs += float(costo_u) * int(item.cantidad)

    margen_bruto = ingresos_netos - cogs
    margen_bruto_pct = round(margen_bruto / ingresos_netos * 100, 1) if ingresos_netos > 0 else 0.0

    # ── Personal (pagos registrados y pagados en el período) ──────────
    nominas = db.session.query(func.sum(StaffPayment.monto)).filter(
        StaffPayment.fecha_pago >= fi, StaffPayment.fecha_pago < ff,
        StaffPayment.pagado == True,
        StaffPayment.tipo.in_(["salario", "bonus"])
    ).scalar() or 0
    comisiones = db.session.query(func.sum(StaffPayment.monto)).filter(
        StaffPayment.fecha_pago >= fi, StaffPayment.fecha_pago < ff,
        StaffPayment.pagado == True,
        StaffPayment.tipo == "comision"
    ).scalar() or 0

    # ── Gastos operativos manuales ────────────────────────────────────
    # Los pagos de nómina/comisión ya se restan arriba y las reversiones de
    # pedidos no son gasto operativo porque esos pedidos no forman ventas.
    gastos_caja = db.session.query(func.sum(Caja.monto)).filter(
        Caja.fecha >= fi,
        Caja.fecha < ff,
        Caja.tipo == "egreso",
        Caja.staff_payment_id.is_(None),
        Caja.pedido_id.is_(None),
    ).scalar() or 0
    # Las ventas ya provienen de pedidos entregados; sumar sus movimientos de
    # caja duplicaría ingresos. Aquí solo entran ingresos manuales no ligados.
    otros_ingresos_caja = db.session.query(func.sum(Caja.monto)).filter(
        Caja.fecha >= fi,
        Caja.fecha < ff,
        Caja.tipo == "ingreso",
        Caja.pedido_id.is_(None),
    ).scalar() or 0

    # ── Cascada final ─────────────────────────────────────────────────
    resultado = (margen_bruto
                 - float(service_commission)
                 - float(nominas)
                 - float(comisiones)
                 - float(gastos_caja)
                 + float(otros_ingresos_caja))
    resultado_pct = round(resultado / ingresos_netos * 100, 1) if ingresos_netos > 0 else 0.0
    ticket_medio = round(ingresos_netos / total_pedidos, 2) if total_pedidos > 0 else 0.0

    return {
        "fecha_ini": fecha_ini, "fecha_fin": fecha_fin,
        # Cascada P&L
        "ventas_brutas": ventas_brutas,
        "descuentos_concedidos": float(descuentos),
        "ingresos_netos": ingresos_netos,
        "cogs": cogs,
        "margen_bruto": margen_bruto,
        "margen_bruto_pct": margen_bruto_pct,
        "nominas": float(nominas),
        "comisiones_repartidor": float(comisiones),
        "service_commission": float(service_commission),
        "merchant_net": float(merchant_net),
        "gastos_caja": float(gastos_caja),
        "otros_ingresos_caja": float(otros_ingresos_caja),
        "resultado": resultado,
        "resultado_pct": resultado_pct,
        "ticket_medio": ticket_medio,
        # Desglose por canal
        "ventas_online": float(ventas_online),
        "ventas_presencial": float(ventas_presencial),
        "ventas_whatsapp": float(ventas_whatsapp),
        "ventas_epicentro": float(ventas_epicentro),
        "ventas_fuera_epicentro": float(ventas_fuera_epicentro),
        # Operacional
        "total_pedidos": total_pedidos,
        "pedidos_cancelados": pedidos_cancelados,
        # Aliases legacy para compatibilidad
        "ingresos": ingresos_netos,
        "egresos": float(gastos_caja),
        "ganancia_bruta": margen_bruto,
        "ganancia_neta": resultado,
    }


def top_productos(limit=10, dias=30, fecha_ini=None, fecha_fin=None):
    """
    Top productos más vendidos.
    Si se pasan fecha_ini/fecha_fin (date objects) se usa ese rango exacto.
    Si no, se usan los últimos `dias` días desde hoy.
    """
    from sqlalchemy import func
    from models import OrderItem, Product, Order as OrderModel
    from datetime import timedelta

    if fecha_ini and fecha_fin:
        desde = datetime(fecha_ini.year, fecha_ini.month, fecha_ini.day, 0, 0, 0)
        hasta = datetime(fecha_fin.year, fecha_fin.month, fecha_fin.day, 0, 0, 0) + timedelta(days=1)
        filtro_fecha = (OrderModel.entregado_en >= desde, OrderModel.entregado_en < hasta)
    else:
        desde = utcnow() - timedelta(days=dias)
        filtro_fecha = (OrderModel.entregado_en >= desde,)

    resultados = db.session.query(
        OrderItem.producto_id,
        func.sum(OrderItem.cantidad).label("total_vendido"),
        func.sum(OrderItem.subtotal).label("total_ingresos"),
    ).join(OrderModel, OrderItem.pedido_id == OrderModel.id)\
     .filter(*filtro_fecha, OrderModel.estado == "entregado")\
     .group_by(OrderItem.producto_id)\
     .order_by(func.sum(OrderItem.cantidad).desc())\
     .limit(limit).all()

    lista = []
    for r in resultados:
        p = db.session.get(Product, r.producto_id)
        if p:
            lista.append({
                "producto": p,
                "total_vendido": int(r.total_vendido or 0),
                "total_ingresos": float(r.total_ingresos or 0),
            })
    return lista


def resumen_ventas_por_categoria(fecha_ini, fecha_fin):
    """Ventas agrupadas por categoría en el período (date objects)."""
    from sqlalchemy import func
    from models import OrderItem, Product, Categoria, Order as OrderModel

    from datetime import timedelta as _td
    fi = datetime(fecha_ini.year, fecha_ini.month, fecha_ini.day, 0, 0, 0)
    ff = datetime(fecha_fin.year, fecha_fin.month, fecha_fin.day, 0, 0, 0) + _td(days=1)

    resultados = db.session.query(
        func.coalesce(Categoria.nombre, "Sin categoría").label("categoria_nombre"),
        func.sum(OrderItem.cantidad).label("unidades"),
        func.sum(OrderItem.subtotal).label("ventas"),
    ).join(Product, OrderItem.producto_id == Product.id)\
     .outerjoin(Categoria, Product.categoria_id == Categoria.id)\
     .join(OrderModel, OrderItem.pedido_id == OrderModel.id)\
     .filter(OrderModel.entregado_en >= fi, OrderModel.entregado_en < ff, OrderModel.estado == "entregado")\
     .group_by("categoria_nombre")\
     .order_by(func.sum(OrderItem.subtotal).desc()).all()

    return [
        {"categoria": r.categoria_nombre, "unidades": int(r.unidades or 0), "ventas": float(r.ventas or 0)}
        for r in resultados
    ]


# ─────────────────────────────────────────────
# NOTIFICACIONES WHATSAPP (M11)
# ─────────────────────────────────────────────

MENSAJES_ESTADO = {
    "pendiente":  (
        "🎉 *¡Pedido recibido!*\n"
        "Tu pedido *{num}* ya está registrado. Total: *€{total}*.\n\n"
        "⏳ Todavía no ha comenzado la preparación. Te avisaremos cuando el equipo empiece.\n\n"
        "Desde aquí mismo puedes:\n"
        "• Escribir *ESTADO* para ver cómo va.\n"
        "• Escribir *CANCELAR* (solo antes de empezar a prepararlo).\n"
        "• Escribir *AGENTE* si necesitas hablar con una persona.\n\n"
        "Puedes seguir consultando el estado por este mismo chat."
    ),
    "armando":    "🔥 *¡Manos a la obra!*\nTu pedido *{num}* está en preparación en este momento.\nEn breve te avisamos cuando esté listo. ✨",
    "listo":      "✅ *¡Tu pedido está listo!*\nEl pedido *{num}* está perfectamente preparado y en breve sale hacia ti. 📦",
    "en_ruta":    "🚀 *¡En camino!*\nTu pedido *{num}* ya va hacia ti.\n\nCuando el repartidor llegue te enviará el código de entrega. No compartas ningún código antes de recibir tu pedido. 🛵",
    "entregado":  "🎊 *¡Pedido entregado!*\n¡Esperamos que te haya encantado! 😍\n¡Gracias por elegirnos! 💛",
    "cancelado":  "😔 *Pedido cancelado*\nTu pedido *{num}* fue cancelado. Sentimos los inconvenientes.\nSi tienes dudas o quieres más información, escríbenos y lo resolvemos juntos. 💬",
}


def mensaje_estado_pedido(pedido: Order) -> str:
    plantilla = MENSAJES_ESTADO.get(pedido.estado)
    if not plantilla:
        return ""
    base = plantilla.format(
        num=pedido.numero_pedido,
        total="%.2f" % float(pedido.total),
        codigo=pedido.codigo_confirmacion or "------",
        puntos=pedido.puntos_ganados or 0,
    )
    if (
        pedido.estado == "entregado"
        and get_store_features()["puntos"]
        and int(pedido.puntos_ganados or 0) > 0
    ):
        loyalty_terms = get_loyalty_terms()
        base += (
            f'\nTu compra sumó *{int(pedido.puntos_ganados)} {loyalty_terms["plural"]}* ☕ '
            "para tu próximo canje."
        )
    # Verificación pasiva antifraude: cuando el pedido acaba de crearse y
    # el motor de riesgo lo marcó como `pending`, invitamos al cliente a
    # confirmar. Sin bloquear el flujo — el equipo puede empezar igual si
    # decide asumir el riesgo. Solo aplica al estado `pendiente` porque en
    # los demás la confirmación ya ocurrió o dejó de ser relevante.
    if pedido.estado == "pendiente" and getattr(pedido, "confirmacion_estado", None) == "pending":
        confirmacion = (
            "⚠️ *ACCIÓN NECESARIA — CONFIRMA TU PRIMER PEDIDO*\n\n"
            "Necesitamos comprobar que este WhatsApp es correcto. "
            "El pedido no empezará a prepararse hasta que respondas.\n\n"
            "👉 Responde *SI* para confirmar el pedido.\n"
            "👉 Responde *NO* si no lo reconoces y quieres anularlo.\n\n"
            "_Sólo te lo pediremos en tu primera compra._"
        )
        return f"{confirmacion}\n\n{base}"
    return base


def mensaje_codigo_entrega(pedido: Order) -> str:
    codigo = pedido.codigo_confirmacion or pedido.generar_codigo_confirmacion()
    return (
        f"🔐 *Código de entrega para tu pedido {pedido.numero_pedido}: {codigo}*\n\n"
        "Compártelo únicamente con el repartidor cuando estés recibiendo tu pedido. "
        "Si elegiste Bizum, confirma primero el pago del importe exacto."
    )


def _bot_http_post(path: str, payload: dict, timeout: int = 8) -> bool:
    """Envia una llamada al bot externo sin bloquear el flujo principal."""
    import requests
    from models import SiteConfig

    # Default a `http://chat:3000` (nombre del servicio Docker) porque
    # 127.0.0.1 dentro del contenedor NO resuelve al servicio del bot en
    # otro contenedor. Env var `BOT_API_URL` sigue teniendo prioridad y
    # `SiteConfig.BOT_API_URL` sobre todo, para permitir override en runtime.
    bot_url = (SiteConfig.get("BOT_API_URL", os.environ.get("BOT_API_URL", "http://chat:3000")) or "").rstrip("/")
    api_key = SiteConfig.get("BOT_API_KEY", "")
    if not bot_url or not api_key:
        return False

    headers = {
        "X-API-Key": api_key,
        "X-Bot-Key": api_key,
    }
    try:
        resp = requests.post(
            f"{bot_url}{path}",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        try:
            data = resp.json()
        except Exception:
            data = {}
        return resp.ok and data.get("ok", True) is not False
    except Exception as exc:
        logger.warning("bot post %s fallo: %s", path, exc)
        return False


def _registrar_notificacion(
    canal: str,
    evento: str,
    destinatario: str,
    payload: dict,
    pedido_id: int | None = None,
    user_id: int | None = None,
    max_intentos: int = 3,
) -> NotificationOutbox | None:
    if not destinatario:
        return None
    job = NotificationOutbox(
        canal=canal,
        evento=evento,
        destinatario=destinatario,
        payload_json=_json_payload(payload),
        pedido_id=pedido_id,
        user_id=user_id,
        max_intentos=max(1, int(max_intentos or 3)),
    )
    db.session.add(job)
    return job


def _marcar_notificacion(job: NotificationOutbox | None, ok: bool, error: str | None = None) -> None:
    if not job:
        return
    job.intentos = (job.intentos or 0) + 1
    if ok:
        job.estado = "sent"
        job.enviado_en = utcnow()
        job.ultimo_error = None
        job.siguiente_intento_en = None
    else:
        job.estado = "failed" if job.intentos >= (job.max_intentos or 3) else "pending"
        job.ultimo_error = (error or "send_failed")[:1000]
        job.siguiente_intento_en = utcnow() + timedelta(
            minutes=min(notification_retry_backoff_max_min(), 2 ** job.intentos)
        )


def _enviar_con_outbox(
    canal: str,
    evento: str,
    destinatario: str,
    payload: dict,
    enviar,
    pedido_id: int | None = None,
    user_id: int | None = None,
) -> bool:
    """Encola dentro de la transacción actual; el worker envía después del commit."""
    del enviar  # La entrega externa pertenece exclusivamente al worker del outbox.
    try:
        return _registrar_notificacion(
            canal,
            evento,
            destinatario,
            payload,
            pedido_id=pedido_id,
            user_id=user_id,
        ) is not None
    except Exception:
        logger.exception("No se pudo encolar notificación %s/%s", canal, evento)
        return False


def purgar_registros_antiguos(now: datetime | None = None) -> dict:
    """Poda periódica de tablas que crecen sin límite en producción.

    Bug detectado en auditoría de disco:
      - `notification_outbox` acumulaba filas para siempre tras `sent`/`failed`.
      - `idempotency_keys` tenía `purge_expired_idempotency_keys()` pero
        ningún caller — grow-forever.

    Estrategia:
      - Retención configurable via SiteConfig (con env fallback):
        * `NOTIFICATION_OUTBOX_RETENTION_DAYS` (default 30 · min 7)
        * `IDEMPOTENCY_PURGE_ENABLED` (default "1")
      - Purga en batches (500 max) para no bloquear la BD.
      - Solo purga `estado in (sent, failed)` — nunca borra pendientes.

    Devuelve dict con conteos por tabla.
    """
    from datetime import timedelta as _td
    from models import NotificationOutbox as _NO, SiteConfig as _SC

    ahora = now or utcnow()
    resultado = {"notification_outbox": 0, "idempotency_keys": 0}

    # ── notification_outbox: sent/failed más viejos que retención ─────
    try:
        retention_days = int(_SC.get("NOTIFICATION_OUTBOX_RETENTION_DAYS", "30") or 30)
    except (TypeError, ValueError):
        retention_days = 30
    retention_days = max(7, min(retention_days, 365))  # cap defensivo
    corte = ahora - _td(days=retention_days)

    # Delete en batch (500) para no bloquear la tabla durante mucho tiempo.
    ids_borrar = [
        row.id for row in _NO.query.filter(
            _NO.estado.in_(("sent", "failed")),
            _NO.enviado_en.isnot(None),
            _NO.enviado_en < corte,
        ).limit(500).all()
    ]
    if ids_borrar:
        _NO.query.filter(_NO.id.in_(ids_borrar)).delete(synchronize_session=False)
        resultado["notification_outbox"] = len(ids_borrar)

    # ── idempotency_keys expiradas ────────────────────────────────────
    try:
        idem_enabled = str(_SC.get("IDEMPOTENCY_PURGE_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}
    except Exception:
        idem_enabled = True
    if idem_enabled:
        try:
            from idempotency import purge_expired_idempotency_keys as _purge_idem
            resultado["idempotency_keys"] = _purge_idem(batch_size=500)
        except Exception:
            logger.exception("purgar_registros_antiguos: fallo idempotency purge")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("purgar_registros_antiguos: commit falló")
        # No re-raise: la purga es best-effort, no debe tumbar el worker.

    if resultado["notification_outbox"] or resultado["idempotency_keys"]:
        logger.info(
            "purga: outbox=%d idempotency=%d (retention=%dd)",
            resultado["notification_outbox"],
            resultado["idempotency_keys"],
            retention_days,
        )
    return resultado


def procesar_notificaciones_pendientes(
    limit: int = 25,
    only_ids: list[int] | tuple[int, ...] | None = None,
) -> dict:
    """Reintenta notificaciones vencidas con una concesión que evita envíos dobles."""
    ahora = utcnow()
    lease_hasta = ahora + timedelta(minutes=5)
    query = NotificationOutbox.query.filter(
            or_(
                and_(
                    NotificationOutbox.estado == "pending",
                    or_(
                        NotificationOutbox.siguiente_intento_en.is_(None),
                        NotificationOutbox.siguiente_intento_en <= ahora,
                    ),
                ),
                and_(
                    NotificationOutbox.estado == "processing",
                    NotificationOutbox.siguiente_intento_en <= ahora,
                ),
            )
        )
    if only_ids is not None:
        ids = [int(job_id) for job_id in only_ids if job_id]
        if not ids:
            return {"procesadas": 0, "enviadas": 0, "fallidas": 0, "saltadas": 0}
        query = query.filter(NotificationOutbox.id.in_(ids))
    # Prioridad: OTP de canje y códigos de entrega van antes que broadcasts
    # o estados. El cliente los está esperando ahora mismo (en checkout o con
    # el repartidor delante) — retrasarlos rompe el flujo. Sin cambio de schema.
    EVENTOS_URGENTES = ("delivery_code", "points_otp", "canje_codigo", "pago_confirmado")
    prioridad_expr = case(
        (NotificationOutbox.evento.in_(EVENTOS_URGENTES), 0),
        else_=1,
    )
    jobs = (
        query
        .order_by(prioridad_expr.asc(), NotificationOutbox.creado_en.asc())
        .with_for_update(skip_locked=True)
        .limit(max(1, int(limit or 25)))
        .all()
    )
    for job in jobs:
        job.estado = "processing"
        job.siguiente_intento_en = lease_hasta
    db.session.commit()

    resultado = {"procesadas": 0, "enviadas": 0, "fallidas": 0, "saltadas": 0}
    for job in jobs:
        job = db.session.get(NotificationOutbox, job.id)
        if not job or job.estado != "processing":
            resultado["saltadas"] += 1
            continue
        payload = job.get_payload()
        ok = False
        error = None
        try:
            if job.canal == "whatsapp" and payload.get("telefono") and payload.get("mensaje"):
                ok = _send_whatsapp_message(payload["telefono"], payload["mensaje"])
            elif job.canal == "push":
                from push_service import send_push_outbox_payload
                ok, error = send_push_outbox_payload(payload)
            else:
                error = f"canal_no_soportado:{job.canal}"
                resultado["saltadas"] += 1
            _marcar_notificacion(job, ok, error)
            resultado["procesadas"] += 1
            resultado["enviadas" if ok else "fallidas"] += 1
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.exception("No se pudo procesar notificación pendiente %s: %s", job.id, exc)
            failed_job = db.session.get(NotificationOutbox, job.id)
            if failed_job and failed_job.estado == "processing":
                _marcar_notificacion(failed_job, False, str(exc))
                db.session.commit()
            resultado["fallidas"] += 1
    return resultado


def enviar_whatsapp_estado(pedido: Order) -> bool:
    """
    Notifica al cliente el estado actual del pedido por WhatsApp.
    No bloquea ni lanza excepción si el bot no está disponible.
    """
    if not pedido.cliente or not pedido.cliente.telefono:
        return False

    mensaje = mensaje_estado_pedido(pedido)
    if not mensaje:
        return False
    payload = {
        "telefono": pedido.cliente.telefono,
        "mensaje": mensaje,
        "numero_pedido": pedido.numero_pedido,
        "estado": pedido.estado,
    }
    return _enviar_con_outbox(
        "whatsapp",
        "order_state",
        pedido.cliente.telefono,
        payload,
        lambda: _send_whatsapp_message(pedido.cliente.telefono, mensaje),
        pedido_id=pedido.id,
        user_id=pedido.cliente_id,
    )


def enviar_whatsapp_codigo_entrega(pedido: Order, actor_id: int | None = None) -> bool:
    """Envia el código solo cuando el repartidor ya está con el cliente."""
    if not pedido.cliente or not pedido.cliente.telefono:
        return False
    mensaje = mensaje_codigo_entrega(pedido)
    payload = {
        "telefono": pedido.cliente.telefono,
        "mensaje": mensaje,
        "numero_pedido": pedido.numero_pedido,
        "estado": pedido.estado,
    }
    ok = _enviar_con_outbox(
        "whatsapp",
        "delivery_code",
        pedido.cliente.telefono,
        payload,
        lambda: _send_whatsapp_message(pedido.cliente.telefono, mensaje),
        pedido_id=pedido.id,
        user_id=pedido.cliente_id,
    )
    if ok:
        registrar_evento_pedido(
            pedido,
            "codigo_entrega_enviado",
            actor_id=actor_id,
            estado_anterior=pedido.estado,
            estado_nuevo=pedido.estado,
            canal="repartidor",
            detalle="Código de entrega enviado al cliente",
        )
    return ok


def _send_whatsapp_message(telefono: str, mensaje: str) -> bool:
    """Envía un mensaje de WhatsApp a un teléfono. Retorna True si OK."""
    if not telefono or not mensaje:
        return False
    from models import SiteConfig
    if str(SiteConfig.get("WHATSAPP_SIMULATE_SEND", "0") or "0").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        # Tail-only para no dejar PII completa en logs.
        logger.info("WhatsApp simulado para tel …%s", (telefono or "")[-3:] or "?")
        return True
    return _bot_http_post("/api/bot/message", {"telefono": telefono, "mensaje": mensaje})


def enviar_whatsapp_generico(
    telefono: str,
    mensaje: str,
    evento: str = "manual",
    pedido_id: int | None = None,
    user_id: int | None = None,
) -> bool:
    """Envía un WhatsApp operativo con trazabilidad en notification_outbox."""
    if not telefono or not mensaje:
        return False
    payload = {"telefono": telefono, "mensaje": mensaje}
    return _enviar_con_outbox(
        "whatsapp",
        evento,
        telefono,
        payload,
        lambda: _send_whatsapp_message(telefono, mensaje),
        pedido_id=pedido_id,
        user_id=user_id,
    )


def encolar_whatsapp_generico(
    telefono: str,
    mensaje: str,
    evento: str = "manual",
    pedido_id: int | None = None,
    user_id: int | None = None,
    max_intentos: int = 3,
    delay_seconds: int | None = None,
) -> NotificationOutbox | None:
    """Deja un WhatsApp en outbox para que lo envie el worker tras el commit."""
    if not telefono or not mensaje:
        return None
    payload = {"telefono": telefono, "mensaje": mensaje}
    job = _registrar_notificacion(
        "whatsapp",
        evento,
        telefono,
        payload,
        pedido_id=pedido_id,
        user_id=user_id,
        max_intentos=max_intentos,
    )
    if job and delay_seconds and delay_seconds > 0:
        job.siguiente_intento_en = utcnow() + timedelta(seconds=int(delay_seconds))
    return job


def enviar_whatsapp_pago_confirmado(pedido: Order) -> bool:
    if not pedido.cliente or not pedido.cliente.telefono:
        return False
    mensaje = (
        f"✅ Pago confirmado para tu pedido {pedido.numero_pedido}.\n"
        f"Total recibido: €{float(pedido.total):.2f}.\n"
        "Ya seguimos preparando tu pedido."
    )
    payload = {
        "telefono": pedido.cliente.telefono,
        "mensaje": mensaje,
        "numero_pedido": pedido.numero_pedido,
        "metodo_pago": pedido.metodo_pago,
    }
    return _enviar_con_outbox(
        "whatsapp",
        "payment_confirmed",
        pedido.cliente.telefono,
        payload,
        lambda: _send_whatsapp_message(pedido.cliente.telefono, mensaje),
        pedido_id=pedido.id,
        user_id=pedido.cliente_id,
    )


def solicitar_resena_pedido(pedido: Order) -> bool:
    """
    Encola una solicitud de reseña via WhatsApp.
    Se dispara después de marcar el pedido como 'entregado'.
    No bloquea el flujo y queda persistida para reintento por worker.
    """
    if not pedido.cliente or not pedido.cliente.telefono:
        return False
    if getattr(pedido, 'resena_enviada', False):
        return False

    mensaje = (
        f"⭐ *¿Cómo estuvo tu pedido {pedido.numero_pedido or pedido.id}?*\n\n"
        "Responde con una calificación del 1 al 5 y, si quieres, un comentario.\n"
        "Tu opinión nos ayuda a mejorar."
    )
    try:
        pedido.resena_enviada = True
        encolar_whatsapp_generico(
            pedido.cliente.telefono,
            mensaje,
            evento="review_request",
            pedido_id=pedido.id,
            user_id=pedido.cliente_id,
            delay_seconds=90,
        )
        logger.info("Solicitud de reseña encolada para pedido %s", pedido.id)
        return True
    except Exception:
        logger.exception("No se pudo encolar reseña para pedido %s", pedido.id)
        return False
