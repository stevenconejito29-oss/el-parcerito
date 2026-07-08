"""
RUTAS CHECKOUT MEJORADO - Sistema El Parcerito
==============================================

Rutas para checkout, validación de carrito y órdenes.
Implementa validaciones de combos, delivery/recogida, puntos, etc.
"""

from flask import Blueprint, request, jsonify, session
from decimal import Decimal
import logging

from extensions import db, csrf
from models import Product, Order, OrderItem, User, Coupon, Stock
from services_core import (
    ConfigService, ComboService, DeliveryService, PointsService,
    StockService, ValidationService, ResultadoOperacion
)
from security_decorators import (
    respuesta_exitosa, respuesta_error, json_requerido,
    validar_parametros, manejar_errores_operacion,
    limitar_por_usuario, auditar_accion
)

logger = logging.getLogger(__name__)

checkout_bp = Blueprint('checkout', __name__, url_prefix='/api/checkout')


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS DE VALIDACIÓN
# ═══════════════════════════════════════════════════════════════════

@checkout_bp.route('/validar-combo', methods=['POST'])
@json_requerido
@manejar_errores_operacion
def validar_combo(data):
    """
    Valida un combo específico con sus selecciones
    
    POST /api/checkout/validar-combo
    {
      "combo_id": 123,
      "selecciones": {"grupo_1": [1, 2], "grupo_2": 3}
    }
    """
    combo_id = data.get('combo_id')
    selecciones = data.get('selecciones', {})

    if not combo_id:
        return respuesta_error("combo_id requerido", "COMBO_REQUERIDO")

    # Validar estructura del combo
    combo = Product.query.get(combo_id)
    if not combo or not combo.es_combo:
        return respuesta_error("Combo no válido", "COMBO_INVALID")

    resultado = ComboService.validar_estructura(combo)
    if not resultado.exito:
        return respuesta_error(resultado.error, resultado.codigo)

    # Validar selecciones del cliente
    resultado = ComboService.validar_selecciones_cliente(combo_id, selecciones)
    if not resultado.exito:
        return respuesta_error(resultado.error, resultado.codigo)

    return respuesta_exitosa(resultado.data, "Combo válido")


@checkout_bp.route('/validar-modalidad-entrega', methods=['POST'])
@json_requerido
@limitar_por_usuario(300)
@manejar_errores_operacion
def validar_modalidad_entrega(data):
    """
    Valida que carrito soporta modalidad de entrega solicitada
    
    POST /api/checkout/validar-modalidad-entrega
    {
      "carrito": {"prod_1": 2, "prod_2": {"selecciones": {...}, "qty": 1}},
      "tipo_entrega": "delivery"
    }
    """
    carrito = data.get('carrito', {})
    tipo_entrega = data.get('tipo_entrega', 'delivery')

    if not carrito:
        return respuesta_error("Carrito vacío", "CARRITO_VACIO")

    # Extraer solo IDs de productos del carrito
    # (puede haber items simples o combos)
    producto_ids = []
    for key, value in carrito.items():
        try:
            if isinstance(value, dict):
                # Es combo con selecciones
                producto_ids.append(int(key))
            else:
                # Es producto simple
                producto_ids.append(int(key))
        except (ValueError, TypeError):
            continue

    if not producto_ids:
        return respuesta_error("Carrito inválido", "CARRITO_INVALID")

    # Validar modalidad
    resultado = DeliveryService.validar_modalidad_para_carrito(
        dict(zip(producto_ids, [1]*len(producto_ids))),
        tipo_entrega
    )

    if not resultado.exito:
        return jsonify({
            'ok': False,
            'error': resultado.error,
            'codigo': resultado.codigo,
            'productos_problema': resultado.data.get('productos_problema', [])
        }), 400

    return respuesta_exitosa({"valido": True}, "Modalidad soportada")


@checkout_bp.route('/opciones-entrega', methods=['GET'])
@limitar_por_usuario(300)
@manejar_errores_operacion
def obtener_opciones_entrega():
    """
    Obtiene opciones de entrega disponibles
    
    GET /api/checkout/opciones-entrega
    """
    opciones = DeliveryService.obtener_opciones_disponibles()
    
    return respuesta_exitosa({
        "opciones": opciones,
        "delivery_habilitado": DeliveryService.puede_usar_delivery(),
        "recogida_habilitada": DeliveryService.puede_usar_recogida()
    }, "Opciones cargadas")


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS DE CARRITO
# ═══════════════════════════════════════════════════════════════════

@checkout_bp.route('/carrito/validar-stock', methods=['POST'])
@json_requerido
@limitar_por_usuario(300)
@manejar_errores_operacion
def validar_stock_carrito(data):
    """
    Valida que hay stock disponible para todos los items
    
    POST /api/checkout/carrito/validar-stock
    {
      "carrito": {"prod_1": 2, "combo_1": 1}
    }
    """
    carrito = data.get('carrito', {})

    if not carrito:
        return respuesta_error("Carrito vacío", "CARRITO_VACIO")

    problemas = []

    for prod_id, cantidad in carrito.items():
        try:
            prod_id = int(prod_id)
            cantidad = int(cantidad)
        except (ValueError, TypeError):
            continue

        # Verificar stock disponible
        disponible = StockService.obtener_stock_disponible(prod_id)
        if disponible < cantidad:
            producto = Product.query.get(prod_id)
            problemas.append({
                "producto_id": prod_id,
                "nombre": producto.nombre if producto else f"Producto {prod_id}",
                "disponible": disponible,
                "solicitado": cantidad,
                "falta": cantidad - disponible
            })

    if problemas:
        return jsonify({
            'ok': False,
            'error': f"{len(problemas)} producto(s) sin stock suficiente",
            'codigo': 'STOCK_INSUFICIENTE',
            'productos_problema': problemas
        }), 400

    return respuesta_exitosa({"stock_valido": True}, "Stock disponible")


@checkout_bp.route('/carrito/calcular-total', methods=['POST'])
@json_requerido
@limitar_por_usuario(300)
@manejar_errores_operacion
def calcular_total_carrito(data):
    """
    Calcula total del carrito con descuentos y aplicables de combos
    
    POST /api/checkout/carrito/calcular-total
    {
      "carrito": {
        "1": 2,
        "3": {"selecciones": {"grupo_1": [5, 6]}, "qty": 1}
      },
      "codigo_cupon": "DESC10"
    }
    """
    carrito = data.get('carrito', {})
    codigo_cupon = data.get('codigo_cupon')

    if not carrito:
        return respuesta_error("Carrito vacío", "CARRITO_VACIO")

    total = Decimal('0')
    detalles_items = []
    errores = []

    try:
        for item_key, item_data in carrito.items():
            try:
                prod_id = int(item_key)
            except (ValueError, TypeError):
                continue

            producto = Product.query.get(prod_id)
            if not producto:
                errores.append(f"Producto {prod_id} no existe")
                continue

            # Manejo de cantidad
            if isinstance(item_data, dict):
                # Es combo con selecciones
                cantidad = item_data.get('qty', 1)
                selecciones = item_data.get('selecciones', {})

                # Validar combo
                resultado = ComboService.validar_selecciones_cliente(prod_id, selecciones)
                if not resultado.exito:
                    errores.append(f"Combo {prod_id}: {resultado.error}")
                    continue

                precio = Decimal(str(resultado.data['precio_final']))
            else:
                # Es producto simple
                cantidad = int(item_data) if isinstance(item_data, (int, str)) else 1
                precio = producto.precio

            # Agregar al total
            subtotal = precio * cantidad
            total += subtotal

            detalles_items.append({
                "producto_id": prod_id,
                "nombre": producto.nombre,
                "cantidad": cantidad,
                "precio_unitario": float(precio),
                "subtotal": float(subtotal)
            })

        # Aplicar cupón si existe
        descuento = Decimal('0')
        if codigo_cupon:
            cupon = Coupon.query.filter_by(codigo=codigo_cupon, activo=True).first()
            if cupon:
                if cupon.tipo == 'porcentaje':
                    descuento = total * (Decimal(cupon.valor) / Decimal('100'))
                else:  # fijo
                    descuento = Decimal(str(cupon.valor))
                total -= descuento
            else:
                errores.append(f"Cupón '{codigo_cupon}' no válido")

        # Validar total > 0
        if total <= 0:
            return respuesta_error("Total debe ser mayor a 0", "TOTAL_INVALID")

        if errores:
            return jsonify({
                'ok': False,
                'error': "Carrito con problemas",
                'codigo': 'CARRITO_INVALID',
                'errores': errores,
                'total': float(total),
                'detalles': detalles_items
            }), 400

        return respuesta_exitosa({
            "total": float(total),
            "subtotal": float(total + descuento),
            "descuento": float(descuento),
            "items": detalles_items,
            "cantidad_items": len(detalles_items)
        }, "Total calculado")

    except Exception as e:
        logger.error(f"Error calculando total: {e}", exc_info=True)
        return respuesta_error(str(e), "CALC_ERROR")


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS DE CREACIÓN DE ORDEN
# ═══════════════════════════════════════════════════════════════════

@checkout_bp.route('/crear-orden', methods=['POST'])
@csrf.exempt  # API puede no tener CSRF
@json_requerido
@limitar_por_usuario(60)  # Máximo 60 órdenes/hora
@manejar_errores_operacion
def crear_orden(data):
    """
    Crea una orden completa con validaciones
    
    POST /api/checkout/crear-orden
    {
      "cliente": {
        "nombre": "Juan",
        "email": "juan@email.com",
        "telefono": "1234567890",
        "direccion": "Calle 1"
      },
      "carrito": {...},
      "tipo_entrega": "delivery",
      "metodo_pago": "bizum",
      "codigo_cupon": "DESC10",
      "puntos_a_canjear": 100
    }
    """
    try:
        cliente_data = data.get('cliente', {})
        carrito = data.get('carrito', {})
        tipo_entrega = data.get('tipo_entrega', 'delivery')
        metodo_pago = data.get('metodo_pago', 'efectivo')
        codigo_cupon = data.get('codigo_cupon')
        puntos_a_canjear = data.get('puntos_a_canjear', 0)

        # Validaciones básicas
        if not cliente_data.get('nombre'):
            return respuesta_error("Nombre requerido", "NOMBRE_REQUERIDO")
        if not cliente_data.get('email'):
            return respuesta_error("Email requerido", "EMAIL_REQUERIDO")
        if not cliente_data.get('telefono'):
            return respuesta_error("Teléfono requerido", "TELEFONO_REQUERIDO")
        if not carrito:
            return respuesta_error("Carrito vacío", "CARRITO_VACIO")

        # Validar formato
        if not ValidationService.validar_email(cliente_data['email']):
            return respuesta_error("Email inválido", "EMAIL_INVALID")
        if not ValidationService.validar_telefono(cliente_data['telefono']):
            return respuesta_error("Teléfono inválido", "TELEFONO_INVALID")

        # Validar modalidad
        resultado = DeliveryService.validar_modalidad_para_carrito(carrito, tipo_entrega)
        if not resultado.exito:
            return respuesta_error(resultado.error, resultado.codigo, 400)

        # Validar stock
        resultado = StockService.validar_stock_para_carrito(carrito)
        if not resultado.exito:
            return respuesta_error(resultado.error, resultado.codigo, 400)

        # Obtener o crear usuario
        usuario = User.query.filter_by(email=cliente_data['email']).first()
        if not usuario:
            usuario = User(
                nombre=cliente_data['nombre'],
                email=cliente_data['email'],
                telefono=cliente_data.get('telefono'),
                direccion=cliente_data.get('direccion'),
                rol='cliente',
                password_hash='',  # Sin contraseña para clientes
                activo=True
            )
            db.session.add(usuario)
            db.session.flush()  # Obtener ID sin commit

        # Crear orden
        orden = Order(
            cliente_id=usuario.id,
            tipo_entrega_cliente=tipo_entrega,
            metodo_pago=metodo_pago,
            estado='pendiente',
            direccion_entrega=cliente_data.get('direccion'),
            origen='online'
        )
        db.session.add(orden)
        db.session.flush()

        # Procesar items y deducir stock
        total = Decimal('0')
        for item_key, item_data in carrito.items():
            try:
                prod_id = int(item_key)
            except (ValueError, TypeError):
                continue

            producto = Product.query.get(prod_id)
            if not producto:
                continue

            # Calcular precio y cantidad
            if isinstance(item_data, dict):
                cantidad = item_data.get('qty', 1)
                # Validar combo nuevamente
                resultado = ComboService.validar_selecciones_cliente(
                    prod_id,
                    item_data.get('selecciones', {})
                )
                if not resultado.exito:
                    db.session.rollback()
                    return respuesta_error(resultado.error, resultado.codigo)
                precio = Decimal(str(resultado.data['precio_final']))
            else:
                cantidad = int(item_data)
                precio = producto.precio

            subtotal = precio * cantidad
            total += subtotal

            # Deducir stock
            deduction = StockService.deducir_stock(prod_id, cantidad)
            if not deduction.exito:
                db.session.rollback()
                return respuesta_error(deduction.error, deduction.codigo)

            # Crear OrderItem
            order_item = OrderItem(
                order_id=orden.id,
                producto_id=prod_id,
                cantidad=cantidad,
                precio_unitario=precio,
                metadata_json=json.dumps(
                    {"stock_deducido": deduction.data}
                    if isinstance(item_data, dict) else {}
                )
            )
            db.session.add(order_item)

        # Aplicar cupón
        if codigo_cupon:
            cupon = Coupon.query.filter_by(codigo=codigo_cupon, activo=True).first()
            if cupon:
                if cupon.tipo == 'porcentaje':
                    descuento = total * (Decimal(cupon.valor) / Decimal('100'))
                else:
                    descuento = Decimal(str(cupon.valor))
                total -= descuento
                orden.cupon_id = cupon.id

        # Aplicar canje de puntos
        if puntos_a_canjear > 0:
            resultado = PointsService.realizar_canje_parcial(usuario, puntos_a_canjear)
            if resultado.exito:
                descuento_puntos = PointsService.calcular_descuento_por_puntos(puntos_a_canjear)
                total -= descuento_puntos
                orden.puntos_canjeados = puntos_a_canjear

        # Validar total
        if total <= 0:
            db.session.rollback()
            return respuesta_error("Total inválido", "TOTAL_INVALID")

        # Actualizar orden
        orden.total = total
        puntos_ganados = PointsService.calcular_puntos_por_monto(total)
        orden.puntos_ganados = puntos_ganados
        usuario.puntos += puntos_ganados

        # Commit
        db.session.add(usuario)
        db.session.add(orden)
        db.session.commit()

        logger.info(f"[ORDEN] Creada orden #{orden.id} por cliente {usuario.email}")

        return respuesta_exitosa({
            "orden_id": orden.id,
            "total": float(total),
            "puntos_ganados": puntos_ganados,
            "estado": "pendiente"
        }, "Orden creada exitosamente", 201)

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creando orden: {e}", exc_info=True)
        return respuesta_error(f"Error: {str(e)}", "ORDEN_ERROR", 500)


# ═══════════════════════════════════════════════════════════════════
# HELPER: Validar stock para carrito
# ═══════════════════════════════════════════════════════════════════

def validar_stock_para_carrito(carrito: dict) -> ResultadoOperacion:
    """Helper que valida stock para todos los items del carrito"""
    for item_id, cantidad in carrito.items():
        try:
            prod_id = int(item_id)
            if isinstance(cantidad, dict):
                cantidad = cantidad.get('qty', 1)
            cantidad = int(cantidad)
        except (ValueError, TypeError):
            continue

        disponible = StockService.obtener_stock_disponible(prod_id)
        if disponible < cantidad:
            return ResultadoOperacion(
                False,
                error=f"Stock insuficiente para producto {prod_id}",
                codigo="STOCK_INSUFICIENTE"
            )

    return ResultadoOperacion(True)


# Agregar helper a StockService
StockService.validar_stock_para_carrito = staticmethod(validar_stock_para_carrito)


if __name__ == "__main__":
    print("✓ Blueprint Checkout Cargado")
    print("  - Validación de combos")
    print("  - Validación de modalidad")
    print("  - Cálculo de totales")
    print("  - Creación de órdenes")
