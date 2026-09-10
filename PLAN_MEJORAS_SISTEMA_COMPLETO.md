# 🏗️ PLAN DE MEJORAS SISTEMA EL PARCERITO
## Documento Técnico Profesional - v1.0
**Fecha:** 2026-07-02  
**Estado:** En Proceso de Implementación

---

## 📋 RESUMEN EJECUTIVO

El sistema **El Parcerito** es una plataforma de e-commerce multi-rol con:
- 🏪 **Tienda principal** + **N bares/proveedores** con inventario independiente
- 💬 **3 interfaces chatbot** (Cliente, Admin, Super Admin)
- 🛒 **Carrito PWA** + **Combos complejos** + **Sistema de puntos**
- 📦 **Gestión FIFO de stock** + **Delivery/Recogida**

### ❌ PROBLEMAS CRÍTICOS ACTUALES

1. **UX/Fluidez**: Transiciones lentas, tirones, recargas innecesarias
2. **Chatbot**: 3 menús incompletos/con errores, respuestas lentas
3. **Combos**: Errores en validación y cálculo de precios
4. **Stock Mezclado**: Clientes no ven claramente qué bar tiene qué
5. **Flujos Condicionales**: Sin adaptación según delivery/recogida
6. **Productos Puntos**: No separados en interfaz
7. **Rendimiento**: Página lenta, no optimizada para múltiples sitios
8. **Datos Hardcodeados**: Logo, nombre, horarios, números sin usar config

---

## 🎯 OBJETIVOS DE MEJORA

| Objetivo | Métrica | Prioridad |
|----------|---------|-----------|
| Fluidez UI/UX | Sin tirones, transiciones <100ms | CRÍTICA |
| Menú Chatbot | Funcional 100%, respuestas <2s | CRÍTICA |
| Stock Separado | Clientes ven bares claramente | CRÍTICA |
| Rendimiento | Lighthouse >85, <2s load | ALTA |
| Combos | Validación correcta, 0 errores | ALTA |
| Optimización | Hospedar 2+ sitios en mismo SSD | ALTA |
| Configuración | 0 datos hardcodeados | MEDIA |

---

## 🏛️ ARQUITECTURA ACTUAL

### **Stack Técnico**
```
Frontend:
  ├─ Vanilla JS (sin framework)
  ├─ Tailwind CSS
  ├─ Service Worker (PWA)
  └─ localStorage (cart)

Backend:
  ├─ Flask + SQLAlchemy
  ├─ PostgreSQL (single)
  └─ REST API

Chatbot:
  ├─ Node.js (port 3000)
  ├─ Evolution API (WhatsApp)
  └─ SQLite (local)

Database:
  ├─ 30+ tables
  ├─ FIFO stock management
  └─ Multi-role access control
```

### **Estructura de Datos Clave**

#### Product (Productos simples + Combos)
```python
Product:
  - id, nombre, descripcion, precio
  - es_combo (bool) → combo_groups, combo_items
  - proveedor_despachador_id (NULL=propio, >0=bar)
  - modalidad_entrega ("ambas", "delivery", "recogida")
  - canjeable_con_puntos, solo_canje
  - stock_mostrar_en_web
  - hora_inicio/fin_visibilidad, dias_semana_json
```

#### Stock (Inventario FIFO)
```python
Stock:
  - producto_id
  - proveedor_id (who owns this stock)
  - cantidad
  - fecha_caducidad (NULL-last, expired ignored)
  - fecha_entrada (FIFO within expiry)
```

#### Proveedor (Bares/Tienda)
```python
Proveedor:
  - id, nombre, logo_url
  - hora_apertura, hora_cierre
  - esta_abierto_ahora() → respeta horarios
  - es_despachador (propios productos)
```

#### Order (Pedidos)
```python
Order:
  - cliente_id, proveedor_id
  - estado (pendiente→armando→listo→en_ruta→entregado/cancelado)
  - tipo_entrega_cliente ("delivery", "recogida")
  - modalidad_pago, referencia_pago
  - total, puntos_ganados, puntos_canjeados
```

---

## 🔧 PLAN DE IMPLEMENTACIÓN (6 FASES)

### **FASE 1: AUDITORÍA Y CORRECCIONES CRÍTICAS** (2-3 días)
**Objetivo**: Hacer el sistema usable sin errores

#### 1.1 Revisar y Corregir Chatbot
- [ ] Mapear todos los comandos: `/menu`, `/catalogo`, `/pedido`, `/puntos`, `/admin`
- [ ] Validar que cada comando responda correctamente
- [ ] Fixear:
  - [ ] Rate limiting (no bloquee mensajes legítimos)
  - [ ] Mensajes fuera de orden
  - [ ] Respuestas que no llegan
  - [ ] Estados de sesión inconsistentes
- [ ] Mejorar logging para debugging

#### 1.2 Validación de Combos
```python
# En models.py → Product class
def validar_combo_integridad(self):
    """Verifica que combo está bien formado"""
    if not self.es_combo:
        return True
    
    # ✓ Tiene grupos
    if not self.combo_groups.count():
        return False, "Combo sin grupos"
    
    # ✓ Grupos tienen ítems
    for group in self.combo_groups:
        if not group.combo_items.count():
            return False, f"Grupo {group.nombre} vacío"
        
        # ✓ Si es seleccionable, tiene restricciones
        if group.tipo == 'seleccion':
            if not group.max_selecciones:
                return False, f"Grupo {group.nombre} sin max_selecciones"
    
    return True, "OK"
```

#### 1.3 Ajustar Flujos Delivery/Recogida
```python
# En models.py → Product class
def disponible_para_compra(self, tipo_entrega_solicitado):
    """
    tipo_entrega_solicitado: "delivery" o "recogida"
    Retorna: (bool disponible, str razón)
    """
    if self.modalidad_entrega == "ambas":
        return True, "OK"
    if self.modalidad_entrega == tipo_entrega_solicitado:
        return True, "OK"
    return False, f"Producto solo disponible para {self.modalidad_entrega}"
```

#### 1.4 Configuración Dinámica
```python
# En models.py → SiteConfig
REQUIRED_CONFIGS = {
    'nombre_negocio': ('El Parcerito', str),
    'logo_url': ('/static/img/logo.png', str),
    'whatsapp_number': ('', str),  # Para botones "Contactar"
    'email_contacto': ('info@parcerito.com', str),
    'color_primario': ('#FF6B35', str),
    'color_secundario': ('#004E89', str),
    'hora_apertura': ('09:00', str),
    'hora_cierre': ('23:00', str),
    'timezone': ('America/Caracas', str),
    'moneda': ('USD', str),
}

# Verificar en app.py startup
for config_key, (default, tipo) in REQUIRED_CONFIGS.items():
    val = SiteConfig.get(config_key)
    if val is None:
        SiteConfig.set(config_key, default)
```

---

### **FASE 2: SEPARACIÓN CLARA DE STOCK (Bares vs Tienda)** (3-4 días)
**Objetivo**: Clientes ven claramente qué bar tiene qué, sin confusiones

#### 2.1 Modelo Lógico: "Bar Shop" Virtual
```python
# Cada proveedor es una "tienda virtual" dentro del marketplace

Proveedor (Bar/Tienda):
  - id, nombre, logo_url
  - es_tienda_principal (bool) → Es "El Parcerito" vs bar asociado
  - descripcion
  - horarios, zona_entrega
  - comisión_por_venta
  - estado ("activo", "inactivo", "pendiente_aprobacion")
  - timestamp creado_en, actualizado_en

# En interfaz PWA: 
# [Tienda El Parcerito] [Bar X] [Bar Y] [Bar Z]
# Cliente elige cuál bar quiere ver → Productos + Stock solo de ese bar
```

#### 2.2 Tabla de Controlador "Tienda Activa"
```python
# En sesión/localStorage cliente
{
  "tienda_activa": {
    "proveedor_id": 1,
    "nombre": "El Parcerito",
    "logo": "...",
    "esta_abierto": true
  }
}

# Endpoint: GET /api/tiendas/disponibles
# Retorna: Lista de bares abiertos con horarios y cantidad de productos
```

#### 2.3 Filtrado de Productos por Tienda
```python
# En routes/public.py
def obtener_catalogo(proveedor_id=None):
    """
    Si proveedor_id es None: Retorna solo "El Parcerito"
    Si es número: Retorna ese bar en particular
    """
    if proveedor_id is None:
        proveedor_id = Proveedor.query.filter_by(es_tienda_principal=True).first().id
    
    proveedor = Proveedor.query.get(proveedor_id)
    if not proveedor or not proveedor.esta_abierto_ahora():
        return jsonify({"ok": False, "error": "Tienda no disponible"}), 404
    
    productos = Product.query.join(Stock).filter(
        Stock.proveedor_id == proveedor_id,
        Stock.cantidad > 0,
        Product.activo == True,
        Product.stock_mostrar_en_web == True
    ).distinct().all()
    
    return jsonify([p.to_dict() for p in productos])
```

#### 2.4 Interfaz de Selección de Tienda (PWA)
```html
<!-- En templates/public/catalogo.html -->
<div class="navbar-tiendas">
  <div class="tiendas-carousel">
    <!-- Carrusel horizontal de bares -->
    <button class="tienda-card" data-proveedor-id="1">
      <img src="/logo-parcerito.png" />
      <p>El Parcerito</p>
      <span class="badge">24 productos</span>
    </button>
    
    <button class="tienda-card" data-proveedor-id="2">
      <img src="/logo-bar-x.png" />
      <p>Bar X</p>
      <span class="badge">12 productos</span>
    </button>
  </div>
</div>

<!-- Script en header-modern.js -->
<script>
document.querySelectorAll('.tienda-card').forEach(card => {
  card.addEventListener('click', () => {
    const tiendaId = card.dataset.proveedorId;
    sessionStorage.setItem('tienda_activa_id', tiendaId);
    // Recargar catálogo (SPA sin refresco completo)
    loadCatalogo(tiendaId);
  });
});
</script>
```

#### 2.5 Stock Visual en Producto
```html
<!-- Mostrar stock solo de tienda activa -->
<div class="product-card">
  <img src="producto.jpg" />
  <h3>Producto X</h3>
  <p>$10.00</p>
  
  <!-- Stock con ícono de tienda -->
  <div class="stock-info">
    <span class="stock-icon">📦</span>
    <span class="stock-qty" id="stock-qty-${product.id}">
      <!-- Cargado dinámicamente via JS -->
    </span>
    <span class="stock-label">en stock (El Parcerito)</span>
  </div>
</div>
```

---

### **FASE 3: MEJORA DE CHATBOT (3 interfaces)** (4-5 días)
**Objetivo**: Menús funcionales, sin errores, respuestas rápidas

#### 3.1 Refactorizar Estructura del Bot
```javascript
// En chat/bot.js
// Crear módulos separados:
/chat
  ├─ handlers/
  │  ├─ clientHandler.js     (menú cliente)
  │  ├─ adminHandler.js      (menú admin/bar)
  │  ├─ superadminHandler.js (menú superadmin)
  │  └─ messageQueue.js      (cola con prioridad)
  ├─ services/
  │  ├─ catalogService.js    (consultar productos)
  │  ├─ orderService.js      (crear/actualizar pedidos)
  │  ├─ pointsService.js     (consultar puntos)
  │  └─ notificationService.js
  ├─ utils/
  │  ├─ messageFormatter.js
  │  ├─ validations.js
  │  └─ logger.js
  └─ bot.js (orquestador principal)
```

#### 3.2 Menú Cliente (Flujo Claro)
```
🏪 *EL PARCERITO*

┌─ Opción 1: 🛍️ COMPRAR
│  ├─ Mostrar catálogo (con fotos)
│  ├─ Cliente elige producto
│  ├─ Confirmar cantidad
│  ├─ Ir a carrito o seguir comprando
│  └─ Checkout (dirección, método pago)
│
├─ Opción 2: 🛒 MI CARRITO
│  ├─ Ver items + precios
│  ├─ Modificar cantidades
│  ├─ Borrar items
│  └─ Proceder al checkout
│
├─ Opción 3: 📍 MIS PEDIDOS
│  ├─ Listar últimos 5 pedidos
│  ├─ Estado en tiempo real
│  ├─ Código confirmación (delivery)
│  └─ Seguimiento
│
├─ Opción 4: ⭐ MIS PUNTOS
│  ├─ Saldo actual + validar con OTP
│  ├─ Historial últimos 20 movimientos
│  ├─ Canjear puntos por productos
│  └─ Beneficios y reglas
│
└─ Opción 5: ❓ AYUDA
   ├─ FAQ frecuentes
   ├─ Contactar con soporte
   └─ Reportar problema
```

#### 3.3 Menú Admin/Bar (Operaciones Diarias)
```
👨‍💼 *PANEL ADMIN - BAR X*

┌─ 1️⃣ PEDIDOS PENDIENTES
│  ├─ Listar: [pendiente, armando]
│  ├─ Marcar "listo"
│  └─ Asignar repartidor
│
├─ 2️⃣ STOCK
│  ├─ Ver inventario actual
│  ├─ Actualizar cantidades
│  ├─ Productos bajo stock (<5)
│  └─ Alerta caducidad próxima
│
├─ 3️⃣ REPORTES DIARIOS
│  ├─ Ventas hoy (cantidad, monto)
│  ├─ Productos más vendidos
│  ├─ Comparativa día anterior
│  └─ Descargar reporte PDF
│
├─ 4️⃣ CONFIGURACIÓN
│  ├─ Horario apertura/cierre
│  ├─ Zona de entrega
│  ├─ Activar/desactivar delivery
│  └─ Activar/desactivar recogida
│
└─ 5️⃣ MI PERSONAL
   ├─ Listar repartidores activos
   ├─ Comisiones hoy
   └─ Pagos pendientes
```

#### 3.4 Menú Super Admin (Configuración Global)
```
👑 *PANEL SUPERADMIN*

┌─ 1️⃣ CONFIGURACIÓN TIENDA
│  ├─ Nombre, logo, colores
│  ├─ Horarios principales
│  ├─ Zona de cobertura
│  ├─ Comisiones y margenes
│  └─ Métodos de pago
│
├─ 2️⃣ BARES/PROVEEDORES
│  ├─ Crear nuevo bar
│  ├─ Editar datos bar
│  ├─ Habilitar/deshabilitar
│  ├─ Ver comisiones
│  └─ Auditoría de acciones
│
├─ 3️⃣ PRODUCTOS
│  ├─ CRUD productos
│  ├─ Gestionar combos
│  ├─ Categorías
│  ├─ Alergénicos/restricciones
│  └─ Visibilidad horaria
│
├─ 4️⃣ SISTEMA DE PUNTOS
│  ├─ Configurar reglas
│  ├─ Tasa: puntos por $
│  ├─ Productos canjeables
│  ├─ Auditoría de canjes
│  └─ Top clientes
│
├─ 5️⃣ USUARIOS & PERMISOS
│  ├─ Crear staff (admin, preparador, etc)
│  ├─ Asignar roles
│  ├─ Ver historial de acciones
│  └─ 2FA/MFA settings
│
└─ 6️⃣ ANALYTICS & AUDITORÍA
   ├─ Dashboard KPI (ventas, RPM, etc)
   ├─ Logs de acceso
   ├─ Cambios en configuración
   ├─ Cancelaciones/rechazos
   └─ Exportar reportes
```

#### 3.5 Mejora de Rendimiento del Bot
```javascript
// messageQueue.js - Procesar mensajes por prioridad
class MessageQueue {
  constructor() {
    this.queues = {
      'superadmin': [],      // Prioridad 1: Critical
      'admin': [],           // Prioridad 2: High
      'client': []           // Prioridad 3: Normal
    };
    this.processing = false;
  }
  
  async enqueue(jid, message, role) {
    const priority = role === 'superadmin' ? 0 : role === 'admin' ? 1 : 2;
    this.queues[role].push({ jid, message, timestamp: Date.now(), priority });
    
    if (!this.processing) {
      this.processNext();
    }
  }
  
  async processNext() {
    this.processing = true;
    
    // Procesar en orden: superadmin → admin → client
    for (const role of ['superadmin', 'admin', 'client']) {
      while (this.queues[role].length > 0) {
        const { jid, message } = this.queues[role].shift();
        
        // Procesar con timeout
        try {
          await Promise.race([
            handleMessage(jid, message),
            new Promise((_, r) => setTimeout(() => r(new Error('Timeout')), 5000))
          ]);
        } catch (err) {
          console.error(`Error processing message for ${jid}:`, err);
        }
        
        // Rate limit mínimo
        await sleep(role === 'superadmin' ? 500 : role === 'admin' ? 900 : 1200);
      }
    }
    
    this.processing = false;
  }
}
```

---

### **FASE 4: FLUJOS CONDICIONALES Y COMBOS** (3-4 días)
**Objetivo**: Flujos correctos según características de producto y disponibilidad

#### 4.1 Tabla de Decisiones: Combos
```python
# En models.py → ComboGroup class

class ComboValidation:
    """Valida estructura y precio de combos"""
    
    @staticmethod
    def validar_combo_completo(combo_id, selecciones_cliente=None):
        """
        Recibe selecciones del cliente en carrito:
        {
          "combo_id": 123,
          "selecciones": {
            "grupo_1": [item_id_1, item_id_2],  # Grupo múltiple
            "grupo_2": item_id_5                 # Grupo única selección
          }
        }
        
        Retorna: (válido: bool, precio_final: Decimal, errores: list)
        """
        combo = Product.query.get(combo_id)
        if not combo or not combo.es_combo:
            return False, None, ["No es un combo válido"]
        
        errores = []
        precio_total = Decimal('0')
        
        for grupo in combo.combo_groups:
            if grupo.tipo == 'fijo':
                # Grupo fijo: agregar todos los items automáticamente
                for item in grupo.combo_items.all():
                    precio_total += item.precio_extra or Decimal('0')
            
            elif grupo.tipo == 'seleccion':
                # Grupo de selección: validar que cliente eligió correctamente
                selecciones = selecciones_cliente.get(f"grupo_{grupo.id}", [])
                if not isinstance(selecciones, list):
                    selecciones = [selecciones]
                
                # Validar rango
                if len(selecciones) < grupo.min_selecciones:
                    errores.append(
                        f"Grupo '{grupo.nombre}': mín {grupo.min_selecciones}"
                    )
                if len(selecciones) > grupo.max_selecciones:
                    errores.append(
                        f"Grupo '{grupo.nombre}': máx {grupo.max_selecciones}"
                    )
                
                # Validar items pertenecen al grupo
                items_en_grupo = {item.id for item in grupo.combo_items.all()}
                for item_id in selecciones:
                    if item_id not in items_en_grupo:
                        errores.append(f"Item {item_id} no pertenece a {grupo.nombre}")
                    else:
                        precio_total += ComboItem.query.get(item_id).precio_extra or Decimal('0')
        
        if errores:
            return False, None, errores
        
        # Calcular precio final según modo
        if combo.combo_precio_modo == 'fijo':
            precio_final = combo.combo_precio_base
        elif combo.combo_precio_modo == 'descuento_porcentaje':
            precio_final = precio_total * (1 - combo.combo_descuento_pct / 100)
        else:  # 'sum_componentes'
            precio_final = precio_total
        
        return True, precio_final, []
```

#### 4.2 Tabla de Decisiones: Delivery vs Recogida
```python
# En routes/public.py

def validar_opciones_entrega_para_carrito(carrito_items, tipo_entrega_solicitado):
    """
    Valida que TODOS los items del carrito soportan el tipo de entrega
    
    Retorna: (válido: bool, motivo: str, productos_problema: list)
    """
    productos_problema = []
    
    for prod_id, qty in carrito_items.items():
        prod = Product.query.get(prod_id)
        
        # 1. Validar modalidad entrega
        if prod.modalidad_entrega == "ambas":
            continue  # OK
        if prod.modalidad_entrega == tipo_entrega_solicitado:
            continue  # OK
        
        # NO es válido
        productos_problema.append({
            "producto_id": prod_id,
            "nombre": prod.nombre,
            "modalidad_actual": prod.modalidad_entrega,
            "solicitada": tipo_entrega_solicitado,
            "razon": f"Solo disponible para {prod.modalidad_entrega}"
        })
    
    if productos_problema:
        return False, f"{len(productos_problema)} productos no disponibles", productos_problema
    
    return True, "OK", []

@public_bp.route('/api/checkout/validar-opciones', methods=['POST'])
def validar_opciones_entrega():
    """
    POST /api/checkout/validar-opciones
    Body: {
      "carrito": {"prod_id": qty, ...},
      "tipo_entrega": "delivery" | "recogida"
    }
    """
    data = request.get_json() or {}
    carrito = data.get('carrito', {})
    tipo_entrega = data.get('tipo_entrega', 'delivery')
    
    válido, motivo, productos = validar_opciones_entrega_para_carrito(carrito, tipo_entrega)
    
    return jsonify({
        'ok': válido,
        'motivo': motivo,
        'productos_problema': productos
    })
```

#### 4.3 Sección de Productos con Puntos (UI)
```html
<!-- En templates/public/club.html o nueva sección -->
<section class="productos-puntos">
  <h2>🌟 CANJEA CON TUS PUNTOS</h2>
  
  <div class="puntos-saldo">
    <p>Saldo disponible: <strong id="puntos-total">0</strong> ⭐</p>
    <button onclick="verHistorialPuntos()">Ver historial</button>
  </div>
  
  <!-- Filtros -->
  <div class="filtros-puntos">
    <button class="filter-btn active" data-filter="todos">Todos</button>
    <button class="filter-btn" data-filter="bajo_costo">&lt; 100 ⭐</button>
    <button class="filter-btn" data-filter="medio_costo">100-500 ⭐</button>
    <button class="filter-btn" data-filter="alto_costo">&gt; 500 ⭐</button>
  </div>
  
  <!-- Grid de productos canjeables -->
  <div class="grid-productos-puntos" id="grid-puntos">
    <!-- Renderizado dinámicamente -->
  </div>
</section>

<script>
// En header-modern.js
async function cargarProductosPuntos() {
  const response = await fetch('/api/productos/puntos');
  const productos = await response.json();
  
  const grid = document.getElementById('grid-puntos');
  grid.innerHTML = productos.map(p => `
    <div class="producto-puntos-card">
      <img src="${p.imagen}" />
      <h4>${p.nombre}</h4>
      <div class="puntos-badge">${p.puntos_para_canje} ⭐</div>
      <button onclick="canjearProducto(${p.id})">
        Canjear
      </button>
    </div>
  `).join('');
}

async function canjearProducto(productoId) {
  const response = await fetch('/api/puntos/canjear', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      producto_id: productoId,
      cantidad: 1
    })
  });
  
  if (response.ok) {
    showToast('✅ Producto canjeado correctamente');
    cargarProductosPuntos();
  }
}
</script>
```

#### 4.4 Flujo Condicional en Checkout
```python
# En routes/public.py → POST /checkout/crear

@public_bp.route('/checkout/crear', methods=['POST'])
def checkout_crear():
    data = request.get_json() or {}
    
    # 1. Obtener carrito de sesión
    carrito = session.get('carrito', {})
    
    # 2. Obtener tipo de entrega seleccionado
    tipo_entrega = data.get('tipo_entrega', 'delivery')
    
    # 3. **VALIDAR** que todos los productos soportan este tipo
    válido, motivo, productos_problema = validar_opciones_entrega_para_carrito(
        carrito, tipo_entrega
    )
    if not válido:
        return jsonify({
            'ok': False,
            'error': motivo,
            'productos_problema': productos_problema
        }), 400
    
    # 4. Validar stock y crear orden...
    # (resto del código)
```

---

### **FASE 5: OPTIMIZACIÓN DE RENDIMIENTO** (3-4 días)
**Objetivo**: Página <2s load, Lighthouse >85, hospedar múltiples sitios

#### 5.1 Auditoría Actual (Checklist)
```bash
# Ejecutar en terminal
# 1. Lighthouse
lighthouse https://yourdomain.com --output-path=./lighthouse-report.html

# 2. PageSpeed Insights
# https://pagespeed.web.dev/

# 3. WebPageTest
# https://www.webpagetest.org/

# 4. Analizar bundle JS/CSS
# npm install -g webpack-bundle-analyzer
# (Si aplica)

# 5. Database query time
# Ver logs de Flask: tiempo en queries
```

#### 5.2 Optimizaciones Frontend (CSS/JS)
```bash
# 1. Minificación de CSS
cd oxidian/static/css
npx cleancss -o tailwind.generated.min.css tailwind.generated.css

# 2. Compresión de imágenes
npx imagemin oxidian/static/img/* --out-dir=oxidian/static/img-optimized

# 3. Generar WebP versions
npx imagemin oxidian/static/img --plugin webp --out-dir oxidian/static/img-webp

# 4. Service Worker: agregar compresión
```

#### 5.3 Optimizar Activos (app.py)
```python
# En app.py
from flask_compress import Compress
from flask_caching import Cache

# Habilitar compresión
Compress(app)

# Caché de API
cache = Cache(app, config={
    'CACHE_TYPE': 'simple',
    'CACHE_DEFAULT_TIMEOUT': 300,  # 5 min
    'CACHE_KEY_PREFIX': 'el_parcerito_'
})

# En routes/public.py
@public_bp.route('/api/catalogo')
@cache.cached(timeout=300)  # Cache 5 minutos
def catalogo():
    """Cachear catálogo (invalida al agregar producto)"""
    # ...

# Al actualizar producto: invalidar cache
@admin_bp.route('/productos/<id>', methods=['PUT'])
def update_product(id):
    # ... update logic ...
    cache.clear()
    return jsonify({'ok': True})
```

#### 5.4 Optimizar Database Queries
```python
# En models.py
class Product(db.Model):
    # ... fields ...
    
    @classmethod
    def get_catalog_optimized(cls, proveedor_id=None):
        """
        Usar joinedload para evitar N+1 queries
        """
        query = cls.query.options(
            joinedload(cls.categoria),
            joinedload(cls.stock_entries),
            joinedload(cls.reviews)
        ).filter_by(activo=True)
        
        if proveedor_id:
            query = query.join(Stock).filter(Stock.proveedor_id == proveedor_id)
        
        return query.all()

# En routes/public.py
@public_bp.route('/api/catalogo')
def catalogo():
    # ANTES: N queries (una por cada producto/stock/reviews)
    # DESPUÉS: 1 query con todas las relaciones
    productos = Product.get_catalog_optimized()
    return jsonify([p.to_dict() for p in productos])
```

#### 5.5 Lazy Loading de Imágenes
```html
<!-- En templates/public/catalogo.html -->
<img 
  src="/static/img/placeholder.jpg"
  data-src="/productos/123.jpg"
  class="lazy-img"
  alt="Producto"
/>

<!-- En header-modern.js -->
<script>
// Intersection Observer para lazy loading
const imageObserver = new IntersectionObserver((entries, observer) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      const img = entry.target;
      img.src = img.dataset.src;
      img.classList.add('loaded');
      observer.unobserve(img);
    }
  });
});

document.querySelectorAll('.lazy-img').forEach(img => {
  imageObserver.observe(img);
});
</script>

<style>
.lazy-img {
  opacity: 0;
  transition: opacity 0.3s ease-in-out;
}

.lazy-img.loaded {
  opacity: 1;
}
</style>
```

#### 5.6 Service Worker Mejorado (Caché Estratégica)
```javascript
// En static/sw.js
const CACHE_VERSION = 'v1';
const CACHE_NAME = `el-parcerito-${CACHE_VERSION}`;
const STATIC_ASSETS = [
  '/static/css/tailwind.generated.min.css',
  '/static/css/oxidian.css',
  '/static/js/header-modern.js',
  '/static/js/carrito.js',
  '/static/img/logo.png'
];

// Estrategia: Stale-while-revalidate
self.addEventListener('fetch', (event) => {
  const { request } = event;
  
  if (request.method !== 'GET') {
    return;
  }
  
  // Para HTML: Network first (siempre pedir actual)
  if (request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(
      fetch(request)
        .catch(() => caches.match(request))
    );
  }
  
  // Para CSS/JS/IMG: Cache first
  if (/\.(js|css|png|jpg|webp)$/.test(request.url)) {
    event.respondWith(
      caches.match(request)
        .then(response => response || fetch(request).then(resp => {
          caches.open(CACHE_NAME).then(cache => cache.put(request, resp.clone()));
          return resp;
        }))
    );
  }
});
```

#### 5.7 Configuración Nginx para Múltiples Sitios
```nginx
# En nginx.conf

# Compresión
gzip on;
gzip_vary on;
gzip_min_length 1000;
gzip_types text/plain text/css text/javascript 
  application/json application/javascript application/xml+rss;

# Cache estático
location ~* \.(jpg|jpeg|png|gif|ico|css|js|webp)$ {
    expires 30d;
    add_header Cache-Control "public, immutable";
}

# Upstream Flask
upstream flask_app {
    server 127.0.0.1:5000;
}

# Sitio 1: El Parcerito
server {
    listen 80;
    server_name parcerito.local;
    
    location / {
        proxy_pass http://flask_app;
        proxy_set_header Host $host;
    }
}

# Sitio 2: Otro negocio (mismo código, otra BD)
server {
    listen 80;
    server_name otro-negocio.local;
    
    location / {
        proxy_pass http://flask_app;
        proxy_set_header Host $host;
    }
}
```

---

### **FASE 6: UX/FLUIDEZ** (3-4 días)
**Objetivo**: UI fluida, sin tirones, transiciones suaves

#### 6.1 Transiciones CSS Suaves
```css
/* En static/css/oxidian.css */

/* Transiciones globales */
* {
    transition: background-color 0.2s ease-in-out,
                color 0.2s ease-in-out,
                opacity 0.2s ease-in-out;
}

/* Evitar transiciones en scroll */
html.scrolling * {
    transition: none !important;
}

/* Animación de carga suave */
@keyframes fadeIn {
    from {
        opacity: 0;
        transform: translateY(10px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

.product-card {
    animation: fadeIn 0.3s ease-in-out;
}

/* Botones con feedback inmediato */
button:active {
    transform: scale(0.98);
}

button:hover {
    transform: scale(1.02);
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}

/* Inputs con focus suave */
input:focus, select:focus {
    outline: none;
    box-shadow: 0 0 0 3px rgba(0,78,137,0.1);
    border-color: #004E89;
}

/* Carrito: animación al agregar */
@keyframes addToCart {
    0% {
        transform: scale(1);
    }
    50% {
        transform: scale(1.1);
    }
    100% {
        transform: scale(1);
    }
}

.add-to-cart-btn.active {
    animation: addToCart 0.4s ease-out;
}
```

#### 6.2 SPA Mejorado (Sin Recargas)
```javascript
// En header-modern.js
class SPA {
  constructor() {
    this.currentPage = 'catalogo';
    this.historyStack = ['catalogo'];
    this.initNavigation();
    this.initPageTransitions();
  }
  
  initNavigation() {
    // Interceptar clicks en links internos
    document.addEventListener('click', (e) => {
      const link = e.target.closest('a[data-page]');
      if (!link) return;
      
      e.preventDefault();
      const page = link.dataset.page;
      this.loadPage(page);
    });
  }
  
  async loadPage(page) {
    // Evitar recargar misma página
    if (page === this.currentPage) return;
    
    // Fade out
    const container = document.getElementById('main-content');
    container.style.opacity = '0';
    
    // Esperar fade out
    await new Promise(r => setTimeout(r, 200));
    
    // Cargar contenido nuevo
    const html = await fetch(`/page/${page}`).then(r => r.text());
    container.innerHTML = html;
    
    // Fade in
    container.style.opacity = '1';
    
    // Actualizar estado
    this.currentPage = page;
    this.historyStack.push(page);
    
    // Scroll al top suavemente
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}

// Inicializar SPA
const spa = new SPA();
```

#### 6.3 Animaciones Micro-interacciones
```javascript
// En storefront-toast.js
class Toast {
  static show(message, type = 'info', duration = 3000) {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    toast.style.animation = 'slideInUp 0.3s ease-out';
    
    document.body.appendChild(toast);
    
    // Auto-remove
    setTimeout(() => {
      toast.style.animation = 'slideOutDown 0.3s ease-out';
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }
}

// Uso
Toast.show('✅ Agregado al carrito', 'success');
```

#### 6.4 Skeleton Screens (Placeholders)
```html
<!-- templates/public/catalogo.html -->
<!-- Mientras carga: mostrar esqueletos -->
<div id="productos-container">
  <template id="skeleton-product">
    <div class="product-card skeleton">
      <div class="skeleton-img"></div>
      <div class="skeleton-text"></div>
      <div class="skeleton-text short"></div>
    </div>
  </template>
</div>

<style>
.skeleton {
  background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);
  background-size: 200% 100%;
  animation: loading 1.5s infinite;
}

@keyframes loading {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

.skeleton-img {
  height: 200px;
  border-radius: 8px;
}

.skeleton-text {
  height: 12px;
  margin-top: 8px;
  border-radius: 4px;
}

.skeleton-text.short {
  width: 60%;
}
</style>

<script>
// Al cargar página
const container = document.getElementById('productos-container');
const template = document.getElementById('skeleton-product');

// Mostrar 12 skeletons
for (let i = 0; i < 12; i++) {
  container.appendChild(template.content.cloneNode(true));
}

// Cargar datos
fetch('/api/catalogo')
  .then(r => r.json())
  .then(productos => {
    container.innerHTML = '';  // Limpiar skeletons
    
    // Renderizar productos
    productos.forEach(p => {
      const card = createProductCard(p);
      container.appendChild(card);
    });
  });
</script>
```

#### 6.5 Reducir Latencia API
```python
# En routes/public.py

# Endpoint rápido: datos mínimos
@public_bp.route('/api/catalogo/mini')
@cache.cached(timeout=60)  # Cache agresivo
def catalogo_mini():
    """
    Versión ultra-rápida: solo ID, nombre, precio, imagen
    para carga inicial rápida
    """
    productos = Product.query.filter_by(
        activo=True, 
        stock_mostrar_en_web=True
    ).all()
    
    return jsonify([
        {
            'id': p.id,
            'nombre': p.nombre,
            'precio': float(p.precio),
            'imagen': p.imagen_url,
            'categoria': p.categoria.nombre if p.categoria else None
        }
        for p in productos
    ])

# Endpoint completo: datos detallados
@public_bp.route('/api/catalogo/full')
@cache.cached(timeout=60)
def catalogo_full():
    """
    Datos completos: descripción, reviews, stock, combos
    Se carga en segundo plano después del mini
    """
    # ... código normal ...

# En PWA: cargar primero mini, luego full
<script>
async function loadCatalog() {
  // 1. Cargar mini (rápido)
  const mini = await fetch('/api/catalogo/mini').then(r => r.json());
  renderProductos(mini);
  
  // 2. En background: cargar full y actualizar
  fetch('/api/catalogo/full').then(r => r.json()).then(full => {
    actualizarDetalles(full);
  });
}
</script>
```

---

## 🚀 ROADMAP DE IMPLEMENTACIÓN

```
SEMANA 1:
├─ Lunes-Martes: Fase 1 (Auditoría + correcciones críticas)
├─ Miércoles-Jueves: Fase 2 (Separación stock)
└─ Viernes: Testing + bugfixes

SEMANA 2:
├─ Lunes-Martes: Fase 3 (Chatbot refactor)
├─ Miércoles: Fase 4 (Combos + flujos)
└─ Jueves-Viernes: Testing

SEMANA 3:
├─ Lunes-Martes: Fase 5 (Optimización)
├─ Miércoles: Fase 6 (UX/Fluidez)
└─ Jueves-Viernes: Testing final + deploy

SEMANA 4:
├─ Lunes-Martes: Mejoras post-launch
├─ Miércoles-Jueves: Documentación
└─ Viernes: Capacitación (admin, staff)
```

---

## 📊 MÉTRICAS DE ÉXITO

| Métrica | Valor Actual | Objetivo | Timeline |
|---------|--------------|----------|----------|
| Load Time | ~4s | <2s | Semana 3 |
| Lighthouse Score | ~55 | >85 | Semana 3 |
| Chatbot Response | ~3-5s | <1s | Semana 2 |
| Combo Errors | ~15% | 0% | Semana 2 |
| Stock Confusion | Alto | Bajo (visual claro) | Semana 1 |
| UX Smoothness | Tirones | 60 FPS | Semana 3 |

---

## 🔐 CONSIDERACIONES SEGURIDAD

1. **CSRF Protection**: Mantener habilitado (ya existe)
2. **Rate Limiting**: Implementar per-user en API
3. **API Keys**: Rotar regularmente (bot + panel)
4. **2FA/MFA**: Requerir para super_admin
5. **Audit Logs**: Registrar todas las acciones admin
6. **SQL Injection**: Usar siempre ORM (ya se hace)
7. **XSS Prevention**: Sanitizar inputs en bot
8. **HTTPS**: Requerir en producción

---

## 📚 DOCUMENTACIÓN A CREAR

- [ ] README.md actualizado con arquitectura
- [ ] API Documentation (Swagger/OpenAPI)
- [ ] Database Schema Diagram
- [ ] Chatbot Commands Reference
- [ ] Admin Panel User Guide
- [ ] Developer Setup Guide
- [ ] Deployment Checklist

---

## ✅ CHECKLIST FINAL DE DEPLOY

- [ ] Fase 1 completada y testeada
- [ ] Fase 2 completada y testeada  
- [ ] Fase 3 completada y testeada
- [ ] Fase 4 completada y testeada
- [ ] Fase 5 completada y testeada
- [ ] Fase 6 completada y testeada
- [ ] Lighthouse score >85
- [ ] Chatbots funcionales 100%
- [ ] Stock separado correctamente
- [ ] Combos validados
- [ ] Puntos funcionando
- [ ] Performance OK
- [ ] Seguridad auditada
- [ ] Documentación completa
- [ ] Capacitación staff done
- [ ] Backup pre-deploy
- [ ] Rollback plan ready

---

**Creado por:** GitHub Copilot  
**Última actualización:** 2026-07-02  
**Estado:** Listo para implementación

