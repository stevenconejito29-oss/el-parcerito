"""
Servicio de fidelización unificado para Oxidian.

Fuente de verdad para canje de puntos en TODOS los canales (web, bot, POS).

Flujo canónico:
  1. solicitar_codigo(cliente)         — genera OTP, lo envía por WhatsApp
  2. verificar_codigo(cliente, codigo) — verifica OTP sin consumir puntos todavía
  3. aplicar_canje_en_pedido(...)      — consume puntos al confirmar el pedido (único punto de deducción)

Regla de oro: los puntos NO se descuentan hasta que el pedido existe en BD.
Esto garantiza idempotencia y evita pérdidas por pedidos fallidos.

El canal bot usaba antes deducción inmediata → ahora centralizado aquí.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
logger = logging.getLogger(__name__)


def bloquear_cliente_puntos(cliente):
    """Bloquea y refresca la fila del cliente antes de calcular o mutar puntos."""
    from extensions import db
    from models import User

    stmt = (
        db.select(User)
        .where(User.id == cliente.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return db.session.execute(stmt).scalar_one()


# ── OTP ───────────────────────────────────────────────────────────────────────

def solicitar_codigo(
    cliente,
    producto=None,
    commit: bool = True,
    permitir_sin_puntos: bool = False,
) -> dict:
    """
    Genera y envía código OTP por WhatsApp para verificar canje de puntos.

    Parámetros:
        cliente  — User con rol='cliente'
        producto — Product (opcional) si el canje es por producto específico

    Retorna dict con ok, msg.
    """
    from extensions import db

    if cliente.puntos <= 0 and not permitir_sin_puntos:
        return {"ok": False, "msg": "No tienes puntos disponibles", "puntos": 0}

    if producto is not None:
        if not producto.canje_directo_disponible():
            return {"ok": False, "msg": "Este producto no admite canje de puntos"}
        if producto.puntos_para_canje > cliente.puntos:
            return {
                "ok": False,
                "msg": f"Puntos insuficientes. Necesitas {producto.puntos_para_canje}, tienes {cliente.puntos}",
            }

    if not cliente.telefono:
        return {"ok": False, "msg": "No tienes teléfono registrado para verificación"}

    from services import enviar_whatsapp_generico
    from models import SiteConfig
    codigo = cliente.generar_cod_puntos()
    nombre_negocio = SiteConfig.get("NOMBRE_NEGOCIO", "Oxidian")
    producto_txt = (
        producto.nombre
        if producto
        else (f"{cliente.puntos} puntos" if cliente.puntos > 0 else "confirmar tu identidad")
    )
    mensaje = (
        f"🔐 *Código de verificación — {nombre_negocio}*\n\n"
        f"Tu código para canjear puntos por *{producto_txt}* es:\n\n"
        f"*{codigo}*\n\n"
        f"⏰ Válido 10 minutos. No lo compartas."
    )
    encolado = enviar_whatsapp_generico(
        cliente.telefono,
        mensaje,
        evento="points_otp",
        user_id=cliente.id,
    )
    if not encolado:
        db.session.rollback()
        return {"ok": False, "msg": "No se pudo preparar el envío del código. Inténtalo de nuevo."}
    if commit:
        db.session.commit()
    else:
        db.session.flush()

    logger.info("OTP de puntos enviado a cliente %s (tel: %s)", cliente.id, cliente.telefono)
    return {"ok": True, "msg": "Código enviado a tu WhatsApp", "puntos": cliente.puntos}


def verificar_codigo(cliente, codigo: str) -> dict:
    """
    Verifica el OTP. NO descuenta puntos — solo confirma la identidad.

    Retorna dict con ok, msg, puntos.
    """
    if not cliente.verificar_cod_puntos(codigo):
        return {"ok": False, "msg": "Código incorrecto o expirado. Solicita uno nuevo."}
    return {"ok": True, "msg": "Código verificado", "puntos": cliente.puntos}


# ── Aplicación real de puntos (único punto de deducción) ─────────────────────

def aplicar_canje_en_pedido(
    cliente,
    pedido,
    *,
    puntos_usar: int = 0,
    producto_canje_id: int | None = None,
) -> dict:
    """
    Aplica el canje de puntos al crear un pedido. ÚNICO punto donde se descuentan.

    Parámetros:
        cliente          — User del cliente
        pedido           — Order ya persistido (tiene .id)
        puntos_usar      — puntos a convertir en descuento (puede ser 0)
        producto_canje_id — ID de producto a añadir gratis (puede ser None)

    Retorna dict con puntos_descontados, producto_canje.
    """
    from extensions import db
    from models import Product, OrderItem, metadata_componente_combo, metadata_item_pedido

    resultado = {"puntos_descontados": 0, "producto_canje": None}
    cliente = bloquear_cliente_puntos(cliente)

    # Idempotencia: si el pedido ya tiene puntos_usados registrados, no deducir de nuevo.
    # Esto protege contra doble submit, retries o llamadas accidentales duplicadas.
    raw_puntos_usados = getattr(pedido, "puntos_usados", 0)
    if isinstance(raw_puntos_usados, str):
        raw_puntos_usados = raw_puntos_usados.strip() or 0

    if isinstance(raw_puntos_usados, (int, float, Decimal, str)):
        try:
            puntos_ya_usados = int(raw_puntos_usados or 0)
        except (TypeError, ValueError):
            puntos_ya_usados = 0
    else:
        puntos_ya_usados = 0

    if puntos_ya_usados > 0:
        logger.warning(
            "aplicar_canje_en_pedido: pedido %s ya tiene %d puntos_usados registrados — omitiendo",
            pedido.numero_pedido, puntos_ya_usados,
        )
        return resultado

    # 4a. Descuento monetario por puntos
    if puntos_usar > 0:
        puntos_real = min(puntos_usar, cliente.puntos)
        if puntos_real > 0:
            try:
                cliente.canjear_puntos(puntos_real, pedido_id=pedido.id)
                pedido.puntos_usados = puntos_real  # marcar en el pedido para idempotencia
                resultado["puntos_descontados"] = puntos_real
                logger.info(
                    "Canje de %d puntos para cliente %s en pedido %s",
                    puntos_real, cliente.id, pedido.numero_pedido,
                )
            except ValueError as e:
                logger.warning("canjear_puntos falló: %s", e)
                raise

    # 4b. Producto gratuito por canje
    if producto_canje_id:
        prod = db.session.get(Product, producto_canje_id)
        puntos_producto = int(prod.puntos_para_canje or 0) if prod else 0
        if prod and prod.canje_directo_disponible() and puntos_producto <= cliente.puntos:
            try:
                cliente.canjear_puntos(puntos_producto, pedido_id=pedido.id)
                if prod.tipo_entrega == "inmediato":
                    if prod.es_combo:
                        prod.descontar_stock_combo(1)
                    else:
                        prod.descontar_stock(1)
                pedido.puntos_usados = int(pedido.puntos_usados or 0) + puntos_producto
                extra_metadata = {
                    "reward": {
                        "tipo": "producto_puntos",
                        "puntos": puntos_producto,
                        "cliente_id": cliente.id,
                    }
                }
                if prod.es_combo:
                    extra_metadata["combo"] = {
                        "componentes": [
                            {
                                **metadata_componente_combo(item),
                                "fijo": True,
                                "grupo": item.grupo.nombre_publico if item.grupo else "Base incluida",
                                "grupo_orden": item.grupo.orden if item.grupo else 0,
                                "notas_preparacion": item.notas_preparacion or "",
                            }
                            for item in prod.combo_items
                            if not item.es_seleccionable
                        ],
                        "selecciones": [],
                    }
                item_canje = OrderItem(
                    pedido_id=pedido.id,
                    producto_id=prod.id,
                    cantidad=1,
                    precio_unit=0,
                    subtotal=0,
                    metadata_json=json.dumps(
                        metadata_item_pedido(prod, extra_metadata),
                        ensure_ascii=False,
                    ),
                )
                db.session.add(item_canje)
                resultado["producto_canje"] = prod
                logger.info(
                    "Producto canje '%s' añadido a pedido %s (cliente %s)",
                    prod.nombre, pedido.numero_pedido, cliente.id,
                )
            except ValueError as e:
                logger.warning("canje producto falló: %s", e)
                raise

    # Limpiar código OTP usado (si existe)
    if cliente.cod_puntos:
        cliente.cod_puntos = None
        cliente.cod_puntos_expira = None

    return resultado


# ── Consulta de puntos ────────────────────────────────────────────────────────

def puntos_disponibles(cliente) -> int:
    """Puntos actuales del cliente."""
    return max(0, cliente.puntos)


def enviar_saldo_puntos(cliente, commit: bool = True) -> bool:
    """Envía el saldo al WhatsApp propietario sin exponerlo en la respuesta web."""
    from models import SiteConfig
    from services import enviar_whatsapp_generico, get_puntos_config

    ratio = max(1, int(get_puntos_config()["ratio"]))
    puntos = max(0, int(cliente.puntos or 0))
    valor = puntos / ratio
    nombre_negocio = SiteConfig.get("NOMBRE_NEGOCIO", "Oxidian")
    mensaje = (
        f"⭐ *Tus puntos en {nombre_negocio}*\n\n"
        f"Tienes *{puntos} puntos* disponibles.\n"
        f"Equivalen hasta a *€{valor:.2f}* de descuento.\n\n"
        "Para canjearlos, arma tu pedido en la web y verifica este mismo WhatsApp "
        "durante la confirmación."
    )
    ok = enviar_whatsapp_generico(
        cliente.telefono,
        mensaje,
        evento="points_balance",
        user_id=cliente.id,
    )
    if ok and commit:
        from extensions import db
        db.session.commit()
    return ok


def euros_por_puntos(puntos: int, ratio: int = 100) -> float:
    """Convierte puntos a euros según el ratio configurado."""
    return round(puntos / ratio, 2) if ratio > 0 else 0.0
