from datetime import datetime, date, timedelta, time, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import logging
import os
import random
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text
from sqlalchemy.orm import validates
from extensions import db

logger = logging.getLogger(__name__)


def utcnow():
    """UTC naïve datetime compatible with existing DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ─────────────────────────────────────────────
# USUARIOS
# ─────────────────────────────────────────────

# Roles del sistema (post-consolidación 2026-06-17):
#  - super_admin: configuración global, integraciones, único con MFA forzado.
#  - admin: operación diaria (productos, caja, pedidos, marketing).
#  - preparacion: prepara stock propio (cocina + almacén unificados).
#  - repartidor: entrega los pedidos.
#  - proveedor: operador de un bar despachador (varios bares posibles).
#  - cliente: identidad comercial interna para pedidos, puntos y marketing.
#    No es una cuenta autenticable ni dispone de panel público.
# Los valores "cocina" y "staff" están deprecados como rol; nada nuevo los
# escribe, pero la columna users.rol los acepta para no romper datos viejos.
ROLES = ["super_admin", "admin", "preparacion", "repartidor", "proveedor", "cliente"]
ROLES_AUTENTICABLES = frozenset({
    "super_admin", "admin", "preparacion", "repartidor", "proveedor",
    # Compatibilidad temporal con cuentas operativas antiguas.
    "cocina", "staff",
})
ROLES_LEGACY_PREPARACION = {"cocina", "staff"}
METODOS_PAGO_VALIDOS = ("efectivo", "bizum")

# Modelo de acuerdo con un proveedor (restaurante despachador):
#   stock_proveedor  → el bar pone, repone y cobra precio_costo por unidad.
#                      Nuestro margen = PVP − precio_costo.
#   stock_propio_bar → nosotros ponemos el stock físicamente en el bar y le
#                      pagamos comision_pct% del PVP por cada combo despachado
#                      como fee de preparación.
MODELOS_ACUERDO_PROVEEDOR = ("stock_proveedor", "stock_propio_bar")

# 14 alérgenos de declaración obligatoria según Reglamento UE 1169/2011
ALERGENOS_EU = [
    ("gluten",       "🌾", "Gluten"),
    ("crustaceos",   "🦐", "Crustáceos"),
    ("huevos",       "🥚", "Huevos"),
    ("pescado",      "🐟", "Pescado"),
    ("cacahuetes",   "🥜", "Cacahuetes"),
    ("soja",         "🫘", "Soja"),
    ("lacteos",      "🥛", "Lácteos"),
    ("frutos_secos", "🌰", "Frutos secos"),
    ("apio",         "🥬", "Apio"),
    ("mostaza",      "🟡", "Mostaza"),
    ("sesamo",       "🌿", "Sésamo"),
    ("sulfitos",     "🍷", "Sulfitos"),
    ("altramuces",   "🫛", "Altramuces"),
    ("moluscos",     "🦑", "Moluscos"),
]
_ALERGEN_ICON = {code: icon for code, icon, _ in ALERGENOS_EU}
ORIGENES_PEDIDO = ["online", "presencial", "whatsapp"]
PRESENCIA_TIMEOUT = int(os.environ.get("PRESENCIA_TIMEOUT", "10"))

TIPOS_ENTREGA = ["inmediato", "programado"]
ADMIN_FEATURES = [
    "caja", "productos", "stock", "cupones",
    "staff_pagos", "reportes", "zonas", "auditoria",
    "marketing", "pos", "whatsapp"
]


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    rol = db.Column(db.String(20), nullable=False, default="cliente")
    telefono = db.Column(db.String(20))
    telefono_normalizado = db.Column(db.String(20), unique=True, index=True)
    direccion = db.Column(db.Text)
    puntos = db.Column(db.Integer, default=0)
    activo = db.Column(db.Boolean, default=True)
    creado_en = db.Column(db.DateTime, default=utcnow)

    # Presencia
    last_seen = db.Column(db.DateTime)
    en_linea = db.Column(db.Boolean, default=False)  # toggle manual disponibilidad

    # Verificación de puntos por WhatsApp
    cod_puntos = db.Column(db.String(8))           # código de 6 dígitos
    cod_puntos_expira = db.Column(db.DateTime)     # expira en 10 min
    cod_puntos_intentos = db.Column(db.Integer, default=0)  # max 5 intentos

    # MFA / 2FA — secreto TOTP base32 (solo poblado mientras MFA está activo)
    mfa_secret = db.Column(db.String(64))
    mfa_enabled = db.Column(db.Boolean, default=False, server_default="false", nullable=False)
    # Incrementar invalida todas las sesiones vivas del usuario (logout global)
    mfa_session_version = db.Column(db.Integer, default=0, server_default="0", nullable=False)

    # Puesto de trabajo (Cocinero, Empacador, Cajero, etc.)
    puesto_trabajo = db.Column(db.String(100))

    # Remuneración (solo staff/repartidor)
    salario_base = db.Column(db.Numeric(10, 2), default=0)       # salario mensual fijo
    tarifa_entrega = db.Column(db.Numeric(10, 2), default=0)     # comisión por pedido entregado

    # Operador de proveedor (solo users con rol='proveedor')
    # Si está informado, este user inicia sesión como personal del restaurante de ese proveedor.
    proveedor_id = db.Column(db.Integer, db.ForeignKey("proveedores.id"), nullable=True)

    # Relaciones
    pedidos = db.relationship("Order", foreign_keys="Order.cliente_id", backref="cliente", lazy="dynamic")
    pedidos_preparados = db.relationship("Order", foreign_keys="Order.preparador_id", backref="preparador", lazy="dynamic")
    pedidos_repartidos = db.relationship("Order", foreign_keys="Order.repartidor_id", backref="repartidor", lazy="dynamic")
    reviews = db.relationship("Review", backref="cliente", lazy="dynamic")
    points_log = db.relationship("PointsLog", backref="cliente", lazy="dynamic")
    movimientos_caja = db.relationship("Caja", backref="registrado_por_user", lazy="dynamic")
    pagos = db.relationship("StaffPayment", foreign_keys="StaffPayment.user_id", backref="empleado", lazy="dynamic")

    @validates("telefono")
    def normalizar_telefono(self, _key, value):
        from phone_utils import normalizar_telefono_cliente

        canonical = normalizar_telefono_cliente(value)
        self.telefono_normalizado = canonical or None
        return canonical or None

    # ── Contraseña ──
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def puede_iniciar_sesion(self):
        return self.activo and self.rol in ROLES_AUTENTICABLES

    # ── Presencia ──
    def marcar_activo(self):
        self.last_seen = utcnow()

    def toggle_disponible(self):
        """Staff activa/desactiva disponibilidad manualmente."""
        self.en_linea = not self.en_linea
        if self.en_linea:
            self.last_seen = utcnow()

    @property
    def disponible_para_pedidos(self):
        """True si está en línea Y activo en los últimos PRESENCIA_TIMEOUT minutos."""
        return self.en_linea and self.esta_conectado

    @property
    def esta_conectado(self):
        if not self.last_seen:
            return False
        return (utcnow() - self.last_seen).total_seconds() < PRESENCIA_TIMEOUT * 60

    @property
    def minutos_inactivo(self):
        if not self.last_seen:
            return None
        return int((utcnow() - self.last_seen).total_seconds() // 60)

    # ── Puntos ──
    def sumar_puntos(self, cantidad, pedido_id=None, descripcion="Compra"):
        self.puntos += cantidad
        log = PointsLog(cliente_id=self.id, pedido_id=pedido_id,
                        tipo="ganado", cantidad=cantidad, descripcion=descripcion)
        db.session.add(log)

    def canjear_puntos(self, cantidad, pedido_id=None):
        if cantidad > self.puntos:
            raise ValueError("Puntos insuficientes")
        self.puntos -= cantidad
        log = PointsLog(cliente_id=self.id, pedido_id=pedido_id,
                        tipo="canjeado", cantidad=-cantidad, descripcion="Canje en pedido")
        db.session.add(log)

    # ── Carga de trabajo actual ──
    def pedidos_activos_como_preparador(self):
        return Order.query.filter(
            Order.preparador_id == self.id,
            Order.estado.in_(["pendiente", "armando"])
        ).count()

    def pedidos_activos_como_repartidor(self):
        return Order.query.filter(
            Order.repartidor_id == self.id,
            Order.estado.in_(["listo", "en_ruta"])
        ).count()

    def es_admin(self):
        return self.rol in ("admin", "super_admin")

    def generar_cod_puntos(self):
        """Genera código de 6 dígitos criptográficamente seguro para verificar canje de puntos."""
        import secrets as _secrets
        self.cod_puntos = str(_secrets.randbelow(1_000_000)).zfill(6)
        self.cod_puntos_expira = utcnow() + timedelta(minutes=10)
        self.cod_puntos_intentos = 0
        return self.cod_puntos

    def verificar_cod_puntos(self, codigo):
        """
        Verifica el código OTP. Retorna True si válido.
        Limita a 5 intentos y borra el código tras éxito para evitar replay attacks.
        """
        if not self.cod_puntos or not self.cod_puntos_expira:
            return False
        if utcnow() > self.cod_puntos_expira:
            self.cod_puntos = None
            self.cod_puntos_expira = None
            return False
        intentos = getattr(self, 'cod_puntos_intentos', 0) or 0
        if intentos >= 5:
            return False
        if self.cod_puntos != str(codigo).strip():
            self.cod_puntos_intentos = intentos + 1
            return False
        # Éxito: borrar código para evitar reutilización
        self.cod_puntos = None
        self.cod_puntos_expira = None
        self.cod_puntos_intentos = 0
        return True

    def __repr__(self):
        return f"<User {self.email} [{self.rol}]>"


def normalizar_metodo_pago(val):
    """Efectivo / Bizum únicos métodos internos; compatibilidad con valores antiguos."""
    v = (val or "efectivo").strip().lower()
    if v in ("transferencia", "tarjeta"):
        return "bizum"
    if v in METODOS_PAGO_VALIDOS:
        return v
    return "efectivo"


# ─────────────────────────────────────────────
# PAGOS AL STAFF
# ─────────────────────────────────────────────

class StaffPayment(db.Model):
    __tablename__ = "staff_payments"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    # salario / comision / bonus / adelanto / descuento

    monto = db.Column(db.Numeric(10, 2), nullable=False)
    concepto = db.Column(db.String(200))
    periodo_inicio = db.Column(db.Date)
    periodo_fin = db.Column(db.Date)
    pedido_id = db.Column(db.Integer, db.ForeignKey("orders.id"))  # para comisiones
    origen = db.Column(db.String(30), nullable=False, default="manual")

    pagado = db.Column(db.Boolean, default=False)
    fecha_pago = db.Column(db.DateTime)

    registrado_por = db.Column(db.Integer, db.ForeignKey("users.id"))
    creado_en = db.Column(db.DateTime, default=utcnow)

    registrador = db.relationship("User", foreign_keys=[registrado_por])
    pedido = db.relationship("Order", backref="comisiones")

    __table_args__ = (
        db.Index(
            "uq_staff_payment_delivery_commission",
            "user_id", "origen", "pedido_id",
            unique=True,
            postgresql_where=text("tipo = 'comision' AND pedido_id IS NOT NULL"),
        ),
        db.CheckConstraint("monto >= 0", name="ck_staff_payment_monto_nonnegative"),
    )

    def marcar_pagado(self):
        self.pagado = True
        self.fecha_pago = utcnow()

    @property
    def descripcion_completa(self):
        base = f"{self.tipo.capitalize()}"
        if self.periodo_inicio and self.periodo_fin:
            base += f" {self.periodo_inicio.strftime('%d/%m')}–{self.periodo_fin.strftime('%d/%m/%Y')}"
        if self.concepto:
            base += f" — {self.concepto}"
        return base


# ─────────────────────────────────────────────
# PROVEEDORES (RESTAURANTES TERCEROS)
# ─────────────────────────────────────────────

class Proveedor(db.Model):
    """Restaurante o tercero que prepara y despacha mercancía bajo nuestra marca.

    Un proveedor:
    - tiene su propio stock por SKU (tabla `proveedor_productos`)
    - puede despachar productos sueltos o combos enteros (`Product.proveedor_despachador_id`)
    - puede tener uno o varios usuarios operadores (`User.proveedor_id`)
    """
    __tablename__ = "proveedores"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    razon_social = db.Column(db.String(200))
    direccion = db.Column(db.Text)
    telefono = db.Column(db.String(20))
    email = db.Column(db.String(120))
    horario = db.Column(db.Text)
    # Horario operativo del bar. Si están informados, los productos del bar
    # se ocultan del catálogo público fuera de este rango. Si quedan NULL,
    # el bar se considera operativo 24h (sus productos siempre disponibles
    # según `Product.visible_ahora`).
    hora_apertura = db.Column(db.Time)
    hora_cierre = db.Column(db.Time)
    # Modelo de acuerdo. Determina cómo se calcula la liquidación periódica.
    modelo_acuerdo = db.Column(
        db.String(30),
        nullable=False,
        default="stock_proveedor",
        server_default="stock_proveedor",
    )
    # En modo 'stock_proveedor': sin uso para liquidación (margen = PVP − precio_costo).
    # En modo 'stock_propio_bar': % del PVP que le pagamos al bar como fee de preparación.
    comision_pct = db.Column(db.Numeric(5, 2), default=0, server_default="0", nullable=False)
    iban = db.Column(db.String(40))
    notas = db.Column(db.Text)
    activo = db.Column(db.Boolean, default=True, nullable=False)
    creado_en = db.Column(db.DateTime, default=utcnow, nullable=False)

    skus = db.relationship(
        "ProveedorProducto",
        backref="proveedor",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    operadores = db.relationship(
        "User",
        foreign_keys="User.proveedor_id",
        backref="proveedor",
        lazy="dynamic",
    )

    __table_args__ = (
        db.Index("ix_proveedores_activo", "activo"),
    )

    @property
    def esta_abierto_ahora(self):
        """True si ahora estamos dentro del horario operativo del bar.

        Si el bar no tiene hora_apertura/hora_cierre configurados, se considera
        siempre abierto (devuelve True). Soporta horarios que cruzan medianoche
        (ej: 18:00 a 02:00)."""
        if not self.hora_apertura or not self.hora_cierre:
            return True
        ahora = datetime.now().time()
        if self.hora_apertura <= self.hora_cierre:
            return self.hora_apertura <= ahora <= self.hora_cierre
        # Cruza medianoche
        return ahora >= self.hora_apertura or ahora <= self.hora_cierre

    def __repr__(self):
        return f"<Proveedor {self.nombre}>"


class ProveedorProducto(db.Model):
    """SKU que un proveedor mantiene en su stock, con su coste interno.

    Un mismo Producto (ej. Coca-Cola 500ml) puede existir en `Stock` propio Y en
    `proveedor_productos` para uno o varios proveedores; cada inventario es
    independiente. Los combos despachados por un proveedor consumen de este
    stock, nunca del propio.
    """
    __tablename__ = "proveedor_productos"

    id = db.Column(db.Integer, primary_key=True)
    proveedor_id = db.Column(
        db.Integer,
        db.ForeignKey("proveedores.id", ondelete="CASCADE"),
        nullable=False,
    )
    producto_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    stock = db.Column(db.Integer, default=0, nullable=False)
    precio_costo = db.Column(db.Numeric(10, 2))
    activo = db.Column(db.Boolean, default=True, nullable=False)
    actualizado_en = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    producto = db.relationship("Product", foreign_keys=[producto_id])

    __table_args__ = (
        db.UniqueConstraint(
            "proveedor_id", "producto_id", name="uq_proveedor_producto"
        ),
        db.Index("ix_proveedor_productos_proveedor", "proveedor_id"),
        db.Index("ix_proveedor_productos_producto", "producto_id"),
    )


# ─────────────────────────────────────────────
# CATÁLOGO
# ─────────────────────────────────────────────

class Categoria(db.Model):
    __tablename__ = "categorias"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), nullable=False)
    descripcion = db.Column(db.Text)
    imagen_url = db.Column(db.Text)
    activo = db.Column(db.Boolean, default=True)
    orden = db.Column(db.Integer, default=0)

    productos = db.relationship("Product", backref="categoria", lazy="dynamic")

    def __repr__(self):
        return f"<Categoria {self.nombre}>"


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    descripcion = db.Column(db.Text)
    precio = db.Column(db.Numeric(10, 2), nullable=False)
    precio_costo = db.Column(db.Numeric(10, 2))
    categoria_id = db.Column(db.Integer, db.ForeignKey("categorias.id"))
    imagen_url = db.Column(db.Text)
    origen_pais = db.Column(db.String(50))
    es_combo = db.Column(db.Boolean, default=False)
    combo_precio_modo = db.Column(db.String(30), nullable=False, default="fijo")
    combo_descuento_pct = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    combo_precio_base = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    # Modularidad: simple | pesable | servicio | materia_prima | otro (libre en UI)
    tipo_producto = db.Column(db.String(50), default="simple")
    # Atributos variables por tipo (JSON): {"peso_kg": 0.5, "sku": "...", "alergenos": []}
    atributos_json = db.Column(db.Text)
    activo = db.Column(db.Boolean, default=True)
    creado_en = db.Column(db.DateTime, default=utcnow)

    # ── Canal de preparación ─────────────────────────────────────────
    # cocina   = requiere cocinarse/elaborarse (default — no rompe nada existente)
    # almacen  = producto de stock: bebidas, envasados, snacks — solo empacar y entregar
    canal_preparacion = db.Column(db.String(20), nullable=False, default="cocina")

    # ── Despachador externo (Proveedor restaurante) ──────────────────
    # Si está set, el producto simple o combo entero lo despacha ese proveedor.
    # En combos, todos sus componentes deben tener entrada en proveedor_productos
    # de ese proveedor. Si NULL, se usa Stock propio.
    proveedor_despachador_id = db.Column(
        db.Integer, db.ForeignKey("proveedores.id"), nullable=True
    )

    # LEGACY: proveedor_id apuntaba a users(id) con rol='proveedor'.
    # Mantenido para no romper datos antiguos durante la transición; la lógica
    # nueva ignora esta columna (usa proveedor_despachador_id para despacho externo y
    # proveedor_productos para mapeo SKU↔proveedor).
    proveedor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # ── Tipo de entrega ──────────────────────────────────────────────
    # inmediato = se prepara y envía al momento
    # programado = llegará en una fecha futura y se puede reservar
    tipo_entrega = db.Column(db.String(20), default="inmediato")
    fecha_llegada = db.Column(db.Date)          # solo tipo=programado
    dias_anticipacion_encargo = db.Column(db.Integer, default=1)  # legado: mantener compatibilidad con datos antiguos

    # ── Visibilidad horaria ──────────────────────────────────────────
    # Si hora_inicio_visibilidad es null, el producto es visible siempre
    hora_inicio_visibilidad = db.Column(db.Time)
    hora_fin_visibilidad = db.Column(db.Time)
    # JSON con lista de días [0=lun … 6=dom]; null = todos los días
    dias_semana_json = db.Column(db.Text)

    # ── Visualización de stock en web ────────────────────────────────
    stock_mostrar_en_web = db.Column(db.Boolean, default=False)

    # ── Canje con puntos ─────────────────────────────────────────────
    canjeable_con_puntos = db.Column(db.Boolean, default=False)
    puntos_para_canje = db.Column(db.Integer)   # cuántos puntos cuesta

    # ── Hipoalergénicos / alérgenos ──────────────────────────────────
    es_hipoalergenico = db.Column(db.Boolean, default=False)
    alergenos_info = db.Column(db.Text)   # legacy — mantener compatibilidad
    alergenos_json = db.Column(db.Text)   # JSON list: ["gluten","lacteos"]

    proveedor = db.relationship("User", foreign_keys=[proveedor_id], backref="productos_proveedor")
    proveedor_despachador = db.relationship(
        "Proveedor", foreign_keys=[proveedor_despachador_id], backref="combos_despachados"
    )
    stock_entries = db.relationship("Stock", backref="producto", lazy="dynamic")
    reviews = db.relationship("Review", backref="producto", lazy="dynamic")
    combo_groups = db.relationship(
        "ComboGroup",
        foreign_keys="ComboGroup.combo_id",
        backref="combo",
        lazy="dynamic",
        cascade="all, delete-orphan",
        order_by="ComboGroup.orden.asc(), ComboGroup.id.asc()",
    )
    combo_items = db.relationship("ComboItem", foreign_keys="ComboItem.combo_id", backref="combo", lazy="dynamic")
    order_items = db.relationship("OrderItem", backref="producto", lazy="dynamic")

    @property
    def stock_total(self):
        try:
            val = db.session.query(db.func.coalesce(db.func.sum(Stock.cantidad), 0))\
                         .filter_by(producto_id=self.id).scalar()
            return int(val or 0)
        except Exception:
            # Ante errores transitorios de consulta, devolver 0 como fallback.
            return 0

    @property
    def usa_stock_propio(self):
        """Indica si este producto inmediato consume Stock propio."""
        if (self.tipo_entrega or "inmediato") != "inmediato":
            return False
        return not self.proveedor_despachador_id

    @property
    def origen_operativo_label(self):
        if self.proveedor_despachador_id:
            nombre = (
                self.proveedor_despachador.nombre
                if self.proveedor_despachador else f"#{self.proveedor_despachador_id}"
            )
            return f"Despachado por: {nombre}"
        return "Stock propio"

    @property
    def admin_nombre_operativo(self):
        return f"{self.nombre} · #{self.id} · {self.origen_operativo_label}"

    @staticmethod
    def _capacidad_combo_item(item):
        componente = item.componente
        if not componente or not componente.activo or item.cantidad <= 0:
            return 0
        if not componente.usa_stock_propio:
            return None
        return componente.stock_total // item.cantidad

    @property
    def combo_stock_total(self):
        """Disponibilidad estimada de un combo según el stock de sus componentes."""
        if not self.es_combo:
            return self.stock_total

        componentes = list(self.combo_items)
        if not componentes:
            return 0

        capacidades = []
        grupos = {}
        for item in componentes:
            if not item.activo:
                continue
            if item.es_seleccionable:
                grupos.setdefault(item.grupo_seleccion or "Seleccion", []).append(item)
                continue
            capacidad = self._capacidad_combo_item(item)
            if capacidad is not None:
                capacidades.append(capacidad)

        for opciones in grupos.values():
            max_sel = max(1, opciones[0].max_selecciones or 1)
            capacidades_opciones = [
                self._capacidad_combo_item(item)
                for item in opciones
                if item.activo
            ]
            if any(capacidad is None for capacidad in capacidades_opciones):
                continue
            disponibles = [v for v in capacidades_opciones if isinstance(v, int)]
            capacidades_grupo = sum(v for v in disponibles if v > 0)
            if capacidades_grupo <= 0:
                capacidades.append(0)
            else:
                capacidades.append(capacidades_grupo // max_sel)

        return min(capacidades) if capacidades else 999999

    def disponible_para_venta(self, cantidad=1):
        if self.es_combo:
            componentes = list(self.combo_items)
            if not componentes:
                return False
            if self.tipo_entrega != "inmediato":
                return True
            if self.proveedor_despachador_id:
                # Disponibilidad = mínimo stock de proveedor por componente requerido
                return self._combo_proveedor_stock_total() >= cantidad
            return self.combo_stock_total >= cantidad
        if self.tipo_entrega != "inmediato":
            return True
        if self.proveedor_despachador_id:
            fila = self._proveedor_producto_fila(for_update=False)
            return bool(fila and int(fila.stock or 0) >= int(cantidad or 1))
        return self.stock_total >= cantidad

    def _proveedor_producto_fila(self, for_update=False):
        if not self.proveedor_despachador_id:
            return None
        query = ProveedorProducto.query.filter_by(
            proveedor_id=self.proveedor_despachador_id,
            producto_id=self.id,
            activo=True,
        )
        if for_update:
            query = query.with_for_update()
        return query.first()

    def _combo_proveedor_stock_total(self):
        """Disponibilidad de un combo despachado por un proveedor según su stock."""
        if not self.proveedor_despachador_id:
            return 0
        componentes = [item for item in self.combo_items if item.activo and item.componente]
        if not componentes:
            return 0
        producto_ids = list({item.producto_id for item in componentes})
        filas = ProveedorProducto.query.filter(
            ProveedorProducto.proveedor_id == self.proveedor_despachador_id,
            ProveedorProducto.producto_id.in_(producto_ids),
            ProveedorProducto.activo.is_(True),
        ).all()
        por_producto = {fila.producto_id: fila.stock for fila in filas}
        capacidades = []
        grupos = {}
        for item in componentes:
            if item.es_seleccionable:
                grupos.setdefault(item.grupo_seleccion or "Seleccion", []).append(item)
                continue
            stock = por_producto.get(item.producto_id, 0)
            req = max(1, item.cantidad or 1)
            capacidades.append(stock // req)

        for opciones in grupos.values():
            max_sel = max(1, opciones[0].max_selecciones or 1)
            disponibles = []
            for item in opciones:
                stock = por_producto.get(item.producto_id, 0)
                req = max(1, item.cantidad or 1)
                disponibles.append(stock // req)
            capacidad_grupo = sum(v for v in disponibles if v > 0)
            capacidades.append(capacidad_grupo // max_sel if capacidad_grupo > 0 else 0)

        return min(capacidades) if capacidades else 0

    def combo_item_stock_disponible(self, item, cantidad=1):
        """Stock del componente dentro del origen operativo real del combo."""
        if not item or not item.activo:
            return False
        componente = item.componente
        if not componente or not componente.activo or not componente.visible_ahora:
            return False
        requerido = max(1, int(item.cantidad or 1)) * max(1, int(cantidad or 1))
        if self.es_combo and self.proveedor_despachador_id:
            fila = ProveedorProducto.query.filter_by(
                proveedor_id=self.proveedor_despachador_id,
                producto_id=item.producto_id,
                activo=True,
            ).first()
            return bool(fila and int(fila.stock or 0) >= requerido)
        if (componente.tipo_entrega or "inmediato") != "inmediato":
            return True
        if not componente.usa_stock_propio:
            return True
        return int(componente.stock_total or 0) >= requerido

    @staticmethod
    def _normalizar_seleccion_combo(seleccion_item_ids):
        if isinstance(seleccion_item_ids, dict):
            counts = {}
            for item_id, qty in seleccion_item_ids.items():
                try:
                    item_id = int(item_id)
                    qty = max(0, int(qty))
                except (TypeError, ValueError):
                    continue
                if qty > 0:
                    counts[item_id] = counts.get(item_id, 0) + qty
            return counts
        counts = {}
        for raw in (seleccion_item_ids or []):
            try:
                item_id = int(raw)
            except (TypeError, ValueError):
                continue
            counts[item_id] = counts.get(item_id, 0) + 1
        return counts

    def _combo_items_para_seleccion(self, seleccion_item_ids=None):
        componentes = list(self.combo_items)
        if not componentes:
            raise ValueError(f"El combo '{self.nombre}' no tiene componentes configurados")
        seleccion_counts = self._normalizar_seleccion_combo(seleccion_item_ids)
        seleccionados = []
        grupos = {}
        for item in componentes:
            if not item.activo:
                continue
            if item.es_seleccionable:
                grupos.setdefault(item.grupo_seleccion or "Seleccion", []).append(item)
            else:
                seleccionados.append(item)

        for grupo, opciones in grupos.items():
            max_sel = max(1, opciones[0].max_selecciones or 1)
            grp = opciones[0].grupo
            min_sel = max(1, int(grp.min_selecciones if grp and grp.min_selecciones else 1))
            elegidos = []
            total_selections = 0
            for item in opciones:
                qty = seleccion_counts.get(item.id, 0)
                elegidos.extend([item] * qty)
                total_selections += qty
            if not elegidos:
                opciones_ordenadas = sorted(
                    opciones,
                    key=lambda item: (not bool(item.es_predeterminado), item.orden or 0, item.id or 0),
                )
                elegidos = [item for item in opciones_ordenadas if item.activo and item.componente][:max_sel]
                total_selections = len(elegidos)
            if total_selections < min_sel:
                raise ValueError(f"El combo '{self.nombre}' requiere elegir al menos {min_sel} opción(es) de {grupo}")
            if total_selections > max_sel:
                raise ValueError(f"El combo '{self.nombre}' permite elegir hasta {max_sel} opción(es) de {grupo}")
            seleccionados.extend(elegidos)
        return seleccionados

    def validar_stock_combo_seleccion(self, cantidad=1, seleccion_item_ids=None):
        """Valida stock exacto de un combo según sus opciones y su origen operativo."""
        if not self.es_combo:
            if not self.disponible_para_venta(cantidad):
                raise ValueError(f"Stock insuficiente para '{self.nombre}'")
            return True
        cantidad = max(1, int(cantidad or 1))
        requeridos = {}
        for item in self._combo_items_para_seleccion(seleccion_item_ids):
            if not item.componente or not item.componente.activo:
                raise ValueError(f"Componente inválido en combo '{self.nombre}'")
            producto_id = item.producto_id
            requeridos[producto_id] = requeridos.get(producto_id, 0) + max(1, int(item.cantidad or 1)) * cantidad
        if not requeridos:
            return True

        if self.proveedor_despachador_id:
            filas = ProveedorProducto.query.filter(
                ProveedorProducto.proveedor_id == self.proveedor_despachador_id,
                ProveedorProducto.producto_id.in_(list(requeridos.keys())),
                ProveedorProducto.activo.is_(True),
            ).all()
            por_producto = {fila.producto_id: fila for fila in filas}
            for producto_id, requerido in requeridos.items():
                fila = por_producto.get(producto_id)
                if not fila:
                    producto = db.session.get(Product, producto_id)
                    nombre = producto.nombre if producto else producto_id
                    raise ValueError(f"El proveedor no tiene registrado '{nombre}' en su inventario")
                if int(fila.stock or 0) < requerido:
                    nombre = fila.producto.nombre if fila.producto else producto_id
                    raise ValueError(f"Stock insuficiente del proveedor para '{nombre}'")
            return True

        for producto_id, requerido in requeridos.items():
            producto = db.session.get(Product, producto_id)
            if not producto or not producto.usa_stock_propio:
                continue
            if int(producto.stock_total or 0) < requerido:
                raise ValueError(f"Stock insuficiente para '{producto.nombre}'")
        return True

    def canje_directo_disponible(self, cantidad=1):
        """Producto elegible para canje directo sin configuracion adicional del cliente."""
        if not self.activo or not self.canjeable_con_puntos or not self.puntos_para_canje:
            return False
        if not self.visible_ahora:
            return False
        if self.es_combo and any(item.es_seleccionable for item in self.combo_items):
            return False
        return self.disponible_para_venta(cantidad)

    @property
    def precio_final(self):
        return float(self.precio)

    @property
    def combo_precio_modo_normalizado(self):
        modo = (self.combo_precio_modo or "fijo").strip().lower()
        return modo if modo in ("fijo", "descuento_porcentaje") else "fijo"

    @property
    def combo_descuento_pct_float(self):
        try:
            return float(self.combo_descuento_pct or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _money(value):
        return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def calcular_precio_base_componentes(self, componentes=None):
        """
        Estima el valor normal del combo.
        Fijos: suma directa. Seleccionables: usa las opciones predeterminadas o,
        si no existen, la opción disponible de menor precio.
        """
        if not self.es_combo:
            return self._money(self.precio)

        items = list(componentes) if componentes is not None else list(self.combo_items)
        total = Decimal("0.00")
        grupos = {}

        for item in items:
            componente = getattr(item, "componente", None)
            if not componente:
                continue
            cantidad = max(1, int(getattr(item, "cantidad", 1) or 1))
            subtotal = self._money(componente.precio_final) * cantidad
            if getattr(item, "es_seleccionable", False):
                grupo = (getattr(item, "grupo_seleccion", None) or "Seleccion").strip() or "Seleccion"
                grupos.setdefault(grupo.lower(), {
                    "max": max(1, int(getattr(item, "max_selecciones", 1) or 1)),
                    "opciones": [],
                })
                grupos[grupo.lower()]["opciones"].append(
                    (subtotal, bool(getattr(item, "es_predeterminado", False)))
                )
            else:
                total += subtotal

        for data in grupos.values():
            defaults = [price for price, is_default in data["opciones"] if is_default][:data["max"]]
            if defaults:
                total += sum(defaults, Decimal("0.00"))
            elif data["opciones"]:
                total += min(price for price, _ in data["opciones"])

        return self._money(total)

    def precio_combo_para_seleccion(self, seleccion_item_ids=None):
        """Calcula el precio unitario real de un combo para una selección concreta."""
        if not self.es_combo:
            return self._money(self.precio_final)

        componentes = list(self.combo_items)
        counts = {}
        for raw_id in (seleccion_item_ids or []):
            try:
                item_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            counts[item_id] = counts.get(item_id, 0) + 1

        seleccionables = [item for item in componentes if item.es_seleccionable]
        if not counts and seleccionables:
            grupos = {}
            for item in seleccionables:
                grupos.setdefault(item.grupo_seleccion or "Seleccion", []).append(item)
            for opciones in grupos.values():
                max_sel = max(1, int(opciones[0].max_selecciones or 1))
                min_sel = max(1, int(opciones[0].grupo.min_selecciones if opciones[0].grupo else 1))
                elegidas = [item for item in opciones if item.es_predeterminado][:max_sel]
                if len(elegidas) < min_sel:
                    disponibles = sorted(
                        [item for item in opciones if item not in elegidas],
                        key=lambda item: (self._money(item.componente.precio_final) if item.componente else Decimal("999999"), item.orden or 0),
                    )
                    elegidas.extend(disponibles[:min_sel - len(elegidas)])
                for item in elegidas:
                    counts[item.id] = counts.get(item.id, 0) + 1

        extras = Decimal("0.00")
        base_seleccion = Decimal("0.00")
        for item in componentes:
            componente = item.componente
            if not componente:
                continue
            cantidad_base = max(1, int(item.cantidad or 1))
            if item.es_seleccionable:
                veces = max(0, int(counts.get(item.id, 0)))
                if veces <= 0:
                    continue
                base_seleccion += self._money(componente.precio_final) * cantidad_base * veces
                extras += self._money(item.precio_extra or 0) * veces
            else:
                base_seleccion += self._money(componente.precio_final) * cantidad_base

        if self.combo_precio_modo_normalizado == "descuento_porcentaje":
            precio = self.precio_desde_descuento_combo(
                base=base_seleccion,
                descuento_pct=self.combo_descuento_pct_float,
            )
            return self._money(precio + extras)
        return self._money(self.precio_final + extras)

    def precio_desde_descuento_combo(self, base=None, descuento_pct=None):
        base_money = self._money(base if base is not None else self.combo_precio_base)
        pct = Decimal(str(descuento_pct if descuento_pct is not None else self.combo_descuento_pct_float))
        pct = min(Decimal("100"), max(Decimal("0"), pct))
        precio = base_money * (Decimal("1") - (pct / Decimal("100")))
        return max(Decimal("0.01"), self._money(precio))

    @property
    def combo_pricing_badge(self):
        if not self.es_combo:
            return None
        if self.combo_precio_modo_normalizado == "descuento_porcentaje" and self.combo_descuento_pct_float > 0:
            return f"{self.combo_descuento_pct_float:g}% dto"
        return "Precio fijo"

    @property
    def visible_ahora(self):
        """True si el producto debe mostrarse según horario y día de semana."""
        if not self.hora_inicio_visibilidad or not self.hora_fin_visibilidad:
            return True
        ahora = datetime.now().time()
        if self.hora_inicio_visibilidad <= self.hora_fin_visibilidad:
            en_horario = self.hora_inicio_visibilidad <= ahora <= self.hora_fin_visibilidad
        else:
            # Ventanas nocturnas, por ejemplo 22:00-02:00.
            en_horario = ahora >= self.hora_inicio_visibilidad or ahora <= self.hora_fin_visibilidad
        if not en_horario:
            return False
        if self.dias_semana_json:
            try:
                dias = json.loads(self.dias_semana_json)
                dia_actual = datetime.now().weekday()  # 0=lun, 6=dom
                if dia_actual not in dias:
                    return False
            except (json.JSONDecodeError, TypeError):
                pass
        return True

    @property
    def badge_info(self):
        """Dict con badges a mostrar en la tarjeta del catálogo."""
        badges = []
        if self.tipo_entrega in ("programado", "encargo") and self.fecha_llegada:
            badges.append({"tipo": "programado", "texto": f"Llega {self.fecha_llegada.strftime('%d/%m')}"})
        elif self.tipo_entrega in ("programado", "encargo"):
            badges.append({"tipo": "programado", "texto": "Programado"})
        if self.stock_mostrar_en_web:
            st = self.stock_total
            if st <= 5:
                badges.append({"tipo": "stock_bajo", "texto": f"¡Solo quedan {st}!"})
            elif st <= 20:
                badges.append({"tipo": "stock_medio", "texto": f"Quedan {st}"})
        if self.es_hipoalergenico:
            badges.append({"tipo": "hipo", "texto": "Hipoalergénico"})
        return badges

    @property
    def alergenos_lista(self):
        """Returns list of allergen codes, merging new JSON field with legacy text."""
        if self.alergenos_json:
            try:
                return json.loads(self.alergenos_json)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    @property
    def alergenos_iconos(self):
        """Returns list of (code, icon, label) for allergens in this product."""
        lista = self.alergenos_lista
        return [(code, icon, label) for code, icon, label in ALERGENOS_EU if code in lista]

    @property
    def rating_promedio(self):
        aprobadas = [r.calificacion for r in self.reviews.filter_by(aprobada=True)]
        return round(sum(aprobadas) / len(aprobadas), 1) if aprobadas else 0

    @staticmethod
    def ratings_bulk(producto_ids: list) -> dict:
        """Devuelve {producto_id: rating_promedio} para una lista de IDs en una sola query.
        Usar en lugar de llamar rating_promedio por cada producto en un bucle."""
        if not producto_ids:
            return {}
        from sqlalchemy import func
        rows = db.session.query(
            Review.producto_id,
            func.avg(Review.calificacion).label("avg"),
        ).filter(
            Review.producto_id.in_(producto_ids),
            Review.aprobada.is_(True),
        ).group_by(Review.producto_id).all()
        return {r.producto_id: round(float(r.avg), 1) for r in rows}

    def get_atributos(self):
        if not self.atributos_json:
            return {}
        try:
            return json.loads(self.atributos_json)
        except json.JSONDecodeError:
            return {}

    def set_atributos(self, data: dict):
        self.atributos_json = json.dumps(data, ensure_ascii=False) if data else None

    def descontar_stock(self, cantidad):
        """Descuenta stock FIFO por fecha de caducidad (más próxima primero).
        Usa SELECT FOR UPDATE en PostgreSQL para evitar race conditions bajo carga concurrente."""
        if cantidad <= 0:
            raise ValueError("La cantidad a descontar debe ser mayor que 0")
        if self.proveedor_despachador_id and not self.es_combo:
            fila = self._proveedor_producto_fila(for_update=True)
            if not fila:
                raise ValueError(f"El proveedor no tiene registrado '{self.nombre}' en su inventario")
            if int(fila.stock or 0) < cantidad:
                raise ValueError(f"Stock insuficiente del proveedor para '{self.nombre}'")
            fila.stock -= cantidad
            return
        if not self.usa_stock_propio:
            return
        pendiente = cantidad
        # with_for_update() bloquea las filas en PostgreSQL hasta el commit.
        lotes = Stock.query.filter_by(producto_id=self.id)\
                           .filter(Stock.cantidad > 0)\
                           .order_by(Stock.fecha_caducidad.asc().nullslast(), Stock.fecha_entrada.asc())\
                           .with_for_update()\
                           .all()
        disponible = sum(lote.cantidad for lote in lotes)
        if disponible < cantidad:
            raise ValueError(f"Stock insuficiente para '{self.nombre}'")

        for lote in lotes:
            if pendiente <= 0:
                break
            usar = min(lote.cantidad, pendiente)
            lote.cantidad -= usar
            pendiente -= usar

    def restaurar_stock(self, cantidad):
        """Devuelve unidades al lote más reciente; crea lote básico si no existe."""
        if self.proveedor_despachador_id and not self.es_combo:
            self._restaurar_stock_simple_proveedor(
                self.proveedor_despachador_id,
                cantidad,
            )
            return
        if not self.usa_stock_propio:
            return
        self._restaurar_stock_propio(cantidad)

    def restaurar_stock_pedido(self, cantidad, metadata=None):
        """Restaura stock usando el origen operativo congelado en el pedido."""
        snapshot = (metadata or {}).get("producto") if isinstance(metadata, dict) else None
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        es_combo = bool(snapshot.get("es_combo")) if "es_combo" in snapshot else bool(self.es_combo)
        proveedor_id = (
            snapshot.get("proveedor_despachador_id")
            if "proveedor_despachador_id" in snapshot
            else self.proveedor_despachador_id
        )
        if es_combo:
            self.restaurar_stock_combo(cantidad, metadata)
            return
        if proveedor_id:
            self._restaurar_stock_simple_proveedor(proveedor_id, cantidad)
            return
        self._restaurar_stock_propio(cantidad)

    def _restaurar_stock_simple_proveedor(self, proveedor_id, cantidad):
        try:
            proveedor_id = int(proveedor_id)
        except (TypeError, ValueError):
            return
        fila = ProveedorProducto.query.filter_by(
            proveedor_id=proveedor_id,
            producto_id=self.id,
        ).with_for_update().first()
        if fila:
            fila.stock = int(fila.stock or 0) + max(1, int(cantidad or 1))

    def _restaurar_stock_propio(self, cantidad):
        lotes = Stock.query.filter_by(producto_id=self.id)\
                           .order_by(Stock.fecha_entrada.desc()).all()
        if lotes:
            lotes[0].cantidad += cantidad
        else:
            db.session.add(Stock(producto_id=self.id, cantidad=cantidad))

    def descontar_stock_combo(self, cantidad, seleccion_item_ids=None):
        """Descuenta stock de componentes fijos y seleccionados de un combo.

        Si el combo tiene `proveedor_despachador_id`, descuenta del stock que el
        proveedor mantiene en `proveedor_productos`. Si NO tiene despachador,
        descuenta del Stock propio (FIFO por caducidad)."""
        if cantidad <= 0:
            raise ValueError("La cantidad a descontar debe ser mayor que 0")
        if not self.es_combo:
            self.descontar_stock(cantidad)
            return
        if self.proveedor_despachador_id:
            self._descontar_stock_combo_proveedor(cantidad, seleccion_item_ids)
            return

        componentes = list(self.combo_items)
        if not componentes:
            raise ValueError(f"El combo '{self.nombre}' no tiene componentes configurados")

        if isinstance(seleccion_item_ids, dict):
            seleccion_counts = {}
            for item_id, qty in seleccion_item_ids.items():
                try:
                    item_id = int(item_id)
                    qty = max(0, int(qty))
                except (TypeError, ValueError):
                    continue
                if qty > 0:
                    seleccion_counts[item_id] = seleccion_counts.get(item_id, 0) + qty
        else:
            seleccion_counts = {}
            for i in (seleccion_item_ids or []):
                try:
                    item_id = int(i)
                except (TypeError, ValueError):
                    continue
                seleccion_counts[item_id] = seleccion_counts.get(item_id, 0) + 1

        seleccionados = []
        grupos = {}
        for item in componentes:
            if not item.activo:
                continue
            if item.es_seleccionable:
                grupos.setdefault(item.grupo_seleccion or "Seleccion", []).append(item)
            else:
                seleccionados.append(item)

        for grupo, opciones in grupos.items():
            max_sel = max(1, opciones[0].max_selecciones or 1)
            grp = opciones[0].grupo
            min_sel = max(1, int(grp.min_selecciones if grp and grp.min_selecciones else 1))
            elegidos = []
            total_selections = 0
            for item in opciones:
                qty = seleccion_counts.get(item.id, 0)
                for _ in range(qty):
                    elegidos.append(item)
                total_selections += qty
            if not elegidos:
                opciones_ordenadas = sorted(opciones, key=lambda item: (not bool(item.es_predeterminado), item.orden or 0, item.id or 0))
                elegidos = [
                    item for item in opciones_ordenadas
                    if (
                        item.activo and item.componente
                        and (
                            not item.componente.usa_stock_propio
                            or item.componente.stock_total >= item.cantidad * cantidad
                        )
                    )
                ][:max_sel]
                total_selections = len(elegidos)
            if total_selections < min_sel:
                raise ValueError(f"El combo '{self.nombre}' requiere elegir al menos {min_sel} opción(es) de {grupo}")
            if total_selections > max_sel:
                raise ValueError(f"El combo '{self.nombre}' permite elegir hasta {max_sel} opción(es) de {grupo}")
            seleccionados.extend(elegidos)

        requeridos = {}
        for item in seleccionados:
            if not item.activo:
                raise ValueError(f"Componente inactivo en combo '{self.nombre}'")
            if not item.componente:
                raise ValueError(f"Componente inválido en combo '{self.nombre}'")
            if not item.componente.usa_stock_propio:
                continue
            requeridos[item.producto_id] = requeridos.get(item.producto_id, 0) + item.cantidad * cantidad

        producto_ids = list(requeridos.keys())
        if producto_ids:
            Stock.query.filter(Stock.producto_id.in_(producto_ids)).with_for_update().all()

        for producto_id, requerido in requeridos.items():
            producto = db.session.get(Product, producto_id)
            if not producto or producto.stock_total < requerido:
                nombre = producto.nombre if producto else producto_id
                raise ValueError(f"Stock insuficiente para '{nombre}'")

        for producto_id, requerido in requeridos.items():
            db.session.get(Product, producto_id).descontar_stock(requerido)

    def _descontar_stock_combo_proveedor(self, cantidad, seleccion_item_ids):
        """Descuenta del stock del proveedor despachador en proveedor_productos.

        Resuelve las opciones del combo igual que el flujo propio (default por
        es_predeterminado u orden) y aplica un descuento atómico por SKU."""
        proveedor_id = self.proveedor_despachador_id
        if not proveedor_id:
            raise ValueError(f"El combo '{self.nombre}' no tiene proveedor despachador")

        componentes = list(self.combo_items)
        if not componentes:
            raise ValueError(f"El combo '{self.nombre}' no tiene componentes configurados")

        if isinstance(seleccion_item_ids, dict):
            seleccion_counts = {}
            for item_id, qty in seleccion_item_ids.items():
                try:
                    item_id = int(item_id)
                    qty = max(0, int(qty))
                except (TypeError, ValueError):
                    continue
                if qty > 0:
                    seleccion_counts[item_id] = seleccion_counts.get(item_id, 0) + qty
        else:
            seleccion_counts = {}
            for i in (seleccion_item_ids or []):
                try:
                    item_id = int(i)
                except (TypeError, ValueError):
                    continue
                seleccion_counts[item_id] = seleccion_counts.get(item_id, 0) + 1

        seleccionados = []
        grupos = {}
        for item in componentes:
            if not item.activo:
                continue
            if item.es_seleccionable:
                grupos.setdefault(item.grupo_seleccion or "Seleccion", []).append(item)
            else:
                seleccionados.append(item)

        for grupo, opciones in grupos.items():
            max_sel = max(1, opciones[0].max_selecciones or 1)
            grp = opciones[0].grupo
            min_sel = max(1, int(grp.min_selecciones if grp and grp.min_selecciones else 1))
            elegidos = []
            total_selections = 0
            for item in opciones:
                qty = seleccion_counts.get(item.id, 0)
                for _ in range(qty):
                    elegidos.append(item)
                total_selections += qty
            if not elegidos:
                opciones_ordenadas = sorted(
                    opciones,
                    key=lambda item: (not bool(item.es_predeterminado), item.orden or 0, item.id or 0),
                )
                elegidos = [item for item in opciones_ordenadas if item.activo and item.componente][:max_sel]
                total_selections = len(elegidos)
            if total_selections < min_sel:
                raise ValueError(f"El combo '{self.nombre}' requiere elegir al menos {min_sel} opción(es) de {grupo}")
            if total_selections > max_sel:
                raise ValueError(f"El combo '{self.nombre}' permite elegir hasta {max_sel} opción(es) de {grupo}")
            seleccionados.extend(elegidos)

        requeridos = {}
        for item in seleccionados:
            if not item.activo:
                raise ValueError(f"Componente inactivo en combo '{self.nombre}'")
            if not item.componente:
                raise ValueError(f"Componente inválido en combo '{self.nombre}'")
            requeridos[item.producto_id] = requeridos.get(item.producto_id, 0) + item.cantidad * cantidad

        if not requeridos:
            return

        filas = ProveedorProducto.query.filter(
            ProveedorProducto.proveedor_id == proveedor_id,
            ProveedorProducto.producto_id.in_(list(requeridos.keys())),
        ).with_for_update().all()
        por_producto = {fila.producto_id: fila for fila in filas}

        for producto_id, requerido in requeridos.items():
            fila = por_producto.get(producto_id)
            if not fila or not fila.activo:
                nombre = db.session.get(Product, producto_id)
                nombre = nombre.nombre if nombre else producto_id
                raise ValueError(
                    f"El proveedor no tiene registrado '{nombre}' en su inventario"
                )
            if fila.stock < requerido:
                nombre = fila.producto.nombre if fila.producto else producto_id
                raise ValueError(f"Stock insuficiente del proveedor para '{nombre}'")

        for producto_id, requerido in requeridos.items():
            por_producto[producto_id].stock -= requerido

    def restaurar_stock_combo(self, cantidad, metadata=None):
        """Devuelve al stock los componentes de un combo a partir de su metadata."""
        if not self.es_combo:
            self.restaurar_stock(cantidad)
            return
        snapshot = (metadata or {}).get("producto") if isinstance(metadata, dict) else None
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        proveedor_id = (
            snapshot.get("proveedor_despachador_id")
            if "proveedor_despachador_id" in snapshot
            else self.proveedor_despachador_id
        )
        if proveedor_id:
            self._restaurar_stock_combo_proveedor(cantidad, metadata, proveedor_id=proveedor_id)
            return

        meta_combo = (metadata or {}).get("combo") or {}
        componentes = []
        componentes.extend(meta_combo.get("componentes") or [])
        for grupo in meta_combo.get("selecciones") or []:
            componentes.extend(grupo.get("opciones") or [])

        if not componentes:
            componentes = [
                {
                    "producto_id": item.producto_id,
                    "cantidad": item.cantidad,
                }
                for item in self.combo_items
                if not item.es_seleccionable
            ]

        for comp in componentes:
            producto_id = comp.get("producto_id")
            if not producto_id:
                continue
            producto = db.session.get(Product, producto_id)
            if producto and producto.usa_stock_propio:
                producto.restaurar_stock(max(1, int(comp.get("cantidad") or 1)) * cantidad)

    def _restaurar_stock_combo_proveedor(self, cantidad, metadata, proveedor_id=None):
        """Devuelve unidades al stock del proveedor despachador.

        Solo restaura componentes que estén registrados en `proveedor_productos`
        para el proveedor del combo; ignora silenciosamente los que no
        encuentre (esto puede ocurrir si el proveedor dejó de stockear ese SKU
        después del pedido)."""
        proveedor_id = proveedor_id if proveedor_id is not None else self.proveedor_despachador_id
        if not proveedor_id:
            return

        meta_combo = (metadata or {}).get("combo") or {}
        componentes = []
        componentes.extend(meta_combo.get("componentes") or [])
        for grupo in meta_combo.get("selecciones") or []:
            componentes.extend(grupo.get("opciones") or [])

        if not componentes:
            componentes = [
                {"producto_id": item.producto_id, "cantidad": item.cantidad}
                for item in self.combo_items
                if not item.es_seleccionable
            ]

        requeridos = {}
        for comp in componentes:
            producto_id = comp.get("producto_id")
            if not producto_id:
                continue
            qty = max(1, int(comp.get("cantidad") or 1)) * cantidad
            requeridos[producto_id] = requeridos.get(producto_id, 0) + qty

        if not requeridos:
            return

        filas = ProveedorProducto.query.filter(
            ProveedorProducto.proveedor_id == proveedor_id,
            ProveedorProducto.producto_id.in_(list(requeridos.keys())),
        ).with_for_update().all()
        por_producto = {fila.producto_id: fila for fila in filas}
        for producto_id, qty in requeridos.items():
            fila = por_producto.get(producto_id)
            if fila:
                fila.stock += qty

    def __repr__(self):
        return f"<Product {self.nombre}>"


class ComboGroup(db.Model):
    __tablename__ = "combo_groups"

    id = db.Column(db.Integer, primary_key=True)
    combo_id = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    nombre = db.Column(db.String(80), nullable=False)
    tipo = db.Column(db.String(20), nullable=False, default="fijo")  # fijo | seleccion
    min_selecciones = db.Column(db.Integer, nullable=False, default=0)
    max_selecciones = db.Column(db.Integer, nullable=False, default=1)
    orden = db.Column(db.Integer, nullable=False, default=0)
    requerido = db.Column(db.Boolean, default=True)
    descripcion = db.Column(db.Text)
    creado_en = db.Column(db.DateTime, default=utcnow)

    items = db.relationship(
        "ComboItem",
        backref="grupo",
        lazy="dynamic",
        cascade="all, delete-orphan",
        order_by="ComboItem.orden.asc(), ComboItem.id.asc()",
    )

    __table_args__ = (
        db.Index("ix_combo_groups_combo_id", "combo_id"),
        db.Index("ix_combo_groups_combo_orden", "combo_id", "orden"),
    )

    @property
    def es_seleccion(self):
        return (self.tipo or "fijo") == "seleccion"

    @property
    def nombre_publico(self):
        return (self.nombre or ("Eleccion" if self.es_seleccion else "Incluido")).strip()


class ComboItem(db.Model):
    __tablename__ = "combo_items"

    id = db.Column(db.Integer, primary_key=True)
    combo_id = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    combo_group_id = db.Column(db.Integer, db.ForeignKey("combo_groups.id", ondelete="CASCADE"))
    producto_id = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    orden = db.Column(db.Integer, nullable=False, default=0)
    precio_extra = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    es_predeterminado = db.Column(db.Boolean, default=False)
    activo = db.Column(db.Boolean, default=True)
    notas_preparacion = db.Column(db.Text)

    # Selección por el cliente: si es_seleccionable=True, el cliente elige entre las
    # opciones del mismo grupo_seleccion. Si False, es fijo y no se puede cambiar.
    es_seleccionable = db.Column(db.Boolean, default=False)
    # Nombre del grupo de selección (ej: "Bebida", "Acompañamiento").
    # Varios ComboItem del mismo combo con el mismo grupo = alternativas entre las que el cliente elige.
    grupo_seleccion = db.Column(db.String(50))
    # Cuántos productos del grupo puede elegir el cliente (default 1).
    max_selecciones = db.Column(db.Integer, default=1)

    componente = db.relationship("Product", foreign_keys=[producto_id])

    __table_args__ = (
        db.Index("ix_combo_items_combo_id", "combo_id"),
        db.Index("ix_combo_items_group_id", "combo_group_id"),
        db.Index("ix_combo_items_producto_id", "producto_id"),
    )

    @property
    def grupo_display(self):
        if self.grupo:
            return self.grupo.nombre_publico
        return (self.grupo_seleccion or "Seleccion").strip() or "Seleccion"

    @property
    def precio_extra_float(self):
        try:
            return float(self.precio_extra or 0)
        except (TypeError, ValueError):
            return 0.0


# ─────────────────────────────────────────────
# STOCK
# ─────────────────────────────────────────────

class Stock(db.Model):
    __tablename__ = "stock"

    id = db.Column(db.Integer, primary_key=True)
    producto_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False, default=0)
    unidad = db.Column(db.String(20), default="unidad")
    lote = db.Column(db.String(50))
    fecha_entrada = db.Column(db.Date, default=date.today)
    fecha_caducidad = db.Column(db.Date)
    alerta_dias = db.Column(db.Integer, default=7)
    ubicacion = db.Column(db.String(100))

    __table_args__ = (
        db.Index("ix_stock_producto_id",    "producto_id"),
        db.Index("ix_stock_caducidad",      "fecha_caducidad"),
    )

    @property
    def dias_para_caducar(self):
        if self.fecha_caducidad:
            return (self.fecha_caducidad - date.today()).days
        return None

    @property
    def esta_en_alerta(self):
        d = self.dias_para_caducar
        return d is not None and 0 <= d <= self.alerta_dias

    @property
    def esta_caducado(self):
        d = self.dias_para_caducar
        return d is not None and d < 0


# ─────────────────────────────────────────────
# PEDIDOS
# ─────────────────────────────────────────────

ESTADOS_PEDIDO = ["pendiente", "armando", "listo", "en_ruta", "entregado", "cancelado"]


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    numero_pedido = db.Column(db.String(20), unique=True, nullable=False)
    cliente_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    estado = db.Column(db.String(30), default="pendiente", nullable=False)
    origen = db.Column(db.String(20), default="online")   # online / presencial

    subtotal = db.Column(db.Numeric(10, 2), nullable=False)
    descuento = db.Column(db.Numeric(10, 2), default=0)
    total = db.Column(db.Numeric(10, 2), nullable=False)
    cupon_id = db.Column(db.Integer, db.ForeignKey("coupons.id"))
    puntos_usados = db.Column(db.Integer, default=0)
    puntos_ganados = db.Column(db.Integer, default=0)
    metodo_pago = db.Column(db.String(30))
    direccion_entrega = db.Column(db.Text)
    notas = db.Column(db.Text)

    # Staff asignado
    preparador_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    repartidor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    cajero_id = db.Column(db.Integer, db.ForeignKey("users.id"))  # quien cobró en POS

    # ── Señal del bar (proveedor) ────────────────────────────────────
    # No cambia la máquina de estados; es un flag informativo.
    proveedor_preparado = db.Column(db.Boolean, default=False, nullable=False)
    proveedor_preparado_en = db.Column(db.DateTime)

    creado_en = db.Column(db.DateTime, default=utcnow)
    entregado_en = db.Column(db.DateTime)

    zona_id = db.Column(db.Integer, db.ForeignKey("zonas_entrega.id"))
    afiliado_codigo_id = db.Column(db.Integer, db.ForeignKey("affiliate_codes.id"))
    es_entrega_epicentro = db.Column(db.Boolean, default=True)

    # ── Confirmación de entrega ──────────────────────────────────────
    # Código de 6 dígitos que se envía al cliente cuando el pedido sale
    codigo_confirmacion = db.Column(db.String(8))
    codigo_confirmado_en = db.Column(db.DateTime)
    intentos_codigo = db.Column(db.Integer, default=0)

    # ── Confirmación de pago digital ─────────────────────────────────
    pago_confirmado = db.Column(db.Boolean, default=False)
    pago_confirmado_por = db.Column(db.Integer, db.ForeignKey("users.id"))
    pago_confirmado_en = db.Column(db.DateTime)

    # ── Notificaciones WhatsApp ──────────────────────────────────────
    whatsapp_enviado_confirmacion = db.Column(db.Boolean, default=False)

    # ── Reseña vía WhatsApp ──────────────────────────────────────────
    resena_calificacion = db.Column(db.Integer)
    resena_comentario = db.Column(db.Text)
    resena_enviada = db.Column(db.Boolean, default=False)

    items = db.relationship("OrderItem", backref="pedido", lazy="dynamic", cascade="all, delete-orphan")
    eventos = db.relationship("OrderEvent", backref="pedido", lazy="dynamic", cascade="all, delete-orphan")
    notificaciones = db.relationship("NotificationOutbox", backref="pedido", lazy="dynamic")
    estados_proveedor = db.relationship(
        "OrderProviderStatus",
        backref="pedido",
        lazy="select",
        cascade="all, delete-orphan",
    )
    cupon = db.relationship("Coupon", backref="pedidos")
    cajero = db.relationship("User", foreign_keys=[cajero_id])
    zona = db.relationship("ZonaEntrega", foreign_keys=[zona_id])
    afiliado_codigo_rel = db.relationship("AffiliateCode", foreign_keys=[afiliado_codigo_id])
    confirmador_pago = db.relationship("User", foreign_keys=[pago_confirmado_por])

    __table_args__ = (
        db.Index("ix_orders_estado",        "estado"),
        db.Index("ix_orders_creado_en",     "creado_en"),
        db.Index("ix_orders_entregado_en",  "entregado_en"),
        db.Index("ix_orders_cliente_id",    "cliente_id"),
        db.Index("ix_orders_preparador",    "preparador_id"),
        db.Index("ix_orders_repartidor",    "repartidor_id"),
    )

    @staticmethod
    def generar_numero(origen="online"):
        """Genera un número de pedido corto y memorable, tipo #1024 o #A45.

        Estrategia:
          - Toma el siguiente correlativo desde el id de la última fila + 1.
            Si el id es 1024 → #1024.
          - Si ya existe (cola), prueba con +1 hasta encontrar uno libre.
          - Sin prefijo de fecha porque encarece la lectura por teléfono.
            El origen lo guarda Order.origen, no hace falta meterlo en el número.
        El tope de la columna sigue siendo VARCHAR(20), suficiente para más de
        un millón de pedidos.
        """
        ultimo = db.session.execute(
            text("SELECT COALESCE(MAX(id), 0) FROM orders")
        ).scalar() or 0
        # Empezamos en max(ultimo+1, 1001) para que los primeros pedidos
        # tampoco sean #1, #2 (más feos al teléfono).
        siguiente = max(int(ultimo) + 1, 1001)
        for _ in range(50):
            numero = f"#{siguiente}"
            existe = db.session.execute(
                text("SELECT 1 FROM orders WHERE numero_pedido = :numero LIMIT 1"),
                {"numero": numero},
            ).scalar()
            if not existe:
                return numero
            siguiente += 1
        # Fallback: timestamp corto (5 caracteres base36) por si todo falla
        import time as _time
        ts = _time.time_ns() % 1_000_000_000
        return f"#{ts:09d}"

    def generar_codigo_confirmacion(self):
        """Genera un código de 6 dígitos criptográficamente seguro para confirmar la entrega."""
        import secrets as _secrets
        self.codigo_confirmacion = str(_secrets.randbelow(1_000_000)).zfill(6)
        self.intentos_codigo = 0
        return self.codigo_confirmacion

    @property
    def tiene_proveedores(self):
        return bool(self.estados_proveedor)

    @property
    def proveedores_listos(self):
        estados = list(self.estados_proveedor)
        if estados:
            return all(estado.preparado for estado in estados)
        return bool(self.proveedor_preparado)

    @property
    def proveedores_pendientes(self):
        return [estado for estado in self.estados_proveedor if not estado.preparado]

    @property
    def es_pedido_almacen(self):
        items = list(self.items)
        return bool(items) and all(
            (item.display_canal_preparacion or "cocina").strip().lower() == "almacen"
            for item in items
        )

    @property
    def costo_envio(self):
        """Importe de entrega cobrado, reconstruido desde el desglose persistido."""
        subtotal = Decimal(str(self.subtotal or 0))
        descuento = Decimal(str(self.descuento or 0))
        total = Decimal(str(self.total or 0))
        return max(Decimal("0.00"), total - subtotal + descuento).quantize(Decimal("0.01"))

    def confirmar_entrega_con_codigo(self, codigo_ingresado):
        """
        Valida el código del repartidor. Retorna (ok, mensaje).
        Máximo 3 intentos fallidos.
        """
        if self.intentos_codigo >= 3:
            return False, "Demasiados intentos fallidos. Contacta al admin."
        if self.codigo_confirmacion and self.codigo_confirmacion == str(codigo_ingresado).strip():
            self.codigo_confirmado_en = utcnow()
            return True, "OK"
        self.intentos_codigo = (self.intentos_codigo or 0) + 1
        restantes = 3 - self.intentos_codigo
        return False, f"Código incorrecto. {restantes} intento(s) restante(s)."

    def avanzar_estado(self):
        if self.estado not in ESTADOS_PEDIDO:
            raise ValueError(f"Estado de pedido desconocido: {self.estado!r}")
        if self.estado in ("entregado", "cancelado"):
            raise ValueError(f"No se puede avanzar un pedido en estado '{self.estado}'")
        idx = ESTADOS_PEDIDO.index(self.estado)
        self.estado = ESTADOS_PEDIDO[idx + 1]
        if self.estado == "en_ruta":
            self.generar_codigo_confirmacion()
        if self.estado == "entregado":
            self.entregado_en = utcnow()

    def cancelar(self, forzar_desde_entregado=False):
        if self.estado == "cancelado":
            raise ValueError("El pedido ya está cancelado")
        if self.estado == "entregado" and not forzar_desde_entregado:
            raise ValueError("No se puede cancelar un pedido ya entregado")
        # Devolver stock al lote más reciente (LIFO — coherente con FIFO de salida).
        # POS descuenta stock de TODOS los productos; web/bot solo descuenta "inmediato".
        # Por tanto, solo restauramos stock si el producto es inmediato O el pedido es presencial.
        for item in self.items:
            producto = item.producto
            if not producto:
                continue
            metadata = item.get_metadata()
            restaurar = (
                self.origen == "presencial"
                or (item.display_tipo_entrega or "inmediato") == "inmediato"
            )
            if not restaurar:
                continue
            producto.restaurar_stock_pedido(item.cantidad, metadata)
        # Ajuste de puntos al cancelar
        if self.cliente_id:
            # Distintos pedidos del mismo cliente pueden cancelarse a la vez.
            # Bloqueamos su saldo para que ninguna devolución pise a otra.
            cliente = (
                User.query.filter_by(id=self.cliente_id)
                .with_for_update()
                .populate_existing()
                .first()
            )
            if cliente:
                # Revertir puntos SOLO si fueron realmente otorgados (PointsLog tipo='ganado')
                if self.puntos_ganados:
                    log_ganado = PointsLog.query.filter_by(
                        cliente_id=self.cliente_id,
                        pedido_id=self.id,
                        tipo="ganado",
                    ).first()
                    if log_ganado:
                        puntos_a_quitar = min(self.puntos_ganados, cliente.puntos)
                        if puntos_a_quitar > 0:
                            cliente.puntos -= puntos_a_quitar
                            db.session.add(PointsLog(
                                cliente_id=self.cliente_id,
                                pedido_id=self.id,
                                tipo="cancelado",
                                cantidad=-puntos_a_quitar,
                                descripcion=f"Puntos ganados revertidos — cancelación {self.numero_pedido}",
                            ))
                # Devolver los puntos que gastó como descuento
                if self.puntos_usados:
                    cliente.puntos += self.puntos_usados
                    db.session.add(PointsLog(
                        cliente_id=self.cliente_id,
                        pedido_id=self.id,
                        tipo="devuelto",
                        cantidad=self.puntos_usados,
                        descripcion=f"Puntos de canje devueltos — cancelación {self.numero_pedido}",
                    ))
        if self.cupon:
            self.cupon.revertir_uso()
        if self.afiliado_codigo_rel:
            self.afiliado_codigo_rel.revertir_uso()

        # Desvincular primero el uso afiliado: PostgreSQL no permite eliminar
        # StaffPayment mientras AffiliateUse conserve su clave foránea.
        for uso in AffiliateUse.query.filter_by(
            pedido_id=self.id,
            comision_pagada=False,
        ).all():
            uso.comision_generada = 0
            uso.staff_payment_id = None
        for pago in StaffPayment.query.filter_by(
            pedido_id=self.id, tipo="comision", pagado=False
        ).all():
            db.session.delete(pago)
        self.estado = "cancelado"

    def __repr__(self):
        return f"<Order {self.numero_pedido} [{self.estado}]>"


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unit = db.Column(db.Numeric(10, 2), nullable=False)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)
    notas = db.Column(db.Text)
    metadata_json = db.Column(db.Text)

    def get_metadata(self):
        if not self.metadata_json:
            return {}
        try:
            return json.loads(self.metadata_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    @property
    def producto_snapshot(self):
        """Datos del producto congelados en el momento de crear el pedido."""
        return (self.get_metadata().get("producto") or {})

    @property
    def display_nombre(self):
        return (
            self.producto_snapshot.get("nombre")
            or (self.producto.nombre if self.producto else None)
            or "Producto eliminado"
        )

    @property
    def display_imagen_url(self):
        return self.producto_snapshot.get("imagen_url") or (
            self.producto.imagen_url if self.producto else None
        )

    @property
    def display_es_combo(self):
        if "es_combo" in self.producto_snapshot:
            return bool(self.producto_snapshot.get("es_combo"))
        return bool(self.producto.es_combo) if self.producto else False

    @property
    def display_tipo_entrega(self):
        return self.producto_snapshot.get("tipo_entrega") or (
            self.producto.tipo_entrega if self.producto else "inmediato"
        )

    @property
    def display_canal_preparacion(self):
        return self.producto_snapshot.get("canal_preparacion") or (
            self.producto.canal_preparacion if self.producto else "cocina"
        )

    @property
    def display_fecha_entrega(self):
        raw = (
            self.get_metadata().get("entrega_programada")
            or self.producto_snapshot.get("fecha_llegada")
        )
        if raw:
            try:
                return date.fromisoformat(str(raw))
            except (TypeError, ValueError):
                pass
        return self.producto.fecha_llegada if self.producto else None

    @property
    def display_categoria(self):
        return self.producto_snapshot.get("categoria_nombre") or (
            self.producto.categoria.nombre if self.producto and self.producto.categoria else None
        )

    @property
    def display_origen_pais(self):
        return self.producto_snapshot.get("origen_pais") or (
            self.producto.origen_pais if self.producto else None
        )

    @property
    def display_alergenos(self):
        raw = self.producto_snapshot.get("alergenos_json")
        if raw:
            try:
                return json.loads(raw) if isinstance(raw, str) else list(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                return []
        return self.producto.alergenos_lista if self.producto else []

    @property
    def reward_metadata(self):
        return self.get_metadata().get("reward") or {}

    @property
    def es_canje_puntos(self):
        return self.reward_metadata.get("tipo") == "producto_puntos"


class OrderProviderStatus(db.Model):
    """Estado independiente de cada proveedor (restaurante) que participa en un pedido."""
    __tablename__ = "order_provider_status"

    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(
        db.Integer,
        db.ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    proveedor_id = db.Column(
        db.Integer, db.ForeignKey("proveedores.id"), nullable=False
    )
    preparado = db.Column(db.Boolean, default=False, nullable=False)
    preparado_en = db.Column(db.DateTime)
    actualizado_por = db.Column(db.Integer, db.ForeignKey("users.id"))

    proveedor = db.relationship("Proveedor", foreign_keys=[proveedor_id])
    actor = db.relationship("User", foreign_keys=[actualizado_por])

    __table_args__ = (
        db.UniqueConstraint(
            "pedido_id",
            "proveedor_id",
            name="uq_order_provider_status_order_provider",
        ),
        db.Index("ix_order_provider_status_proveedor", "proveedor_id", "preparado"),
        db.Index("ix_order_provider_status_pedido", "pedido_id"),
    )


class IdempotencyKey(db.Model):
    """Garantiza que una operación crítica (crear pedido) no se ejecute dos
    veces aunque el cliente reintente. La unidad de unicidad es (scope, key)."""
    __tablename__ = "idempotency_keys"

    id = db.Column(db.Integer, primary_key=True)
    # Espacio de nombres: 'checkout_web' | 'pos' | 'bot' — evita colisiones de
    # keys autogenerados entre canales distintos.
    scope = db.Column(db.String(40), nullable=False)
    # Clave proporcionada por el cliente (Idempotency-Key header) o derivada
    # del cuerpo de la petición. Hasta 120 chars para UUIDs largos.
    key = db.Column(db.String(120), nullable=False)
    # SHA-256 del cuerpo de la petición. Si el mismo (scope, key) llega con un
    # body distinto, devolvemos HTTP 409 (conflict).
    request_hash = db.Column(db.String(64), nullable=False)
    # Snapshot de la primera respuesta ganadora.
    response_status = db.Column(db.Integer, nullable=False)
    response_body = db.Column(db.Text)
    # Enlaces opcionales para auditoría.
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    creado_en = db.Column(db.DateTime, default=utcnow, nullable=False)
    expira_en = db.Column(db.DateTime, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),
        db.Index("ix_idempotency_expira", "expira_en"),
    )


class OrderEvent(db.Model):
    """Timeline auditable de cambios relevantes de un pedido."""
    __tablename__ = "order_events"

    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    estado_anterior = db.Column(db.String(30))
    estado_nuevo = db.Column(db.String(30))
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    canal = db.Column(db.String(30))
    detalle = db.Column(db.Text)
    metadata_json = db.Column(db.Text)
    creado_en = db.Column(db.DateTime, default=utcnow, nullable=False)

    actor = db.relationship("User", foreign_keys=[actor_id])

    __table_args__ = (
        db.Index("ix_order_events_pedido_id", "pedido_id"),
        db.Index("ix_order_events_creado_en", "creado_en"),
        db.Index("ix_order_events_tipo", "tipo"),
    )

    def get_metadata(self):
        if not self.metadata_json:
            return {}
        try:
            return json.loads(self.metadata_json)
        except (json.JSONDecodeError, TypeError):
            return {}


class NotificationOutbox(db.Model):
    """Registro persistente de notificaciones externas y sus intentos."""
    __tablename__ = "notification_outbox"

    id = db.Column(db.Integer, primary_key=True)
    canal = db.Column(db.String(30), nullable=False)
    evento = db.Column(db.String(60), nullable=False)
    destinatario = db.Column(db.String(200), nullable=False)
    payload_json = db.Column(db.Text, nullable=False)
    estado = db.Column(db.String(20), default="pending", nullable=False)
    intentos = db.Column(db.Integer, default=0, nullable=False)
    max_intentos = db.Column(db.Integer, default=3, nullable=False)
    siguiente_intento_en = db.Column(db.DateTime)
    ultimo_error = db.Column(db.Text)
    pedido_id = db.Column(db.Integer, db.ForeignKey("orders.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    creado_en = db.Column(db.DateTime, default=utcnow, nullable=False)
    enviado_en = db.Column(db.DateTime)

    usuario = db.relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        db.Index("ix_notification_outbox_estado", "estado"),
        db.Index("ix_notification_outbox_pedido_id", "pedido_id"),
        db.Index("ix_notification_outbox_siguiente", "siguiente_intento_en"),
    )

    def get_payload(self):
        try:
            return json.loads(self.payload_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}


def snapshot_producto_para_pedido(producto):
    """Crea una foto estable del producto para trazabilidad de pedidos."""
    if not producto:
        return {}
    return {
        "id": producto.id,
        "nombre": producto.nombre,
        "descripcion": producto.descripcion,
        "precio": float(producto.precio or 0),
        "precio_final": float(producto.precio_final or 0),
        "precio_costo": float(producto.precio_costo or 0) if producto.precio_costo is not None else None,
        "categoria_id": producto.categoria_id,
        "categoria_nombre": producto.categoria.nombre if producto.categoria else None,
        "imagen_url": producto.imagen_url,
        "origen_pais": producto.origen_pais,
        "es_combo": bool(producto.es_combo),
        "combo_precio_modo": producto.combo_precio_modo_normalizado if producto.es_combo else None,
        "combo_descuento_pct": float(producto.combo_descuento_pct or 0) if producto.es_combo else 0,
        "combo_precio_base": float(producto.combo_precio_base or 0) if producto.es_combo else 0,
        "tipo_producto": producto.tipo_producto or "simple",
        "tipo_entrega": producto.tipo_entrega or "inmediato",
        "fecha_llegada": producto.fecha_llegada.isoformat() if producto.fecha_llegada else None,
        "dias_anticipacion_encargo": int(producto.dias_anticipacion_encargo or 0),
        "canal_preparacion": producto.canal_preparacion or "cocina",
        # LEGACY: proveedor_id (FK users) — retenido para no romper trazabilidad antigua.
        "proveedor_id": producto.proveedor_id,
        # Despachador externo (FK proveedores), para producto simple o combo.
        "proveedor_despachador_id": producto.proveedor_despachador_id,
        "proveedor_despachador_nombre": (
            producto.proveedor_despachador.nombre
            if producto.proveedor_despachador else None
        ),
        # Congelamos modelo de acuerdo y comisión al crear el pedido para que
        # cambios futuros no rompan liquidaciones de pedidos pasados.
        "proveedor_modelo_acuerdo": (
            producto.proveedor_despachador.modelo_acuerdo
            if producto.proveedor_despachador else None
        ),
        "proveedor_comision_pct": (
            float(producto.proveedor_despachador.comision_pct or 0)
            if producto.proveedor_despachador else None
        ),
        "stock_mostrar_en_web": bool(producto.stock_mostrar_en_web),
        "canjeable_con_puntos": bool(producto.canjeable_con_puntos),
        "puntos_para_canje": int(producto.puntos_para_canje or 0),
        "es_hipoalergenico": bool(producto.es_hipoalergenico),
        "alergenos_json": producto.alergenos_json,
        "atributos": producto.get_atributos(),
    }


def metadata_componente_combo(combo_item, proveedor_despachador_id=None):
    """Snapshot mínimo de un componente para no confundir stock propio y proveedor.

    Si `proveedor_despachador_id` viene informado (combo despachado por un
    proveedor), congela también el `precio_costo` del SKU en `proveedor_productos`
    en el momento del pedido para la liquidación posterior.
    """
    componente = combo_item.componente if combo_item else None
    snapshot = {
        "combo_item_id": combo_item.id if combo_item else None,
        "producto_id": combo_item.producto_id if combo_item else None,
        "nombre": componente.nombre if componente else "",
        "cantidad": combo_item.cantidad if combo_item else 1,
        # LEGACY: proveedor_id apuntaba al User-proveedor del componente. Se mantiene
        # para no romper la lectura de pedidos antiguos. La lógica nueva ignora este
        # campo y usa proveedor_despachador_id del combo padre.
        "proveedor_id": componente.proveedor_id if componente else None,
        "proveedor_nombre": (
            componente.proveedor.nombre
            if componente and componente.proveedor else None
        ),
        "canal_preparacion": (
            componente.canal_preparacion if componente else "cocina"
        ) or "cocina",
        "tipo_entrega": (
            componente.tipo_entrega if componente else "inmediato"
        ) or "inmediato",
        "proveedor_despachador_id": proveedor_despachador_id,
        "origen_operativo": "proveedor" if proveedor_despachador_id else "propio",
        "precio_costo_congelado": None,
    }
    if proveedor_despachador_id and componente:
        fila = ProveedorProducto.query.filter_by(
            proveedor_id=proveedor_despachador_id,
            producto_id=componente.id,
        ).first()
        if fila and fila.precio_costo is not None:
            snapshot["precio_costo_congelado"] = float(fila.precio_costo)
    return snapshot


def metadata_item_pedido(producto, metadata=None):
    """Fusiona metadata de combo/flujo con snapshot estable del producto."""
    data = dict(metadata or {})
    data["producto"] = snapshot_producto_para_pedido(producto)
    return data


# ─────────────────────────────────────────────
# RESEÑAS
# ─────────────────────────────────────────────

class Review(db.Model):
    __tablename__ = "reviews"

    id = db.Column(db.Integer, primary_key=True)
    producto_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    cliente_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    pedido_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=True)
    calificacion = db.Column(db.Integer, nullable=False)
    comentario = db.Column(db.Text)
    aprobada = db.Column(db.Boolean, default=False)
    creado_en = db.Column(db.DateTime, default=utcnow)

    pedido = db.relationship("Order", backref="reviews")


# ─────────────────────────────────────────────
# CUPONES
# ─────────────────────────────────────────────

class Coupon(db.Model):
    __tablename__ = "coupons"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(30), unique=True, nullable=False)
    descripcion = db.Column(db.String(200))
    tipo = db.Column(db.String(20), nullable=False)
    valor = db.Column(db.Numeric(10, 2), nullable=False)
    minimo_pedido = db.Column(db.Numeric(10, 2), default=0)
    usos_maximos = db.Column(db.Integer)
    usos_actuales = db.Column(db.Integer, default=0)
    activo = db.Column(db.Boolean, default=True)
    fecha_inicio = db.Column(db.Date)
    fecha_fin = db.Column(db.Date)

    def es_valido(self):
        hoy = date.today()
        if not self.activo:
            return False, "Cupón inactivo"
        if self.fecha_inicio and hoy < self.fecha_inicio:
            return False, "Cupón no vigente aún"
        if self.fecha_fin and hoy > self.fecha_fin:
            return False, "Cupón expirado"
        if self.usos_maximos and self.usos_actuales >= self.usos_maximos:
            return False, "Cupón agotado"
        return True, "OK"

    def calcular_descuento(self, subtotal):
        valido, msg = self.es_valido()
        if not valido:
            raise ValueError(msg)
        if float(subtotal) < float(self.minimo_pedido):
            raise ValueError(f"Mínimo de pedido: €{self.minimo_pedido}")
        if self.tipo == "porcentaje":
            return min(round(float(subtotal) * float(self.valor) / 100, 2), float(subtotal))
        elif self.tipo == "monto_fijo":
            return min(float(self.valor), float(subtotal))
        elif self.tipo == "envio_gratis":
            return 0
        return 0

    def registrar_uso(self):
        # UPDATE condicional atómico: solo incrementa si no se ha alcanzado el límite.
        # En PostgreSQL con READ COMMITTED, la segunda transacción concurrente verá
        # la fila actualizada por la primera y la condición fallará → rowcount = 0.
        if self.usos_maximos:
            result = db.session.execute(
                text("UPDATE coupons SET usos_actuales = usos_actuales + 1 WHERE id = :id AND usos_actuales < usos_maximos"),
                {"id": self.id},
            )
            if result.rowcount == 0:
                raise ValueError("Cupón agotado")
        else:
            db.session.execute(
                text("UPDATE coupons SET usos_actuales = usos_actuales + 1 WHERE id = :id"),
                {"id": self.id},
            )
        self.usos_actuales = (self.usos_actuales or 0) + 1

    def revertir_uso(self):
        db.session.execute(
            text(
                "UPDATE coupons SET usos_actuales = "
                "CASE WHEN usos_actuales > 0 THEN usos_actuales - 1 ELSE 0 END "
                "WHERE id = :id"
            ),
            {"id": self.id},
        )
        db.session.expire(self, ["usos_actuales"])


# ─────────────────────────────────────────────
# PUNTOS — LOG
# ─────────────────────────────────────────────

class PointsLog(db.Model):
    __tablename__ = "points_log"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    pedido_id = db.Column(db.Integer, db.ForeignKey("orders.id"))
    tipo = db.Column(db.String(20), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    descripcion = db.Column(db.String(200))
    creado_en = db.Column(db.DateTime, default=utcnow)

    pedido_rel = db.relationship("Order", backref="points_logs")

    __table_args__ = (
        db.Index("ix_points_log_cliente_id", "cliente_id"),
        db.Index("ix_points_log_creado_en",  "creado_en"),
        db.Index(
            "uq_points_log_order_earned",
            "cliente_id", "pedido_id", "tipo",
            unique=True,
            postgresql_where=text("tipo = 'ganado' AND pedido_id IS NOT NULL"),
        ),
    )


# ─────────────────────────────────────────────
# CAJA
# ─────────────────────────────────────────────

class Caja(db.Model):
    __tablename__ = "caja"

    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)      # ingreso / egreso
    categoria = db.Column(db.String(30), default="general")
    # categorias: venta_online / venta_presencial / pago_staff /
    #             compra_insumos / gasto_operativo / adelanto / general
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    concepto = db.Column(db.String(200))
    pedido_id = db.Column(db.Integer, db.ForeignKey("orders.id"))
    staff_payment_id = db.Column(db.Integer, db.ForeignKey("staff_payments.id"))
    registrado_por = db.Column(db.Integer, db.ForeignKey("users.id"))
    fecha = db.Column(db.DateTime, default=utcnow)

    pedido = db.relationship("Order", backref="movimientos_caja")
    pago_staff = db.relationship("StaffPayment", backref="movimiento_caja")

    __table_args__ = (
        db.Index("ix_caja_fecha",  "fecha"),
        db.Index("ix_caja_tipo",   "tipo"),
        db.Index(
            "uq_caja_order_income",
            "pedido_id",
            unique=True,
            postgresql_where=text("tipo = 'ingreso' AND pedido_id IS NOT NULL"),
        ),
        db.Index(
            "uq_caja_staff_payment_expense",
            "staff_payment_id",
            unique=True,
            postgresql_where=text("tipo = 'egreso' AND staff_payment_id IS NOT NULL"),
        ),
        db.Index(
            "uq_caja_order_refund",
            "pedido_id",
            unique=True,
            postgresql_where=text(
                "tipo = 'egreso' AND categoria = 'devolucion' AND pedido_id IS NOT NULL"
            ),
        ),
    )


# ─────────────────────────────────────────────
# ZONAS DE ENTREGA
# ─────────────────────────────────────────────

class ZonaEntrega(db.Model):
    __tablename__ = "zonas_entrega"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    descripcion = db.Column(db.Text)
    # True = ventas contabilizadas como “Epicentro Carmona”; False = fuera / periferia
    es_epicentro = db.Column(db.Boolean, default=True)
    activo = db.Column(db.Boolean, default=True)
    precio_envio = db.Column(db.Numeric(10, 2), default=0)
    tiempo_estimado_min = db.Column(db.Integer, default=30)
    gratis_desde = db.Column(db.Numeric(10, 2))
    orden = db.Column(db.Integer, default=0)

    # ── Geodata (opcional) — habilita matching real por dirección ───────
    # Si ambos están informados, el checkout asigna esta zona cuando la
    # dirección del cliente cae dentro de `radio_km`. Si no, se usa el
    # fallback por orden (legacy).
    centro_lat = db.Column(db.Float)
    centro_lng = db.Column(db.Float)
    radio_km = db.Column(db.Float)

    @property
    def tiene_geo(self) -> bool:
        return (
            self.centro_lat is not None
            and self.centro_lng is not None
            and self.radio_km is not None
            and self.radio_km > 0
        )

    def __repr__(self):
        return f"<ZonaEntrega {self.nombre}>"


# ─────────────────────────────────────────────
# CÓDIGOS DE AFILIADO
# ─────────────────────────────────────────────

class AffiliateCode(db.Model):
    __tablename__ = "affiliate_codes"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(30), unique=True, nullable=False)
    descripcion = db.Column(db.String(200))
    tipo = db.Column(db.String(20), default="externo")       # staff / externo
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    descuento_tipo = db.Column(db.String(20))                # porcentaje / monto_fijo
    descuento_valor = db.Column(db.Numeric(10, 2), default=0)
    comision_tipo = db.Column(db.String(20))                 # porcentaje / monto_fijo
    comision_valor = db.Column(db.Numeric(10, 2), default=0)
    activo = db.Column(db.Boolean, default=True)
    usos_maximos = db.Column(db.Integer)
    usos_actuales = db.Column(db.Integer, default=0)
    fecha_inicio = db.Column(db.Date)
    fecha_fin = db.Column(db.Date)
    creado_en = db.Column(db.DateTime, default=utcnow)
    creado_por = db.Column(db.Integer, db.ForeignKey("users.id"))

    afiliado = db.relationship("User", foreign_keys=[user_id], backref="codigos_afiliado")
    creador = db.relationship("User", foreign_keys=[creado_por])
    usos = db.relationship("AffiliateUse", backref="codigo", lazy="dynamic")

    def es_valido(self):
        hoy = date.today()
        if not self.activo:
            return False, "Código inactivo"
        if self.fecha_inicio and hoy < self.fecha_inicio:
            return False, "Código no vigente aún"
        if self.fecha_fin and hoy > self.fecha_fin:
            return False, "Código expirado"
        if self.usos_maximos and self.usos_actuales >= self.usos_maximos:
            return False, "Código agotado"
        return True, "OK"

    def calcular_descuento(self, subtotal):
        valido, msg = self.es_valido()
        if not valido:
            raise ValueError(msg)
        if self.descuento_tipo == "porcentaje":
            return round(float(subtotal) * float(self.descuento_valor) / 100, 2)
        elif self.descuento_tipo == "monto_fijo":
            return min(float(self.descuento_valor), float(subtotal))
        return 0

    def calcular_comision(self, total_pedido):
        if self.comision_tipo == "porcentaje":
            return round(float(total_pedido) * float(self.comision_valor) / 100, 2)
        elif self.comision_tipo == "monto_fijo":
            return float(self.comision_valor)
        return 0

    def registrar_uso(self):
        if self.usos_maximos:
            result = db.session.execute(
                text(
                    "UPDATE affiliate_codes "
                    "SET usos_actuales = usos_actuales + 1 "
                    "WHERE id = :id AND usos_actuales < usos_maximos"
                ),
                {"id": self.id},
            )
            if result.rowcount == 0:
                raise ValueError("Código de afiliado agotado")
        else:
            db.session.execute(
                text("UPDATE affiliate_codes SET usos_actuales = usos_actuales + 1 WHERE id = :id"),
                {"id": self.id},
            )
        self.usos_actuales = (self.usos_actuales or 0) + 1

    def revertir_uso(self):
        db.session.execute(
            text(
                "UPDATE affiliate_codes SET usos_actuales = "
                "CASE WHEN usos_actuales > 0 THEN usos_actuales - 1 ELSE 0 END "
                "WHERE id = :id"
            ),
            {"id": self.id},
        )
        db.session.expire(self, ["usos_actuales"])


class AffiliateUse(db.Model):
    __tablename__ = "affiliate_uses"

    id = db.Column(db.Integer, primary_key=True)
    codigo_id = db.Column(db.Integer, db.ForeignKey("affiliate_codes.id"), nullable=False)
    pedido_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    cliente_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    descuento_aplicado = db.Column(db.Numeric(10, 2), default=0)
    comision_generada = db.Column(db.Numeric(10, 2), default=0)
    comision_pagada = db.Column(db.Boolean, default=False)
    staff_payment_id = db.Column(db.Integer, db.ForeignKey("staff_payments.id"))
    creado_en = db.Column(db.DateTime, default=utcnow)

    pedido = db.relationship("Order", backref="afiliado_use")
    cliente = db.relationship("User", foreign_keys=[cliente_id])
    staff_payment = db.relationship("StaffPayment", foreign_keys=[staff_payment_id])

    __table_args__ = (
        db.UniqueConstraint("codigo_id", "pedido_id", name="uq_affiliate_use_order"),
    )


# ─────────────────────────────────────────────
# CONFIGURACIÓN DEL MENÚ / BANNERS
# ─────────────────────────────────────────────

class MenuConfig(db.Model):
    __tablename__ = "menu_config"

    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(30), nullable=False)
    # tipos: banner / producto_destacado / texto_promo / categoria_orden / seccion
    titulo = db.Column(db.String(200))
    contenido = db.Column(db.Text)
    imagen_url = db.Column(db.Text)
    enlace_url = db.Column(db.Text)
    orden = db.Column(db.Integer, default=0)
    activo = db.Column(db.Boolean, default=True)
    pagina = db.Column(db.String(30), default="home")  # home / menu / checkout
    categoria_id = db.Column(db.Integer, db.ForeignKey("categorias.id"))
    producto_id = db.Column(db.Integer, db.ForeignKey("products.id"))
    creado_por = db.Column(db.Integer, db.ForeignKey("users.id"))
    actualizado_en = db.Column(db.DateTime, default=utcnow)

    categoria = db.relationship("Categoria")
    producto = db.relationship("Product")
    creador = db.relationship("User", foreign_keys=[creado_por])


# ─────────────────────────────────────────────
# HISTORIAL DE PRECIOS
# ─────────────────────────────────────────────

class PriceHistory(db.Model):
    __tablename__ = "price_history"

    id = db.Column(db.Integer, primary_key=True)
    producto_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    precio_anterior = db.Column(db.Numeric(10, 2))
    precio_nuevo = db.Column(db.Numeric(10, 2), nullable=False)
    cambiado_por = db.Column(db.Integer, db.ForeignKey("users.id"))
    cambiado_en = db.Column(db.DateTime, default=utcnow)
    motivo = db.Column(db.String(200))

    producto = db.relationship("Product", backref="historial_precios")
    usuario = db.relationship("User", foreign_keys=[cambiado_por])


# ─────────────────────────────────────────────
# CONFIGURACIÓN DEL SISTEMA (clave/valor)
# ─────────────────────────────────────────────

class SiteConfig(db.Model):
    __tablename__ = "site_config"

    id = db.Column(db.Integer, primary_key=True)
    clave = db.Column(db.String(50), unique=True, nullable=False)
    valor = db.Column(db.Text)
    descripcion = db.Column(db.String(200))
    actualizado_en = db.Column(db.DateTime, default=utcnow)
    actualizado_por = db.Column(db.Integer, db.ForeignKey("users.id"))

    usuario = db.relationship("User", foreign_keys=[actualizado_por])

    @staticmethod
    def get(clave, default=None):
        try:
            from flask import g
            cache = g.__dict__.setdefault("_siteconfig_cache", {})
            if clave not in cache:
                entry = SiteConfig.query.filter_by(clave=clave).first()
                cache[clave] = entry.valor if entry else None
            val = cache[clave]
            return val if val is not None else default
        except Exception:
            logger.exception("No se pudo leer SiteConfig %s", clave)
            return default

    @staticmethod
    def set(clave, valor, user_id=None, descripcion=None):
        """Upsert de una clave de configuración. NO hace commit — el llamador es responsable."""
        try:
            from flask import g
            g.__dict__.get("_siteconfig_cache", {}).pop(clave, None)
            entry = SiteConfig.query.filter_by(clave=clave).first()
            valor_normalizado = None if valor is None else str(valor)
            if entry:
                entry.valor = valor_normalizado
                entry.actualizado_en = utcnow()
                entry.actualizado_por = user_id
            else:
                entry = SiteConfig(clave=clave, valor=valor_normalizado,
                                   descripcion=descripcion, actualizado_por=user_id)
                db.session.add(entry)
            return entry
        except Exception:
            logger.exception("No se pudo guardar SiteConfig %s", clave)
            return None


# ─────────────────────────────────────────────
# LOG DE AUDITORÍA
# ─────────────────────────────────────────────

class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    accion = db.Column(db.String(100), nullable=False)
    recurso = db.Column(db.String(50))
    recurso_id = db.Column(db.Integer)
    detalle = db.Column(db.Text)
    ip = db.Column(db.String(50))
    creado_en = db.Column(db.DateTime, default=utcnow)

    usuario = db.relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        db.Index("ix_audit_log_creado_en", "creado_en"),
        db.Index("ix_audit_log_user_id",   "user_id"),
    )

    @staticmethod
    def registrar(user_id, accion, recurso=None, recurso_id=None, detalle=None, ip=None):
        entry = AuditLog(user_id=user_id, accion=accion, recurso=recurso,
                         recurso_id=recurso_id, detalle=detalle, ip=ip)
        db.session.add(entry)
        return entry


# ─────────────────────────────────────────────
# FEATURES POR ADMIN (control de módulos)
# ─────────────────────────────────────────────

class AdminFeature(db.Model):
    """
    Controla qué módulos tiene habilitados cada admin.
    Solo el super_admin puede activar/desactivar.
    Si un admin no tiene registro para un feature, se considera desactivado.
    """
    __tablename__ = "admin_features"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    feature = db.Column(db.String(50), nullable=False)
    # caja | productos | stock | cupones | staff_pagos | reportes |
    # zonas | auditoria | marketing | pos | whatsapp
    activo = db.Column(db.Boolean, default=False)
    actualizado_por = db.Column(db.Integer, db.ForeignKey("users.id"))
    actualizado_en = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (db.UniqueConstraint("user_id", "feature", name="uq_admin_feature"),)

    usuario = db.relationship("User", foreign_keys=[user_id], backref="features")
    actualizador = db.relationship("User", foreign_keys=[actualizado_por])

    @staticmethod
    def tiene_acceso(user_id, feature):
        """True si el usuario tiene el feature activo o es super_admin."""
        user = db.session.get(User, user_id)
        if not user:
            return False
        if user.rol == "super_admin":
            return True
        if user.rol != "admin":
            return False
        af = AdminFeature.query.filter_by(user_id=user_id, feature=feature).first()
        return af.activo if af else False

    @staticmethod
    def inicializar_para_admin(user_id, activar_todos=False):
        """Crea los registros de features para un nuevo admin."""
        for feat in ADMIN_FEATURES:
            existe = AdminFeature.query.filter_by(user_id=user_id, feature=feat).first()
            if not existe:
                af = AdminFeature(user_id=user_id, feature=feat, activo=activar_todos)
                db.session.add(af)


# ─────────────────────────────────────────────
# CAMPAÑAS DE MARKETING
# ─────────────────────────────────────────────

class CampanaMarketing(db.Model):
    __tablename__ = "campanas_marketing"

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    mensaje = db.Column(db.Text, nullable=False)
    filtro_audiencia = db.Column(db.String(50), default="todos")
    # todos | con_puntos | sin_compra_30 | por_zona
    zona_id = db.Column(db.Integer, db.ForeignKey("zonas_entrega.id"))
    enviados = db.Column(db.Integer, default=0)
    estado = db.Column(db.String(20), default="borrador")
    # borrador | enviando | enviado | error
    error_detalle = db.Column(db.Text)
    creado_por = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    creado_en = db.Column(db.DateTime, default=utcnow)
    enviado_en = db.Column(db.DateTime)

    zona = db.relationship("ZonaEntrega")
    creador = db.relationship("User", foreign_keys=[creado_por])


# ─────────────────────────────────────────────
# PUSH SUBSCRIPTIONS (Web Push API)
# ─────────────────────────────────────────────

class PushSubscription(db.Model):
    """Suscripciones Web Push para notificaciones en tiempo real."""
    __tablename__ = "push_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text, nullable=False)   # clave pública del cliente
    auth = db.Column(db.String(100), nullable=False)  # secreto de auth
    rol = db.Column(db.String(30))                # rol snapshot para targeting
    user_agent = db.Column(db.String(300))
    creado_en = db.Column(db.DateTime, default=utcnow)
    ultimo_uso = db.Column(db.DateTime, default=utcnow)
    activo = db.Column(db.Boolean, default=True)

    usuario = db.relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        db.Index("ix_push_sub_user_id", "user_id"),
        db.Index("ix_push_sub_rol", "rol"),
    )
