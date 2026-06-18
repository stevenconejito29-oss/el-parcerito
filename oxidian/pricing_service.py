"""
Motor de pricing unificado para Oxidian.

Fuente de verdad única para el cálculo de totales de pedido.
Usado por: web checkout, POS, API bot.

Reglas de stacking (en orden de aplicación):
  1. Cupón de descuento        — máx. MAX_CUPON_PCT del subtotal
  2. Código de afiliado        — máx. MAX_AFILIADO_PCT del subtotal
  3. Puntos de fidelidad       — convertidos según ratio configurado
  Cap final: descuento total ≤ subtotal; total mínimo = 0.01 €

Este módulo es PURO: no tiene efectos secundarios (no persiste nada).
El llamador es responsable de registrar usos de cupón, afiliado y puntos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

# ── Límites de política ─────────────────────────────────────────────────────
MAX_CUPON_PCT     = 0.50   # cupón ≤ 50 % del subtotal
MAX_AFILIADO_PCT  = 0.30   # afiliado ≤ 30 % del subtotal
TOTAL_MINIMO      = Decimal("0.01")


# ── Resultado inmutable ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class PricingResult:
    subtotal:           float
    descuento_promo:    float
    descuento_cupon:    float
    descuento_afiliado: float
    descuento_puntos:   float
    descuento_manual:   float
    costo_envio:        float
    descuento_total:    float
    total:              float
    promos_aplicadas:   tuple = field(default_factory=tuple)
    # Metadatos para auditoría (no afectan cálculo)
    cupon_id:           int | None = None
    afiliado_codigo_id: int | None = None
    puntos_usados:      int = 0


def _dec(v) -> Decimal:
    return Decimal(str(v))


def calcular_precio(
    items: list[dict],
    subtotal: float,
    *,
    cupon=None,
    afiliado=None,
    puntos_usar: int = 0,
    zona=None,
    ratio_puntos: int = 100,
    descuento_manual: float = 0.0,
) -> PricingResult:
    """
    Calcula el total del pedido aplicando todos los descuentos en orden.

    Parámetros:
        items         — lista de dicts {"producto": Product, "cantidad": int, "subtotal": float}
        subtotal      — suma de items (precio × cantidad)
        cupon         — objeto Coupon o None (ya validado por el llamador)
        afiliado      — objeto AffiliateCode o None (ya validado por el llamador)
        puntos_usar   — puntos a canjear como descuento
        zona          — objeto ZonaEntrega o None
        ratio_puntos  — puntos necesarios para 1 € de descuento
    """
    sub = _dec(subtotal)

    d_promo = _dec(0)
    promos_aplicadas: list = []

    # 1. Cupón
    d_cupon = _dec(0)
    cupon_id = None
    if cupon is not None:
        raw = _dec(cupon.calcular_descuento(float(sub)))
        d_cupon = min(raw, sub * _dec(MAX_CUPON_PCT))
        cupon_id = cupon.id

    # 3. Afiliado
    d_afiliado = _dec(0)
    afiliado_id = None
    if afiliado is not None:
        raw = _dec(afiliado.calcular_descuento(float(sub)))
        d_afiliado = min(raw, sub * _dec(MAX_AFILIADO_PCT))
        afiliado_id = afiliado.id

    # 4. Puntos
    puntos_usar = max(0, int(puntos_usar))
    ratio_puntos = max(1, int(ratio_puntos))  # nunca dividir por cero
    puntos_usados_reales = 0
    d_puntos = _dec(0)
    if puntos_usar > 0:
        descuento_pre_puntos = min(d_promo + d_cupon + d_afiliado, sub)
        max_descuento_puntos = max(_dec(0), sub - descuento_pre_puntos)
        descuento_solicitado = (_dec(puntos_usar) / _dec(ratio_puntos)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        d_puntos = min(descuento_solicitado, max_descuento_puntos)
        if d_puntos == descuento_solicitado:
            puntos_usados_reales = puntos_usar
        else:
            puntos_usados_reales = int(
                (d_puntos * _dec(ratio_puntos)).to_integral_value(rounding=ROUND_FLOOR)
            )

    # Costo de envío — cupón envio_gratis lo anula directamente
    costo_envio = _dec(0)
    cupon_es_envio_gratis = (cupon is not None and getattr(cupon, "tipo", None) == "envio_gratis")
    if zona is not None and not cupon_es_envio_gratis:
        if zona.gratis_desde is not None and sub >= _dec(zona.gratis_desde):
            costo_envio = _dec(0)
        else:
            costo_envio = _dec(zona.precio_envio)

    # Descuento manual (POS) — se aplica sobre el total ya rebajado, sin caps propios
    d_manual = _dec(max(0.0, float(descuento_manual)))

    # Total con caps
    d_total = d_promo + d_cupon + d_afiliado + d_puntos + d_manual
    d_total = min(d_total, sub)           # descuento total ≤ subtotal
    total = max(sub - d_total + costo_envio, TOTAL_MINIMO)
    total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return PricingResult(
        subtotal=float(sub),
        descuento_promo=float(d_promo),
        descuento_cupon=float(d_cupon),
        descuento_afiliado=float(d_afiliado),
        descuento_puntos=float(d_puntos),
        descuento_manual=float(d_manual),
        costo_envio=float(costo_envio),
        descuento_total=float(d_total),
        total=float(total),
        promos_aplicadas=tuple(promos_aplicadas),
        cupon_id=cupon_id,
        afiliado_codigo_id=afiliado_id,
        puntos_usados=puntos_usados_reales,
    )


def descuento_total(result: PricingResult) -> float:
    """Conveniente: descuento total como float."""
    return result.descuento_total
