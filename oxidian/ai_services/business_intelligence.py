"""Motor de inteligencia comercial para el asesor IA.

Produce **insights derivados** —comparativas, rankings por rentabilidad,
anomalías, patrones cross-sell y de horarios— para que el modelo pueda
hablar con hechos y no con generalidades.

Todo se calcula con SQL agregado sobre la BD real. Portable: sin dependencias
externas y sin PII (nombres, teléfonos, direcciones). Se ejecuta cada vez
que el asesor arma el snapshot; para catálogos pequeños/medianos toma <200ms.

Uso desde ``ai_advisor.build_snapshot()``::

    from ai_services.business_intelligence import compute_insights
    snapshot["insights"] = compute_insights()
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func

from models import Order, OrderItem, Product, User, db


# ─────────────────────────────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────────────────────────────

ESTADOS_VENTA = ("entregado", "listo", "pagado", "en_ruta")
ESTADOS_CANCELADO = ("cancelado",)


def _round(value, digits=2) -> float:
    if value is None:
        return 0.0
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return 0.0


def _pct_change(actual, previo) -> float | None:
    """Cambio porcentual con nulos → None."""
    if not previo:
        return None
    return _round((float(actual) - float(previo)) / float(previo) * 100, 1)


def _trend_flag(delta_pct: float | None) -> str:
    if delta_pct is None:
        return "sin_dato_previo"
    if delta_pct >= 15:
        return "subida_fuerte"
    if delta_pct >= 5:
        return "subida"
    if delta_pct <= -15:
        return "caida_fuerte"
    if delta_pct <= -5:
        return "caida"
    return "estable"


# ─────────────────────────────────────────────────────────────────────
# 1. Comparativas de periodos (semana / mes)
# ─────────────────────────────────────────────────────────────────────

def _sumario_periodo(desde: date, hasta: date) -> dict:
    """Métricas base del rango [desde, hasta)."""
    q = Order.query.filter(
        Order.creado_en >= desde,
        Order.creado_en < hasta,
    )
    pedidos = q.all()
    ventas = [p for p in pedidos if p.estado in ESTADOS_VENTA]
    cancelados = [p for p in pedidos if p.estado in ESTADOS_CANCELADO]
    total_ventas = sum(float(p.total or 0) for p in ventas)
    ingreso_neto = sum(float(p.merchant_net_amount or p.total or 0) for p in ventas)
    ticket_medio = (total_ventas / len(ventas)) if ventas else 0.0
    clientes_unicos = len({p.cliente_id for p in ventas if p.cliente_id})
    tasa_cancel = (len(cancelados) / len(pedidos) * 100) if pedidos else 0.0
    return {
        "pedidos_totales": len(pedidos),
        "pedidos_venta": len(ventas),
        "pedidos_cancelados": len(cancelados),
        "clientes_unicos": clientes_unicos,
        "ventas_eur": _round(total_ventas),
        "ingreso_neto_eur": _round(ingreso_neto),
        "ticket_medio_eur": _round(ticket_medio),
        "tasa_cancelacion_pct": _round(tasa_cancel, 1),
    }


def comparativa_periodos() -> dict:
    """Compara últimos 7d vs 7d previos, y últimos 30d vs 30d previos."""
    hoy = date.today()
    result = {}
    for ventana_dias, key in [(7, "semana"), (30, "mes")]:
        hasta_actual = hoy
        desde_actual = hoy - timedelta(days=ventana_dias)
        hasta_previo = desde_actual
        desde_previo = desde_actual - timedelta(days=ventana_dias)
        actual = _sumario_periodo(desde_actual, hasta_actual)
        previo = _sumario_periodo(desde_previo, hasta_previo)
        deltas = {
            k: _pct_change(actual[k], previo[k])
            for k in ("ventas_eur", "pedidos_venta", "ticket_medio_eur",
                     "clientes_unicos", "ingreso_neto_eur")
        }
        result[key] = {
            "actual": actual,
            "previo": previo,
            "cambio_pct": deltas,
            "tendencia_ventas": _trend_flag(deltas.get("ventas_eur")),
            "tendencia_pedidos": _trend_flag(deltas.get("pedidos_venta")),
            "ventana_dias": ventana_dias,
        }
    return result


# ─────────────────────────────────────────────────────────────────────
# 2. Ranking de productos por rentabilidad (margen × velocidad)
# ─────────────────────────────────────────────────────────────────────

def ranking_productos_rentabilidad(dias: int = 30, limite: int = 8) -> list[dict]:
    """Top productos por CONTRIBUCIÓN MARGINAL total (no solo unidades).

    contribucion = (precio_unit_pagado - precio_costo) × cantidad_vendida.
    Ordena por contribucion desc. Solo cuenta pedidos vendidos.
    """
    desde = date.today() - timedelta(days=dias)
    rows = (
        db.session.query(
            OrderItem.producto_id,
            func.sum(OrderItem.cantidad).label("unidades"),
            func.sum(OrderItem.subtotal).label("ingreso"),
        )
        .join(Order, Order.id == OrderItem.pedido_id)
        .filter(Order.creado_en >= desde, Order.estado.in_(ESTADOS_VENTA))
        .group_by(OrderItem.producto_id)
        .all()
    )
    productos = {p.id: p for p in Product.query.filter(
        Product.id.in_([r.producto_id for r in rows])
    ).all()}
    enriched = []
    for r in rows:
        prod = productos.get(r.producto_id)
        if not prod:
            continue
        costo = float(prod.precio_costo or 0)
        unidades = int(r.unidades or 0)
        ingreso = float(r.ingreso or 0)
        precio_medio = ingreso / unidades if unidades else 0
        margen_unit = precio_medio - costo
        contribucion = margen_unit * unidades
        margen_pct = (margen_unit / precio_medio * 100) if precio_medio else 0
        enriched.append({
            "producto": prod.nombre,
            "categoria": (prod.categoria.nombre if prod.categoria else "—"),
            "unidades": unidades,
            "ingreso_eur": _round(ingreso),
            "precio_medio_eur": _round(precio_medio),
            "coste_unit_eur": _round(costo),
            "margen_unit_eur": _round(margen_unit),
            "margen_pct": _round(margen_pct, 1),
            "contribucion_eur": _round(contribucion),
            "sin_coste_registrado": costo == 0,
        })
    enriched.sort(key=lambda x: x["contribucion_eur"], reverse=True)
    return enriched[:limite]


# ─────────────────────────────────────────────────────────────────────
# 3. Heatmap por hora del día y día de semana
# ─────────────────────────────────────────────────────────────────────

def heatmap_horarios(dias: int = 30) -> dict:
    """Ventas y pedidos por hora (0-23) y por día de semana (0=lunes)."""
    desde = date.today() - timedelta(days=dias)
    ventas_por_hora = {h: {"pedidos": 0, "ingreso": 0.0} for h in range(24)}
    ventas_por_dow = {d: {"pedidos": 0, "ingreso": 0.0} for d in range(7)}
    q = Order.query.filter(
        Order.creado_en >= desde,
        Order.estado.in_(ESTADOS_VENTA),
    ).with_entities(Order.creado_en, Order.total).all()
    for creado_en, total in q:
        if not creado_en:
            continue
        h = creado_en.hour
        dow = creado_en.weekday()
        total_f = float(total or 0)
        ventas_por_hora[h]["pedidos"] += 1
        ventas_por_hora[h]["ingreso"] += total_f
        ventas_por_dow[dow]["pedidos"] += 1
        ventas_por_dow[dow]["ingreso"] += total_f
    for h in ventas_por_hora:
        ventas_por_hora[h]["ingreso"] = _round(ventas_por_hora[h]["ingreso"])
    for d in ventas_por_dow:
        ventas_por_dow[d]["ingreso"] = _round(ventas_por_dow[d]["ingreso"])
    hora_pico = max(ventas_por_hora.items(), key=lambda kv: kv[1]["pedidos"])
    dia_pico_dow, dia_pico_stats = max(ventas_por_dow.items(), key=lambda kv: kv[1]["pedidos"])
    dow_labels = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    return {
        "hora_pico": {"hora": hora_pico[0], **hora_pico[1]},
        "dia_pico": {"dia": dow_labels[dia_pico_dow], **dia_pico_stats},
        "por_hora": [{"hora": h, **v} for h, v in ventas_por_hora.items()],
        "por_dia_semana": [{"dia": dow_labels[d], **v} for d, v in ventas_por_dow.items()],
    }


# ─────────────────────────────────────────────────────────────────────
# 4. Cross-sell: pares de productos que se compran juntos
# ─────────────────────────────────────────────────────────────────────

def cross_sell_pairs(dias: int = 60, limite: int = 6) -> list[dict]:
    """Top N pares (A, B) que aparecen en el mismo pedido con más frecuencia."""
    desde = date.today() - timedelta(days=dias)
    # Solo pedidos vendidos con >=2 productos distintos
    pedidos_ids = [
        r[0] for r in db.session.query(OrderItem.pedido_id)
        .join(Order, Order.id == OrderItem.pedido_id)
        .filter(Order.creado_en >= desde, Order.estado.in_(ESTADOS_VENTA))
        .group_by(OrderItem.pedido_id)
        .having(func.count(func.distinct(OrderItem.producto_id)) >= 2)
        .all()
    ]
    if not pedidos_ids:
        return []
    items = (
        db.session.query(OrderItem.pedido_id, OrderItem.producto_id)
        .filter(OrderItem.pedido_id.in_(pedidos_ids))
        .distinct()
        .all()
    )
    pares: dict[tuple[int, int], int] = {}
    from collections import defaultdict
    por_pedido: dict[int, list[int]] = defaultdict(list)
    for pid, prod in items:
        por_pedido[pid].append(prod)
    for lista in por_pedido.values():
        lista = sorted(set(lista))
        for i in range(len(lista)):
            for j in range(i + 1, len(lista)):
                key = (lista[i], lista[j])
                pares[key] = pares.get(key, 0) + 1
    if not pares:
        return []
    top = sorted(pares.items(), key=lambda kv: kv[1], reverse=True)[:limite]
    ids_necesarios = {i for pair, _ in top for i in pair}
    productos = {p.id: p.nombre for p in Product.query.filter(Product.id.in_(ids_necesarios)).all()}
    return [
        {
            "producto_a": productos.get(a, f"#{a}"),
            "producto_b": productos.get(b, f"#{b}"),
            "veces_juntos": count,
        }
        for (a, b), count in top
    ]


# ─────────────────────────────────────────────────────────────────────
# 5. Salud de la base de clientes
# ─────────────────────────────────────────────────────────────────────

def salud_clientes() -> dict:
    """Segmenta clientes por recencia: activos / dormidos / perdidos / nuevos."""
    hoy = date.today()
    hace_30 = hoy - timedelta(days=30)
    hace_90 = hoy - timedelta(days=90)
    hace_7 = hoy - timedelta(days=7)

    total_clientes = User.query.filter_by(rol="cliente", activo=True).count()
    # Actividad por última compra
    ultima_compra = (
        db.session.query(
            Order.cliente_id,
            func.max(Order.creado_en).label("ultima"),
        )
        .filter(Order.estado.in_(ESTADOS_VENTA))
        .group_by(Order.cliente_id)
        .all()
    )
    activos_30 = sum(1 for r in ultima_compra if r.ultima and r.ultima.date() >= hace_30)
    dormidos = sum(1 for r in ultima_compra if r.ultima and hace_90 <= r.ultima.date() < hace_30)
    perdidos = sum(1 for r in ultima_compra if r.ultima and r.ultima.date() < hace_90)
    # Nuevos = primera compra en últimos 7 días
    primera_compra = (
        db.session.query(
            Order.cliente_id,
            func.min(Order.creado_en).label("primera"),
        )
        .filter(Order.estado.in_(ESTADOS_VENTA))
        .group_by(Order.cliente_id)
        .all()
    )
    nuevos_7 = sum(1 for r in primera_compra if r.primera and r.primera.date() >= hace_7)
    return {
        "total_clientes": total_clientes,
        "activos_30d": activos_30,
        "nuevos_7d": nuevos_7,
        "dormidos_30_90d": dormidos,
        "perdidos_90d+": perdidos,
        "nunca_compraron": total_clientes - len(ultima_compra),
    }


# ─────────────────────────────────────────────────────────────────────
# 6. Rendimiento por método de pago
# ─────────────────────────────────────────────────────────────────────

def mix_metodos_pago(dias: int = 30) -> list[dict]:
    desde = date.today() - timedelta(days=dias)
    rows = (
        db.session.query(
            Order.metodo_pago,
            func.count(Order.id).label("pedidos"),
            func.sum(Order.total).label("ingreso"),
        )
        .filter(Order.creado_en >= desde, Order.estado.in_(ESTADOS_VENTA))
        .group_by(Order.metodo_pago)
        .all()
    )
    total_ingreso = sum(float(r.ingreso or 0) for r in rows)
    return [
        {
            "metodo": r.metodo_pago or "sin_metodo",
            "pedidos": int(r.pedidos or 0),
            "ingreso_eur": _round(r.ingreso or 0),
            "share_pct": _round((float(r.ingreso or 0) / total_ingreso * 100) if total_ingreso else 0, 1),
        }
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────
# 7. Detección simple de anomalías
# ─────────────────────────────────────────────────────────────────────

def anomalias() -> list[str]:
    """Alertas legibles sobre patrones que se salen de lo esperado."""
    alertas = []
    comp = comparativa_periodos()
    sem = comp["semana"]
    if sem["tendencia_ventas"] == "caida_fuerte":
        d = sem["cambio_pct"]["ventas_eur"]
        alertas.append(f"⚠️ Ventas de la última semana cayeron {abs(d)}% vs la semana anterior.")
    if sem["actual"]["tasa_cancelacion_pct"] >= 15:
        alertas.append(
            f"⚠️ Tasa de cancelación de la semana es "
            f"{sem['actual']['tasa_cancelacion_pct']}% (>15%)."
        )
    if sem["actual"]["ticket_medio_eur"] < sem["previo"]["ticket_medio_eur"] * 0.85:
        alertas.append(
            f"⚠️ Ticket medio cayó de €{sem['previo']['ticket_medio_eur']} a "
            f"€{sem['actual']['ticket_medio_eur']} en 7 días."
        )
    # Clientes nuevos frenados
    salud = salud_clientes()
    if salud["nuevos_7d"] == 0 and salud["total_clientes"] > 5:
        alertas.append("⚠️ Cero clientes nuevos esta semana.")
    # Productos sin coste registrado (impide medir margen real)
    top_prod = ranking_productos_rentabilidad(dias=30, limite=5)
    sin_coste = [p["producto"] for p in top_prod if p["sin_coste_registrado"]]
    if sin_coste:
        alertas.append(
            f"⚠️ Top vendidos SIN precio_costo registrado — no puedo calcular "
            f"margen real: {', '.join(sin_coste)}."
        )
    return alertas


# ─────────────────────────────────────────────────────────────────────
# API pública: un solo dict con TODO
# ─────────────────────────────────────────────────────────────────────

def compute_insights() -> dict[str, Any]:
    """Snapshot de inteligencia comercial completo, listo para el modelo.

    Se llama desde ``ai_advisor.build_snapshot()`` y se anida bajo la
    clave ``insights`` del payload final. El prompt del asesor ya obliga
    al modelo a citar datos concretos; con esta capa podrá referirse a
    tendencias, comparativas y patrones específicos, no genéricos.
    """
    try:
        return {
            "generado_en": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "comparativa_periodos": comparativa_periodos(),
            "top_productos_por_rentabilidad_30d": ranking_productos_rentabilidad(),
            "heatmap_horarios_30d": heatmap_horarios(),
            "cross_sell_pairs_60d": cross_sell_pairs(),
            "salud_clientes": salud_clientes(),
            "mix_metodos_pago_30d": mix_metodos_pago(),
            "anomalias": anomalias(),
        }
    except Exception as exc:
        # El asesor debe seguir funcionando incluso si algún cómputo falla.
        return {
            "error": f"insights_no_disponibles: {type(exc).__name__}",
            "detalle": str(exc)[:200],
        }
