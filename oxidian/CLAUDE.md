# El Parcerito — guía técnica para futuras sesiones

> **Snapshot histórico del 2026-06-18.** Se conserva para rastrear decisiones,
> pero no es la guía vigente. Empieza por [`../AGENTS.md`](../AGENTS.md) y
> [`../docs/README.md`](../docs/README.md).

Estado del proyecto al cierre del 2026-06-18. Marketplace de delivery con un
hub central (El Parcerito) y bares externos que despachan productos bajo la
misma marca.

## Arquitectura

```
Cliente (WhatsApp)
        │
        ▼
+34633096706 ─ Evolution API → chat/bot.js (Node)
                                  ↕
                              api_bot.py (Flask)
                                  ↕
                       PostgreSQL oxidian + Redis
                                  ↕
                  Roles: super_admin / admin / preparacion / repartidor / proveedor
```

- **`oxidian/`** Flask + SQLAlchemy + PostgreSQL. Vistas, API, lógica de negocio.
- **`chat/bot.js`** Node + whatsapp-web.js (Evolution). NUNCA contiene lógica de stock o precios — siempre llama a `/api/bot/*`.
- **Docker compose**: 7 servicios (oxidian, postgres oxidian, redis oxidian, evolution-api, postgres evolution, redis evolution, gateway nginx). Único puerto público `5070`.

## Conceptos clave del modelo

### Roles (5 + cliente)

| Rol | Acceso |
|---|---|
| `super_admin` | Todo. Único con MFA forzado (configurable con `OXIDIAN_MFA_ENFORCED`). |
| `admin` | Operación diaria sin tocar config global. |
| `preparacion` | Cocina + almacén unificados (rol propio que prepara stock interno). |
| `repartidor` | Entregas y comisiones. |
| `proveedor` | Operador de un bar (1 bar por user, 1+ users por bar posible). |
| `cliente` | Marketplace público. |

Los roles legacy `cocina` y `staff` fueron fusionados en `preparacion`.
La columna `users.rol` los acepta por retro-compatibilidad pero nada nuevo los escribe.

### Bares (Proveedor) — entidad propia, no rol de usuario

`models.Proveedor` es un restaurante con:
- `nombre`, `direccion`, `horario`.
- `telefono` (WhatsApp directo del bar; usado por el bot para derivar clientes).
- `modelo_acuerdo`: `stock_proveedor` (bar pone stock, le pagamos coste) o `stock_propio_bar` (nosotros stock, fee % por preparar).
- `comision_pct` (solo aplica al modelo B).

### Stock — independiente por bar

`Stock` (tabla nuestra, FIFO por caducidad) es exclusivamente para SKUs propios.
`ProveedorProducto(proveedor_id, producto_id, stock, precio_costo)` es el inventario del bar.
**Un mismo SKU puede estar en `Stock` propio Y en `ProveedorProducto` de varios bares simultáneamente.** Cada inventario es independiente.

### Combos — origen único

Un combo solo puede armarse con productos del MISMO origen:
- Combo propio → todos los componentes deben estar en `Stock` propio.
- Combo del Bar X → todos los componentes deben estar en `ProveedorProducto[Bar X]`.

El constructor de combos (`/admin/combos/nuevo`) filtra el grid de productos según el "Origen del combo" elegido. La validación al guardar bloquea componentes inválidos.

### Despacho del pedido

| Contenido | Quién prepara | Quién despacha |
|---|---|---|
| 100% propio | preparacion (cocina interna) | repartidor propio |
| 100% Bar X | Bar X (vía `OrderProviderStatus`) | repartidor propio |
| Mixto (propio + Bar X) | preparacion + Bar X (ambas confirmaciones necesarias) | repartidor propio |

Cuando el pedido es 100% del bar, `distribuir_pedido` NO asigna preparador interno. Cuando el bar marca `OrderProviderStatus.preparado=True` y no hay más estados pendientes, el pedido avanza automáticamente a `listo` (servicio implementado en `routes/proveedor.py marcar_preparado` Y en `routes/api_bot.py bar_marcar_preparado`).

### Snapshot congelado

Cada `OrderItem.metadata_json` guarda un snapshot del producto al momento de crear el pedido (precios, proveedor despachador, modelo de acuerdo, comisión). Eso garantiza que cambios futuros en el producto NO afecten pedidos pasados — la liquidación y la cancelación leen del snapshot.

## Chatbot

### Modelo de derivación

El único número que ve el cliente es `+34633096706` (TELEFONO_NEGOCIO, configurable desde `/superadmin/config`).
El bot redirige al cliente al WhatsApp directo del bar SOLO cuando es necesario y el bar está realmente operativo (activo + WhatsApp configurado + al menos 1 SKU activo).

Centralizado en `_pedido_bot_payload` (`routes/api_bot.py`):

```
si TODOS los items del pedido son del MISMO bar activo + WhatsApp + SKUs:
    → bar_contacto: {tipo: "bar", whatsapp_url: "wa.me/<bar>"}
en cualquier otro caso (mixto, propio, bar inerte):
    → bar_contacto: {tipo: "propio", whatsapp_url: "wa.me/<TELEFONO_NEGOCIO>"}
```

### Identificación automática

- **Cliente con pedido activo** → bot lo saluda con resumen del pedido (`resumenPedidoActivo` en bot.js).
- **Remitente es operador de bar** → bot le presenta `barMenu()` en lugar del cliente (`identificarBarOperador` consulta `/api/bot/bar/identify`).

### Comandos del cliente

| Comando | Acción |
|---|---|
| `MENU` o número 1-7 | Menú principal |
| `ESTADO` o `2` | Listar pedidos activos del cliente |
| `CANCELAR` o `CANCELAR <num>` | Cancela si está en `pendiente`; si no, deriva al bar |
| `REPORTAR <texto>` o `REPORTAR <num> <texto>` | Guarda incidencia en panel del bar + ofrece su WhatsApp directo |
| `AGENTE` o `7` | Deriva al bar correspondiente si último pedido es de un bar; cola general si no |
| Texto natural | `detectClientIntent` mapea keywords (pedido, cancelar, puntos, cobertura, horario, agente, etc.) |

### Comandos del bar (vía su WhatsApp)

Cuando el operador del bar escribe a `+34633096706`, se le presenta:

| Opción | Acción | Endpoint |
|---|---|---|
| 1️⃣ Mis pedidos pendientes | Lista pedidos `pendiente/armando` del bar con items | `GET /api/bot/bar/pedidos` |
| 2️⃣ Marcar preparado | Por número de pedido. Si solo-bar, avanza automático a `listo` y asigna repartidor | `POST /api/bot/bar/pedido/<id>/preparado` |
| 3️⃣ Incidencias | Reportes de clientes; enlace al panel web para gestión completa | `GET /api/bot/bar/incidencias` |
| 4️⃣ Mi inventario | Link a `/proveedor/inventario` | — |
| 5️⃣ Contactar admin general | Handoff estándar a admins propios | — |

Autorización: cada endpoint compara el `telefono` recibido contra `Proveedor.telefono` de bares activos. Si no coincide → 403.

## Operaciones críticas

### Configuración

| Concepto | Dónde se cambia |
|---|---|
| TELEFONO_NEGOCIO | `/superadmin/config` (SiteConfig) |
| NOMBRE_NEGOCIO | `/superadmin/config` |
| Bar (alta, WhatsApp, SKUs, comisión) | `/admin/proveedores` |
| OWNER_NUMBER / SUPERADMINS (tu admin del bot) | `.env.cosmos.local` (requiere `docker compose up -d`) |
| Zonas geográficas | `/superadmin/zonas` |
| Horarios, puntos | `/superadmin/config` |

### Backups

`scripts/backup.sh` corre cron diario a las 03:30 — `~/oxidian-backups/<ts>/` con:
- `oxidian.dump`, `evolution.dump` (pg_dump -Fc)
- `images.tar.gz`, `chatbot_data.tar.gz` (volúmenes)
- `SHA256SUMS` (integridad)

Restauración: `scripts/restore.sh <dir> [--dry-run]`.

### Migraciones

`scripts/apply_schema_migrations.py` corre al arrancar el contenedor. Idempotente, con advisory lock (`pg_advisory_lock(-5273401983142671019)`) para evitar race conditions entre instancias.

Cada migración tiene un `id` único; se guardan en `schema_migrations`. NO usa Alembic (decisión consciente para mantener simplicidad).

## Idempotency

Tabla `idempotency_keys(scope, key, request_hash, response_body)` con UNIQUE.
Endpoints protegidos: checkout web, POS `/pos/cobrar`, bot `/api/bot/pedido/crear`. Aceptan header `Idempotency-Key` y de fallback usan un hash automático del body con ventana de 30s (defensa contra double-click).

## Auditoría visual

`scripts/visual_audit.mjs` (Playwright) — recorre todas las vistas, genera capturas en `~/Vídeos/juni/pantallazos_actuales/<timestamp>/`. Run típico: 69 capturas, ≤1 fallo, 0 desbordamientos.

## Estado actual

- ✅ 4/4 bloqueantes de producción cerrados (idempotency, backups, MFA opt-in super_admin, advisory lock).
- ✅ 3/3 críticos amarillos cerrados (zonas geo, headers seguridad nginx, validación combo↔proveedor).
- ✅ Modelo de proveedores como entidad propia + stock independiente por bar.
- ✅ Bot con identificación automática (cliente y bar), menú propio para cada bar, derivación inteligente.
- ✅ Incidencias del cliente visibles en panel del bar.
- ✅ Roles consolidados a 5 + cliente.
- ✅ Auditoría visual sin desbordamientos horizontales.
- ✅ Documentación operacional en `OPERACIONES.md`.

### Lo que SIGUE pendiente (decidido posponer en sesiones anteriores)

- Tests automatizados — solo smoke scripts en `scripts/`.
- CI/CD — no configurado.
- Drop de columnas legacy (`Product.proveedor_id`, `Order.proveedor_preparado*`) — deprecadas en código pero no eliminadas en BD.
- Bot Node aún no envía `Idempotency-Key` explícito en `/pedido/crear` (cubierto por el auto-fallback).
- Frontend POS/checkout no envía `Idempotency-Key` explícito por intento (idem auto-fallback).
- HSTS comentado en nginx — descomentar al activar HTTPS final.
- SMTP transaccional — sin emails, todo por WhatsApp.

## Puntos donde NO tocar sin entender

- `models.snapshot_producto_para_pedido` y `metadata_componente_combo` — la trazabilidad de pedidos depende de estos snapshots.
- `services.cancelar_pedido_operativo` → `Order.cancelar()` → `restaurar_stock_pedido` — el snapshot decide si restaura al bar o al stock propio. Cambiarlo rompe pedidos en curso si el admin modifica el producto entre que se creó y se canceló.
- `api_bot._pedido_bot_payload` → es la fuente única de verdad de `bar_contacto`. Ningún flujo del bot calcula contacto por su cuenta.
- `apply_schema_migrations.py` orden de migraciones — NO reorganizar el array.

## Comandos diarios

```bash
# Levantar
cd /home/panzeta/Documentos
docker compose --file oxidian/docker-compose.cosmos-local.yml \
               --env-file oxidian/.env.cosmos.local up -d

# Rebuild tras cambios de código
docker compose --file oxidian/docker-compose.cosmos-local.yml \
               --env-file oxidian/.env.cosmos.local build oxidian

# Logs
docker logs -f oxidian-oxidian-1

# Backup manual
bash scripts/backup.sh
```

URL pública: `http://localhost:5070` (o `http://192.168.1.41:5070`).
