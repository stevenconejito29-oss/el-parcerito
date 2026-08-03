"""Métricas temporales del ciclo de vida de pedidos.

La escritura de hitos vive en ``services.py`` porque forma parte del flujo del
pedido. Este módulo es deliberadamente de solo lectura: convierte timestamps
UTC persistidos en indicadores y filas exportables sin modificar el negocio.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from math import ceil

from sqlalchemy.orm import joinedload

from models import Order


ETAPAS = (
    {
        "key": "preparacion",
        "label": "Pedido → preparado",
        "description": "Tiempo total hasta que operación marca el pedido listo.",
        "inicio": "creado_en",
        "fin": "preparado_en",
        "delivery_only": False,
    },
    {
        "key": "asignacion",
        "label": "Preparado → asignado",
        "description": "Espera hasta encontrar un repartidor.",
        "inicio": "preparado_en",
        "fin": "repartidor_asignado_en",
        "delivery_only": True,
    },
    {
        "key": "aceptacion",
        "label": "Asignado → tomado",
        "description": "Tiempo que tarda el repartidor en aceptar el pedido.",
        "inicio": "repartidor_asignado_en",
        "fin": "repartidor_tomado_en",
        "delivery_only": True,
    },
    {
        "key": "despacho",
        "label": "Tomado → en ruta",
        "description": "Tiempo desde la aceptación hasta la salida real.",
        "inicio": "repartidor_tomado_en",
        "fin": "en_ruta_en",
        "delivery_only": True,
    },
    {
        "key": "reparto",
        "label": "En ruta → entregado",
        "description": "Duración efectiva del desplazamiento y entrega.",
        "inicio": "en_ruta_en",
        "fin": "entregado_en",
        "delivery_only": True,
    },
)


def _period_bounds(fecha_ini: date, fecha_fin: date) -> tuple[datetime, datetime]:
    return datetime.combine(fecha_ini, time.min), datetime.combine(
        fecha_fin + timedelta(days=1), time.min,
    )


def _duracion_minutos(inicio: datetime | None, fin: datetime | None) -> float | None:
    if not inicio or not fin or fin < inicio:
        return None
    return round((fin - inicio).total_seconds() / 60, 2)


def _percentil(valores: list[float], porcentaje: float) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    indice = max(0, ceil((porcentaje / 100) * len(ordenados)) - 1)
    return round(ordenados[indice], 1)


def _resumen_valores(valores: list[float]) -> dict:
    if not valores:
        return {"muestras": 0, "promedio": None, "mediana": None, "p90": None}
    return {
        "muestras": len(valores),
        "promedio": round(sum(valores) / len(valores), 1),
        "mediana": _percentil(valores, 50),
        "p90": _percentil(valores, 90),
    }


def filas_tiempos_operativos(
    fecha_ini: date,
    fecha_fin: date,
) -> list[dict]:
    """Devuelve una fila estable por pedido creado dentro del período."""
    desde, hasta = _period_bounds(fecha_ini, fecha_fin)
    pedidos = (
        Order.query
        .options(joinedload(Order.repartidor), joinedload(Order.zona))
        .filter(Order.creado_en >= desde, Order.creado_en < hasta)
        .order_by(Order.creado_en.asc(), Order.id.asc())
        .all()
    )
    filas = []
    for pedido in pedidos:
        es_delivery = bool(pedido.requiere_reparto)
        fila = {
            "pedido_id": pedido.id,
            "numero_pedido": pedido.numero_pedido,
            "estado": pedido.estado,
            "origen": pedido.origen,
            "tipo_entrega": pedido.tipo_entrega_cliente,
            "zona": pedido.zona_nombre_aplicada,
            "creado_en": pedido.creado_en,
            "preparado_en": pedido.preparado_en,
            "repartidor_asignado_en": pedido.repartidor_asignado_en,
            "repartidor_tomado_en": pedido.repartidor_tomado_en,
            "en_ruta_en": pedido.en_ruta_en,
            "entregado_en": pedido.entregado_en,
            "repartidor": pedido.repartidor.nombre if pedido.repartidor else "",
            "es_delivery": es_delivery,
        }
        for etapa in ETAPAS:
            fila[f"min_{etapa['key']}"] = _duracion_minutos(
                getattr(pedido, etapa["inicio"]),
                getattr(pedido, etapa["fin"]),
            )
        fila["min_total"] = _duracion_minutos(
            pedido.creado_en,
            pedido.entregado_en,
        )
        filas.append(fila)
    return filas


def calcular_metricas_operativas(fecha_ini: date, fecha_fin: date) -> dict:
    """Resume tiempos, cobertura de datos y tendencia diaria."""
    filas = filas_tiempos_operativos(fecha_ini, fecha_fin)
    operativos = [
        fila for fila in filas
        if fila["origen"] not in {"presencial", "pos"}
    ]
    etapas = []
    for definicion in ETAPAS:
        elegibles = [
            fila for fila in operativos
            if not definicion["delivery_only"] or fila["es_delivery"]
        ]
        valores = [
            fila[f"min_{definicion['key']}"]
            for fila in elegibles
            if fila[f"min_{definicion['key']}"] is not None
        ]
        resumen = _resumen_valores(valores)
        resumen.update({
            "key": definicion["key"],
            "label": definicion["label"],
            "description": definicion["description"],
            "elegibles": len(elegibles),
            "cobertura_pct": round(
                (len(valores) / len(elegibles) * 100) if elegibles else 0,
                1,
            ),
        })
        etapas.append(resumen)

    totales = [
        fila["min_total"] for fila in operativos
        if fila["min_total"] is not None
    ]
    resumen_total = _resumen_valores(totales)
    candidatos_cuello = [
        etapa for etapa in etapas
        if etapa["promedio"] is not None and etapa["muestras"]
    ]
    cuello = (
        max(candidatos_cuello, key=lambda etapa: etapa["promedio"])
        if candidatos_cuello else None
    )

    por_dia = defaultdict(list)
    for fila in operativos:
        if fila["min_total"] is not None:
            por_dia[fila["creado_en"].date().isoformat()].append(fila["min_total"])
    tendencia = []
    cursor = fecha_ini
    while cursor <= fecha_fin:
        valores = por_dia.get(cursor.isoformat(), [])
        tendencia.append({
            "fecha": cursor.isoformat(),
            "label": cursor.strftime("%d/%m"),
            "pedidos": len(valores),
            "promedio": round(sum(valores) / len(valores), 1) if valores else None,
        })
        cursor += timedelta(days=1)

    return {
        "total_registros": len(filas),
        "total_operativos": len(operativos),
        "entregados": sum(1 for fila in operativos if fila["estado"] == "entregado"),
        "activos": sum(
            1 for fila in operativos
            if fila["estado"] in {"pendiente", "armando", "listo", "en_ruta"}
        ),
        "cancelados": sum(1 for fila in operativos if fila["estado"] == "cancelado"),
        "total": resumen_total,
        "etapas": etapas,
        "cuello_botella": cuello,
        "tendencia": tendencia,
    }
