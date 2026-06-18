import logging
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from functools import wraps
from extensions import db, get_or_404
from models import (Order, OrderEvent, OrderProviderStatus, Product, Proveedor,
                    ProveedorProducto, User, utcnow)
from services import (lineas_proveedor_pedido, registrar_evento_pedido,
                       es_pedido_solo_bar, distribuir_repartidor,
                       cancelar_pedido_operativo)

proveedor_bp = Blueprint("proveedor", __name__)
logger = logging.getLogger(__name__)

ROLES_PROVEEDOR = {"proveedor", "admin", "super_admin"}
_ESTADOS_ACTIVOS = ("pendiente", "armando")


def proveedor_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.rol not in ROLES_PROVEEDOR:
            flash("Acceso restringido.", "danger")
            return redirect(url_for("public.index"))
        return f(*args, **kwargs)
    return decorated


def _proveedor_id_efectivo():
    """Devuelve el proveedor (entidad) al que pertenece el usuario actual.

    Para admin/super_admin sin un proveedor enlazado, retorna None (no filtra y
    el listado muestra todos los proveedores). Para un user con rol='proveedor',
    retorna su `User.proveedor_id` (FK a tabla `proveedores`)."""
    if current_user.rol in ("admin", "super_admin") and not current_user.proveedor_id:
        return None
    return current_user.proveedor_id


def _notificar_preparador(pedido, titulo, mensaje):
    if not pedido.preparador_id:
        return
    try:
        from push_service import notify_user
        notify_user(
            pedido.preparador_id,
            titulo,
            mensaje,
            url="/preparador/pedidos",
        )
    except Exception:
        logger.exception("No se pudo notificar al preparador del pedido %s", pedido.id)


@proveedor_bp.route("/pedidos")
@proveedor_required
def pedidos():
    prov_id = _proveedor_id_efectivo()

    if prov_id:
        base = Order.query.join(OrderProviderStatus).filter(
            Order.estado.in_(_ESTADOS_ACTIVOS),
            OrderProviderStatus.proveedor_id == prov_id,
        )
    else:
        base = Order.query.join(OrderProviderStatus).filter(
            Order.estado.in_(_ESTADOS_ACTIVOS),
        ).distinct()

    pendientes = base.filter(Order.estado == "pendiente").order_by(Order.creado_en).all()
    armando    = base.filter(Order.estado == "armando").order_by(Order.creado_en).all()

    return render_template(
        "proveedor/pedidos.html",
        pendientes=pendientes,
        armando=armando,
        prov_id=prov_id,
        lineas_proveedor_pedido=lineas_proveedor_pedido,
        proveedores=(
            Proveedor.query.filter_by(activo=True).order_by(Proveedor.nombre).all()
            if not prov_id else []
        ),
    )


@proveedor_bp.route("/pedidos/<int:pedido_id>/preparado", methods=["POST"])
@proveedor_required
def marcar_preparado(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.estado not in _ESTADOS_ACTIVOS:
        flash("Este pedido ya no está activo.", "warning")
        return redirect(url_for("proveedor.pedidos"))
    prov_id = _proveedor_id_efectivo()
    if not prov_id and current_user.rol in ("admin", "super_admin"):
        prov_id = request.form.get("proveedor_id", type=int)

    estados_q = OrderProviderStatus.query.filter_by(pedido_id=pedido.id)
    estados_totales = estados_q.count()
    if not prov_id and estados_totales > 1:
        flash("Selecciona el proveedor concreto que confirmó su preparación.", "warning")
        return redirect(url_for("proveedor.pedidos"))
    if prov_id:
        estados_q = estados_q.filter_by(proveedor_id=prov_id)
    estados = estados_q.with_for_update().all()
    if not estados:
        if _proveedor_id_efectivo():
            flash("Este pedido no contiene tus productos.", "danger")
        else:
            flash("Este pedido no tiene proveedores pendientes registrados.", "warning")
        return redirect(url_for("proveedor.pedidos"))

    if all(estado.preparado for estado in estados):
        flash(f"Pedido {pedido.numero_pedido} ya estaba marcado como preparado.", "info")
        return redirect(url_for("proveedor.pedidos"))

    ahora = utcnow()
    for estado in estados:
        estado.preparado = True
        estado.preparado_en = ahora
        estado.actualizado_por = current_user.id
    db.session.flush()
    estado_anterior = pedido.estado
    registrar_evento_pedido(
        pedido,
        "proveedor_preparado",
        actor_id=current_user.id,
        estado_anterior=estado_anterior,
        estado_nuevo=pedido.estado,
        canal="proveedor",
        metadata={"proveedor_ids": [estado.proveedor_id for estado in estados]},
    )

    # Auto-advance: si el pedido es 100% del bar y TODOS sus OrderProviderStatus
    # están preparados, no hay preparador interno que pueda marcarlo listo —
    # avanzamos el estado nosotros y asignamos repartidor.
    db.session.expire(pedido, ["estados_proveedor"])
    todos_listos = bool(pedido.estados_proveedor) and not pedido.proveedores_pendientes
    if todos_listos and es_pedido_solo_bar(pedido) and pedido.estado in ("pendiente", "armando"):
        registrar_evento_pedido(
            pedido,
            "estado_cambiado",
            actor_id=current_user.id,
            estado_anterior=pedido.estado,
            estado_nuevo="listo",
            canal="proveedor",
            detalle="Auto-advance: pedido 100% del bar con todos los proveedores listos.",
        )
        pedido.estado = "listo"
        try:
            distribuir_repartidor(pedido)
        except Exception:
            logger.exception("No se pudo asignar repartidor automático al pedido %s", pedido.id)
    try:
        db.session.commit()
        try:
            from push_service import notify_roles
            notify_roles(
                ["preparacion"],
                "Proveedor listo",
                f"El proveedor confirmó su parte del pedido #{pedido.numero_pedido}.",
                url="/preparador/pedidos",
            )
        except Exception:
            logger.exception("No se pudo avisar a preparación del pedido %s", pedido.id)
        flash(f"Pedido {pedido.numero_pedido} marcado como preparado.", "success")
    except Exception as exc:
        db.session.rollback()
        logger.exception("Error marcando pedido %s como preparado", pedido_id)
        flash(f"Error al guardar: {exc}", "danger")
        return redirect(url_for("proveedor.pedidos"))
    _notificar_preparador(
        pedido,
        "Proveedor listo",
        f"#{pedido.numero_pedido}: la preparación externa está lista.",
    )
    return redirect(url_for("proveedor.pedidos"))


@proveedor_bp.route("/finanzas")
@proveedor_required
def finanzas():
    """Resumen contable del bar: vendido, cancelado, extraviado y reparto del
    dinero entre el bar y el marketplace según el modelo_acuerdo del proveedor."""
    from datetime import date, datetime, timedelta
    from decimal import Decimal
    from models import Proveedor as _Prov, ProveedorProducto as _ProvProd

    prov_id = _proveedor_id_efectivo()
    es_admin_general = not prov_id and current_user.rol in ("admin", "super_admin")
    bar = db.session.get(_Prov, prov_id) if prov_id else None
    if not bar and not es_admin_general:
        flash("Tu cuenta no está enlazada a ningún bar.", "warning")
        return redirect(url_for("public.index"))
    # Si un admin general llega aquí sin bar, el panel /proveedor/finanzas
    # no es la herramienta correcta: tiene una vista global mejor en
    # /admin/liquidacion-proveedores. Redirigimos con mensaje claro.
    if es_admin_general and not request.args.get("forzar"):
        flash(
            "Esta vista es la del bar. Para el resumen global por bar usa "
            "Liquidación de proveedores.",
            "info",
        )
        return redirect(url_for("admin.liquidacion_proveedores"))

    # Selector de fechas (default último mes)
    fecha_inicio_str = (request.args.get("fecha_inicio") or "").strip()
    fecha_fin_str = (request.args.get("fecha_fin") or "").strip()
    try:
        fecha_inicio = (datetime.strptime(fecha_inicio_str, "%Y-%m-%d").date()
                        if fecha_inicio_str else date.today().replace(day=1))
        fecha_fin = (datetime.strptime(fecha_fin_str, "%Y-%m-%d").date()
                     if fecha_fin_str else date.today())
    except ValueError:
        flash("Fechas inválidas, mostrando mes actual.", "warning")
        fecha_inicio = date.today().replace(day=1)
        fecha_fin = date.today()
    if fecha_inicio > fecha_fin:
        fecha_inicio, fecha_fin = fecha_fin, fecha_inicio

    # Trae pedidos con OPS del bar en el rango (vendidos = entregados,
    # cancelados normales y extravíos van por OrderEvent posterior).
    q = (
        Order.query
        .join(OrderProviderStatus, OrderProviderStatus.pedido_id == Order.id)
        .filter(db.func.date(Order.creado_en) >= fecha_inicio)
        .filter(db.func.date(Order.creado_en) <= fecha_fin)
    )
    if bar:
        q = q.filter(OrderProviderStatus.proveedor_id == bar.id)
    pedidos = q.order_by(Order.creado_en.desc()).all()

    # Buscar qué pedidos tienen evento extraviado
    extraviados_ids = set()
    if pedidos:
        ids = [p.id for p in pedidos]
        ext_eventos = OrderEvent.query.filter(
            OrderEvent.tipo == "pedido_extraviado",
            OrderEvent.pedido_id.in_(ids),
        ).all()
        extraviados_ids = {e.pedido_id for e in ext_eventos}

    # Clasificación + cálculo
    def _coste_pedido_bar(pedido, bar_id):
        """Coste del pedido para el bar = suma(precio_costo congelado × cantidad).
        Si no hay congelado, fallback a ProveedorProducto vivo."""
        total = Decimal("0")
        for oi in pedido.items:
            metadata = oi.get_metadata() if hasattr(oi, "get_metadata") else {}
            combo = (metadata or {}).get("combo") or {}
            componentes = list(combo.get("componentes") or [])
            for grp in combo.get("selecciones") or []:
                componentes.extend(grp.get("opciones") or [])
            if componentes:
                # Es combo: sumamos precio_costo de cada componente
                for c in componentes:
                    cant = max(1, int(c.get("cantidad") or 1)) * int(oi.cantidad or 1)
                    congelado = c.get("precio_costo_congelado")
                    if congelado is not None:
                        total += Decimal(str(congelado)) * cant
                    elif c.get("producto_id"):
                        fila = _ProvProd.query.filter_by(
                            proveedor_id=bar_id, producto_id=c["producto_id"]).first()
                        if fila and fila.precio_costo is not None:
                            total += Decimal(str(fila.precio_costo)) * cant
            else:
                # Item suelto del bar
                fila = _ProvProd.query.filter_by(
                    proveedor_id=bar_id, producto_id=oi.producto_id).first()
                if fila and fila.precio_costo is not None:
                    total += Decimal(str(fila.precio_costo)) * int(oi.cantidad or 1)
        return total

    vendidos, cancelados, extraviados = [], [], []
    pvp_vendido = Decimal("0")
    coste_bar_vendido = Decimal("0")
    pvp_extraviado = Decimal("0")
    coste_bar_extraviado = Decimal("0")
    pvp_cancelado = Decimal("0")

    for p in pedidos:
        # Determinar el bar específico de cada OPS para cálculos correctos
        # cuando admin general mira sin filtro
        ops_bar_ids = [ops.proveedor_id for ops in p.estados_proveedor]
        bar_id_calc = bar.id if bar else (ops_bar_ids[0] if ops_bar_ids else None)
        if not bar_id_calc:
            continue
        coste_p = _coste_pedido_bar(p, bar_id_calc)
        total_p = Decimal(str(p.total or 0))
        item = {
            "pedido": p,
            "total": total_p,
            "coste_bar": coste_p,
        }
        if p.estado == "entregado":
            vendidos.append(item)
            pvp_vendido += total_p
            coste_bar_vendido += coste_p
        elif p.id in extraviados_ids:
            extraviados.append(item)
            pvp_extraviado += total_p
            coste_bar_extraviado += coste_p
        elif p.estado == "cancelado":
            cancelados.append(item)
            pvp_cancelado += total_p
        # En otros estados (pendiente/armando/listo/en_ruta) no entran al
        # resumen — son "en curso" y se ven en /proveedor/pedidos.

    # Liquidación según modelo del bar
    bar_para_calculo = bar if bar else None
    modelo = bar_para_calculo.modelo_acuerdo if bar_para_calculo else "stock_proveedor"
    comision_pct = Decimal(str(bar_para_calculo.comision_pct or 0)) if bar_para_calculo else Decimal("0")

    if modelo == "stock_propio_bar":
        # Nosotros ponemos stock; al bar le pagamos comision% del PVP por preparar.
        a_pagar_bar = (pvp_vendido * comision_pct / Decimal("100")).quantize(Decimal("0.01"))
        ingreso_marketplace = (pvp_vendido - a_pagar_bar).quantize(Decimal("0.01"))
        formula = f"{comision_pct}% del PVP vendido"
    else:
        # Bar pone stock; le pagamos precio_costo por unidad despachada.
        a_pagar_bar = coste_bar_vendido.quantize(Decimal("0.01"))
        ingreso_marketplace = (pvp_vendido - a_pagar_bar).quantize(Decimal("0.01"))
        formula = "Coste registrado por componente"

    return render_template(
        "proveedor/finanzas.html",
        bar=bar,
        es_admin_general=es_admin_general,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        vendidos=vendidos,
        cancelados=cancelados,
        extraviados=extraviados,
        pvp_vendido=pvp_vendido,
        pvp_cancelado=pvp_cancelado,
        pvp_extraviado=pvp_extraviado,
        coste_bar_vendido=coste_bar_vendido,
        coste_bar_extraviado=coste_bar_extraviado,
        a_pagar_bar=a_pagar_bar,
        ingreso_marketplace=ingreso_marketplace,
        formula=formula,
        modelo=modelo,
        comision_pct=comision_pct,
    )


@proveedor_bp.route("/pedidos/<int:pedido_id>/extraviado", methods=["POST"])
@proveedor_required
def marcar_extraviado(pedido_id):
    """El bar declara que un pedido se extravió/perdió. Se cancela el pedido
    (restaura stock al bar) y queda registrado el evento para diferenciarlo
    de una cancelación normal en finanzas."""
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    prov_id = _proveedor_id_efectivo()
    if prov_id:
        ops = OrderProviderStatus.query.filter_by(
            pedido_id=pedido.id, proveedor_id=prov_id
        ).first()
        if not ops:
            flash("Este pedido no es de tu bar.", "danger")
            return redirect(url_for("proveedor.pedidos"))
    if pedido.estado in ("entregado", "cancelado"):
        flash(f"El pedido {pedido.numero_pedido} ya estaba en estado {pedido.estado}.", "warning")
        return redirect(url_for("proveedor.pedidos"))

    motivo = (request.form.get("motivo") or "").strip()[:300]
    # Primero registramos el evento de extravío (queda visible en finanzas)
    registrar_evento_pedido(
        pedido,
        "pedido_extraviado",
        actor_id=current_user.id,
        estado_anterior=pedido.estado,
        estado_nuevo="cancelado",
        canal="proveedor",
        detalle=motivo or "Extravío reportado por el bar",
        metadata={"proveedor_id": prov_id, "motivo": motivo},
    )
    try:
        cancelar_pedido_operativo(
            pedido,
            actor_id=current_user.id,
            canal="proveedor_extravio",
            detalle=f"Extravío reportado por bar: {motivo or 'sin motivo detallado'}",
        )
        db.session.commit()
        flash(f"Pedido {pedido.numero_pedido} marcado como extraviado.", "warning")
    except Exception as exc:
        db.session.rollback()
        logger.exception("Error marcando extravío del pedido %s", pedido_id)
        flash(f"No se pudo registrar el extravío: {exc}", "danger")
    return redirect(url_for("proveedor.pedidos"))


@proveedor_bp.route("/incidencias", methods=["GET", "POST"])
@proveedor_required
def incidencias():
    """Bandeja de incidencias: novedades reportadas por clientes sobre los
    pedidos que despacha el bar del operador (o del admin si no tiene bar)."""
    prov_id = _proveedor_id_efectivo()
    es_admin = current_user.rol in ("admin", "super_admin") and not prov_id

    if request.method == "POST":
        accion = request.form.get("accion", "marcar_atendida")
        if accion == "marcar_atendida":
            evento_id = request.form.get("evento_id", type=int)
            evento = db.session.get(OrderEvent, evento_id) if evento_id else None
            if not evento or evento.tipo != "cliente_reporto_novedad":
                flash("Incidencia no encontrada.", "warning")
                return redirect(url_for("proveedor.incidencias"))
            # Verificar que la incidencia pertenece a un pedido de este bar
            if prov_id:
                ops = OrderProviderStatus.query.filter_by(
                    pedido_id=evento.pedido_id, proveedor_id=prov_id
                ).first()
                if not ops:
                    flash("Esa incidencia no es de un pedido tuyo.", "danger")
                    return redirect(url_for("proveedor.incidencias"))
            from services import registrar_evento_pedido
            registrar_evento_pedido(
                evento.pedido,
                "incidencia_atendida",
                actor_id=current_user.id,
                estado_anterior=evento.pedido.estado,
                estado_nuevo=evento.pedido.estado,
                canal="proveedor",
                detalle=f"Incidencia #{evento.id} marcada como atendida",
                metadata={"incidencia_id": evento.id},
            )
            db.session.commit()
            flash("Incidencia marcada como atendida.", "success")
            return redirect(url_for("proveedor.incidencias"))

    # Listado: novedades cliente cuyas órdenes pertenecen al bar.
    q = (
        OrderEvent.query
        .filter(OrderEvent.tipo == "cliente_reporto_novedad")
        .join(Order, OrderEvent.pedido_id == Order.id)
        .order_by(OrderEvent.creado_en.desc())
    )
    if prov_id:
        # Solo incidencias de pedidos donde el bar participa
        q = q.join(
            OrderProviderStatus,
            (OrderProviderStatus.pedido_id == Order.id) &
            (OrderProviderStatus.proveedor_id == prov_id),
        )
    incidencias_list = q.limit(100).all()

    # Marcar las ya atendidas para resaltarlas suaves: si para esa incidencia
    # existe un evento posterior 'incidencia_atendida' con su id en metadata.
    atendidas_ids = set()
    if incidencias_list:
        ids = [e.id for e in incidencias_list]
        eventos_atendidos = OrderEvent.query.filter(
            OrderEvent.tipo == "incidencia_atendida"
        ).all()
        for ev in eventos_atendidos:
            meta = ev.get_metadata() if hasattr(ev, "get_metadata") else {}
            iid = meta.get("incidencia_id")
            if iid in ids:
                atendidas_ids.add(iid)

    return render_template(
        "proveedor/incidencias.html",
        incidencias=incidencias_list,
        atendidas_ids=atendidas_ids,
        es_admin=es_admin,
    )


@proveedor_bp.route("/inventario", methods=["GET", "POST"])
@proveedor_required
def inventario():
    """Vista del operador del proveedor para ajustar stock y precio_costo de sus SKUs."""
    prov_id = _proveedor_id_efectivo()
    if not prov_id:
        flash("Tu cuenta no está enlazada a ningún proveedor.", "warning")
        return redirect(url_for("public.index"))

    proveedor = db.session.get(Proveedor, prov_id)
    if not proveedor:
        flash("Proveedor no encontrado.", "danger")
        return redirect(url_for("public.index"))

    if request.method == "POST":
        accion = request.form.get("accion", "actualizar_sku")
        if accion == "actualizar_sku":
            fila_id = request.form.get("fila_id", type=int)
            stock = request.form.get("stock", type=int)
            precio_costo = request.form.get("precio_costo")
            fila = db.session.get(ProveedorProducto, fila_id) if fila_id else None
            if not fila or fila.proveedor_id != prov_id:
                flash("SKU no encontrado en tu inventario.", "danger")
                return redirect(url_for("proveedor.inventario"))
            if stock is not None and stock >= 0:
                fila.stock = stock
            try:
                fila.precio_costo = float(precio_costo) if precio_costo else None
            except (TypeError, ValueError):
                fila.precio_costo = None
            try:
                db.session.commit()
                flash(f"«{fila.producto.nombre}» actualizado.", "success")
            except Exception as exc:
                db.session.rollback()
                flash(f"Error al guardar: {exc}", "danger")
            return redirect(url_for("proveedor.inventario"))

    skus = (
        ProveedorProducto.query
        .filter_by(proveedor_id=prov_id)
        .join(Product, ProveedorProducto.producto_id == Product.id)
        .order_by(Product.nombre)
        .all()
    )
    return render_template(
        "proveedor/inventario.html",
        proveedor=proveedor,
        skus=skus,
    )


@proveedor_bp.route("/pedidos/<int:pedido_id>/reabrir", methods=["POST"])
@proveedor_required
def reabrir_preparacion(pedido_id):
    pedido = Order.query.filter_by(id=pedido_id).with_for_update().first_or_404()
    if pedido.estado not in _ESTADOS_ACTIVOS:
        flash("Solo se puede corregir un pedido que siga en preparación.", "warning")
        return redirect(url_for("proveedor.pedidos"))

    prov_id = _proveedor_id_efectivo()
    if not prov_id:
        prov_id = request.form.get("proveedor_id", type=int)

    estados_q = OrderProviderStatus.query.filter_by(pedido_id=pedido.id)
    if prov_id:
        estados_q = estados_q.filter_by(proveedor_id=prov_id)
    estados = estados_q.with_for_update().all()
    if not estados:
        flash("No se encontró la preparación de proveedor que quieres corregir.", "warning")
        return redirect(url_for("proveedor.pedidos"))
    if len(estados) > 1:
        flash("Selecciona el proveedor concreto que debe reabrir su preparación.", "warning")
        return redirect(url_for("proveedor.pedidos"))
    if not estados[0].preparado:
        flash("Esta preparación ya estaba pendiente.", "info")
        return redirect(url_for("proveedor.pedidos"))

    estado = estados[0]
    estado.preparado = False
    estado.preparado_en = None
    estado.actualizado_por = current_user.id
    registrar_evento_pedido(
        pedido,
        "proveedor_reabierto",
        actor_id=current_user.id,
        estado_anterior=pedido.estado,
        estado_nuevo=pedido.estado,
        canal="proveedor",
        metadata={"proveedor_ids": [estado.proveedor_id]},
    )
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception("Error reabriendo proveedor del pedido %s", pedido_id)
        flash(f"Error al guardar: {exc}", "danger")
        return redirect(url_for("proveedor.pedidos"))

    flash(f"Preparación de {pedido.numero_pedido} reabierta.", "warning")
    _notificar_preparador(
        pedido,
        "Proveedor reabrió preparación",
        f"#{pedido.numero_pedido}: la preparación externa vuelve a estar pendiente.",
    )
    return redirect(url_for("proveedor.pedidos"))
