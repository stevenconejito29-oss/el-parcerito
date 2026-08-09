"""Asesor comercial determinista disponible incluso sin proveedor de IA.

Genera propuestas, no mutaciones. Todos los importes salen del catálogo o de
ventas agregadas y cada recomendación declara la evidencia que la sostiene.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func

from extensions import db
from models import Order, OrderItem, Product
from store_config import get_store_value


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _round_price(value: Decimal) -> Decimal:
    """Redondeo comercial a décimas sin esconder el cálculo base."""
    return value.quantize(Decimal("0.10"), rounding=ROUND_HALF_UP)


def _margin_pct(price: Decimal, cost: Decimal) -> Decimal | None:
    if price <= 0 or cost <= 0:
        return None
    return ((price - cost) / price * 100).quantize(Decimal("0.1"))


def _target_margin() -> Decimal:
    try:
        value = Decimal(str(get_store_value("AI_TARGET_MARGIN_PCT", "35") or "35"))
    except Exception:
        value = Decimal("35")
    return min(Decimal("80"), max(Decimal("10"), value))


def _sales_by_product(days: int = 30) -> dict[int, dict]:
    from datetime import timedelta
    from models import utcnow

    cutoff = utcnow() - timedelta(days=days)
    rows = (
        db.session.query(
            OrderItem.producto_id,
            func.coalesce(func.sum(OrderItem.cantidad), 0),
            func.coalesce(func.sum(OrderItem.subtotal), 0),
        )
        .join(Order, Order.id == OrderItem.pedido_id)
        .filter(
            Order.creado_en >= cutoff,
            Order.estado == "entregado",
            OrderItem.producto_id.isnot(None),
        )
        .group_by(OrderItem.producto_id)
        .all()
    )
    return {
        int(product_id): {"units": int(units or 0), "revenue": _money(revenue)}
        for product_id, units, revenue in rows
    }


def build_commercial_diagnostic() -> dict:
    target = _target_margin()
    products = Product.query.filter_by(activo=True, es_combo=False).all()
    sales = _sales_by_product()
    priced = []
    missing_cost = []

    for product in products:
        price = _money(product.precio)
        cost = _money(product.precio_costo)
        if cost <= 0:
            missing_cost.append(product.nombre)
            continue
        margin = _margin_pct(price, cost)
        floor = _round_price(cost / (Decimal("1") - target / Decimal("100")))
        priced.append({
            "id": product.id,
            "name": product.nombre,
            "category": product.categoria.nombre if product.categoria else "Sin categoría",
            "price": price,
            "cost": cost,
            "margin_pct": margin,
            "target_price": max(price, floor),
            "units_30d": sales.get(product.id, {}).get("units", 0),
        })

    price_alerts = sorted(
        [item for item in priced if item["margin_pct"] is not None and item["margin_pct"] < target],
        key=lambda item: (item["margin_pct"], -item["units_30d"]),
    )[:6]

    ranked = sorted(
        priced,
        key=lambda item: (item["units_30d"] > 0, item["units_30d"], item["margin_pct"] or 0),
        reverse=True,
    )
    combo_candidates = []
    combo_pairs = set()
    for anchor in ranked:
        companion = next((
            item for item in ranked
            if item["id"] != anchor["id"]
            and item["category"] != anchor["category"]
            and frozenset((item["id"], anchor["id"])) not in combo_pairs
        ), None)
        if not companion:
            continue
        combo_pairs.add(frozenset((anchor["id"], companion["id"])))
        regular = anchor["price"] + companion["price"]
        cost = anchor["cost"] + companion["cost"]
        proposed = _round_price(regular * Decimal("0.92"))
        minimum = _round_price(cost / (Decimal("1") - target / Decimal("100")))
        proposed = max(proposed, minimum)
        if proposed >= regular:
            continue
        margin = _margin_pct(proposed, cost)
        combo_candidates.append({
            "name": f"{anchor['name']} + {companion['name']}",
            "regular_price": regular,
            "suggested_price": proposed,
            "saving": regular - proposed,
            "margin_pct": margin,
            "evidence": (
                "ventas de los últimos 30 días"
                if anchor["units_30d"] or companion["units_30d"]
                else "afinidad de categorías y margen del catálogo; falta validar demanda"
            ),
        })
        if len(combo_candidates) == 3:
            break

    delivered = Order.query.filter_by(estado="entregado").count()
    avg_ticket = _money(
        db.session.query(func.coalesce(func.avg(Order.total), 0))
        .filter(Order.estado == "entregado")
        .scalar()
    )
    coupon = None
    if delivered:
        threshold = _round_price(avg_ticket * Decimal("1.25"))
        coupon = {
            "threshold": threshold,
            "discount_pct": 8,
            "reason": "umbral 25% por encima del ticket medio histórico",
        }

    campaigns = []
    if combo_candidates:
        lead = combo_candidates[0]
        campaigns.append({
            "name": "Prueba de combo para elevar ticket",
            "action": f"Destacar {lead['name']} a {lead['suggested_price']:.2f} € durante 7 días",
            "metric": "ticket medio, unidades del combo y margen bruto",
            "stop_rule": "detener si reduce el margen total o no añade ventas incrementales",
            "evidence": lead["evidence"],
        })
    if coupon:
        campaigns.append({
            "name": "Cupón sobre ticket medio",
            "action": f"Probar 8% desde {coupon['threshold']:.2f} € en un segmento controlado",
            "metric": "ticket incremental, conversión, coste del descuento y repetición",
            "stop_rule": "detener si el aumento de ticket no compensa el descuento",
            "evidence": coupon["reason"],
        })
    elif products:
        campaigns.append({
            "name": "Validación de demanda sin descuento",
            "action": "Destacar una propuesta del catálogo durante 7 días sin rebajar precio",
            "metric": "clics, adiciones al carrito y pedidos entregados",
            "stop_rule": "no aplicar cupones hasta disponer de ticket medio y margen observados",
            "evidence": "catálogo disponible; todavía no hay entregas suficientes",
        })

    return {
        "engine": "local",
        "target_margin_pct": target,
        "products_analyzed": len(products),
        "products_with_cost": len(priced),
        "missing_cost": missing_cost[:8],
        "price_alerts": price_alerts,
        "combo_candidates": combo_candidates,
        "delivered_orders": delivered,
        "average_ticket": avg_ticket,
        "coupon": coupon,
        "campaign_candidates": campaigns[:3],
        "has_demand_evidence": any(item["units_30d"] for item in priced),
    }


def answer_commercial_question(question: str, diagnostic: dict | None = None) -> str:
    diagnostic = diagnostic or build_commercial_diagnostic()
    lower = (question or "").casefold()
    lines = ["Análisis local del catálogo (sin API externa):"]

    if any(word in lower for word in ("combo", "pack", "ticket", "venta", "estrateg", "campa")):
        combos = diagnostic["combo_candidates"]
        if combos:
            lines.append("\nCombos para validar:")
            for item in combos:
                lines.append(
                    f"• {item['name']}: {item['suggested_price']:.2f} € "
                    f"(por separado {item['regular_price']:.2f} €, margen estimado {item['margin_pct']}%). "
                    f"Base: {item['evidence']}."
                )
        else:
            lines.append("\nNo hay suficientes productos con coste y categorías distintas para proponer un combo seguro.")

    if any(word in lower for word in ("precio", "margen", "ganancia", "rentab")):
        alerts = diagnostic["price_alerts"]
        if alerts:
            lines.append(f"\nPrecios bajo el margen objetivo del {diagnostic['target_margin_pct']}%:")
            for item in alerts:
                lines.append(
                    f"• {item['name']}: margen {item['margin_pct']}%; "
                    f"precio mínimo orientativo {item['target_price']:.2f} € (actual {item['price']:.2f} €)."
                )
        else:
            lines.append("\nNo detecto productos con coste conocido por debajo del margen objetivo.")

    if any(word in lower for word in ("cupon", "cupón", "descuento", "ticket")):
        coupon = diagnostic["coupon"]
        if coupon:
            lines.append(
                f"\nPrueba controlada: 8% desde {coupon['threshold']:.2f} €. "
                f"El umbral se basa en el ticket medio de {diagnostic['average_ticket']:.2f} €; "
                "mide uso, margen y ticket incremental antes de mantenerlo."
            )
        else:
            lines.append("\nAún no hay entregas suficientes para fijar un cupón basado en ticket medio; evita inventar un umbral.")

    if any(word in lower for word in ("campaña", "campana", "estrateg", "venta", "marketing")):
        lines.append("\nPruebas comerciales sugeridas:")
        for campaign in diagnostic["campaign_candidates"]:
            lines.append(
                f"• {campaign['name']}: {campaign['action']}. "
                f"Medir {campaign['metric']}; {campaign['stop_rule']}. "
                f"Base: {campaign['evidence']}."
            )

    if len(lines) == 1:
        lines.append(
            f"\nRevisé {diagnostic['products_analyzed']} productos: "
            f"{len(diagnostic['price_alerts'])} alertas de margen y "
            f"{len(diagnostic['combo_candidates'])} oportunidades de combo. "
            "Pregunta por combos, precios, márgenes, descuentos o ticket medio."
        )
    if diagnostic["missing_cost"]:
        lines.append(
            "\nDatos pendientes: completa el coste de "
            + ", ".join(diagnostic["missing_cost"][:4])
            + " para obtener recomendaciones fiables."
        )
    lines.append("\nEstas son propuestas para revisión; no se aplicó ningún cambio automáticamente.")
    return "\n".join(lines)
