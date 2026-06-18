# OXIDIAN — Arquitectura y Flujos de la Plataforma
> Sistema integral de delivery de productos latinos
> Versión: 1.0 — Fase 1

---

## 1. ESTRUCTURA DE ARCHIVOS

```
oxidian/
├── app.py                        # Punto de entrada, factory, config
├── models.py                     # ORM: todos los modelos SQLAlchemy
├── requirements.txt
├── config.py                     # Configuración por entorno
├── extensions.py                 # db, login_manager, etc.
│
├── routes/
│   ├── __init__.py
│   ├── public.py                 # Catálogo, carrito, checkout y puntos por WhatsApp
│   ├── admin.py                  # Dashboard, caja, stock, cupones
│   ├── staff.py                  # Inventario básico
│   ├── preparador.py             # Vista de pedidos a armar
│   ├── repartidor.py             # Vista de ruta y entrega
│   └── auth.py                   # Login/logout exclusivo para empleados
│
├── templates/
│   ├── base.html                 # Layout base (dark mode, Tailwind)
│   ├── public/
│   │   ├── index.html            # Landing / menú por secciones
│   │   ├── producto.html         # Detalle de producto
│   │   ├── carrito.html          # Carrito de compras
│   │   ├── checkout.html         # Paso de confirmación y pago
│   │   ├── pedido_confirmado.html
│   │   └── puntos_consulta.html  # Consulta privada de puntos por WhatsApp
│   ├── admin/
│   │   ├── dashboard.html        # Resumen general
│   │   ├── caja.html             # Control de entradas/salidas
│   │   ├── productos.html        # CRUD de productos
│   │   ├── stock.html            # Gestión de stock + caducidad
│   │   ├── pedidos.html          # Todos los pedidos
│   │   ├── cupones.html          # CRUD cupones y combos
│   │   ├── usuarios.html         # Gestión de roles y usuarios
│   │   └── reportes.html         # Estadísticas y exportes
│   ├── staff/
│   │   └── inventario.html       # Vista limitada de stock
│   ├── preparador/
│   │   └── pedidos.html          # Cola de pedidos a armar
│   ├── repartidor/
│   │   └── ruta.html             # Lista de entregas del día
│   └── auth/
│       └── login.html            # Acceso interno de empleados
│
└── static/
    ├── css/
    │   └── oxidian.css           # Variables y overrides del tema
    └── js/
        ├── carrito.js            # Lógica de carrito (localStorage)
        └── admin.js              # Helpers de dashboard
```

---

## 2. BASE DE DATOS — ESQUEMA POSTGRESQL

### Tabla: `users`
| Columna          | Tipo         | Notas                                      |
|------------------|--------------|--------------------------------------------|
| id               | INTEGER PK   | Auto-increment                             |
| nombre           | VARCHAR(100) | NOT NULL                                   |
| email            | VARCHAR(120) | UNIQUE, NOT NULL                           |
| password_hash    | VARCHAR(256) | bcrypt                                     |
| rol              | VARCHAR(20)  | admin / staff / preparador / repartidor / cliente |
| telefono         | VARCHAR(20)  |                                            |
| direccion        | TEXT         |                                            |
| puntos           | INTEGER      | DEFAULT 0 — Club de Clientes               |
| activo           | BOOLEAN      | DEFAULT TRUE                               |
| creado_en        | DATETIME     | DEFAULT NOW                                |

---

### Tabla: `categorias`
| Columna     | Tipo         | Notas             |
|-------------|--------------|-------------------|
| id          | INTEGER PK   |                   |
| nombre      | VARCHAR(80)  | Ej: Snacks, Bebidas, Dulces |
| descripcion | TEXT         |                   |
| imagen_url  | VARCHAR(300) |                   |
| activo      | BOOLEAN      | DEFAULT TRUE      |

---

### Tabla: `products`
| Columna          | Tipo           | Notas                              |
|------------------|----------------|------------------------------------|
| id               | INTEGER PK     |                                    |
| nombre           | VARCHAR(150)   | NOT NULL                           |
| descripcion      | TEXT           |                                    |
| precio           | DECIMAL(10,2)  | Precio de venta                    |
| precio_costo     | DECIMAL(10,2)  | Para margen en reportes            |
| categoria_id     | FK → categorias|                                    |
| imagen_url       | VARCHAR(300)   |                                    |
| origen_pais      | VARCHAR(50)    | Ej: México, Colombia, Cuba         |
| es_combo         | BOOLEAN        | DEFAULT FALSE                      |
| activo           | BOOLEAN        | DEFAULT TRUE                       |
| creado_en        | DATETIME       |                                    |

---

### Tabla: `stock`
| Columna          | Tipo          | Notas                                    |
|------------------|---------------|------------------------------------------|
| id               | INTEGER PK    |                                          |
| producto_id      | FK → products |                                          |
| cantidad         | INTEGER       | Unidades disponibles                     |
| unidad           | VARCHAR(20)   | unidad / kg / litro                      |
| lote             | VARCHAR(50)   | Número de lote para trazabilidad         |
| fecha_entrada    | DATE          |                                          |
| fecha_caducidad  | DATE          | NULL si no aplica                        |
| alerta_dias      | INTEGER       | DEFAULT 7 — días antes de alerta         |
| ubicacion        | VARCHAR(100)  | Estante / nevera / almacén               |

---

### Tabla: `combo_items`  *(productos dentro de un combo)*
| Columna          | Tipo          | Notas              |
|------------------|---------------|--------------------|
| id               | INTEGER PK    |                    |
| combo_id         | FK → products | El producto combo  |
| producto_id      | FK → products | Componente         |
| cantidad         | INTEGER       | Unidades incluidas |
| es_seleccionable | BOOLEAN       | Si el cliente puede elegirlo |
| grupo_seleccion  | VARCHAR(50)   | Ej: Bebida, Salsa, Acompañamiento |
| max_selecciones  | INTEGER       | Cantidad de opciones a elegir en el grupo |

---

### Tabla: `orders`
| Columna           | Tipo          | Notas                                        |
|-------------------|---------------|----------------------------------------------|
| id                | INTEGER PK    |                                              |
| numero_pedido     | VARCHAR(20)   | UNIQUE — ej: OX-20260429-001                 |
| cliente_id        | FK → users    |                                              |
| estado            | VARCHAR(30)   | pendiente / armando / listo / en_ruta / entregado / cancelado |
| subtotal          | DECIMAL(10,2) |                                              |
| descuento         | DECIMAL(10,2) | DEFAULT 0                                    |
| total             | DECIMAL(10,2) |                                              |
| cupon_id          | FK → coupons  | NULL si no aplica                            |
| puntos_usados     | INTEGER       | DEFAULT 0                                    |
| puntos_ganados    | INTEGER       | DEFAULT 0                                    |
| metodo_pago       | VARCHAR(30)   | efectivo / tarjeta / transferencia           |
| direccion_entrega | TEXT          |                                              |
| notas             | TEXT          |                                              |
| preparador_id     | FK → users    | Staff asignado a armar                       |
| repartidor_id     | FK → users    | Repartidor asignado                          |
| creado_en         | DATETIME      |                                              |
| entregado_en      | DATETIME      | NULL hasta entrega                           |

---

### Tabla: `order_items`
| Columna       | Tipo          | Notas                        |
|---------------|---------------|------------------------------|
| id            | INTEGER PK    |                              |
| pedido_id     | FK → orders   |                              |
| producto_id   | FK → products | Producto vendido; puede ser combo |
| cantidad      | INTEGER       |                              |
| precio_unit   | DECIMAL(10,2) | Precio al momento de compra  |
| subtotal      | DECIMAL(10,2) |                              |
| notas         | TEXT          | Resumen visible del combo o personalización |
| metadata_json | TEXT          | Snapshot JSON de componentes y selecciones para cocina, devoluciones y auditoría |

---

### Tabla: `reviews`
| Columna      | Tipo          | Notas                             |
|--------------|---------------|-----------------------------------|
| id           | INTEGER PK    |                                   |
| producto_id  | FK → products |                                   |
| cliente_id   | FK → users    |                                   |
| pedido_id    | FK → orders   | Reseña ligada a compra verificada |
| calificacion | INTEGER       | 1-5 estrellas                     |
| comentario   | TEXT          |                                   |
| aprobada     | BOOLEAN       | DEFAULT FALSE — moderación admin  |
| creado_en    | DATETIME      |                                   |

---

### Tabla: `coupons`
| Columna          | Tipo          | Notas                                     |
|------------------|---------------|-------------------------------------------|
| id               | INTEGER PK    |                                           |
| codigo           | VARCHAR(30)   | UNIQUE                                    |
| descripcion      | VARCHAR(200)  |                                           |
| tipo             | VARCHAR(20)   | porcentaje / monto_fijo / envio_gratis    |
| valor            | DECIMAL(10,2) | % o monto según tipo                      |
| minimo_pedido    | DECIMAL(10,2) | DEFAULT 0                                 |
| usos_maximos     | INTEGER       | NULL = ilimitado                          |
| usos_actuales    | INTEGER       | DEFAULT 0                                 |
| activo           | BOOLEAN       | DEFAULT TRUE                              |
| fecha_inicio     | DATE          |                                           |
| fecha_fin        | DATE          |                                           |

---

### Tabla: `points_log`  *(trazabilidad del Club de Clientes)*
| Columna      | Tipo          | Notas                              |
|--------------|---------------|------------------------------------|
| id           | INTEGER PK    |                                    |
| cliente_id   | FK → users    |                                    |
| pedido_id    | FK → orders   | NULL si es ajuste manual           |
| tipo         | VARCHAR(20)   | ganado / canjeado / ajuste         |
| cantidad     | INTEGER       |                                    |
| descripcion  | VARCHAR(200)  |                                    |
| creado_en    | DATETIME      |                                    |

---

### Tabla: `caja`  *(control de caja diario)*
| Columna      | Tipo          | Notas                                      |
|--------------|---------------|--------------------------------------------|
| id           | INTEGER PK    |                                            |
| tipo         | VARCHAR(20)   | ingreso / egreso                           |
| monto        | DECIMAL(10,2) |                                            |
| concepto     | VARCHAR(200)  | Ej: Pago proveedor, Venta #OX-001          |
| pedido_id    | FK → orders   | NULL si no está ligado a pedido            |
| registrado_por | FK → users  |                                            |
| fecha        | DATETIME      | DEFAULT NOW                                |

---

## 3. ROLES Y PERMISOS

| Rol          | Acceso                                                                 |
|--------------|------------------------------------------------------------------------|
| `admin`      | Todo: dashboard, caja, stock, pedidos, usuarios, cupones, reportes     |
| `staff`      | Inventario básico: ver y actualizar stock, sin acceso a caja ni users  |
| `preparador` | Ver pedidos en estado `pendiente` → marcarlos `armando` → `listo`      |
| `repartidor` | Ver pedidos `listo` asignados → marcarlos `en_ruta` → `entregado`      |
| `cliente`    | Catálogo, carrito, mis pedidos, reseñas, puntos del club               |

---

## 4. FLUJOS COMPLETOS DE LA PLATAFORMA

---

### FLUJO 1 — Login interno de empleados

```
[Login /auth/login]
    ├─ Email + Password → verificar hash bcrypt
    ├─ Rechaza registros rol=cliente
    ├─ Si rol=admin      → redirect /admin/dashboard
    ├─ Si rol=preparacion → redirect /preparador/pedidos
    ├─ Si rol=repartidor → redirect /repartidor/ruta
    └─ Si rol=proveedor  → redirect /proveedor/pedidos

Los clientes no crean cuenta ni inician sesión. El teléfono identifica
internamente pedidos, puntos y comunicaciones.
```

---

### FLUJO 2 — Compra Pública (Cliente)

```
[/ — Catálogo]
    ├─ Filtro por categoría (tabs horizontales)
    ├─ Tarjetas de producto con: foto, precio, país de origen, rating
    ├─ Botón "Agregar al carrito" → JS actualiza carrito en localStorage
    └─→ [/producto/<id> — Detalle]
            ├─ Descripción completa, reseñas aprobadas, calificación promedio
            └─ Botón agregar al carrito

[/carrito — Carrito]
    ├─ Lista de ítems: cantidad editable, eliminar
    ├─ Campo "Código de cupón" → validación AJAX → aplica descuento
    ├─ Campo "Usar puntos" → descuento por puntos del club
    ├─ Resumen: subtotal, descuento, total
    └─→ [/checkout — Confirmación]
            ├─ Nombre, teléfono y dirección recordados en el dispositivo
            ├─ Método de pago
            ├─ Notas adicionales
            ├─ Confirmar pedido
            │    ├─ Crea registro en `orders` con estado=pendiente
            │    ├─ Crea registros en `order_items`
            │    ├─ Descuenta stock (stock.cantidad -= item.cantidad)
            │    ├─ Registra ingreso en `caja`
            │    ├─ Calcula y suma puntos_ganados (1 punto por cada €1)
            │    └─ Registra en `points_log`
            └─→ [/pedido-confirmado/<id>]
                    └─ Número de pedido, resumen, puntos ganados
```

---

### FLUJO 3 — Club de Clientes (Puntos)

```
[/club — Consulta privada]
    ├─ El cliente introduce su WhatsApp
    ├─ El saldo se envía al propio número, no se revela en el navegador
    └─ Para canjear en carrito/checkout se exige un código OTP por WhatsApp

Reglas de puntos (configurables en config.py):
    - 1 punto por cada €1 gastado
    - 100 puntos = €1 de descuento
    - No se pueden ganar y canjear en el mismo pedido
```

---

### FLUJO 4 — Reseñas

```
Después de entrega (estado=entregado):
    ├─ El chatbot puede solicitar la valoración al cliente
    ├─ Se asocia al pedido y al teléfono registrado
    ├─ Se guarda con aprobada=False
    └─ Admin aprueba/rechaza en /admin/productos → pestaña Reseñas

En el catálogo:
    └─ Solo se muestran reseñas con aprobada=True
```

---

### FLUJO 5 — Admin: Control de Pedidos

```
[/admin/pedidos]
    ├─ Tabla filtrable: todos los estados, fecha, cliente, repartidor
    ├─ Vista Kanban opcional: pendiente | armando | listo | en_ruta | entregado
    ├─ Asignar preparador al pedido
    ├─ Asignar repartidor al pedido
    └─ Cancelar pedido → devuelve stock + anula puntos
```

---

### FLUJO 6 — Admin: Control de Caja

```
[/admin/caja]
    ├─ Resumen del día: total ingresos, total egresos, saldo neto
    ├─ Filtro por fecha o rango
    ├─ Registrar egreso manual: concepto + monto (ej: pago a proveedor)
    ├─ Los ingresos de pedidos se registran automáticamente al confirmar
    └─ Exportar CSV del rango seleccionado
```

---

### FLUJO 7 — Admin: Gestión de Stock

```
[/admin/stock]
    ├─ Lista de stock por producto: cantidad, lote, fecha caducidad
    ├─ Alerta visual si fecha_caducidad - hoy <= alerta_dias
    ├─ Agregar entrada de stock: producto, cantidad, lote, fecha cad., ubicación
    ├─ Ajuste manual de cantidad (merma, rotura)
    └─ Historial de movimientos por producto
```

---

### FLUJO 8 — Admin: Cupones y Combos

```
[/admin/cupones]
    ├─ CRUD de cupones: código, tipo, valor, fechas, límite de usos
    └─ Estadísticas: veces usado, ahorro generado a clientes

[/admin/productos] → Combos
    ├─ Crear producto con es_combo=True
    ├─ Seleccionar componentes y cantidades (combo_items)
    └─ El stock se descuenta del componente, no del combo en sí
```

---

### FLUJO 9 — Preparador

```
[/preparador/pedidos]
    ├─ Cola de pedidos en estado=pendiente
    ├─ Card por pedido: número, cliente, ítems, notas
    ├─ Botón "Empezar a armar" → estado=armando (se registra hora)
    └─ Botón "Listo para despacho" → estado=listo
```

---

### FLUJO 10 — Repartidor

```
[/repartidor/ruta]
    ├─ Lista de pedidos estado=listo asignados a este repartidor
    ├─ Dirección de entrega + notas del cliente
    ├─ Botón "Salir a entregar" → estado=en_ruta
    └─ Botón "Entregado" → estado=entregado
            ├─ Registra entregado_en=NOW
            └─ Suma puntos_ganados al cliente
```

---

### FLUJO 11 — Staff: Inventario Básico

```
[/staff/inventario]
    ├─ Ver stock actual de todos los productos
    ├─ Registrar entrada de stock (sin acceso a precios de costo)
    └─ Ver alertas de caducidad próxima
```

---

## 5. DISEÑO — TEMA OXIDIAN

| Token            | Valor          | Uso                              |
|------------------|----------------|----------------------------------|
| `bg-base`        | `#0F0F0F`      | Fondo principal                  |
| `bg-surface`     | `#1A1A2E`      | Cards, modales                   |
| `bg-elevated`    | `#16213E`      | Navbar, sidebar                  |
| `accent-gold`    | `#F5A623`      | CTAs primarios, badges           |
| `accent-red`     | `#E63946`      | Alertas, precios tachados        |
| `accent-green`   | `#2EC4B6`      | Confirmaciones, stock OK         |
| `text-primary`   | `#EAEAEA`      | Textos principales               |
| `text-muted`     | `#8A8A9A`      | Subtextos, labels                |
| Border radius    | `rounded-xl`   | Tailwind estándar                |
| Font             | Inter / system | Google Fonts CDN                 |

Tailwind CDN en base.html. Sin build step en Fase 1.

---

## 6. DEPENDENCIAS (requirements.txt)

```
Flask==3.1.0
Flask-SQLAlchemy==3.1.1
Flask-Login==0.6.3
Flask-WTF==1.2.1
Werkzeug==3.1.3
python-dotenv==1.0.1
email-validator==2.2.0
```

---

## 7. VARIABLES DE ENTORNO (.env)

```
SECRET_KEY=oxidian-super-secret-key-cambiar-en-produccion
DATABASE_URL=postgresql://usuario:password@host:5432/oxidian
PUNTOS_POR_EURO=1
PUNTOS_CANJE_RATIO=100
ALERTA_CADUCIDAD_DIAS=7
DEBUG=True
```

---

## 8. PUERTOS Y DOMINIO

### Regla de oro: un solo dominio, sin puertos públicos expuestos
En producción **nginx** es el único servicio con puerto público (80/443).
Flask y el bot viven en la red interna de Docker y **nunca** son accesibles directamente desde fuera.

### Tabla de puertos

| Servicio               | Puerto   | Contexto                              | ¿Expuesto públicamente? |
|------------------------|----------|---------------------------------------|--------------------------|
| **Flask dev local**    | **5055** | `arrancar_local.sh` / `app.py`        | No — solo 127.0.0.1       |
| **Flask / Gunicorn**   | **5000** | Docker interno → nginx proxy          | No                        |
| **nginx → Cosmos**     | **5070** | Cosmos conecta aquí, redirige al dom. | Solo desde Cosmos OS      |
| Bot Node.js            | 3000     | Llamado por Flask (red Docker)        | No                        |
| Evolution API          | 8080     | Llamado por bot (red Docker)          | No                        |
| PostgreSQL             | 5432     | Flask / scripts de migración          | No                        |

> **Lo que ve el usuario final:** solo `https://tudominio.com` (Cosmos SSL).
> Ningún puerto numérico es visible ni accesible desde Internet.

### Puerto de desarrollo local: **5055** (FIJO — no cambiar)

```
http://localhost:5055       ← navegador en esta máquina
http://192.168.x.x:5055    ← tablet o móvil en la misma red LAN
```

Fuente de verdad: `.env` → `OXIDIAN_PORT=5055`
Todos los scripts leen esa variable; el valor nunca cambia entre sesiones.

| Comando                               | Cuándo usarlo                        |
|---------------------------------------|--------------------------------------|
| `bash arrancar_local.sh`              | Dev rápido, BD real de desarrollo    |
| `python app.py`                       | Debug directo (lee `.env`)           |

### Flujo en producción (Cosmos OS)

```
Internet → Cosmos (SSL + dominio) → nginx :5070 → Flask :5000
                                                  ↘ Bot Node.js :3000 → Evolution :8080
```

- Cosmos actúa como reverse proxy externo (gestiona SSL, certificados Let's Encrypt).
- nginx (`nginx.single-domain.conf`) recibe en el puerto `5070` del host y distribuye
  todo el tráfico del dominio hacia Flask. No hay rutas públicas para el bot ni Evolution.

```
tudominio.com/          → Flask (menú, checkout, admin, API)
tudominio.com/static/   → Flask (assets — cache 30 días)
tudominio.com/uploads/  → Flask (imágenes de productos — cache 7 días)
```

---

## 9. ROADMAP DE FASES

| Fase | Contenido                                                    |
|------|--------------------------------------------------------------|
| 1    | Este documento: estructura, modelos, flujos base             |
| 2    | Implementación Flask: app.py, models.py, auth, catálogo      |
| 3    | Carrito, checkout, pedidos, panel preparador/repartidor      |
| 4    | Admin completo: caja, stock, cupones, reportes               |
| 5    | Docker + Portainer, .env de producción, migración PostgreSQL |
| 6    | Notificaciones (email/WhatsApp), app móvil opcional          |
