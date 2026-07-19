# Claves de configuración (SiteConfig)

Fuente única de verdad para las claves runtime configurables. Todas viven en
la tabla `site_config` (modelo `SiteConfig`) y se leen con
`SiteConfig.get("CLAVE", default)`.

Al arrancar la app, `config_defaults.sembrar_defaults()` inserta las claves
nuevas que no existan. Editables desde `/superadmin/config` (o directamente
en BD).

## Claves fiscales (España) — Fase 9

| Clave | Tipo | Default | Dónde se lee | Qué hace |
|---|---|---|---|---|
| `IVA_DEFAULT_COMIDA` | float | `10.00` | `models._resolver_iva_pct_producto` | IVA aplicado a productos `vertical=comida` que no tienen `iva_pct` propio. |
| `IVA_DEFAULT_RETAIL` | float | `21.00` | `models._resolver_iva_pct_producto` | IVA aplicado a productos retail/servicios sin `iva_pct` propio. |
| `NOMBRE_FISCAL` | str | `""` | Cabeceras de factura y export CSV | Razón social. Cae a `NOMBRE_NEGOCIO` si vacío. |
| `NIF_NEGOCIO` | str | `""` | Cabecera de factura fiscal | NIF/CIF del negocio. Obligatorio para facturar en España. |
| `DIRECCION_FISCAL` | str | `""` | Cabecera de factura | Domicilio fiscal completo. |

## Claves anti-hardcoding — Fase 9

| Clave | Tipo | Default | Dónde se lee | Qué hace |
|---|---|---|---|---|
| `DELIVERY_CODE_MAX_INTENTOS` | int | `3` | `Order.confirmar_entrega_con_codigo` | Máx. intentos fallidos del repartidor al validar el código de entrega antes de bloqueo. |
| `COD_PUNTOS_MAX_INTENTOS` | int | `5` | `User.verificar_cod_puntos` | Máx. intentos fallidos del cliente al canjear puntos por OTP. |
| `COD_PUNTOS_TTL_MINUTOS` | int | `10` | `User.generar_cod_puntos` | Vigencia del OTP de canje de puntos (cap 1–60). |

## Retención / poda de tablas — PR #17 disk fix

Sembradas por `config_defaults` desde PR #17. Consumidas por
`services.purgar_registros_antiguos`, invocada por el worker de outbox cada
`OUTBOX_PURGE_EVERY_SECONDS` (env, default 3600s = 1h).

| Clave | Tipo | Default | Qué hace |
|---|---|---|---|
| `NOTIFICATION_OUTBOX_RETENTION_DAYS` | int | `30` | Días que se conservan filas `sent`/`failed` en `notification_outbox`. Cap defensivo 7-365. NUNCA borra pendientes. |
| `IDEMPOTENCY_PURGE_ENABLED` | bool | `"1"` | Habilita purga de `idempotency_keys` expiradas junto con la de outbox. `0` para diagnóstico. |
| `OTP_MIN_RESEND_SECONDS` | int | `60` | Ventana mínima entre 2 solicitudes de OTP de puntos del mismo cliente. Anti-flood. |
| `ADMIN_CLIENTES_PAGE_SIZE` | int | `40` | Paginación de `/admin/clientes` (cap 10-200). |
| `ADMIN_PEDIDOS_PAGE_SIZE` | int | `30` | Paginación de `/admin/pedidos` (cap 10-100). |

## Variables env infraestructura (no SiteConfig — se leen al arranque)

| Clave | Default | Qué hace |
|---|---|---|
| `DOCKER_LOG_MAX_SIZE` | `10m` | Tamaño máximo por archivo de log Docker (driver json-file). |
| `DOCKER_LOG_MAX_FILE` | `3` | Máximo archivos rotados por servicio. |
| `OUTBOX_PURGE_EVERY_SECONDS` | `3600` | Frecuencia poda automática (mín 300s). |
| `OUTBOX_INTERVAL_SECONDS` | `2` | Intervalo del worker outbox entre lotes. |
| `OUTBOX_LIMIT` | `25` | Máx. notificaciones procesadas por lote. |
| `BOT_INBOUND_MAX_ATTEMPTS` | `5` | Reintentos del bot antes de dead-letter. |
| `BOT_INBOUND_RETENTION_SECS` | `86400` | Retención de `inbound_messages` procesados en el bot. |

## Límites de flujo del chatbot

Estas claves se sincronizan con el proceso de WhatsApp cuando cambian desde
superadmin. El bot conserva valores defensivos de arranque, pero la fuente de
verdad operativa es `SiteConfig`.

| Clave | Tipo | Default | Qué hace |
|---|---|---|---|
| `BOT_REPORT_RATE_WINDOW_SEC` | int | `3600` | Ventana por cliente para limitar el envío repetido de incidencias. |
| `BOT_REPORT_RATE_MAX` | int | `3` | Máximo de incidencias del mismo cliente dentro de la ventana. |
| `BOT_HANDOFF_SLA_WARNING_SEC` | int | `600` | Espera que marca una solicitud humana con alerta de SLA. |
| `BOT_HANDOFF_OWNER_ALERT_THRESHOLD` | int | `3` | Clientes en cola que disparan aviso de saturación al responsable; `0` lo desactiva. |
| `BOT_HANDOFF_OWNER_ALERT_COOLDOWN_SEC` | int | `600` | Intervalo mínimo entre avisos de saturación. |
| `BOT_HANDOFF_QUEUE_MAX_SEC` | int | `86400` | Antigüedad máxima de una solicitud abandonada antes de archivarla. |
| `BOT_HANDOFF_INACTIVITY_SEC` | int | `900` | Inactividad máxima de un chat asignado antes de cerrarlo; `0` lo desactiva. |

## Claves ya existentes referenciadas (contexto — no nuevas)

Estas ya vivían en `app._seed_admin` antes de la fase 9 y se documentan aquí
para consulta rápida:

| Clave | Uso resumido |
|---|---|
| `NOMBRE_NEGOCIO`, `DIRECCION_NEGOCIO`, `TELEFONO_NEGOCIO`, `EMAIL_CONTACTO` | Datos públicos del negocio (branding + contacto). |
| `PUNTOS_POR_EURO`, `PUNTOS_CANJE_RATIO` | Reglas del club de puntos. |
| `HORARIO_APERTURA`, `HORARIO_CIERRE`, `TIENDA_FORZAR_CERRADA` | Ventana operativa. |
| `CENTRO_LAT`, `CENTRO_LON`, `RADIO_ENTREGA_KM` | Geo-validación de radio. |
| `SERVICE_COMMISSION_PCT` | Comisión de servicio en modo white-label. |
| `DELIVERY_CODE_TTL_HOURS` | Vigencia (horas) del código de confirmación de entrega. |
| `FEATURE_DELIVERY`, `FEATURE_RECOGIDA`, `FEATURE_PEDIDOS_PROGRAMADOS`, `FEATURE_PUNTOS` | Toggles modulares. |
| `MODO_TIENDA` | `propia` o `bar_servicio`. |

## Endpoint de exportación fiscal

`GET /superadmin/finanzas/export?desde=YYYY-MM-DD&hasta=YYYY-MM-DD`

Devuelve CSV con una fila por (pedido × tasa IVA):
`fecha, numero_pedido, cliente_nif, base_imponible, iva_pct, iva_importe, total, metodo_pago, estado`.

Default de rango: primer día del trimestre actual → hoy.

## Propuestas pendientes (no ejecutadas)

Requieren refactor mayor y se dejan documentadas:

- **Precio de envío / zonas**: valores literales `precio_envio` en `ZonaEntrega`
  no se centralizan porque cada zona tiene su propia tarifa por diseño.
- **Fees de service commission por vertical**: `SERVICE_COMMISSION_PCT` es única.
  Para verticals distintos convendría desdoblarla en `SERVICE_COMMISSION_PCT_COMIDA`
  y `SERVICE_COMMISSION_PCT_RETAIL`. Requiere migración de callers.
- **Rate-limit / TTL de idempotencia**: gestionados por infra (Flask-Limiter,
  ventana de 30s de idempotency), fuera del alcance de SiteConfig por diseño.
- **Cap `1..168h` de `DELIVERY_CODE_TTL_HOURS`**: sigue duro en código; podría
  moverse a dos claves `MIN`/`MAX` pero no aporta valor operativo.
