"""
SERVICIOS CORE - Sistema El Parcerito
=======================================

Módulo de servicios centralizados para lógica de negocio reutilizable.
Separa concerns entre routes, modelos y lógica de negocio.

Estructura:
- ConfigService: Manejo de configuración dinámica
- RoleService: Control de acceso y permisos
- ComboService: Validación y cálculo de combos
- StockService: Gestión de inventario FIFO
- OrderService: Creación y manejo de órdenes
- DeliveryService: Lógica de delivery/recogida
- PointsService: Sistema de puntos y canje
- ValidationService: Validaciones centralizadas
"""

from datetime import datetime, time, timedelta
from decimal import Decimal
import logging
from functools import wraps
from enum import Enum

from extensions import db
from models import (
    SiteConfig, Product, ComboGroup, ComboItem, Stock,
    Order, OrderItem, User, Proveedor, Coupon,
    PointsLog, Categoria, ZonaEntrega
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# ENUMS Y CONSTANTES
# ═══════════════════════════════════════════════════════════════════

class RoleEnum(str, Enum):
    """Roles del sistema con jerarquía"""
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    STAFF_COCINA = "cocina"
    STAFF_PREPARACION = "preparacion"
    STAFF_REPARTIDOR = "repartidor"
    CLIENTE = "cliente"

    @property
    def nivel(self) -> int:
        """Jerarquía: mayor = más permisos"""
        hierarchy = {
            "super_admin": 100,
            "admin": 80,
            "preparacion": 40,
            "cocina": 40,
            "repartidor": 30,
            "cliente": 0,
        }
        return hierarchy.get(self.value, 0)


class ModalidadEntregaEnum(str, Enum):
    """Opciones de entrega disponibles"""
    DELIVERY = "delivery"
    RECOGIDA = "recogida"
    AMBAS = "ambas"


class EstadoOrdenEnum(str, Enum):
    """Estados de una orden en el flujo"""
    PENDIENTE = "pendiente"
    ARMANDO = "armando"
    LISTO = "listo"
    EN_RUTA = "en_ruta"
    ENTREGADO = "entregado"
    CANCELADO = "cancelado"


class TipoComboEnum(str, Enum):
    """Tipos de precio para combos"""
    FIJO = "fijo"
    DESCUENTO_PORCENTAJE = "descuento_porcentaje"
    SUMA_COMPONENTES = "suma_componentes"


class ResultadoOperacion:
    """Wrapper para resultados de operaciones con manejo de errores"""
    def __init__(self, exito: bool, data=None, error: str = None, codigo: str = None):
        self.exito = exito
        self.data = data
        self.error = error
        self.codigo = codigo  # Código de error para debugging

    def to_dict(self):
        """Convertir a diccionario para respuestas HTTP"""
        return {
            'ok': self.exito,
            'data': self.data,
            'error': self.error,
            'codigo': self.codigo
        }


# ═══════════════════════════════════════════════════════════════════
# SERVICIO DE CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════════

class ConfigService:
    """Gestiona toda la configuración dinámica del sistema"""

    # Configuraciones críticas con valores por defecto
    CONFIGS_CRITICAS = {
        'nombre_negocio': ('El Parcerito', str),
        'logo_url': ('/static/img/logo.png', str),
        'whatsapp_country_code': ('34', str),
        'moneda': ('USD', str),
        'timezone': ('America/Caracas', str),
        'hora_apertura': ('09:00', str),
        'hora_cierre': ('23:00', str),
        'email_contacto': ('info@parcerito.com', str),
        'color_primario': ('#FF6B35', str),
        'color_secundario': ('#004E89', str),
        'whatsapp_number': ('', str),
        'delivery_habilitado': (True, bool),
        'recogida_habilitada': (True, bool),
        'modo_servicio_habilitado': (False, bool),
        'tasa_puntos_por_monto': (1.0, float),  # 1 punto por $
        'valor_punto_en_monto': (0.1, float),   # 1 punto = $0.1
    }

    @classmethod
    def obtener(cls, clave: str, default=None):
        """Obtener configuración, con fallback a default"""
        config = SiteConfig.query.filter_by(clave=clave).first()
        if config:
            return config.obtener_valor()
        
        # Si no existe y está en CONFIGS_CRITICAS, crear con default
        if clave in cls.CONFIGS_CRITICAS:
            valor_default, tipo = cls.CONFIGS_CRITICAS[clave]
            cls.establecer(clave, valor_default)
            return valor_default
        
        return default

    @classmethod
    def establecer(cls, clave: str, valor):
        """Establecer configuración (crear si no existe)"""
        config = SiteConfig.query.filter_by(clave=clave).first()
        if not config:
            config = SiteConfig(clave=clave, valor=str(valor))
        else:
            config.valor = str(valor)
        
        db.session.add(config)
        db.session.commit()
        logger.info(f"[CONFIG] {clave} = {valor}")
        return config

    @classmethod
    def validar_criticas(cls) -> tuple[bool, list]:
        """Valida que todas las configuraciones críticas existan"""
        faltantes = []
        
        for clave, (default, tipo) in cls.CONFIGS_CRITICAS.items():
            valor = cls.obtener(clave)
            if valor is None:
                cls.establecer(clave, default)
                faltantes.append(clave)
        
        return len(faltantes) == 0, faltantes

    @classmethod
    def obtener_todas_criticas(cls) -> dict:
        """Obtener todas las configuraciones en un diccionario"""
        return {
            clave: cls.obtener(clave)
            for clave in cls.CONFIGS_CRITICAS.keys()
        }


# ═══════════════════════════════════════════════════════════════════
# SERVICIO DE ROLES Y PERMISOS
# ═══════════════════════════════════════════════════════════════════

class RoleService:
    """Control de acceso y permisos basado en roles"""

    # Permisos por rol (matriz de control de acceso)
    PERMISOS = {
        RoleEnum.SUPER_ADMIN: [
            'configuracion', 'usuarios', 'roles', 'auditoria', 'integraciones',
            'productos', 'combos', 'categorias', 'cupones', 'stock',
            'pedidos', 'reportes', 'staff_pagos', 'zonas', 'bares'
        ],
        RoleEnum.ADMIN: [
            'productos', 'combos', 'categorias', 'cupones', 'stock',
            'pedidos', 'caja', 'reportes', 'staff_pagos', 'zonas'
        ],
        RoleEnum.STAFF_COCINA: ['pedidos_cocina', 'stock_consulta'],
        RoleEnum.STAFF_PREPARACION: ['pedidos', 'stock_consulta'],
        RoleEnum.STAFF_REPARTIDOR: ['pedidos_reparto', 'tracking'],
        RoleEnum.CLIENTE: ['perfil', 'pedidos_propios', 'puntos', 'historial'],
    }

    @classmethod
    def tiene_permiso(cls, usuario: User, permiso: str) -> bool:
        """Verifica si usuario tiene permiso específico"""
        if not usuario or not usuario.activo:
            return False
        
        permisos_role = cls.PERMISOS.get(RoleEnum(usuario.rol), [])
        return permiso in permisos_role

    @classmethod
    def puede_ver_ordenes_de(cls, usuario: User, orden: Order) -> bool:
        """Verifica si usuario puede ver una orden específica"""
        if usuario.rol == RoleEnum.SUPER_ADMIN or usuario.rol == RoleEnum.ADMIN:
            return True
        if usuario.rol == RoleEnum.CLIENTE and usuario.id == orden.cliente_id:
            return True
        return False

    @classmethod
    def validar_rol_autenticable(cls, rol: str) -> bool:
        """Solo ciertos roles pueden autenticarse"""
        return rol in ['super_admin', 'admin', 'cocina', 'preparacion', 'repartidor']

    @classmethod
    def requiere_rol(cls, rol_requerido):
        """Decorator para validar rol en rutas"""
        def decorator(f):
            @wraps(f)
            def wrapper(*args, **kwargs):
                from flask_login import current_user
                if not current_user.is_authenticated:
                    from flask import abort
                    abort(401)
                if current_user.rol != rol_requerido:
                    from flask import abort
                    abort(403)
                return f(*args, **kwargs)
            return wrapper
        return decorator


# ═══════════════════════════════════════════════════════════════════
# SERVICIO DE COMBOS
# ═══════════════════════════════════════════════════════════════════

class ComboService:
    """Validación y cálculo de precios para combos"""

    @classmethod
    def validar_estructura(cls, combo: Product) -> ResultadoOperacion:
        """Valida que la estructura del combo sea correcta"""
        if not combo or not combo.es_combo:
            return ResultadoOperacion(False, error="No es un combo válido", codigo="INVALID_COMBO")

        errores = []

        # Validar que tiene grupos
        grupos = list(combo.combo_groups)
        if not grupos:
            errores.append("Combo sin grupos de selección")

        # Validar cada grupo
        for grupo in grupos:
            items = list(grupo.combo_items)
            if not items:
                errores.append(f"Grupo '{grupo.nombre}' sin items")

            if grupo.tipo == 'seleccion':
                if not hasattr(grupo, 'max_selecciones') or not grupo.max_selecciones:
                    errores.append(f"Grupo '{grupo.nombre}' sin max_selecciones")
                if hasattr(grupo, 'max_selecciones') and grupo.max_selecciones < 1:
                    errores.append(f"Grupo '{grupo.nombre}' max_selecciones < 1")

        # Validar precio
        if combo.combo_precio_modo == TipoComboEnum.FIJO:
            if not combo.combo_precio_base or combo.combo_precio_base <= 0:
                errores.append("Combo precio fijo inválido (debe ser > 0)")
        elif combo.combo_precio_modo == TipoComboEnum.DESCUENTO_PORCENTAJE:
            if combo.combo_descuento_pct < 0 or combo.combo_descuento_pct > 100:
                errores.append("Descuento porcentaje fuera de rango [0-100]")

        if errores:
            return ResultadoOperacion(False, error=" | ".join(errores), codigo="COMBO_INVALID")

        return ResultadoOperacion(True, data={"valido": True})

    @classmethod
    def validar_selecciones_cliente(cls, combo_id: int, selecciones_dict: dict) -> ResultadoOperacion:
        """
        Valida que las selecciones del cliente sean correctas y calcula precio final
        
        selecciones_dict = {
            'grupo_1': [item_1, item_2],  # Múltiple
            'grupo_2': item_3             # Única
        }
        """
        combo = Product.query.get(combo_id)
        if not combo or not combo.es_combo:
            return ResultadoOperacion(False, error="No es un combo válido", codigo="INVALID_COMBO")

        # Validar estructura primero
        resultado = cls.validar_estructura(combo)
        if not resultado.exito:
            return resultado

        errores = []
        precio_total = Decimal('0')

        try:
            for grupo in combo.combo_groups:
                grupo_id_str = f"grupo_{grupo.id}"
                selecciones = selecciones_dict.get(grupo_id_str, [])

                # Normalizar a lista
                if not isinstance(selecciones, list):
                    selecciones = [selecciones] if selecciones else []

                if grupo.tipo == 'fijo':
                    # Agregar todos los items del grupo fijo
                    for item in grupo.combo_items:
                        precio_total += item.precio_extra or Decimal('0')

                elif grupo.tipo == 'seleccion':
                    # Validar cantidad de selecciones
                    min_sel = getattr(grupo, 'min_selecciones', 1) or 1
                    max_sel = getattr(grupo, 'max_selecciones', 1) or 1

                    if len(selecciones) < min_sel:
                        errores.append(f"{grupo.nombre}: mínimo {min_sel} selección(es)")
                    if len(selecciones) > max_sel:
                        errores.append(f"{grupo.nombre}: máximo {max_sel} selección(es)")

                    # Validar items y agregar precio
                    items_validos = {item.id for item in grupo.combo_items}
                    for item_id in selecciones:
                        if item_id not in items_validos:
                            errores.append(f"Item {item_id} inválido para {grupo.nombre}")
                        else:
                            item = ComboItem.query.get(item_id)
                            if item:
                                precio_total += item.precio_extra or Decimal('0')

            if errores:
                return ResultadoOperacion(False, error=" | ".join(errores), codigo="SELECCION_INVALID")

            # Calcular precio final según modo
            if combo.combo_precio_modo == TipoComboEnum.FIJO:
                precio_final = combo.combo_precio_base
            elif combo.combo_precio_modo == TipoComboEnum.DESCUENTO_PORCENTAJE:
                descuento = precio_total * (Decimal(combo.combo_descuento_pct) / Decimal('100'))
                precio_final = precio_total - descuento
            else:  # suma_componentes
                precio_final = precio_total

            return ResultadoOperacion(True, data={
                "precio_final": float(precio_final),
                "precio_total": float(precio_total),
                "selecciones": selecciones_dict
            })

        except Exception as e:
            logger.error(f"Error validando combo {combo_id}: {e}", exc_info=True)
            return ResultadoOperacion(False, error=str(e), codigo="COMBO_ERROR")


# ═══════════════════════════════════════════════════════════════════
# SERVICIO DE MODALIDAD DE ENTREGA
# ═══════════════════════════════════════════════════════════════════

class DeliveryService:
    """Gestiona lógica de delivery, recogida y validaciones"""

    @classmethod
    def validar_modalidad_para_carrito(
        cls, 
        carrito_items: dict, 
        modalidad_solicitada: str
    ) -> ResultadoOperacion:
        """
        Valida que TODOS los items del carrito soportan la modalidad solicitada
        
        Args:
            carrito_items: {"prod_id": qty, "combo_id": {"selecciones": {...}, "qty": 1}}
            modalidad_solicitada: "delivery" o "recogida"
        """
        if modalidad_solicitada not in ['delivery', 'recogida']:
            return ResultadoOperacion(
                False, 
                error=f"Modalidad '{modalidad_solicitada}' inválida",
                codigo="MODALIDAD_INVALID"
            )

        productos_problema = []

        try:
            for item_id, cantidad in carrito_items.items():
                # Ignorar si no es un ID válido
                try:
                    prod_id = int(item_id) if isinstance(item_id, str) else item_id
                except (ValueError, TypeError):
                    continue

                prod = Product.query.get(prod_id)
                if not prod:
                    continue

                # Validar modalidad del producto
                if prod.modalidad_entrega == ModalidadEntregaEnum.AMBAS:
                    continue  # OK

                if prod.modalidad_entrega == modalidad_solicitada:
                    continue  # OK

                # Problema encontrado
                productos_problema.append({
                    "producto_id": prod.id,
                    "nombre": prod.nombre,
                    "modalidad_actual": prod.modalidad_entrega,
                    "solicitada": modalidad_solicitada,
                    "razon": f"Solo disponible para {prod.modalidad_entrega}"
                })

            if productos_problema:
                return ResultadoOperacion(
                    False,
                    data={"productos_problema": productos_problema},
                    error=f"{len(productos_problema)} producto(s) no disponibles para {modalidad_solicitada}",
                    codigo="MODALIDAD_NO_SOPORTADA"
                )

            return ResultadoOperacion(True, data={"valido": True})

        except Exception as e:
            logger.error(f"Error validando modalidad: {e}", exc_info=True)
            return ResultadoOperacion(False, error=str(e), codigo="DELIVERY_ERROR")

    @classmethod
    def puede_usar_delivery(cls) -> bool:
        """Verifica si delivery está habilitado"""
        return ConfigService.obtener('delivery_habilitado', True)

    @classmethod
    def puede_usar_recogida(cls) -> bool:
        """Verifica si recogida está habilitada"""
        return ConfigService.obtener('recogida_habilitada', True)

    @classmethod
    def obtener_opciones_disponibles(cls) -> list[str]:
        """Retorna opciones de entrega disponibles"""
        opciones = []
        if cls.puede_usar_delivery():
            opciones.append('delivery')
        if cls.puede_usar_recogida():
            opciones.append('recogida')
        return opciones


# ═══════════════════════════════════════════════════════════════════
# SERVICIO DE PUNTOS
# ═══════════════════════════════════════════════════════════════════

class PointsService:
    """Gestiona sistema de puntos y canjes"""

    @classmethod
    def calcular_puntos_por_monto(cls, monto: Decimal) -> int:
        """Calcula puntos ganados según monto de compra"""
        tasa = Decimal(str(ConfigService.obtener('tasa_puntos_por_monto', 1.0)))
        return int(monto * tasa)

    @classmethod
    def calcular_descuento_por_puntos(cls, puntos_a_canjear: int) -> Decimal:
        """Calcula descuento en $ por puntos"""
        valor_punto = Decimal(str(ConfigService.obtener('valor_punto_en_monto', 0.1)))
        return Decimal(puntos_a_canjear) * valor_punto

    @classmethod
    def puede_canjear_producto(cls, usuario: User, producto: Product) -> ResultadoOperacion:
        """Verifica si usuario puede canjear un producto específico"""
        if not producto.canjeable_con_puntos:
            return ResultadoOperacion(
                False,
                error="Producto no es canjeable",
                codigo="PRODUCTO_NO_CANJEABLE"
            )

        puntos_necesarios = producto.puntos_para_canje or 0
        if usuario.puntos < puntos_necesarios:
            return ResultadoOperacion(
                False,
                error=f"Puntos insuficientes. Necesitas {puntos_necesarios}, tienes {usuario.puntos}",
                codigo="PUNTOS_INSUFICIENTES"
            )

        return ResultadoOperacion(True, data={"puede_canjear": True})

    @classmethod
    def realizar_canje(
        cls,
        usuario: User,
        producto: Product,
        cantidad: int = 1
    ) -> ResultadoOperacion:
        """Realiza canje de puntos por producto"""
        try:
            # Validar
            validacion = cls.puede_canjear_producto(usuario, producto)
            if not validacion.exito:
                return validacion

            puntos_totales = (producto.puntos_para_canje or 0) * cantidad

            # Restar puntos
            usuario.puntos -= puntos_totales
            db.session.add(usuario)

            # Registrar en log
            log = PointsLog(
                usuario_id=usuario.id,
                tipo='canje',
                cantidad=-puntos_totales,
                razon=f"Canje: {producto.nombre} x{cantidad}",
                producto_id=producto.id
            )
            db.session.add(log)
            db.session.commit()

            logger.info(f"[PUNTOS] Usuario {usuario.id} canjeó {puntos_totales} puntos")
            return ResultadoOperacion(True, data={"puntos_restantes": usuario.puntos})

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error realizando canje: {e}", exc_info=True)
            return ResultadoOperacion(False, error=str(e), codigo="CANJE_ERROR")


# ═══════════════════════════════════════════════════════════════════
# SERVICIO DE STOCK
# ═══════════════════════════════════════════════════════════════════

class StockService:
    """Gestión de stock con FIFO"""

    @classmethod
    def obtener_stock_disponible(cls, producto_id: int, proveedor_id: int = None) -> int:
        """
        Obtiene cantidad disponible de un producto (FIFO válido, sin expirados)
        """
        query = Stock.query.filter(
            Stock.producto_id == producto_id,
            Stock.fecha_caducidad >= datetime.now().date()  # No expirados
        )

        if proveedor_id:
            query = query.filter(Stock.proveedor_id == proveedor_id)

        query = query.order_by(
            Stock.fecha_caducidad.asc().nullslast(),
            Stock.fecha_entrada.asc()
        )

        total = sum(s.cantidad for s in query.all())
        return max(0, total)

    @classmethod
    def deducir_stock(cls, producto_id: int, cantidad: int) -> ResultadoOperacion:
        """
        Deduce stock usando FIFO (más antiguo primero)
        Retorna info de qué fue deducido
        """
        try:
            disponible = cls.obtener_stock_disponible(producto_id)
            if disponible < cantidad:
                return ResultadoOperacion(
                    False,
                    error=f"Stock insuficiente. Disponible: {disponible}, Solicitado: {cantidad}",
                    codigo="STOCK_INSUFICIENTE"
                )

            deducido = 0
            detalles = []

            # Deducir en orden FIFO
            stock_entries = Stock.query.filter(
                Stock.producto_id == producto_id,
                Stock.fecha_caducidad >= datetime.now().date()
            ).order_by(
                Stock.fecha_caducidad.asc().nullslast(),
                Stock.fecha_entrada.asc()
            ).with_for_update().all()

            for entry in stock_entries:
                if deducido >= cantidad:
                    break

                a_deducir = min(cantidad - deducido, entry.cantidad)
                entry.cantidad -= a_deducir
                deducido += a_deducir

                detalles.append({
                    "stock_id": entry.id,
                    "cantidad": a_deducir,
                    "fecha_entrada": entry.fecha_entrada,
                    "fecha_caducidad": entry.fecha_caducidad
                })

                if entry.cantidad <= 0:
                    db.session.delete(entry)

            db.session.commit()
            logger.info(f"[STOCK] Deducido {cantidad} unidades de producto {producto_id}")

            return ResultadoOperacion(True, data={"deducido": deducido, "detalles": detalles})

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error deduciendo stock: {e}", exc_info=True)
            return ResultadoOperacion(False, error=str(e), codigo="STOCK_ERROR")


# ═══════════════════════════════════════════════════════════════════
# SERVICIO DE VALIDACIÓN
# ═══════════════════════════════════════════════════════════════════

class ValidationService:
    """Validaciones centralizadas reutilizables"""

    @classmethod
    def validar_email(cls, email: str) -> bool:
        """Valida formato de email"""
        import re
        patron = r'^[^@]+@[^@]+\.[^@]+$'
        return re.match(patron, email) is not None

    @classmethod
    def validar_telefono(cls, telefono: str) -> bool:
        """Valida formato de teléfono"""
        import re
        # Acepta: +34600000000, 600000000, 34600000000
        patron = r'^(\+\d{1,3})?[0-9]{6,15}$'
        return re.match(patron, telefono.replace(' ', '')) is not None

    @classmethod
    def validar_precio(cls, precio) -> bool:
        """Valida que precio sea válido (> 0)"""
        try:
            p = Decimal(str(precio))
            return p > 0
        except:
            return False


if __name__ == "__main__":
    # Testing básico
    print("✓ Servicios Core Cargados")
    print(f"  - ConfigService: {len(ConfigService.CONFIGS_CRITICAS)} configs")
    print(f"  - RoleService: {len(RoleService.PERMISOS)} roles")
    print(f"  - ComboService")
    print(f"  - DeliveryService")
    print(f"  - PointsService")
    print(f"  - StockService")
    print(f"  - ValidationService")
