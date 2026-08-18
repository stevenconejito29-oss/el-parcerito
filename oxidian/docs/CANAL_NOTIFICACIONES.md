# Canal de notificaciones al cliente (gate anti-baneo Meta)

Fuente: `canal_service.py`. Superadmin config: sección **Notificaciones al cliente** (`/superadmin/config#notificaciones-cliente`).

## Por qué existe

Meta banea instancias de WhatsApp que envían mensajes "no solicitados" (marketing / avisos genéricos) fuera de la ventana de 24h desde el último inbound del cliente. Sólo los llamados *service messages* (códigos, OTP, respuestas a un intent claro) están permitidos siempre.

Este servicio decide, para cada notificación al cliente, si usar WhatsApp o si enrutar la notificación por Web Push (PWA) o por el chat integrado (`/chat`).

## Diagrama de decisión

```
evento
 │
 ▼
1) evento ∈ TRANSACTIONAL_ALWAYS_WA ? ── sí ─▶ canal = wa   (bypass)
 │ no
 ▼
2) notif_gate_activo = 0 ?           ── sí ─▶ canal = wa   (escape hatch)
 │ no
 ▼
3) cliente is None ?                 ── sí ─▶ canal = wa
 │ no
 ▼
4) filtrar canales permitidos para el evento (override JSON o default)
 │
 ▼
5) push activo y web activo ?        ── sí ─▶ canal = push+web
 │ no
 ▼
6) push activo ?                     ── sí ─▶ canal = push
 │ no
 ▼
7) web activo ?                      ── sí ─▶ canal = web
 │ no
 ▼
8) inbound WA <= ventana_horas ?     ── sí ─▶ canal = wa   (fallback ventana)
 │ no
 ▼
9) canal = none  →  se registra skip en notification_outbox (estado='skipped')
                    con razón, para métricas y potencial replay.
```

## Eventos siempre-WA (`TRANSACTIONAL_ALWAYS_WA`)

Hardcodeados en `canal_service.py`:

- `order_confirmation` — confirmación al hacer un pedido.
- `delivery_code` — código de entrega al llegar el rider.
- `points_otp` — OTP para gestionar puntos.
- `canje_codigo` — código de canje de recompensa.
- `web_chat_handoff` — aviso al cliente de que un humano tomó su chat web.

**Regla dura:** el gate no puede sacar estos eventos de WA. `tests/test_canal_service.py::test_transactional_nunca_cae_a_none` lo verifica.

## Configuración

| Clave | Default | Descripción |
|---|---|---|
| `notif_gate_activo` | `1` | Escape hatch. `0` = todo por WA como legacy. |
| `notif_ventana_wa_horas` | `24` | Ventana Meta de service messages (horas desde inbound). |
| `notif_canales_por_evento` | `` | Override JSON por evento. Ejemplo: `{"delivery_en_camino":["push","web"]}`. Vacío = defaults del código. |
| `delivery_notificar_camino_texto` | plantilla | Texto "voy en camino". Placeholders `{nombre}`, `{codigo}`. |
| `delivery_franjas_notificar_puerta_texto` | plantilla | Texto "en la puerta". Mismos placeholders. |

Todas gestionables desde `/superadmin/config#notificaciones-cliente` sin redeploy.

## Cómo interpretar `scripts/notif_metrics.py`

Ejecución:

```
docker exec oxidian python3 scripts/notif_metrics.py --dias 30
```

Salida (JSON) por día: totales, canales usados y top razones de canal=none/skipped. Si `none` supera el 10% durante varios días, revisar: (a) push suscripciones bajas, (b) `notif_gate_activo` está desactivando canales, (c) los clientes no responden por WA nunca (ventana no se abre).

## Cuándo desactivar el gate

Nunca en régimen normal. Sólo si Evolution/Meta cambian políticas y hay que revertir temporalmente. En ese caso: `notif_gate_activo=0` desde superadmin y crear ticket para revisar en <48h. El logger emite `WARNING` en cada decisión `canal=none` para no perder trazabilidad.

## Flujo de notificaciones al cliente (delivery)

Orden real de eventos hacia el cliente en un pedido delivery:

1. `order_confirmation` — WA (transactional).
2. `delivery_en_camino` — canal_service (push preferente).
3. `delivery_en_puerta` — canal_service (push preferente).
4. `delivery_code` — WA (transactional).

Todos idempotentes: el rider puede pulsar los botones varias veces sin re-notificar. Los primeros dos usan `Order.en_camino_at` y `Order.en_punto_encuentro`/`en_punto_encuentro_en` como marca; además chequean `NotificationOutbox` previa por `(pedido_id, evento)`.
