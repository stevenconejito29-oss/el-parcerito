"""
POS — Punto de Venta Presencial
Permite al admin registrar ventas físicas sin pasar por el checkout online.
Flujo: seleccionar productos → descuento manual → cobrar → registra Order + Caja.
"""
import json
import math
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from functools import wraps
from sqlalchemy.exc import IntegrityError
from extensions import db, get_or_404
from models import Product, Categoria, Order, OrderItem, User, Coupon, Caja, ComboItem, AdminFeature, SiteConfig, IdempotencyKey, normalizar_metodo_pago, utcnow, metadata_componente_combo, metadata_item_pedido
from idempotency import request_idempotency_key, request_body_hash, IDEMPOTENCY_TTL
from services import (
    calcular_puntos_ganados,
    cancelar_pedido_operativo,
    registrar_ingreso,
    registrar_egreso,
    registrar_pedido_creado,
    registrar_pago_pedido,
)
from pricing_service import calcular_precio
from permissions import can_read_order_ticket
from product_options_service import (
    product_option_catalog_payload,
    validate_product_option_selection,
)
from product_presentations_service import (
    presentation_metadata,
    product_presentation_catalog_payload,
    validate_product_presentation_selection,
)

pos_bp = Blueprint("pos", __name__)

ROLES_POS = {"admin", "super_admin"}


def _pos_max_qty():
    try:
        return max(1, min(999, int(SiteConfig.get("CART_MAX_QTY", "20") or 20)))
    except (TypeError, ValueError):
        return 20


def _pos_combo_config(producto):
    """Serializa un combo para que el POS pueda pedir elecciones explícitas."""
    componentes = ComboItem.query.filter_by(combo_id=producto.id)\
        .order_by(ComboItem.orden.asc(), ComboItem.id.asc()).all()
    incluidos = []
    fixed_base = 0.0
    grupos = {}
    for item in componentes:
        if not item.componente or not item.componente.activo:
            continue
        option = {
            "id": item.id,
            "nombre": (
                f"{item.componente.nombre} · {item.presentacion.label}"
                if item.presentacion else item.componente.nombre
            ),
            "precio_extra": float(item.precio_extra or 0),
            "precio_base": (
                float(item.componente.precio_final)
                + (item.presentacion.precio_extra_float if item.presentacion else 0.0)
            ) * max(1, int(item.cantidad or 1)),
            "predeterminado": bool(item.es_predeterminado),
            "disponible": bool(producto.combo_item_stock_disponible(item)),
        }
        if not item.es_seleccionable:
            fixed_base += option["precio_base"]
            incluidos.append({
                "nombre": option["nombre"],
                "cantidad": int(item.cantidad or 1),
            })
            continue
        group_name = item.grupo.nombre_publico if item.grupo else (item.grupo_seleccion or "Seleccion")
        group_key = str(item.combo_group_id or group_name)
        group = grupos.setdefault(group_key, {
            "id": f"combo-{group_key}",
            "nombre": group_name,
            "min": max(1, int(item.grupo.min_selecciones if item.grupo else 1)),
            "max": max(1, int(item.grupo.max_selecciones if item.grupo else (item.max_selecciones or 1))),
            "opciones": [],
        })
        option["selection_kind"] = "combo"
        group["opciones"].append(option)
    return {
        "incluidos": incluidos,
        "grupos": list(grupos.values()),
        "precio_modo": producto.combo_precio_modo_normalizado,
        "descuento_pct": producto.combo_descuento_pct_float,
        "precio_fijo": float(producto.precio_final),
        "base_fija": round(fixed_base, 2),
    }


def _pos_product_option_config(producto):
    """Opciones configurables del producto para la venta presencial."""
    groups = [
        {
            **group,
            "id": f"product-{group['id']}",
            "opciones": [
                {
                    **option,
                    "selection_kind": "product",
                    "disponible": True,
                    "predeterminado": False,
                    "precio_base": 0.0,
                }
                for option in group["opciones"]
            ],
        }
        for group in product_option_catalog_payload(producto)
    ]
    presentations = product_presentation_catalog_payload(producto)
    max_flavor_capacity = max(
        [int((row.get("flavor_policy") or {}).get("max") or 0) for row in presentations] or [0]
    )
    if max_flavor_capacity:
        for group in groups:
            if group.get("tipo") == "sabor":
                group["min"] = 0
                group["max"] = max_flavor_capacity
                for option in group.get("opciones") or []:
                    option["max_cantidad"] = max_flavor_capacity
    if presentations:
        groups.append({
            "id": "presentation",
            "nombre": "Tamaño",
            "min": 1,
            "max": 1,
            "tipo": "presentacion",
            "opciones": [
                {
                    "id": row["id"],
                    "nombre": row["label"],
                    "precio_extra": row["precio_extra"],
                    "precio_base": 0.0,
                    "selection_kind": "presentation",
                    "disponible": True,
                    "predeterminado": index == 0,
                }
                for index, row in enumerate(presentations)
            ],
        })
    return {
        "grupos": groups,
        "presentation_policies": {
            str(row["id"]): row.get("flavor_policy") or {}
            for row in presentations
        },
    }


def _combo_order_payload(producto, seleccion_item_ids):
    if not producto.es_combo:
        return "", {}
    try:
        seleccion_item_ids = {int(i) for i in (seleccion_item_ids or [])}
    except (TypeError, ValueError):
        raise ValueError("La selección del combo no tiene un formato válido")
    componentes = ComboItem.query.filter_by(combo_id=producto.id)\
        .order_by(ComboItem.orden.asc(), ComboItem.id.asc()).all()
    fijos = [item for item in componentes if not item.es_seleccionable]
    seleccionables = [item for item in componentes if item.es_seleccionable]
    resumen = [f"{item.cantidad}x {item.componente.nombre}" for item in fijos if item.componente]
    grupos_meta = []
    grupos = {}
    for item in seleccionables:
        grupos.setdefault(item.grupo.nombre_publico if item.grupo else (item.grupo_seleccion or "Seleccion"), []).append(item)
    for grupo, opciones in grupos.items():
        min_sel = max(1, int(opciones[0].grupo.min_selecciones if opciones[0].grupo else 1))
        max_sel = max(1, int(opciones[0].grupo.max_selecciones if opciones[0].grupo else (opciones[0].max_selecciones or 1)))
        elegidos = [item for item in opciones if item.id in seleccion_item_ids]
        if not elegidos:
            elegidos = [
                item for item in opciones
                if item.es_predeterminado and item.componente
                and producto.combo_item_stock_disponible(item)
            ][:max_sel]
            if len(elegidos) < min_sel:
                restantes = sorted(
                    [
                        item for item in opciones
                        if item not in elegidos and item.componente
                        and producto.combo_item_stock_disponible(item)
                    ],
                    key=lambda item: (float(item.componente.precio_final), item.orden or 0),
                )
                elegidos.extend(restantes[:min_sel - len(elegidos)])
        if len(elegidos) < min_sel or len(elegidos) > max_sel:
            raise ValueError(f"El combo '{producto.nombre}' requiere elegir entre {min_sel} y {max_sel} opción(es) de {grupo}")
        if any(not item.componente or not producto.combo_item_stock_disponible(item) for item in elegidos):
            raise ValueError(f"Una opción de '{grupo}' no tiene stock disponible")
        if elegidos:
            resumen.append(f"{grupo}: {', '.join((item.componente.nombre + (f' +€{float(item.precio_extra or 0):.2f}' if float(item.precio_extra or 0) > 0 else '')) for item in elegidos if item.componente)}")
            grupos_meta.append({
                "grupo": grupo,
                "opciones": [
                    {"combo_item_id": item.id, "producto_id": item.producto_id,
                     **metadata_componente_combo(item, producto.proveedor_despachador_id),
                     "grupo_id": item.combo_group_id,
                     "nombre": item.componente.nombre if item.componente else "",
                     "cantidad": item.cantidad,
                     "grupo_orden": item.grupo.orden if item.grupo else 0,
                     "precio_extra": float(item.precio_extra or 0),
                     "extra_total": float(item.precio_extra or 0),
                     "notas_preparacion": item.notas_preparacion or ""}
                    for item in elegidos
                ],
            })
            if opciones and opciones[0].grupo:
                grupos_meta[-1]["grupo_id"] = opciones[0].grupo.id
                grupos_meta[-1]["tipo"] = "seleccion"
                grupos_meta[-1]["orden"] = opciones[0].grupo.orden
                grupos_meta[-1]["max_selecciones"] = opciones[0].grupo.max_selecciones
    grupos_meta.sort(key=lambda g: (g.get("orden") or 0, g.get("grupo") or ""))
    extras_total = round(sum(
        option.get("extra_total", 0)
        for group in grupos_meta
        for option in (group.get("opciones") or [])
    ), 2)
    return " | ".join(resumen), {
        "combo": {
            "extras_total": extras_total,
            "componentes": [
                {"combo_item_id": item.id, "producto_id": item.producto_id,
                 **metadata_componente_combo(item, producto.proveedor_despachador_id),
                 "grupo_id": item.combo_group_id,
                 "nombre": item.componente.nombre if item.componente else "",
                 "cantidad": item.cantidad, "fijo": True,
                 "grupo": item.grupo.nombre_publico if item.grupo else "Base incluida",
                 "grupo_orden": item.grupo.orden if item.grupo else 0,
                 "notas_preparacion": item.notas_preparacion or ""}
                for item in fijos
            ],
            "selecciones": grupos_meta,
        }
    }


def _producto_requiere_flujo_operativo(producto):
    """Productos que NO pueden venderse directamente en POS porque exigen flujo
    de preparación operativo (programados o despachados por proveedor)."""
    if (producto.tipo_entrega or "inmediato") != "inmediato":
        return True
    if producto.proveedor_despachador_id:
        return True
    if producto.es_combo:
        for combo_item in producto.combo_items:
            componente = combo_item.componente
            if componente and (componente.tipo_entrega or "inmediato") != "inmediato":
                return True
    return False


def pos_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.rol not in ROLES_POS:
            flash("Acceso restringido.", "danger")
            return redirect(url_for("public.index"))
        if current_user.rol == "admin" and not AdminFeature.tiene_acceso(current_user.id, "pos"):
            flash("No tienes acceso al módulo «pos».", "warning")
            return redirect(url_for("admin.dashboard"))
        return f(*args, **kwargs)
    return decorated


# ─── PANTALLA PRINCIPAL POS ──────────────────

@pos_bp.route("/")
@pos_required
def venta():
    categorias = Categoria.query.filter_by(activo=True).order_by(Categoria.orden.asc(), Categoria.nombre.asc()).all()
    categoria_id = request.args.get("categoria", type=int)
    query = Product.query.filter_by(activo=True)
    if categoria_id:
        query = query.filter_by(categoria_id=categoria_id)
    productos = query.order_by(Product.categoria_id.asc(), Product.nombre.asc()).all()
    clientes = User.query.filter_by(rol="cliente", activo=True)\
                         .order_by(User.nombre).all()
    pos_combos = {
        str(producto.id): _pos_combo_config(producto)
        for producto in productos
        if producto.es_combo
    }
    pos_product_options = {}
    for producto in productos:
        option_config = _pos_product_option_config(producto)
        if option_config["grupos"]:
            pos_product_options[str(producto.id)] = option_config
    return render_template("pos/venta.html",
                           productos=productos,
                           categorias=categorias,
                           categoria_activa=categoria_id,
                           clientes=clientes,
                           pos_combos=pos_combos,
                           pos_product_options=pos_product_options,
                           pos_max_qty=_pos_max_qty())


# ─── BUSCAR PRODUCTO (AJAX) ──────────────────

@pos_bp.route("/buscar")
@pos_required
def buscar_producto():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    productos = Product.query.filter(
        Product.nombre.ilike(f"%{q}%"),
        Product.activo == True
    ).limit(10).all()
    return jsonify([
        {"id": p.id, "nombre": p.nombre,
         "precio": float(p.precio),
         "stock": p.combo_stock_total if p.es_combo else p.stock_total,
         "es_combo": p.es_combo}
        for p in productos
    ])


# ─── PROCESAR VENTA ──────────────────────────

@pos_bp.route("/cobrar", methods=["POST"])
@pos_required
def cobrar():
    """
    Recibe JSON con:
      {
        "items": [{"producto_id": 1, "cantidad": 2}, ...],
        "metodo_pago": "efectivo",
        "descuento_manual": 5.00,
        "cliente_id": 3,          # opcional
        "notas": "...",
        "cupon_codigo": "...",    # opcional
      }
    """
    # ── Idempotency guard ────────────────────────────────────────
    # Para POS la key se usa para evitar que un cajero pulse "Cobrar" dos
    # veces y se generen dos pedidos por la misma venta.
    idem_key = request_idempotency_key("pos", auto_seed=str(current_user.id))
    body_h = request_body_hash()
    prev = IdempotencyKey.query.filter_by(scope="pos", key=idem_key).first()
    if prev:
        if prev.request_hash != body_h:
            return jsonify({
                "ok": False,
                "msg": "Idempotency-Key duplicada con datos distintos",
            }), 409
        try:
            cached = json.loads(prev.response_body or "{}")
        except (json.JSONDecodeError, TypeError):
            cached = {}
        return jsonify(cached), prev.response_status

    data = request.json or {}
    items_data = data.get("items", [])
    metodo_pago = normalizar_metodo_pago(data.get("metodo_pago"))
    try:
        descuento_manual = float(data.get("descuento_manual", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "Descuento manual inválido"}), 400
    if not math.isfinite(descuento_manual) or descuento_manual < 0:
        return jsonify({"ok": False, "msg": "Descuento manual inválido"}), 400
    cliente_id = data.get("cliente_id")
    notas = str(data.get("notas", "") or "").strip()
    cupon_codigo = data.get("cupon_codigo", "").strip().upper()

    if not isinstance(items_data, list) or not items_data:
        return jsonify({"ok": False, "msg": "No hay ítems en la venta"}), 400
    if len(items_data) > 100:
        return jsonify({"ok": False, "msg": "Demasiadas líneas en una sola venta"}), 400
    if len(notas) > 1000:
        return jsonify({"ok": False, "msg": "Las notas no pueden superar 1000 caracteres"}), 400
    max_qty = _pos_max_qty()

    # Verificar cliente
    if cliente_id:
        cliente = db.session.get(User, cliente_id)
        if not cliente or cliente.rol != "cliente" or not cliente.activo:
            return jsonify({"ok": False, "msg": "Cliente no encontrado"}), 400
    else:
        cliente = _get_or_create_cliente_anonimo()

    # Procesar items
    items_procesados = []
    subtotal = 0.0
    for item_d in items_data:
        try:
            pid = int(item_d["producto_id"])
            cantidad = int(item_d.get("cantidad", 1))
        except (KeyError, ValueError, TypeError):
            return jsonify({"ok": False, "msg": "Formato de ítem inválido"}), 400
        if cantidad <= 0:
            continue
        if cantidad > max_qty:
            return jsonify({"ok": False, "msg": f"Máximo {max_qty} unidades por línea"}), 400
        p = db.session.get(Product, pid)
        if not p or not p.activo:
            return jsonify({"ok": False, "msg": f"Producto {pid} no disponible"}), 400
        if (p.modalidad_entrega or "ambas") == "delivery":
            return jsonify({
                "ok": False,
                "msg": f"'{p.nombre}' es exclusivo de delivery y no puede cerrarse como venta presencial.",
            }), 400
        if _producto_requiere_flujo_operativo(p):
            return jsonify({
                "ok": False,
                "msg": (
                    f"'{p.nombre}' requiere cocina/proveedor o entrega programada. "
                    "Crea el pedido desde el flujo online para asignarlo al rol correcto."
                ),
            }), 400
        # Solo validar stock para productos de entrega inmediata
        if p.tipo_entrega == "inmediato" and not p.disponible_para_venta(cantidad):
            return jsonify({"ok": False, "msg": f"Stock insuficiente para '{p.nombre}'"}), 400
        item_notas = None
        item_metadata = None
        if p.es_combo:
            try:
                item_notas, item_metadata = _combo_order_payload(
                    p,
                    item_d.get("combo_item_ids") or [],
                )
            except ValueError as exc:
                return jsonify({"ok": False, "msg": str(exc)}), 400
        raw_product_options = item_d.get("opciones_producto") or {}
        if not isinstance(raw_product_options, dict):
            return jsonify({
                "ok": False,
                "msg": f"{p.nombre}: la personalización no tiene un formato válido.",
            }), 400
        presentation, presentation_error = validate_product_presentation_selection(
            p, item_d.get("presentation_id")
        )
        if presentation_error:
            return jsonify({"ok": False, "msg": f"{p.nombre}: {presentation_error}"}), 400
        selected_options, option_rows, option_unit, option_error = (
            validate_product_option_selection(
                p, raw_product_options, presentation
            )
        )
        if option_error:
            return jsonify({"ok": False, "msg": f"{p.nombre}: {option_error}"}), 400
        item_metadata = item_metadata or {}
        flavor_rows = [row for row in option_rows if row.get("tipo") == "sabor"]
        extras_rows = [row for row in option_rows if row.get("tipo") != "sabor"]
        if flavor_rows:
            item_metadata["sabores"] = {"opciones": flavor_rows}
        if extras_rows:
            item_metadata["extras"] = {
                "total_unitario": option_unit,
                "opciones": extras_rows,
            }
        if presentation:
            item_metadata["presentacion"] = presentation_metadata(presentation)
        precio_venta = (
            float(p.precio_combo_para_seleccion(item_d.get("combo_item_ids") or []))
            if p.es_combo else float(p.precio_final)
        )
        precio_venta += option_unit + (
            presentation.precio_extra_float if presentation else 0.0
        )
        precio_venta = round(precio_venta, 2)
        item_total = round(precio_venta * cantidad, 2)
        subtotal += item_total
        items_procesados.append({"producto": p, "cantidad": cantidad, "subtotal": item_total,
                                 "precio_unit": precio_venta,
                                 "combo_item_ids": item_d.get("combo_item_ids") or [],
                                 "opciones_producto": selected_options,
                                 "presentation_id": presentation.id if presentation else None,
                                 "combo_notas": item_notas,
                                 "combo_metadata": item_metadata})

    # Resolver cupón — validar sin registrar uso todavía
    cupon = None
    if cupon_codigo:
        cupon = Coupon.query.filter_by(codigo=cupon_codigo).first()
        if not cupon:
            return jsonify({"ok": False, "msg": "Cupón no encontrado"}), 400
        ok_c, msg_c = cupon.es_valido()
        if not ok_c:
            return jsonify({"ok": False, "msg": msg_c}), 400

    # Motor de pricing unificado (caps + mínimos igual que web y bot)
    try:
        precio = calcular_precio(
            items_procesados, subtotal,
            cupon=cupon,
            descuento_manual=descuento_manual,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "msg": str(exc)}), 400
    total = precio.total
    descuento_total = precio.descuento_total

    from store_config import get_service_commission
    service_fee = get_service_commission(total)

    # Crear pedido
    pedido = Order(
        numero_pedido=Order.generar_numero("presencial"),
        cliente_id=cliente.id,
        estado="entregado",
        origen="presencial",
        subtotal=subtotal,
        descuento=descuento_total,
        total=total,
        service_commission_pct=service_fee["pct"],
        service_commission_amount=service_fee["amount"],
        merchant_net_amount=service_fee["merchant_net"],
        cupon_id=cupon.id if cupon else None,
        metodo_pago=metodo_pago,
        direccion_entrega="Tienda física",
        notas=notas,
        es_entrega_epicentro=True,
        cajero_id=current_user.id,
        entregado_en=utcnow(),
        tipo_entrega_cliente="recogida",
    )
    db.session.add(pedido)
    db.session.flush()
    registrar_pedido_creado(
        pedido,
        actor_id=current_user.id,
        canal="pos",
        detalle="venta presencial",
        metadata={"estado_inicial": pedido.estado},
    )
    registrar_pago_pedido(pedido, actor_id=current_user.id, canal="pos", detalle="venta presencial")

    for item in items_procesados:
        item_notas = item.get("combo_notas")
        item_metadata = item.get("combo_metadata")
        oi = OrderItem(
            pedido_id=pedido.id,
            producto_id=item["producto"].id,
            cantidad=item["cantidad"],
            precio_unit=item.get("precio_unit", item["producto"].precio_final),
            subtotal=item["subtotal"],
            notas=item_notas,
            metadata_json=json.dumps(
                metadata_item_pedido(item["producto"], item_metadata or {}),
                ensure_ascii=False,
            ),
        )
        db.session.add(oi)
        if item["producto"].tipo_entrega == "inmediato":
            try:
                if item["producto"].es_combo:
                    item["producto"].descontar_stock_combo(item["cantidad"], item.get("combo_item_ids") or [])
                else:
                    item["producto"].descontar_stock(item["cantidad"])
            except ValueError as e:
                db.session.rollback()
                return jsonify({"ok": False, "msg": str(e)}), 400

    # Registrar uso del cupón DESPUÉS de que el pedido existe
    if cupon:
        try:
            cupon.registrar_uso()
        except ValueError as e:
            db.session.rollback()
            return jsonify({"ok": False, "msg": str(e)}), 400

    # Puntos si el cliente es registrado (no anónimo) — lee ratio desde BD igual que bot y web
    puntos_ganados = 0
    if cliente_id and cliente.rol == "cliente":
        puntos_ganados = calcular_puntos_ganados(total)
        pedido.puntos_ganados = puntos_ganados
        if puntos_ganados > 0:
            cliente.sumar_puntos(
                puntos_ganados,
                pedido_id=pedido.id,
                descripcion=f"Venta presencial {pedido.numero_pedido}",
            )

    registrar_ingreso(total, f"Venta presencial {pedido.numero_pedido}",
                      categoria="venta_presencial", pedido_id=pedido.id,
                      registrado_por=current_user.id)

    respuesta_payload = {
        "ok": True,
        "numero_pedido": pedido.numero_pedido,
        "total": total,
        "puntos_ganados": puntos_ganados,
        "pedido_id": pedido.id,
    }
    db.session.add(IdempotencyKey(
        scope="pos",
        key=idem_key,
        request_hash=body_h,
        response_status=200,
        response_body=json.dumps(respuesta_payload, default=str),
        order_id=pedido.id,
        user_id=current_user.id,
        expira_en=utcnow() + IDEMPOTENCY_TTL,
    ))

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("pos.cobrar commit error: %s", exc)
        return jsonify({"ok": False, "msg": "Error al guardar la venta. Inténtalo de nuevo."}), 500

    # Notificación push a admins (venta POS)
    try:
        from push_service import notify_new_order
        notify_new_order(pedido)
    except Exception:
        current_app.logger.exception("No se pudo enviar push de venta POS %s", pedido.id)

    return jsonify(respuesta_payload)


# ─── HISTORIAL POS ───────────────────────────

@pos_bp.route("/historial")
@pos_required
def historial():
    ventas = Order.query.filter_by(origen="presencial")\
                        .order_by(Order.creado_en.desc()).limit(100).all()
    return render_template("pos/historial.html", ventas=ventas)


# ─── DEVOLVER VENTA ──────────────────────────

@pos_bp.route("/devolver/<int:pedido_id>", methods=["POST"])
@pos_required
def devolver_venta(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.origen != "presencial":
        flash("Solo se pueden devolver ventas presenciales desde aquí.", "warning")
        return redirect(url_for("pos.historial"))
    if pedido.estado == "cancelado":
        flash("Este pedido ya fue cancelado.", "info")
        return redirect(url_for("pos.historial"))

    motivo = request.form.get("motivo", "Devolución").strip()

    try:
        cancelar_pedido_operativo(
            pedido,
            actor_id=current_user.id,
            canal="pos",
            detalle=motivo,
            forzar_desde_entregado=True,
        )
        db.session.commit()
        flash(f"Venta {pedido.numero_pedido} devuelta. Stock repuesto.", "success")
    except ValueError as e:
        db.session.rollback()
        flash(str(e), "danger")

    return redirect(url_for("pos.historial"))


# ─── REGISTRO MANUAL DE ENTRADA/SALIDA ───────

@pos_bp.route("/movimiento", methods=["POST"])
@pos_required
def movimiento_manual():
    """Registrar entrada o salida de dinero sin pedido (arqueo, gastos menores)."""
    tipo = request.form.get("tipo")
    concepto = request.form.get("concepto", "").strip()
    categoria = request.form.get("categoria", "general")
    try:
        monto = float(request.form.get("monto", 0))
    except (ValueError, TypeError):
        monto = 0

    if tipo not in ("ingreso", "egreso") or monto <= 0 or not concepto:
        flash("Datos inválidos.", "danger")
        return redirect(url_for("pos.venta"))

    entry = Caja(tipo=tipo, categoria=categoria, monto=monto,
                 concepto=concepto, registrado_por=current_user.id)
    db.session.add(entry)
    db.session.commit()
    flash(f"{tipo.capitalize()} de €{monto:.2f} registrado.", "success")
    return redirect(url_for("pos.venta"))


# ─── TICKET (JSON para imprimir) ─────────────

@pos_bp.route("/ticket/<int:pedido_id>")
@login_required
def ticket(pedido_id):
    """El ticket para pegar al pedido tiene que estar disponible para todo
    el staff operativo — cocina, preparación, repartidor, admin y super_admin.
    No es una operación destructiva; solo renderiza para imprimir."""
    pedido = get_or_404(Order, pedido_id)
    if not can_read_order_ticket(current_user, pedido):
        flash("No tienes acceso a este ticket.", "danger")
        destino = "repartidor.ruta" if current_user.rol == "repartidor" else "public.index"
        return redirect(url_for(destino))
    return render_template(
        "pos/ticket.html",
        pedido=pedido,
        es_reimpresion=request.args.get("reprint") == "1",
    )


# ─── HELPERS ─────────────────────────────────

def _get_or_create_cliente_anonimo():
    """Cliente genérico para ventas presenciales sin cuenta."""
    EMAIL_ANONIMO = SiteConfig.get("EMAIL_ANONIMO_PRESENCIAL", "cliente.presencial@oxidian.internal")
    cliente = User.query.filter_by(email=EMAIL_ANONIMO).first()
    if not cliente:
        cliente = User(nombre="Cliente Presencial", email=EMAIL_ANONIMO, rol="cliente")
        cliente.set_password("no-login-posible-" + EMAIL_ANONIMO)
        cliente.activo = False  # no puede hacer login
        db.session.add(cliente)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            cliente = User.query.filter_by(email=EMAIL_ANONIMO).first()
            if not cliente:
                raise
    return cliente
