# Despliegue Cosmos OS

## Tres Pilares Fusionados

El sistema tiene tres pilares técnicos, pero funcionan como **una sola aplicación visible** bajo el dominio de Oxidian. No hay panel público separado del bot ni panel público de Evolution:

- **Oxidian**: única interfaz pública. Incluye tienda, paneles, POS, staff, marketing y Super Admin.
- **Chatbot Node**: pilar interno para WhatsApp. Se administra solo desde `Super Admin -> Chatbot`.
- **Evolution API**: pilar técnico interno para sesión WhatsApp, QR y webhooks. No se expone como panel independiente.

En local el archivo `docker-compose.cosmos-local.yml` levanta también una segunda base PostgreSQL para Evolution, porque Evolution mantiene su propia sesión/datos.

## Prueba Local Igual A Cosmos

Desde `/ruta/del/proyecto`:

```bash
cp .env.cosmos.example .env.cosmos.local
docker-compose --env-file .env.cosmos.local -f docker-compose.cosmos-local.yml up -d --build
```

URL local principal:

- Aplicación única: `http://localhost:5070`
- Configuración del chatbot: `http://localhost:5070/superadmin/chatbot`
- Worker outbox: corre dentro del contenedor `oxidian`, sin puerto público propio.

Servicios internos de diagnóstico, accesibles desde Docker, no como puertos públicos:

- Oxidian interno dentro del contenedor: `http://127.0.0.1:5000`
- Chatbot interno dentro del contenedor Oxidian: `http://127.0.0.1:3000/api/status`
- Evolution interno en red Docker: `http://evolution-api:8080`

Credenciales iniciales:

- Admin: valor de `ADMIN_EMAIL`
- Super admin: valor de `SUPERADMIN_EMAIL`
- Password: valor de `SEED_PASSWORD`

## Verificación Rápida

```bash
curl -s http://localhost:5070/
curl -s http://localhost:5070/health
docker compose --env-file .env.cosmos.local -f docker-compose.cosmos-local.yml exec oxidian \
  curl -s http://127.0.0.1:3000/api/status
```

Validar contrato del bot sin crear pedidos por WhatsApp:

```bash
curl -s http://localhost:5070/api/bot/asistente -H "X-Bot-Key: TU_BOT_API_KEY"
curl -s "http://localhost:5070/api/bot/pedido/estado?telefono=34600000100" -H "X-Bot-Key: TU_BOT_API_KEY"
```

El cliente compra desde la tienda online. El chatbot solo consulta catálogo, puntos, estado del pedido y deriva a humano. La gestión visual del chatbot ocurre exclusivamente desde `Super Admin -> Chatbot`.

## Configuración En Cosmos

En Cosmos usa la misma estructura del compose. Cambia:

- `SECRET_KEY`
- `SEED_PASSWORD`
- `OXIDIAN_DB_PASSWORD`
- `EVOLUTION_DB_PASSWORD`
- `EVOLUTION_API_KEY`
- `BOT_API_KEY`
- `BOT_PANEL_KEY`
- `OXIDIAN_PUBLIC_URL`
- `TIENDA_URL`
- `OUTBOX_LIMIT` y `OUTBOX_INTERVAL_SECONDS` para ajustar reintentos de notificaciones.
- `SIMULATE_EVO_SEND=0` cuando WhatsApp real esté conectado.
- `SESSION_COOKIE_SECURE=1` cuando el dominio de Cosmos tenga HTTPS.
- `WEB_CONCURRENCY`, `WEB_THREADS`, `DB_POOL_SIZE` y `DB_MAX_OVERFLOW` para ajustar capacidad.

Valores internos que deben mantenerse en Docker:

- Oxidian habla con el bot interno por `BOT_API_URL=http://127.0.0.1:3000`.
- Chatbot habla con Oxidian por `OXIDIAN_URL=http://127.0.0.1:5000`.
- Chatbot habla con Evolution por `EVOLUTION_API_URL=http://evolution-api:8080`.
- La imagen Oxidian aplica migraciones idempotentes con `scripts/apply_schema_migrations.py` antes de arrancar web.
- El orden dentro del contenedor es: bootstrap, migraciones, chatbot Node, worker outbox y Gunicorn.
- Redis de Oxidian (`oxidian-redis`) guarda estado efímero de rate limiting para que varios workers compartan límites.

## Webhook Evolution

El compose incluye el servicio `evolution-setup`, que crea la instancia y configura el webhook automáticamente al arrancar.

Si alguna vez necesitas repetirlo manualmente:

```bash
curl -X POST http://localhost:8080/webhook/set/oxidian \
  -H "Content-Type: application/json" \
  -H "apikey: localevoapikey" \
  -d '{"webhook":{"enabled":true,"url":"http://oxidian:5000/webhook/evolution","headers":{"X-Webhook-Secret":"TU_WEBHOOK_SECRET"},"byEvents":false,"base64":false,"events":["MESSAGES_UPSERT","CONNECTION_UPDATE"]}}'
```

Para envío real, entra a `Super Admin -> Chatbot`, conecta la instancia y escanea el QR mostrado por Oxidian. Evolution sigue funcionando por detrás, sin panel público.
