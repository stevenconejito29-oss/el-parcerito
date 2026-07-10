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
import inspect
from decimal import Decimal
logger = logging.getLogger(__name__)


def _to_int(value, default: int = 0) -> int:
    """Coerción defensiva a int para campos que pueden venir como str/Decimal/None
    desde ORM, formularios o payloads externos. Devuelve `default` ante fallo."""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, Decimal)):
        try:
            return int(value)
        except (ValueError, OverflowError):
            return default
    if isinstance(value, str):
        try:
            return int(value.strip() or default)
        except ValueError:
            return default
    return default


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

    # Throttle anti-flood: si ya hay un código válido reciente, no reemitir.
    # Se deduce el instante de emisión a partir de `cod_puntos_expira - TTL`.
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    from models import SiteConfig
    try:
        min_gap = int(SiteConfig.get("OTP_MIN_RESEND_SECONDS", "60") or 60)
    except (TypeError, ValueError):
        min_gap = 60
    try:
        ttl_min = int(SiteConfig.get("COD_PUNTOS_TTL_MINUTOS", "10") or 10)
    except (TypeError, ValueError):
        ttl_min = 10
    if cliente.cod_puntos_expira and cliente.cod_puntos:
        ahora = _dt.now(_tz.utc).replace(tzinfo=None)
        try:
            emitido_en = cliente.cod_puntos_expira - _td(minutes=ttl_min)
            if (ahora - emitido_en).total_seconds() < min_gap:
                return {
                    "ok": False,
                    "msg": "Ya te enviamos un código hace un momento. Revisa tu WhatsApp.",
                    "puntos": cliente.puntos,
                }
        except (TypeError, ValueError):
            pass

    from services import enviar_whatsapp_generico
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

    # NO loguear teléfono en claro: solo cliente_id + últimos 3 dígitos para trazar
    # incidencias sin exponer PII completa en logs de gunicorn/journald.
    _tail = (cliente.telefono or "")[-3:] if cliente.telefono else "?"
    logger.info("OTP de puntos enviado a cliente %s (tel …%s)", cliente.id, _tail)
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
    origen_operativo: str | None = None,
) -> dict:
    """
    Aplica el canje de puntos al crear un pedido. ÚNICO punto donde se descuentan.

    Parámetros:
        cliente          — User del cliente
        pedido           — Order ya persistido (tiene .id)
        puntos_usar      — puntos a convertir en descuento (puede ser 0)
        producto_canje_id — ID de producto a añadir gratis (puede ser None)
        origen_operativo  — inventario del establecimiento del pedido

    Retorna dict con puntos_descontados, producto_canje.
    """
    from extensions import db
    from models import Product, OrderItem, metadata_componente_combo, metadata_item_pedido

    resultado = {"puntos_descontados": 0, "producto_canje": None}
    cliente = bloquear_cliente_puntos(cliente)

    # Idempotencia: si el pedido ya tiene puntos_usados registrados, no deducir de nuevo.
    # Protege contra doble submit, retries y llamadas duplicadas.
    puntos_ya_usados = _to_int(getattr(pedido, "puntos_usados", 0))

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
        origen_canje = origen_operativo or (prod.origen_operativo_key if prod else None)
        puntos_producto = int(prod.puntos_para_canje or 0) if prod else 0
        producto_valido = bool(
            prod
            and origen_canje
            and prod.activo
            and prod.canjeable_con_puntos
            and prod.puntos_para_canje
            and prod.visible_ahora
            and not (
                prod.es_combo
                and any(item.es_seleccionable for item in prod.combo_items)
            )
            and prod.pertenece_a_origen(origen_canje)
            and prod.disponible_para_venta_en_origen(origen_canje)
        )
        if not producto_valido:
            raise ValueError("El producto de canje no está disponible en el origen del pedido")
        if puntos_producto > cliente.puntos:
            raise ValueError("Puntos insuficientes para el producto de canje")
        if producto_valido:
            try:
                cliente.canjear_puntos(puntos_producto, pedido_id=pedido.id)
                if prod.tipo_entrega == "inmediato":
                    prod.descontar_stock_en_origen(origen_canje, 1)
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
                metadata_params = inspect.signature(metadata_item_pedido).parameters
                if "origen_operativo" in metadata_params:
                    item_metadata = metadata_item_pedido(
                        prod,
                        extra_metadata,
                        origen_operativo=origen_canje,
                    )
                else:
                    item_metadata = metadata_item_pedido(prod, extra_metadata)
                    snapshot = item_metadata.setdefault("producto", {})
                    snapshot["origen_operativo_key"] = origen_canje
                    snapshot["origen_operativo"] = (
                        "propio" if origen_canje == "propio" else "proveedor"
                    )
                    snapshot["proveedor_despachador_id"] = (
                        int(origen_canje.split(":", 1)[1])
                        if origen_canje.startswith("proveedor:")
                        else None
                    )
                item_canje = OrderItem(
                    pedido_id=pedido.id,
                    producto_id=prod.id,
                    cantidad=1,
                    precio_unit=0,
                    subtotal=0,
                    metadata_json=json.dumps(
                        item_metadata,
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
    """Puntos actuales del cliente. Aplica reset periódico si toca antes de leer."""
    try:
        reset_periodico_si_toca()
    except Exception:
        # No debemos fallar la consulta si el reset tiene un problema puntual.
        pass
    return max(0, cliente.puntos)


def messaging_service_available() -> bool:
    """Comprueba si el canal WhatsApp está disponible sin exponer al cliente.
    Un cliente no debe saber si su número existe, pero sí saber si el servicio
    de mensajería está caído para reintentar más tarde."""
    try:
        from services import _bot_http_get  # type: ignore
        return bool(_bot_http_get("/api/bot/health"))
    except Exception:
        # Best-effort: si no hay health check, asumimos disponible para no
        # dar falsos negativos en producción.
        return True


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


# ── Reset periódico de puntos ────────────────────────────────────────────────

def _puntos_reset_config():
    """Lee la config de reset periódico. Devuelve (period_days, last_reset_iso)."""
    from models import SiteConfig
    try:
        period_days = int(SiteConfig.get("POINTS_RESET_PERIOD_DAYS", "0") or 0)
    except (ValueError, TypeError):
        period_days = 0
    last_reset = SiteConfig.get("POINTS_LAST_RESET_AT", "")
    return period_days, last_reset


def reset_periodico_si_toca():
    """Comprueba si toca resetear los puntos globalmente y lo hace.
    Devuelve True si reseteó, False si no.
    Usa advisory lock para evitar carrera si se llama concurrentemente."""
    from datetime import datetime, timedelta
    from extensions import db
    from models import SiteConfig, User, PointsLog

    period_days, last_reset_iso = _puntos_reset_config()
    if period_days <= 0:
        return False  # reset desactivado
    from models import utcnow as _utcnow_helper
    ahora = _utcnow_helper()
    if last_reset_iso:
        try:
            last = datetime.fromisoformat(last_reset_iso)
            if ahora - last < timedelta(days=period_days):
                return False  # aún no toca
        except (ValueError, TypeError):
            pass  # fecha corrupta → reseteamos y grabamos ahora
    # Advisory lock para exclusión mutua entre workers
    try:
        db.session.execute(db.text("SELECT pg_advisory_lock(-53214112)"))
        # Re-check post-lock (double-check locking)
        _, last_reset_iso = _puntos_reset_config()
        if last_reset_iso:
            try:
                last = datetime.fromisoformat(last_reset_iso)
                if ahora - last < timedelta(days=period_days):
                    return False
            except (ValueError, TypeError):
                pass
        # Reset: puntos → 0 para todos los clientes con puntos > 0
        afectados = 0
        for u in User.query.filter(User.rol == "cliente", User.puntos > 0).all():
            previo = int(u.puntos or 0)
            u.puntos = 0
            db.session.add(PointsLog(cliente_id=u.id, tipo="reset", cantidad=-previo,
                                     descripcion=f"Reset periódico ({period_days}d)"))
            afectados += 1
        SiteConfig.set("POINTS_LAST_RESET_AT", ahora.isoformat(),
                       descripcion="Timestamp del último reset automático de puntos")
        db.session.commit()
        return afectados
    finally:
        try:
            db.session.execute(db.text("SELECT pg_advisory_unlock(-53214112)"))
            db.session.commit()
        except Exception:
            db.session.rollback()
