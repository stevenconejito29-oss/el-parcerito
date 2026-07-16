# Oxidian — Diagrama de Flujos del Sistema

> **Especificación histórica (2026-05-09).** Algunos roles y módulos cambiaron.
> El mapa vigente está en
> [`../docs/PROJECT_STRUCTURE.md`](../docs/PROJECT_STRUCTURE.md).

Versión: 2026-05-09
Stack: Flask + SQLAlchemy + PostgreSQL · Bot WhatsApp (Node.js) · POS

---

## Índice

1. [Arquitectura general](#1-arquitectura-general)
2. [Flujo de autenticación y roles](#2-flujo-de-autenticacion-y-roles)
3. [Flujo de pedido online (web)](#3-flujo-de-pedido-online-web)
4. [Flujo de pedido WhatsApp (bot)](#4-flujo-de-pedido-whatsapp-bot)
5. [Flujo de pedido presencial (POS)](#5-flujo-de-pedido-presencial-pos)
6. [Ciclo de vida del pedido (estados)](#6-ciclo-de-vida-del-pedido-estados)
7. [Sistema de puntos (fidelización)](#7-sistema-de-puntos-fidelizacion)
8. [Gestión de stock (FIFO)](#8-gestion-de-stock-fifo)
9. [Sistema de precios y descuentos](#9-sistema-de-precios-y-descuentos)
10. [Afiliados y comisiones](#10-afiliados-y-comisiones)
11. [Pagos de staff (nóminas)](#11-pagos-de-staff-nominas)
12. [Caja (movimientos financieros)](#12-caja-movimientos-financieros)
13. [Campañas de marketing WhatsApp](#13-campanas-de-marketing-whatsapp)
14. [Administración de productos](#14-administracion-de-productos)
15. [Control de acceso por módulo (AdminFeature)](#15-control-de-acceso-por-modulo-adminfeature)
16. [Notificaciones WhatsApp](#16-notificaciones-whatsapp)
17. [Integridad de datos — reglas críticas](#17-integridad-de-datos-reglas-criticas)

---

## 1. Arquitectura general

```
Cliente web/móvil
       │
       ▼
Flask (puerto 5000)
  ├── /public/*         → catálogo, carrito, checkout y puntos por WhatsApp
  ├── /auth/*           → login/logout exclusivo para personal interno
  ├── /admin/*          → panel admin (roles: admin, super_admin)
  ├── /superadmin/*     → configuración, P&L, auditoría (solo super_admin)
  ├── /marketing/*      → campañas, puntos, afiliados (roles: marketing, admin, super_admin)
  ├── /preparador/*     → cola de preparación (cocina, preparacion, admin)
  ├── /repartidor/*     → ruta y entregas (repartidor, admin)
  ├── /pos/*            → punto de venta presencial (staff, admin)
  ├── /staff/*          → inventario físico (staff, admin)
  ├── /api/bot/*        → API REST para bot WhatsApp (autenticada con BOT_API_KEY)
  └── /uploads/*        → subida y servicio de imágenes

Bot WhatsApp (Node.js, puerto 3000)
  └── /api/bot/message  → recibe mensajes a enviar desde Flask
  └── /api/oxidian/*    → llama al Flask para catálogo, pedidos, puntos

Base de datos única de negocio: PostgreSQL Oxidian. Evolution utiliza su propia PostgreSQL técnica.
Imágenes compartidas:     /images/{productos,logo,categorias,banners}/
```

---

## 2. Flujo de autenticación y roles

### Roles del sistema
| Rol | Acceso | Redirect tras login |
|-----|--------|---------------------|
| `super_admin` | Todo sin restricción | `/superadmin/dashboard` |
| `admin` | Panel admin (limitado por AdminFeature) | `/admin/dashboard` |
| `marketing` | Cupones, afiliados, promociones, campañas, puntos | `/marketing/dashboard` |
| `cocina` | Pedidos de productos tipo "inmediato" | `/preparador/pedidos` |
| `preparacion` | Pedidos de productos tipo "encargo"/"programado" | `/preparador/pedidos` |
| `repartidor` | Cola de reparto | `/repartidor/ruta` |
| `staff` | Inventario físico, POS | `/staff/inventario` |
| `cliente` | Registro comercial interno; no inicia sesión | — |

### Login
```
POST /auth/login
  ├── Input: email, password
  ├── Valida: User.activo=True, check_password()
  ├── session.permanent = True (8h PERMANENT_SESSION_LIFETIME)
  └── Redirect según REDIRECT_POR_ROL[user.rol]
```

### Creación de usuarios
- `super_admin` crea desde `/superadmin/admins/crear` (roles: admin, super_admin)
- `admin/super_admin` crea cuentas laborales desde `/admin/usuarios/crear`
- El checkout y el bot crean registros internos `rol=cliente`, identificados por teléfono y sin acceso al login
- Bot crea clientes en `/api/bot/cliente/registrar`

**Reglas de seguridad:**
- Un admin NO puede cambiar el rol de otro admin/super_admin
- Un admin NO puede crear roles admin/super_admin
- Un admin NO puede desactivar a otros admins
- Email y teléfono (para clientes) son únicos

---

## 3. Flujo de pedido online (web)

```
Cliente → /carrito/agregar/<id>  (session["carrito"] = {pid: qty})
        → /carrito                (ver, actualizar, eliminar)
        → /carrito/cupon          (AJAX validar cupón/afiliado)
        → /puntos/solicitar-codigo (AJAX → envía OTP WhatsApp)
        → /puntos/verificar-codigo (AJAX → guarda en session["cart_puntos"])
        → /checkout GET           (muestra formulario)
        → /checkout POST
            ├── Lee: direccion, metodo_pago, notas(max 1000 chars), cupon_id,
            │         zona_id, nombre_invitado, telefono_invitado, fecha_encargo
            ├── Valida: carrito no vacío, zona activa, fecha encargo si hay encargos
            ├── Valida: radio de entrega (si VALIDAR_RADIO_ENTREGA=1)
            ├── Resuelve cliente (_resolve_checkout_customer):
            │     ├── Si logueado → current_user
            │     └── Si invitado → busca por telefono → crea si no existe
            ├── Valida cupón (es_valido()) y afiliado (es_valido())
            ├── Lee puntos verificados de session["cart_puntos"]
            ├── calcular_precio() → PricingResult (inmutable)
            │     Orden: promo_auto → cupón(≤50%) → afiliado(≤30%) → puntos → envío
            ├── Registra uso del cupón (cupon.registrar_uso())
            ├── Crea Order (estado="pendiente", origen="online")
            ├── Crea OrderItems
            │     └── Si el item es combo: guarda notas + metadata_json con componentes y selecciones
            ├── Descuenta stock solo para tipo_entrega="inmediato"
            │     ├── Producto simple → Product.descontar_stock()
            │     └── Combo → Product.descontar_stock_combo() sobre componentes reales
            ├── aplicar_canje_en_pedido() (loyalty_service — único punto deducción puntos)
            ├── sumar_puntos() si puntos_ganados > 0
            ├── registrar_uso_afiliado() → AffiliateUse + StaffPayment comisión
            ├── registrar_ingreso() en Caja (categoria="venta_online")
            ├── distribuir_pedido() → asigna preparador automáticamente
            ├── db.session.commit()
            ├── Limpia session: carrito, cart_puntos, cart_producto_canje_id, notas_combo, combo_selecciones
            └── enviar_whatsapp_estado(pedido) → notifica al cliente
```

**Variables de sesión usadas:**
- `session["carrito"]` → `{str(producto_id): cantidad}`
- `session["cart_puntos"]` → `{cliente_id, puntos_usados, descuento, puntos_totales}`
- `session["cart_producto_canje_id"]` → int | None
- `session["notas_combo"]` → `{str(producto_id): notas_personalizacion}`
- `session["combo_selecciones"]` → `{str(combo_id): {grupo: [combo_item_id, ...]}}`
- `session["guest_order_tokens"]` → `{str(pedido_id): token_hex}`

---

## 4. Flujo de pedido WhatsApp (bot)

```
Bot → GET /api/bot/catalogo          (catálogo filtrado por visible_ahora)
    → GET /api/bot/catalogo/completo  (con tipo_entrega, promo, badges)
    → GET /api/bot/cliente?telefono=X (busca cliente)
    → POST /api/bot/cliente/registrar (crea cliente si no existe)
    → GET /api/bot/puntos?telefono=X  (saldo de puntos)
    → POST /api/bot/validar-cupon     (valida cupón o código afiliado)
    → POST /api/bot/pedido/crear
          ├── Input: telefono_cliente, items[], metodo_pago, direccion_entrega,
          │          zona_id, notas, cupon_codigo, puntos_usar
          ├── Valida: cliente existe, items válidos, stock suficiente (solo inmediato)
          ├── calcular_precio() mismo motor que web
          ├── Crea Order (estado="pendiente", origen="whatsapp")
          ├── descontar_stock() solo para tipo_entrega="inmediato"
          ├── aplicar_canje_en_pedido() (loyalty_service)
          ├── sumar_puntos()
          ├── registrar_ingreso() (registrado_por=None — bot no tiene user_id)
          ├── distribuir_pedido()
          ├── registrar_uso_afiliado() si afiliado
          └── db.session.commit()
```

**Nota:** El bot llama al Flask con header `X-Bot-Key` = `SiteConfig.BOT_API_KEY`.

---

## 5. Flujo de pedido presencial (POS)

```
Staff/Admin → GET /pos/              (catálogo + búsqueda)
           → GET /pos/buscar?q=X     (AJAX busca productos)
           → POST /pos/cobrar        (JSON)
                 ├── Input: items[], metodo_pago, descuento_manual, cliente_id, notas, cupon_codigo
                 ├── Valida: stock solo para tipo_entrega="inmediato"
                 ├── calcular_precio() mismo motor que web/bot
                 ├── Crea Order (estado="entregado", origen="presencial", cajero_id=current_user.id)
                 ├── descontar_stock() solo para tipo_entrega="inmediato"
                 ├── Registra cupón
                 ├── sumar_puntos() si cliente_id registrado
                 ├── registrar_ingreso() (categoria="venta_presencial")
                 └── db.session.commit()
           → POST /pos/devolver/<id>  (cancela pedido POS)
                 ├── cancelar_pedido_operativo(forzar_desde_entregado=True)
                 ├── restaura stock SOLO de items tipo="inmediato"
                 └── revierte el ingreso cobrado (categoria="devolucion")
           → POST /pos/movimiento     (entrada/salida manual de caja)
```

**Diferencia clave POS vs web/bot:**
- POS crea el pedido directamente en estado `"entregado"` (sin pasar por la cadena de estados)
- La devolución POS usa `forzar_desde_entregado=True` en `cancelar_pedido_operativo()`

---

## 6. Ciclo de vida del pedido (estados)

```
ESTADOS_PEDIDO = ["pendiente", "armando", "listo", "en_ruta", "entregado", "cancelado"]

pendiente ──► armando ──► listo ──► en_ruta ──► entregado
    │             │          │          │
    └─────────────┴──────────┴──────────┴──────► cancelado

Transiciones:
  pendiente → armando   : preparador.empezar_armar()   → notifica WA "armando"
  armando   → listo     : preparador.marcar_listo()    → distribuir_repartidor() + notifica WA "listo"
  listo     → en_ruta   : repartidor.salir_entregar()  → generar_codigo_confirmacion() + notifica WA "en_ruta" con código
  en_ruta   → entregado : repartidor.confirmar_entrega() → valida código 6 dígitos + cobro_recibido + generar_comision_entrega() + notifica WA "entregado" + solicitar_resena_pedido() (90s delay)

Cancelación:
  Desde cualquier estado (excepto "entregado" sin forzar, y "cancelado"):
  - Restaura stock (solo items "inmediato" en online/whatsapp; todos en presencial)
  - Devuelve puntos_usados al cliente
  - Quita puntos_ganados del cliente (hasta su saldo actual)
  - Libera cupón/afiliado y comisiones pendientes
  - Si existía ingreso en Caja, crea una única reversión `devolucion`

Asignación y recuperación:
  - Preparador: asignación/reasignación únicamente en `pendiente`
  - Repartidor: asignación/reasignación únicamente en `listo` y delivery
  - Zona compatible y menor carga tienen prioridad en el reparto automático
  - El rebalanceo automático solo mueve trabajo no iniciado (`pendiente`/`listo`)
  - `armando` y `en_ruta` conservan responsable para evitar duplicar trabajo físico

Confirmación pago digital (bizum):
  admin.confirmar_pago_digital() → pago_confirmado=True + notifica WA
  admin.rechazar_pago_digital()  → cancelar_pedido_operativo() + notifica WA
```

**Campos de control en Order:**
- `codigo_confirmacion` — 6 dígitos, generado al pasar a en_ruta
- `intentos_codigo` — máx. 3 intentos
- `pago_confirmado` / `pago_confirmado_por` / `pago_confirmado_en`
- `puntos_usados` / `puntos_ganados`
- `cajero_id` — quién cobró en POS

---

## 7. Sistema de puntos (fidelización)

### Flujo canónico (loyalty_service.py)

```
1. GANAR PUNTOS (sumar_puntos):
   puntos_ganados = int(total_pedido * PUNTOS_POR_EURO)
   → llamado en: checkout web, api_bot crear_pedido, pos cobrar
   → registra PointsLog(tipo="ganado")

2. SOLICITAR CANJE (solicitar_codigo):
   → valida puntos > 0, telefono registrado
   → generar_cod_puntos() → OTP 6 dígitos, expira 10 min
   → envía WA al cliente
   → registra en User.cod_puntos, User.cod_puntos_expira

3. VERIFICAR CÓDIGO (verificar_codigo):
   → verifica OTP sin descontar todavía
   → guarda en session["cart_puntos"] (web) o responde JSON (bot)

4. APLICAR CANJE (aplicar_canje_en_pedido) — ÚNICO PUNTO DE DEDUCCIÓN:
   → llamado DESPUÉS de crear el pedido en BD
   → canjear_puntos(puntos_usar) → registra PointsLog(tipo="canjeado")
   → opcionalmente añade producto gratis (OrderItem precio=0)
   → limpia OTP usado

5. CANCELAR PEDIDO (cancelar_pedido_operativo):
   → Quita puntos_ganados (hasta saldo actual): PointsLog(tipo="cancelado")
   → Devuelve puntos_usados: PointsLog(tipo="ganado")
```

### Reglas de puntos
- 1 punto = 1/PUNTOS_CANJE_RATIO euros de descuento (default: 100 puntos = 1€)
- PUNTOS_POR_EURO y PUNTOS_CANJE_RATIO se leen SIEMPRE desde SiteConfig (BD), no Flask config
- Ajuste manual desde `/marketing/puntos/ajustar` (admin/marketing)
- Pre-canje bot (sin pedido): `api_bot/puntos/verificar-codigo` descuenta directamente con `canjear_puntos()` — NO pasa por loyalty_service (flujo distinto)

---

## 8. Gestión de stock (FIFO)

### Entrada de stock
```
admin → POST /admin/stock/agregar
staff → POST /staff/stock/entrada
  ├── Input: producto_id, cantidad, lote, fecha_caducidad, ubicacion
  └── Crea Stock(producto_id, cantidad, fecha_entrada=hoy, fecha_caducidad)
```

### Salida de stock (FIFO por caducidad)
```
Product.descontar_stock(cantidad):
  ├── Ordena lotes: fecha_caducidad ASC (más próxima primero), nullslast, fecha_entrada ASC
  ├── Consume FIFO hasta cubrir la cantidad pedida
  └── Lanza ValueError si stock insuficiente

Llamado desde:
  - checkout web: solo tipo_entrega="inmediato"
  - api_bot crear_pedido: solo tipo_entrega="inmediato"
  - pos cobrar: solo tipo_entrega="inmediato"
```

### Ajuste manual de lote
```
admin → POST /admin/stock/<lote_id>/ajustar
  ├── Input: cantidad_nueva
  ├── Valida: cantidad_nueva >= 0
  └── Actualiza Stock.cantidad directamente (no FIFO)
```

### Alertas de caducidad
```
_count_alertas_stock() en admin.dashboard:
  ├── Stock.esta_en_alerta: 0 <= dias_para_caducar <= alerta_dias (default 7)
  └── Mostrado en dashboard como contador
```

### Stock al cancelar pedido
```
Order.cancelar():
  ├── POS (origen="presencial"): restaura stock de TODOS los items
  └── Web/Bot (otros): restaura stock SOLO de items tipo_entrega="inmediato"
  └── Estrategia: LIFO (añade al lote más reciente)
```

---

## 9. Sistema de precios y descuentos

### Motor unificado: `calcular_precio()` (pricing_service.py)

```
Orden de aplicación (todos los canales: web, bot, POS):

1. Promociones automáticas (aplicar_promociones):
   ├── aplica_a="todos": % o monto_fijo sobre subtotal
   ├── aplica_a="categoria": sobre items de esa categoría
   └── aplica_a="producto": 2x1, 3x2, %, monto_fijo
   Prioridad: Promotion.prioridad DESC; respeta apilable=False

2. Cupón (máx 50% del subtotal):
   ├── tipo="porcentaje": subtotal * valor/100
   ├── tipo="monto_fijo": min(valor, subtotal)
   └── tipo="envio_gratis": 0 (aplicado al costo envío)

3. Afiliado (máx 30% del subtotal):
   ├── descuento_tipo="porcentaje": subtotal * descuento_valor/100
   └── descuento_tipo="monto_fijo": min(descuento_valor, subtotal)

4. Puntos de fidelidad:
   descuento_puntos = puntos_usar / PUNTOS_CANJE_RATIO

5. Costo de envío (ZonaEntrega):
   ├── gratis_desde: si subtotal >= gratis_desde → envío=0
   └── precio_envio: costo fijo de la zona

6. Descuento manual (solo POS):
   descuento_manual libre (sin cap)

Cap final: descuento_total ≤ subtotal; total mínimo = €0.01
```

### PricingResult (inmutable, dataclass frozen)
```python
PricingResult:
  subtotal, descuento_promo, descuento_cupon, descuento_afiliado,
  descuento_puntos, descuento_manual, costo_envio, descuento_total,
  total, promos_aplicadas, cupon_id, afiliado_codigo_id, puntos_usados
```

---

## 10. Afiliados y comisiones

```
Creación del código:
  admin → POST /admin/afiliados/crear
    ├── codigo (único), descuento_tipo, descuento_valor
    ├── comision_tipo, comision_valor (% o monto_fijo sobre total pedido)
    └── user_id (empleado que cobra comisión) — puede ser NULL

Uso en pedido:
  checkout/bot: registrar_uso_afiliado(codigo, pedido, cliente, descuento_aplicado)
    ├── Crea AffiliateUse(codigo_id, pedido_id, cliente_id, descuento_aplicado, comision_generada)
    ├── codigo.registrar_uso() → usos_actuales++
    └── Si codigo.user_id y comision > 0: crea StaffPayment(tipo="comision")

Pago de comisión:
  admin → POST /admin/afiliados/<id>/pagar  → marcar_pagado() + registrar_egreso()
  admin → POST /admin/afiliados/<id>/pagar-individual → paga un AffiliateUse específico
```

---

## 11. Pagos de staff (nóminas)

### Tipos de StaffPayment
| tipo | Origen | Descripción |
|------|--------|-------------|
| `salario` | Manual admin | Salario mensual fijo |
| `comision` | Auto: entrega | Tarifa × entregas del repartidor |
| `comision` | Auto: afiliado | % del pedido por código afiliado |
| `bonus` | Manual admin | Bono extraordinario |
| `adelanto` | Manual admin | Anticipo de nómina |
| `descuento` | Manual admin | Reduce el neto; no genera salida de caja |

### Flujo de generación automática
```
Entrega completada:
  repartidor.confirmar_entrega() → generar_comision_entrega(pedido)
    └── Si repartidor.tarifa_entrega > 0: StaffPayment(tipo="comision", monto=tarifa)

Generación por período:
  admin → POST /admin/pagos-staff/generar-comisiones-repartidores
    └── Completa pedidos delivery históricos sin comisión; una comisión por pedido

Generación de salarios:
  admin → POST /admin/pagos-staff/generar-salarios
    └── Para cada usuario con salario_base > 0, crea StaffPayment(tipo="salario")
```

### Pago
```
admin → POST /admin/pagos-staff/<id>/pagar  → marcar_pagado() + registrar_egreso()
admin → POST /admin/pagos-staff/pagar-seleccion → batch de IDs → marcar_pagado() + egreso
Los asientos `descuento` se marcan procesados pero nunca crean `Caja.egreso`.
```

---

## 12. Caja (movimientos financieros)

### Categorías de Caja
| categoria | Tipo | Origen |
|-----------|------|--------|
| `venta_online` | ingreso | checkout web |
| `venta_whatsapp` | ingreso | api_bot |
| `venta_presencial` | ingreso | pos cobrar |
| `pago_staff` | egreso | marcar pago staff |
| `devolucion` | egreso | reversión idempotente de un pedido cancelado |
| `compra_insumos` | egreso | manual admin |
| `gasto_operativo` | egreso | manual admin, devolución POS |
| `adelanto` | egreso | adelanto de nómina |
| `general` | ingreso/egreso | movimiento manual |

### Flujo
```
registrar_ingreso(monto, concepto, categoria, pedido_id, registrado_por)
  → Caja(tipo="ingreso", ...)

registrar_egreso(monto, concepto, categoria, staff_payment_id, registrado_por)
  → Caja(tipo="egreso", ...)

cancelar_pedido_operativo(...)
  → si el pedido tenía un ingreso, registra una sola devolución por pedido

Exportar: GET /admin/caja/exportar → CSV con filtro de fechas
```

### Resumen financiero
```
resumen_caja_hoy() → (ingresos_hoy, egresos_hoy)
calcular_pl(fecha_ini, fecha_fin) → P&L completo con:
  ventas por canal, pedidos, descuentos, nóminas, comisiones, ganancia_neta
```

---

## 13. Campañas de marketing WhatsApp

```
Crear borrador:
  marketing/admin → POST /marketing/campanas/crear
    ├── Input: titulo, mensaje (max 4096 chars), filtro_audiencia, zona_id
    └── Filtros: todos | con_puntos | sin_compra_30 | por_zona

Enviar campaña:
  marketing/admin → POST /marketing/campanas/<id>/enviar
    ├── Construye audiencia según filtro (Users con rol=cliente + telefono)
    ├── Cambia estado a "enviando", guarda count
    ├── Registra AuditLog
    ├── Lanza hilo daemon (con app.app_context):
    │     for tel in telefonos: _send_whatsapp_message(tel, mensaje) + sleep(1.2s)
    └── Actualiza estado a "enviado" + count real al terminar

Restricción anti-ban: 1 mensaje cada 1.2 segundos (~50/min máximo)
```

---

## 14. Administración de productos

### Flujo de creación de producto
```
admin → POST /admin/productos/crear
  ├── _parsear_campos_producto(form):
  │     ├── nombre (obligatorio)
  │     ├── precio > 0
  │     ├── tipo_entrega: inmediato | encargo | programado
  │     ├── puntos_para_canje > 0 SI canjeable_con_puntos=True
  │     └── atributos_json: JSON válido si se provee
  ├── _guardar_imagen_producto_desde_request(files) → _save_image()
  ├── Product(**campos)
  └── notificar_bot_sync() → fuerza resync catálogo en bot
```

### Combos
```
Combo = Product(es_combo=True) + ComboGroup[] + ComboItem[] con componentes

Precio del combo:
  ├── fijo → combo_precio_modo=fijo + precio final configurado
  └── porcentual → combo_precio_modo=descuento_porcentaje
                   + combo_descuento_pct aplicado a combo_precio_base

ComboItem:
  ├── combo_id → Product
  ├── producto_id → Product (componente)
  ├── cantidad
  ├── es_seleccionable + grupo_seleccion + max_selecciones
  └── El combo no descuenta stock de componentes individualmente
      (el stock se descuenta a nivel del combo completo)
```

### Visibilidad horaria de productos
```
Product.visible_ahora:
  ├── Si hora_inicio_visibilidad/hora_fin_visibilidad son NULL → siempre visible
  ├── Verifica hora actual esté en [inicio, fin]
  └── Si dias_semana_json: verifica día de la semana (0=lun, 6=dom)
```

---

## 15. Control de acceso por módulo (AdminFeature)

```
AdminFeature(user_id, feature, activo)

Módulos: caja | productos | stock | cupones | staff_pagos | reportes |
         zonas | auditoria | marketing | pos | whatsapp

Flujo:
  1. super_admin crea admin → AdminFeature.inicializar_para_admin(id, activar_todos=False)
  2. super_admin → /superadmin/admins/<id>/features/guardar → activa/desactiva módulos
  3. admin_bp.before_request → verificar_feature_acceso():
     ├── super_admin: siempre pasa
     ├── marketing: siempre pasa en sus rutas
     └── admin: verifica AdminFeature.tiene_acceso(user_id, feature)

URL → Feature map:
  /admin/caja          → caja
  /admin/stock         → stock
  /admin/pagos-staff   → staff_pagos
  /admin/analytics     → reportes
  /superadmin/chatbot  → whatsapp
  /admin/productos     → productos
  /admin/categorias    → productos
  /admin/cupones       → cupones
  /admin/promociones   → marketing
  /admin/afiliados     → marketing
  /admin/menu-config   → marketing
  /admin/resenas       → marketing
```

---

## 16. Notificaciones WhatsApp

### enviar_whatsapp_estado(pedido)
```
Disparado automáticamente en cada cambio de estado del pedido.
Llamado DESPUÉS del commit para garantizar que los datos están persistidos.

Plantillas por estado:
  pendiente : "✅ Tu pedido {num} fue recibido. Total: €{total}. ¡Ya lo estamos preparando!"
  armando   : "👨‍🍳 Estamos armando tu pedido {num}. En breve saldrá."
  listo     : "📦 Tu pedido {num} está listo y pronto saldrá a entregarse."
  en_ruta   : "🚀 Tu pedido {num} está en camino. Código de entrega: *{codigo}*."
  entregado : "🎉 ¡Pedido {num} entregado! Gracias. Ganaste {puntos} puntos. 💛"
  cancelado : "❌ Tu pedido {num} fue cancelado."

Configuración:
  BOT_API_URL (SiteConfig) → URL base del bot (default: http://chatbot:3000)
  BOT_API_KEY (SiteConfig) → clave de autenticación

Si el bot no responde: falla silenciosamente (timeout=3s), nunca bloquea el flujo principal.
```

### solicitar_resena_pedido(pedido)
```
Disparado por repartidor.confirmar_entrega() después del commit.
├── Lanza hilo daemon: espera 90 segundos, luego llama bot /api/bot/review-request
├── Marca Order.resena_enviada=True inmediatamente (en request context)
└── El bot envía mensaje al cliente solicitando calificación 1-5
```

---

## 17. Integridad de datos — reglas críticas

### Invariantes del sistema

| Invariante | Dónde se garantiza |
|------------|-------------------|
| Puntos solo se descuentan en `aplicar_canje_en_pedido()` (checkout web/bot) | loyalty_service.py |
| Stock solo se descuenta para tipo_entrega="inmediato" (web/bot) | public.py, api_bot.py, pos.py |
| Stock se restaura solo para items que lo tuvieron descontado en cancelar() | models.py Order.cancelar() |
| Total mínimo de pedido: €0.01 | pricing_service.py TOTAL_MINIMO |
| Descuento cupón máx 50% del subtotal | pricing_service.py MAX_CUPON_PCT |
| Descuento afiliado máx 30% del subtotal | pricing_service.py MAX_AFILIADO_PCT |
| Código de confirmación de entrega: 6 dígitos, generado al pasar a en_ruta | models.py avanzar_estado() |
| Máx 3 intentos de código de entrega | models.py confirmar_entrega_con_codigo() |
| OTP de puntos: 6 dígitos, expira 10 minutos | models.py generar_cod_puntos() |

### Campos críticos de Order
| Campo | Quién lo escribe | Quién lo lee |
|-------|-----------------|--------------|
| `puntos_ganados` | checkout, api_bot, pos (int(total*PPE)) | cancelar(), template perfil |
| `puntos_usados` | loyalty_service.aplicar_canje_en_pedido() | cancelar(), template |
| `cajero_id` | pos.cobrar() | (disponible para reports/audit) |
| `pago_confirmado` | admin confirmar_pago, repartidor confirmar_entrega | dashboard, pagos_pendientes |
| `codigo_confirmacion` | avanzar_estado() en_ruta | repartidor UI, enviar_whatsapp_estado() |
| `afiliado_codigo_id` | checkout, api_bot | registrar_uso_afiliado() (ya ejecutado) |

### Configuraciones en SiteConfig (claves)
| Clave | Descripción | Default |
|-------|-------------|---------|
| `PUNTOS_POR_EURO` | Puntos por cada euro de compra | 1 |
| `PUNTOS_CANJE_RATIO` | Puntos necesarios para 1€ de descuento | 100 |
| `BOT_API_KEY` | Clave de autenticación Flask ↔ Bot | UUID aleatorio |
| `BOT_API_URL` | URL del bot WhatsApp | http://chatbot:3000 |
| `NOMBRE_NEGOCIO` | Nombre del negocio | Oxidian |
| `TELEFONO_NEGOCIO` | Teléfono para contacto | — |
| `VALIDAR_RADIO_ENTREGA` | Activa validación geográfica | 0 |
| `RADIO_ENTREGA_KM` | Radio máximo de entrega | 5 |
| `CENTRO_LAT/LON` | Coordenadas del negocio | 37.4698, -5.6435 |
| `HORARIO_APERTURA/CIERRE` | Para el bot | 09:00 / 22:30 |
| `LOGO_URL` | URL del logo del negocio | — |

### Flujo de integridad al eliminar entidades
| Entidad eliminada | Impacto | Protección |
|------------------|---------|------------|
| Coupon | Order.cupon_id queda con FK sin referencia | FK nullable, historial intacto |
| ZonaEntrega | Order.zona_id queda con FK sin referencia | FK nullable, historial intacto |
| Product (sin pedidos) | Solo eliminable si ~order_items.any() | reset_demo valida |
| User (admin) | Solo super_admin puede desactivar admins | toggle_usuario guard |
| AffiliateCode | AffiliateUse.codigo_id queda huérfano | FK nullable |

### Acciones que registran AuditLog
- crear/editar usuario, toggle usuario
- confirmar/rechazar pago digital
- cambiar precio de producto
- crear/editar/toggle: cupón, promoción, zona, afiliado
- aprobar/eliminar reseña
- asignar pedido
- ajuste manual de puntos
- actualizar features de admin
- enviar campaña
- reset_demo
- pedido entregado
- regenerar BOT_API_KEY
- guardar/seed configuración del sistema
