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
import logging
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import and_, or_
from extensions import db
from models import User, Order, Caja, StaffPayment, OrderEvent, NotificationOutbox
from store_config import get_store_features

logger = logging.getLogger(__name__)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    return registrar_evento_pedido(
        pedido,
        "pedido_creado",
        actor_id=actor_id,
        estado_nuevo=pedido.estado,
        canal=canal or pedido.origen,
        detalle=detalle,
        metadata=metadata,
    )


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
            f"• {linea['cantidad']}× {linea['nombre']}"
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


def _canales_preparacion_pedido(pedido: Order) -> set[str]:
    return {
        (item.display_canal_preparacion or "cocina").strip().lower()
        for item in lineas_preparacion_interna(pedido)
    }


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


def _almacen_mixto_preparado(pedido: Order) -> bool:
    evento = OrderEvent.query.filter(
        OrderEvent.pedido_id == pedido.id,
        OrderEvent.tipo.in_(["almacen_preparado", "almacen_reabierto"]),
    ).order_by(OrderEvent.id.desc()).first()
    return bool(evento and evento.tipo == "almacen_preparado")


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


def cancelar_pedido_operativo(
    pedido: Order,
    actor_id: int | None = None,
    canal: str | None = None,
    detalle: str | None = None,
    forzar_desde_entregado: bool = False,
) -> None:
    estado_anterior = pedido.estado
    pedido.cancelar(forzar_desde_entregado=forzar_desde_entregado)
    registrar_evento_pedido(
        pedido,
        "pedido_cancelado",
        actor_id=actor_id,
        estado_anterior=estado_anterior,
        estado_nuevo=pedido.estado,
        canal=canal,
        detalle=detalle,
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
        deg_lat = radio_km / 111.0 * 1.5   # margen 50 % sobre el radio
        deg_lon = radio_km / (111.0 * math.cos(math.radians(centro_lat))) * 1.5
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

    Reglas:
    - Si NINGUNA zona tiene geodata (centro_lat/lng/radio_km), devuelve la
      primera zona activa por orden (fallback histórico — compatibilidad).
    - Si HAY zonas con geodata pero no podemos geocodificar la dirección,
      devuelve None (el caller debe pedir una dirección verificable).
    - Si la dirección geocodifica, recorre todas las zonas con geodata y
      devuelve la más cercana cuyo radio contiene al cliente. Si ninguna lo
      contiene → None (cliente fuera de cobertura).

    Las zonas sin geodata se ignoran cuando hay otras con geodata.
    """
    if not zonas:
        return None
    geo_zonas = [z for z in zonas if z.activo and z.tiene_geo]
    if not geo_zonas:
        # Fallback legacy: primera zona activa por orden.
        activas = [z for z in zonas if z.activo]
        return activas[0] if activas else None

    from models import SiteConfig
    ciudad = SiteConfig.get("CIUDAD_NEGOCIO", "")
    coords = geocodificar_direccion(direccion or "", ciudad=ciudad) if direccion else None
    if coords is None:
        return None
    lat, lon = coords
    candidatos = []
    for z in geo_zonas:
        d = _haversine_km(z.centro_lat, z.centro_lng, lat, lon)
        if d <= float(z.radio_km):
            candidatos.append((d, z))
    if not candidatos:
        return None
    candidatos.sort(key=lambda t: (t[0], t[1].orden or 0, t[1].id))
    return candidatos[0][1]


def asignar_zona_por_coordenadas(lat, lon, zonas):
    """Resuelve zona y distancia usando coordenadas concedidas por el navegador."""
    if not zonas:
        return None, None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    geo_zonas = [zona for zona in zonas if zona.activo and zona.tiene_geo]
    if not geo_zonas:
        activas = [zona for zona in zonas if zona.activo]
        return (activas[0], None) if activas else (None, None)
    candidatos = []
    for zona in geo_zonas:
        distancia = _haversine_km(zona.centro_lat, zona.centro_lng, lat, lon)
        if distancia <= float(zona.radio_km):
            candidatos.append((distancia, zona))
    if not candidatos:
        return None, None
    candidatos.sort(key=lambda row: (row[0], row[1].orden or 0, row[1].id))
    distancia, zona = candidatos[0]
    return zona, round(distancia, 2)


def validar_radio_entrega(direccion: str) -> dict:
    """
    Valida si una dirección está dentro del radio de entrega configurado.
    Devuelve {"ok": bool, "distancia_km": float|None, "mensaje": str}.
    """
    from models import SiteConfig

    if not _to_bool_service(SiteConfig.get("VALIDAR_RADIO_ENTREGA", "1")):
        return {"ok": True, "distancia_km": None, "mensaje": ""}

    # Longitud mínima para considerarse una dirección real (calle + número mínimo)
    if not direccion or len(direccion.strip()) < 6:
        return {
            "ok": False,
            "distancia_km": None,
            "mensaje": "Escribe la dirección completa con calle y número.",
        }

    try:
        centro_lat = float(SiteConfig.get("CENTRO_LAT", ""))
        centro_lon = float(SiteConfig.get("CENTRO_LON", ""))
        radio_km   = float(SiteConfig.get("RADIO_ENTREGA_KM", "5"))
    except (ValueError, TypeError):
        return {
            "ok": False,
            "distancia_km": None,
            "mensaje": "La cobertura todavía no está configurada. Contacta con el negocio.",
        }

    ciudad = SiteConfig.get("CIUDAD_NEGOCIO", "")
    coords = geocodificar_direccion(direccion, ciudad=ciudad)
    if coords is None:
        if _to_bool_service(SiteConfig.get("BLOQUEAR_DIRECCION_NO_VERIFICADA", "1")):
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
    distancia = _haversine_km(centro_lat, centro_lon, lat, lon)

    if distancia > radio_km:
        return {
            "ok": False,
            "distancia_km": round(distancia, 2),
            "mensaje": (
                f"Lo sentimos, tu dirección queda fuera de nuestra zona de reparto"
                f"{f' en {ciudad}' if ciudad else ''} "
                f"({distancia:.1f} km del centro). Solo entregamos dentro del radio configurado."
            ),
        }

    return {"ok": True, "distancia_km": round(distancia, 2), "mensaje": ""}


def _to_bool_service(val):
    return str(val).strip().lower() in ("1", "true", "si", "sí", "on", "yes")


def tienda_abierta_en_horario(apertura: str, cierre: str, ahora: str | None = None, forzada_cerrada: bool = False) -> bool:
    """Evalua horario HH:MM, incluyendo ventanas nocturnas como 20:00-02:00."""
    if forzada_cerrada:
        return False
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


def redistribuir_pendientes_sin_asignar() -> int:
    """
    Asigna a preparadores online los pedidos 'pendiente' sin preparador.
    Se llama cuando un preparador se pone online — los pedidos que esperaban
    se reparten de forma equitativa de inmediato.

    Hace flush() tras cada asignación para que pedidos_activos_como_preparador()
    refleje la carga real al distribuir el siguiente pedido.

    Devuelve el número de pedidos asignados.
    """
    pedidos = Order.query.filter_by(
        estado="pendiente", preparador_id=None
    ).order_by(Order.creado_en).with_for_update(skip_locked=True).all()

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
    if es_pedido_solo_bar(pedido):
        logger.info(
            "distribuir_pedido: pedido %s es 100%% del bar, no se asigna preparador interno.",
            pedido.numero_pedido,
        )
        pedido.preparador_id = None
        return None

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
        candidatos.sort(key=lambda u: (u.pedidos_activos_como_preparador(), u.id))
        pedido.preparador_id = candidatos[0].id
        return candidatos[0]

    tipo = _tipo_pedido(pedido)
    rol_preferido  = "cocina" if tipo == "inmediato" else "preparacion"
    rol_alternativo = "preparacion" if tipo == "inmediato" else "cocina"

    # 1. Intentar con el rol preferido
    candidatos_pref = User.query.filter_by(rol=rol_preferido, activo=True).all()
    candidatos = _candidatos_disponibles(candidatos_pref)

    # 2. Alternativa: el otro rol
    if not candidatos:
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

    candidatos.sort(key=lambda u: (u.pedidos_activos_como_preparador(), u.id))
    asignado = candidatos[0]
    pedido.preparador_id = asignado.id
    return asignado


def distribuir_repartidor(pedido: Order) -> User | None:
    """
    Asigna al repartidor disponible con menos carga cuando el pedido pasa a 'listo'.
    Solo usa repartidores con disponibilidad manual activa y presencia reciente.
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

    candidatos.sort(key=lambda u: (u.pedidos_activos_como_repartidor(), u.id))
    asignado = candidatos[0]
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
            _Order.estado.in_(["pendiente", "armando"]),
            _Order.preparador_id.isnot(None),
        ).group_by(_Order.preparador_id).all()
    }
    # Un solo SELECT para contar carga de repartidores activos
    carga_rep = {
        row.repartidor_id: row.n
        for row in db.session.query(
            _Order.repartidor_id, func.count(_Order.id).label("n")
        ).filter(
            _Order.estado.in_(["listo", "en_ruta"]),
            _Order.repartidor_id.isnot(None),
        ).group_by(_Order.repartidor_id).all()
    }

    features = get_store_features()
    roles = ["cocina", "staff", "admin"]
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
    Se llama SOLO cuando el estado cambia a 'entregado'.
    Idempotente: si ya existe un PointsLog tipo='ganado' para este pedido, no hace nada.
    Retorna la cantidad de puntos otorgados (0 si ya se habían otorgado o no corresponde).
    """
    if not pedido.puntos_ganados or pedido.puntos_ganados <= 0:
        return 0
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
    cliente.sumar_puntos(
        pedido.puntos_ganados,
        pedido_id=pedido.id,
        descripcion=f"Pedido {pedido.numero_pedido} entregado",
    )
    logger.info("Puntos otorgados: %d pts → cliente %s (pedido %s)",
                pedido.puntos_ganados, pedido.cliente_id, pedido.numero_pedido)
    return pedido.puntos_ganados


# ─────────────────────────────────────────────
# COMISIONES AUTOMÁTICAS
# ─────────────────────────────────────────────

def generar_comision_entrega(pedido: Order) -> StaffPayment | None:
    """
    Crea un registro de comisión para el repartidor cuando entrega un pedido.
    Usa la tarifa configurada del repartidor. Si no existe, usa el coste de
    entrega cobrado como compatibilidad con instalaciones anteriores.
    """
    if not pedido.repartidor_id:
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
    """Devuelve monto total pendiente de pago al staff."""
    from sqlalchemy import func
    total = db.session.query(func.sum(StaffPayment.monto)).filter_by(pagado=False).scalar() or 0
    return float(total)


# ─────────────────────────────────────────────
# AFILIADOS
# ─────────────────────────────────────────────

def registrar_uso_afiliado(codigo, pedido, cliente, descuento_aplicado):
    """Registra el uso de un código de afiliado y genera StaffPayment si corresponde."""
    from models import AffiliateUse, StaffPayment

    comision = codigo.calcular_comision(float(pedido.total))
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

    return uso


# ─────────────────────────────────────────────
# ANALYTICS / P&L
# ─────────────────────────────────────────────

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
        "Desde aquí mismo puedes:\n"
        "• Escribir *ESTADO* para ver cómo va.\n"
        "• Escribir *CANCELAR* (solo antes de empezar a prepararlo).\n"
        "• Escribir *AGENTE* si necesitas hablar con una persona.\n\n"
        "¡Estamos en ello ahora mismo! 🔥"
    ),
    "armando":    "🔥 *¡Manos a la obra!*\nTu pedido *{num}* está en preparación en este momento.\nEn breve te avisamos cuando esté listo. ✨",
    "listo":      "✅ *¡Tu pedido está listo!*\nEl pedido *{num}* está perfectamente preparado y en breve sale hacia ti. 📦",
    "en_ruta":    "🚀 *¡En camino!*\nTu pedido *{num}* ya va hacia ti.\n\nCuando el repartidor llegue te enviará el código de entrega. No compartas ningún código antes de recibir tu pedido. 🛵",
    "entregado":  "🎊 *¡Pedido entregado!*\n¡Esperamos que te haya encantado! 😍\nGanaste *{puntos} puntos* 🌟 — van sumando para tu próximo descuento.\n¡Gracias por elegirnos! 💛",
    "cancelado":  "😔 *Pedido cancelado*\nTu pedido *{num}* fue cancelado. Sentimos los inconvenientes.\nSi tienes dudas o quieres más información, escríbenos y lo resolvemos juntos. 💬",
}


def mensaje_estado_pedido(pedido: Order) -> str:
    plantilla = MENSAJES_ESTADO.get(pedido.estado)
    if not plantilla:
        return ""
    return plantilla.format(
        num=pedido.numero_pedido,
        total="%.2f" % float(pedido.total),
        codigo=pedido.codigo_confirmacion or "------",
        puntos=pedido.puntos_ganados or 0,
    )


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
        job.siguiente_intento_en = utcnow() + timedelta(minutes=min(60, 2 ** job.intentos))


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
    jobs = (
        query
        .order_by(NotificationOutbox.creado_en.asc())
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
        logger.info("WhatsApp simulado para %s", telefono)
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
