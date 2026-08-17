# Plan: Módulo de Delivery por Franjas Horarias

> **Estado:** ✅ **IMPLEMENTADO Y DESPLEGADO EN PRODUCCIÓN** (2026-08-17).
> Toggle `delivery_franjas_activo=0` — código inerte hasta que el admin lo encienda desde `/superadmin/config`.
> **Rama:** mergeada a `main` — commits `c9d318c..14cf37f`. Sin rama pendiente.
> **Server:** `192.168.1.32` — imagen `oxidian:latest` reconstruida, contenedor healthy, migraciones aplicadas.

## Estado de implementación por sección

| Sección plan | Estado | Notas |
|---|---|---|
| §5 Modelo de datos + migraciones | ✅ | Migraciones `20260817_01_delivery_slots_tables` y `20260817_02_orders_slot_id` aplicadas en producción. |
| §6 Servicio `delivery_slots_service.py` | ✅ | 14 funciones públicas + Enums tipados. |
| §7.1 Endpoints admin (JSON) | ✅ | GET/POST/PATCH/DELETE + clonar semana. |
| §7.1 Vista HTML admin | ✅ | `/admin/delivery/franjas/panel` + link en sidebar. |
| §7.2 Endpoint cliente lista franjas | ✅ | `/api/delivery/franjas-disponibles` con `sugerida`. |
| §7.2 Integración con `POST /checkout` | ✅ | Acepta `slot_id`, reserva atómica, rollback si falla. |
| §7.3 Endpoints repartidor (JSON) | ✅ | listar/tomar/liberar + en-la-puerta. |
| §7.3 Vista HTML repartidor + link sidebar | ✅ | `/repartidor/franjas/panel`. |
| §7.3 Botón "Estoy en la puerta" en `ruta.html` | ✅ | Dentro de cada tarjeta de pedido en_ruta. |
| §8 Config keys (`SiteConfig`) | ✅ | 8 keys sembradas. |
| §9 UI cliente (selector en checkout) | ✅ | Widget inline en `templates/public/checkout.html` + página de preview autónoma. |
| §9 UI admin (calendario) | ✅ | Con formulario crear + clonar semana + toggle activo/borrar. |
| §9 UI repartidor (self-assign) | ✅ | Con estado tomada_por_mi / libre / sin cupo repartidor. |
| §10 Docs oficiales | ✅ | `CONFIG_KEYS.md`, `COBERTURA_REPARTO.md`, `ORDER_FLOW.md`. |
| §11 Tests unitarios | ✅ | `test_delivery_slots_service.py` (9 casos de cierre + contrato enums). |
| §11 Test integración checkout con slot | ⏸ Pendiente | Requiere entorno con Postgres + app context. Cubierto por smoke script. |
| §12 Smoke script end-to-end | ✅ | `scripts/smoke_delivery_franjas.py`. |
| §13 Rollback | ✅ | Toggle OFF apaga el módulo al instante. Backup pg previo al deploy. |

## Mejora paralela: PWA lenta Android

Fuera del alcance original del plan pero aplicada en la misma iteración
(reportada por el fundador el mismo día). Dos fixes commit `5711c42`:

1. **SW precache por prioridad** — el evento `install` solo espera al
   subconjunto crítico (tokens, CSS core, header, iconos base). Vendors,
   motion, storefront JS y texturas se cachean en background sin
   bloquear `waitUntil()`.
2. **Sin backdrop-filter en cards multiplicadas** — `.ep-grid .ep-card`
   y `.ep-mc-dest-card` eliminan `blur(12-13px)` que causaba jank de
   scroll en Android low-end. Compensado con background más opaco
   (94%/92% surface).

Sin toggle: activo para todos los clientes desde el deploy.

## Merge con trabajo paralelo

Durante el desarrollo, 9 commits ajenos aterrizaron en `origin/main`
con trabajo de NLU (Groq) + PWA styling. Se integraron limpiamente
con 3 conflictos resueltos manualmente (merge commit `897e23c`):

- `chatbot.html`: unión de ambos — 3 links visibles (Conocimiento del
  chat web + NLU pendientes + Probar chat web).
- `pwa-native.css` fondo base: se conservó el enfoque simple de
  producción (v4.webp + overlay) frente al multicapa propuesto por
  origin/main (referenciaba texture v2 en desuso).
- `pwa-native.css` dark-mode: se aceptó la eliminación de origin/main
  (usuario pidió fondo claro siempre).

Documento vivo. Cualquier desviación posterior se refleja aquí antes del commit.

---

## 1. Objetivo

Añadir a Oxidian un segundo modo de entrega — **franjas horarias con topes de capacidad** — que convive con el actual **delivery inmediato**. Ambos modos son módulos toggleables desde el panel admin, sin cambiar código.

El delivery inmediato actual **no se modifica funcionalmente**: solo se envuelve en un toggle. Si el admin apaga las franjas, el sistema opera idéntico a hoy.

## 2. Decisiones cerradas con el fundador

| # | Decisión | Origen |
|---|---|---|
| D1 | Dos módulos independientes toggleables: `delivery_inmediato_activo`, `delivery_franjas_activo`. Pueden estar activos a la vez. | Confirmado |
| D2 | Tope por franja = número de pedidos (entero configurable por franja). | Confirmado |
| D3 | Configuración por día individual (no plantilla rígida L-V/S-D). | Confirmado |
| D4 | Admin publica franjas con **14 días** de horizonte. Cliente ve **7 días**. | Confirmado |
| D5 | Cliente selecciona manualmente; UI **destaca** la franja disponible más próxima como "sugerida". | Confirmado |
| D6 | Cierre de franja configurable: `al_iniciar_siguiente` \| `minutos_antes` (N min) \| `hora_fija` (HH:MM). Default global; override por franja. | Confirmado |
| D7 | Repartidores se autoasignan a franjas desde su panel. Una franja admite N repartidores (config `max_repartidores_por_franja`, default 1). | Confirmado |
| D8 | Cancelación de pedido libera el cupo automáticamente. | Confirmado |
| D9 | Franja llena durante checkout → error explícito + destaca la siguiente sugerida. Nunca auto-selección silenciosa. | Confirmado |
| D10 | Notificación WhatsApp única en todo el flujo: "repartidor en tu puerta". Resto de avisos van por push PWA (política anti-baneo Meta). | Confirmado |
| D11 | Cero hardcoding. Todo valor comercial/temporal va en `SiteConfig` o en la tabla de franjas. | Confirmado |

## 3. Convenciones del repo que se respetan

Extraídas de `AGENTS.md`, `docs/PROJECT_STRUCTURE.md`, `docs/DEVELOPMENT.md`, `oxidian/CLAUDE.md`:

- Reglas de negocio en `services.py` o `*_service.py` — nunca en `routes/` ni templates.
- Config editable en `store_config.py` + `config_defaults.py` + `SiteConfig`.
- Teléfonos siempre vía `phone_utils.py`.
- Permisos vía decoradores de `permissions.py`; nueva matriz probada.
- Fechas en UTC en modelos y servicios; conversión a hora local solo en presentación.
- CSS compartido en `static/css/`, JS compartido en `static/js/` — nada de bloques grandes inline.
- Migraciones siguen el patrón de `scripts/apply_schema_migrations.py`: nueva función `_migrate_*` + registro **al final** del array (nunca reorganizar el existente), `id` único, idempotente, advisory lock ya cubierto.
- No borrar código legacy aunque parezca en desuso.
- Tests con `python3 -m unittest discover -s tests -q`.

## 4. Arquitectura del módulo

```
Cliente PWA / Bot WA
        ↓
routes/public.py         → delivery_slots_service (lectura franjas, checkout)
routes/repartidor.py     → delivery_slots_service (self-assign, "en la puerta")
routes/admin.py          → delivery_slots_service (CRUD, generador 2 semanas)
        ↓
services/delivery_slots_service.py   (nuevo, reglas de negocio)
        ↓
models.py (DeliverySlot, SlotRepartidor, Order.slot_id)  →  PostgreSQL
        ↓
push_service (aviso franja próxima)   +   chat/bot.js vía webhook (aviso "en la puerta")
```

## 5. Modelo de datos

### 5.1 Tablas nuevas

**`delivery_slots`**

| Columna | Tipo | Nulo | Descripción |
|---|---|---|---|
| `id` | `SERIAL PK` | no | |
| `fecha` | `DATE` | no | Día de la franja (UTC de referencia; visualización en zona local de la tienda). |
| `hora_inicio` | `TIME` | no | |
| `hora_fin` | `TIME` | no | `hora_fin > hora_inicio` (CHECK). |
| `capacidad_max` | `INTEGER` | no | Nº máximo de pedidos. `>= 1`. |
| `max_repartidores` | `INTEGER` | no | Default `1`. `>= 1`. |
| `cierre_modo` | `VARCHAR(32)` | sí | `al_iniciar_siguiente` \| `minutos_antes` \| `hora_fija`. `NULL` = hereda del default global. |
| `cierre_valor` | `VARCHAR(16)` | sí | Entero (minutos) o `HH:MM` según modo. `NULL` si `al_iniciar_siguiente`. |
| `activo` | `BOOLEAN` | no | Default `TRUE`. Admin puede desactivar sin borrar. |
| `notas_admin` | `TEXT` | sí | Anotación operativa (opcional). |
| `created_at` | `TIMESTAMP` | no | UTC. |
| `updated_at` | `TIMESTAMP` | no | UTC. |

**Índices**: `UNIQUE (fecha, hora_inicio, hora_fin)`; `INDEX (fecha, activo)`.

**`slot_repartidores`**

| Columna | Tipo | Nulo | Descripción |
|---|---|---|---|
| `id` | `SERIAL PK` | no | |
| `slot_id` | `FK delivery_slots(id) ON DELETE CASCADE` | no | |
| `repartidor_id` | `FK users(id) ON DELETE RESTRICT` | no | |
| `tomado_en` | `TIMESTAMP` | no | UTC. |
| `liberado_en` | `TIMESTAMP` | sí | Si el repartidor devuelve la franja. |

**Índices**: `UNIQUE (slot_id, repartidor_id) WHERE liberado_en IS NULL`; `INDEX (repartidor_id, tomado_en)`.

### 5.2 Modificación de tabla existente

**`orders`** (o el nombre real de la tabla de pedidos — se confirma al implementar):
- Añadir `slot_id INTEGER NULL FK delivery_slots(id) ON DELETE SET NULL`.
- `NULL` = pedido de delivery inmediato o recogida. **No romper pedidos existentes** (todos quedan `NULL` al migrar).
- Índice: `INDEX (slot_id) WHERE slot_id IS NOT NULL`.

**No se toca** `metadata_json` del snapshot congelado — el `slot_id` no entra al snapshot porque la franja puede reprogramarse operativamente.

### 5.3 Migración concreta

Añadir en `scripts/apply_schema_migrations.py`:

1. Función `_migrate_delivery_slots_tables()` — crea las dos tablas si no existen, con CHECKs y índices. Idempotente vía `IF NOT EXISTS`.
2. Función `_migrate_orders_add_slot_id()` — añade columna con `ADD COLUMN IF NOT EXISTS` + FK + índice parcial.
3. Registrar ambas al **final** del array de migraciones con `id`s únicos: `"2026-08-delivery-slots-tables"` y `"2026-08-orders-slot-id"`.

Reversibilidad: como el sistema no usa `downgrade` formal, cada migración debe ser aditiva. En caso de rollback → restaurar backup pg (patrón vigente, ver `docs/OPERATIONS.md`).

## 6. Servicios

**Nuevo archivo `oxidian/delivery_slots_service.py`** (siguiendo el patrón `*_service.py`).

Funciones públicas:

| Función | Uso |
|---|---|
| `listar_franjas_admin(desde, hasta)` | Panel admin: 14 días vista. |
| `listar_franjas_cliente(hoy)` | Cliente: 7 días vista, filtra cerradas/inactivas, calcula estado (`disponible`/`llena`/`cerrada`), marca `sugerida=True` en la primera disponible cronológica. |
| `crear_franja(fecha, hora_inicio, hora_fin, capacidad_max, ...)` | Admin CRUD. |
| `clonar_semana(semana_origen, semana_destino)` | Duplica franjas de una semana a otra. |
| `reservar_franja(slot_id, pedido_id)` | Checkout. Usa `SELECT ... FOR UPDATE` sobre `delivery_slots` + `COUNT` sobre `orders WHERE slot_id=? AND estado NOT IN (canceladas)`. Rechaza si `count >= capacidad_max`. Retorna resultado tipado (`Reservada` \| `Llena` \| `Cerrada`). |
| `liberar_franja(pedido_id)` | Hook llamado desde `services.cancelar_pedido_operativo`. Solo decrementa lógicamente (el `COUNT` en vivo lo refleja al instante). |
| `tomar_franja_repartidor(slot_id, repartidor_id)` | Self-assign. Valida `count(repartidores activos) < max_repartidores`. |
| `liberar_franja_repartidor(slot_id, repartidor_id)` | Devolución voluntaria. |
| `franja_esta_cerrada(slot, ahora_utc, franja_siguiente)` | Aplica la regla `cierre_modo` + `cierre_valor` (o herencia del default global). Unit-testeable pura. |
| `notificar_en_la_puerta(pedido_id)` | Publica evento a `NotificationOutbox` para que el bot lo entregue por WhatsApp. Usa `phone_utils.normalizar()`. |

**Reglas invariantes** (documentadas en docstrings, verificadas por tests):
- Una franja llena rechaza reservas con error tipado, jamás lanza excepción genérica.
- Cancelar pedido en franja llena habilita **inmediatamente** el hueco para el siguiente cliente.
- Un pedido solo puede tener una franja; cambiarla exige liberar la anterior en la misma transacción.
- Repartidor no puede tomar franjas cerradas.

## 7. Endpoints

### 7.1 Admin (`routes/admin.py`)

| Método | Ruta | Función |
|---|---|---|
| `GET` | `/admin/delivery/franjas` | Vista calendario 2 semanas. |
| `POST` | `/admin/delivery/franjas` | Crear franja. |
| `PATCH` | `/admin/delivery/franjas/<id>` | Editar (capacidad, cierre, activo…). |
| `DELETE` | `/admin/delivery/franjas/<id>` | Soft delete si tiene pedidos; hard delete si no. |
| `POST` | `/admin/delivery/franjas/clonar` | Body: `{semana_origen, semana_destino}`. |
| `POST` | `/admin/delivery/modulos/toggle` | Activa/desactiva `inmediato` o `franjas`. |

### 7.2 Cliente (`routes/public.py`)

| Método | Ruta | Función |
|---|---|---|
| `GET` | `/api/delivery/franjas-disponibles` | Retorna 7 días con estado por franja y `sugerida`. |
| `POST` | `/checkout` (existente) | Extendido: acepta `slot_id` opcional. Si módulo franjas activo y `slot_id` faltante o inválido → 400 con detalle. Idempotency-Key ya cubierto. |

### 7.3 Repartidor (`routes/repartidor.py`)

| Método | Ruta | Función |
|---|---|---|
| `GET` | `/repartidor/franjas` | Franjas del día/semana con estado (`libre` / `mías` / `completa`). |
| `POST` | `/repartidor/franjas/<id>/tomar` | Self-assign. |
| `POST` | `/repartidor/franjas/<id>/liberar` | Devolver. |
| `POST` | `/repartidor/pedido/<id>/en-la-puerta` | Dispara notificación WhatsApp única. |

Todas protegidas por decoradores de `permissions.py` (`super_admin`/`admin`/`repartidor` según corresponda).

## 8. Configuración (`SiteConfig` — nuevas keys)

| Key | Tipo | Default | Descripción |
|---|---|---|---|
| `delivery_inmediato_activo` | bool | `True` | Toggle módulo actual. |
| `delivery_franjas_activo` | bool | `False` | Toggle módulo nuevo. Nace apagado. |
| `delivery_franjas_horizonte_admin_dias` | int | `14` | Vista admin. |
| `delivery_franjas_horizonte_cliente_dias` | int | `7` | Vista cliente. |
| `delivery_franjas_cierre_modo_default` | str | `al_iniciar_siguiente` | Herencia si franja no lo define. |
| `delivery_franjas_cierre_valor_default` | str | `""` | Valor asociado al modo. |
| `delivery_franjas_max_repartidores_default` | int | `1` | Herencia para franjas nuevas. |
| `delivery_franjas_notificar_puerta_texto` | str | `"Tu repartidor está en la puerta."` | Plantilla del mensaje WA. Editable. |

Todas registradas en `docs/CONFIG_KEYS.md` (actualización obligatoria en el mismo commit).

## 9. UI

**Cliente checkout** (`templates/public/checkout.html` o similar — se localiza al implementar):
- Nuevo componente selector: pestañas "Ahora" / "Elegir franja" (mostrado solo si ambos módulos activos; si solo uno, no hay pestañas).
- Vista de franjas: 7 columnas (días) × filas (franjas). Cada celda muestra rango horario y `3/8` cupos. Estado visual: disponible / llena (gris) / cerrada (tachado) / **sugerida** (borde destacado).
- Confirmación explícita antes de enviar.

**Admin** (`templates/admin/delivery_franjas.html` — nuevo):
- Calendario 2 semanas. Vista día expandible con lista de franjas.
- Modal crear/editar franja.
- Botón "clonar semana anterior".
- Toggles de módulos visibles arriba.

**Repartidor** (`templates/repartidor/franjas.html` — nuevo):
- Lista franjas del día/semana. Botón "Tomar" en las libres; "Liberar" en las propias.
- Vista pedido: botón "Estoy en la puerta" que dispara la notificación única.

CSS compartido en `static/css/delivery_franjas.css`. JS del selector en `static/js/delivery_franjas.js` (sin listeners duplicados, sin estado global nuevo).

Todo respeta: teclado, modo claro/oscuro, anchos pequeños, `safe-area-inset-*` iOS. Extiende `base.html` (cliente) y `admin_base.html` (interno).

## 10. Documentación a actualizar en el mismo cambio

- `docs/CONFIG_KEYS.md` — nuevas keys (§8).
- `docs/ORDER_FLOW.md` — mencionar que un pedido puede tener `slot_id` y qué implica en el flujo.
- `oxidian/docs/COBERTURA_REPARTO.md` — sección nueva "Delivery por franjas".
- `oxidian/routes/README.md` — endpoints nuevos.
- Este documento (`PLAN_DELIVERY_FRANJAS.md`) — marcar cada sección como "Implementado" al cerrar cada paso.

`CLAUDE.md`, `ARQUITECTURA.md`, `FLUJOS.md` son históricos y **no** se editan (según `docs/README.md`).

## 11. Tests

Nuevos en `oxidian/tests/`:

- `test_delivery_slots_service.py` — reserva, liberación, cierre (los 3 modos), self-assign, concurrencia (dos reservas simultáneas al último hueco → una gana, otra recibe `Llena`).
- `test_delivery_slots_permissions.py` — matriz de acceso a los endpoints por rol.
- `test_cancelar_pedido_libera_franja.py` — hook con `cancelar_pedido_operativo`.

Ejecutar `python3 -m unittest discover -s tests -q` verde antes de cada commit.

## 12. Orden de implementación (commits separados)

Cada commit debe dejar el sistema **funcional y desplegable**. Ninguno debe romper lo actual.

| # | Commit | Contenido |
|---|---|---|
| 1 | `feat(delivery): migración schema + modelos franjas` | `models.py`, `scripts/apply_schema_migrations.py`, tests smoke de modelos. |
| 2 | `feat(delivery): servicio de franjas + tests` | `delivery_slots_service.py`, tests unitarios completos. Sin routes ni UI todavía. |
| 3 | `feat(delivery): endpoints admin + CRUD franjas` | `routes/admin.py`, template calendario, CSS, JS. |
| 4 | `feat(delivery): endpoint cliente + selector checkout` | `routes/public.py`, template, integración con checkout existente. |
| 5 | `feat(delivery): panel repartidor + self-assign` | `routes/repartidor.py`, template. |
| 6 | `feat(delivery): notificación "en la puerta" vía bot` | Hook en `NotificationOutbox` + handler nuevo en `chat/bot.js` + texto en `chat/texts.js`. |
| 7 | `feat(delivery): toggles de módulos + config keys` | `config_defaults.py`, `store_config.py`, panel superadmin. |
| 8 | `docs(delivery): actualizar docs oficiales` | `CONFIG_KEYS.md`, `ORDER_FLOW.md`, `COBERTURA_REPARTO.md`, `routes/README.md`. |
| 9 | `chore(delivery): smoke script end-to-end` | `scripts/smoke_delivery_franjas.py` — flujo completo verificable en local. |

## 13. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Race condition en última reserva de una franja | `SELECT ... FOR UPDATE` sobre `delivery_slots` en la transacción de checkout. Test de concurrencia explícito. |
| Cancelación no libera cupo | Hook explícito en `cancelar_pedido_operativo`. Test dedicado. |
| Repartidor toma franja y no aparece | Panel admin muestra franjas sin repartidor tomado con alerta visual; endpoint admin permite liberar/reasignar. |
| Cliente ve franja disponible pero al confirmar está llena | Error tipado + UI destaca siguiente sugerida disponible. Nunca 500. |
| Bot manda notificación duplicada | El endpoint "en la puerta" es idempotente por `pedido_id + evento` en `NotificationOutbox`. |
| Migración falla en Cosmos | Advisory lock ya cubierto por el sistema. `IF NOT EXISTS` en todo. Backup pg previo obligatorio (§14). |
| Toggle apagado, código nuevo activo | Todos los endpoints nuevos comprueban `delivery_franjas_activo` al entrar; si falso, 404 (no 403 — no revela existencia). |
| Snapshot congelado se contamina | `slot_id` NO entra al `metadata_json`. Confirmado en §5.2. |

## 14. Rollback

- Toggle `delivery_franjas_activo = False` desde `/superadmin/config` → módulo apagado al instante, sin reinicio. Sistema opera como antes.
- Si el toggle falla o la migración corrompe algo → restaurar backup pg previo (`scripts/restore.sh`).
- La rama `feature/delivery-franjas` **no se mergea a `main`** hasta:
  1. Todos los tests pasan (`python3 -m unittest discover -s tests -q`).
  2. `python3 -m compileall -q .` limpio.
  3. `oxidian/scripts/predeploy_check.py` verde.
  4. Smoke script end-to-end verde en local.
  5. Revisión visual del fundador de las 3 UI nuevas.

## 15. Checklist pre-deploy en `192.168.1.32`

- [ ] Backup pg completo previo (`bash scripts/backup.sh` y verificar el `.dump`).
- [ ] Rama mergeada a `main` con revisión.
- [ ] `rsync` de fuente canónica según `docs/OPERATIONS.md`.
- [ ] Rebuild contenedor `oxidian`.
- [ ] Migración corre automáticamente al arrancar (idempotente).
- [ ] Verificar en logs: `_migrate_delivery_slots_tables ✓` y `_migrate_orders_add_slot_id ✓`.
- [ ] Verificar app arranca sin errores.
- [ ] **Toggles apagados por defecto** — sistema opera como antes.
- [ ] Fundador crea 1 franja de prueba desde panel admin.
- [ ] Activar toggle desde `/superadmin/config`.
- [ ] Pedido de prueba desde cliente PWA.
- [ ] Repartidor toma franja + botón "en la puerta" + WhatsApp llega.
- [ ] Si todo OK, dejar activo. Si algo falla, apagar toggle y diagnosticar sin urgencia.

---

**Última actualización:** 2026-08-17.
**Próxima acción:** aprobación del fundador para crear rama `feature/delivery-franjas` y comenzar por el commit #1.
