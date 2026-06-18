# QA final y lanzamiento

Fecha de referencia: 2026-06-13

Este checklist acompana la fase 10. La suite automatizada cubre los flujos de negocio principales; las validaciones visuales y de infraestructura deben ejecutarse en el entorno real antes de abrir pedidos reales.

## Verificacion automatizada obligatoria

- `docker compose --env-file .env.cosmos.local -f docker-compose.cosmos-local.yml config`
- `docker compose --env-file .env.cosmos.local -f docker-compose.cosmos-local.yml exec -T oxidian python -m compileall -q /app`
- `docker compose --env-file .env.cosmos.local -f docker-compose.cosmos-local.yml exec -T oxidian node --check /app/chat/bot.js`
- `docker compose --env-file .env.cosmos.local -f docker-compose.cosmos-local.yml exec -T oxidian python scripts/predeploy_check.py`
- `curl -fsS http://127.0.0.1:5070/health/live`
- `curl -fsS http://127.0.0.1:5070/health/ready`
- `curl -I https://DOMINIO/` y comprobar CSP, HSTS, `nosniff`, COOP, Referrer-Policy y Permissions-Policy.
- `curl -I https://DOMINIO/sw.js` y comprobar `Cache-Control: no-store` y `Service-Worker-Allowed: /`.
- Validar `/manifest.webmanifest`: MIME correcto, iconos 192/512/maskable, screenshots y `scope=/`.
- Recorrido HTTP autenticado por roles internos: super admin, preparacion y repartidor.
- Integridad ORM: ninguna tabla o columna de negocio fuera de los modelos, salvo `schema_migrations`.
- El predeploy debe confirmar que no existen nombres de endpoint Flask duplicados dentro de un blueprint.

## Checklist de produccion

- Definir `FLASK_ENV=production` o arrancar con `create_app("production")`.
- Definir `SECRET_KEY` fuerte y unico del entorno.
- Usar exclusivamente PostgreSQL. SQLite y lanzadores alternativos no están soportados.
- Definir `BOT_API_KEY` compartida con el servicio de WhatsApp.
- Revisar `BOT_API_URL`, `BOT_PANEL_KEY`, `OXIDIAN_PUBLIC_URL` y `TIENDA_URL`.
- Confirmar HTTPS delante de Flask para que `SESSION_COOKIE_SECURE=True` funcione correctamente.
- Confirmar que la cookie de sesión usa `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/` y prefijo `__Host-`.
- Confirmar que `/health/live` no depende de PostgreSQL y `/health/ready` devuelve 503 si PostgreSQL no está disponible.
- Confirmar que el proxy entrega `X-Forwarded-Proto=https` y `TRUST_PROXY_HEADERS=1`.
- Confirmar que el service worker no almacena HTML, carrito, checkout, perfil ni respuestas API.
- Configurar copias de seguridad de base de datos y carpeta `images/`.
- Revisar permisos de escritura de `images/` y logs.
- Confirmar que `MAX_CONTENT_LENGTH` cumple el limite deseado de subida.

## Checklist manual antes de abrir ventas

- Desktop: menu, producto, carrito, checkout y pedido confirmado por token.
- Android real: menu, modal quick-add, carrito, checkout y confirmacion.
- iOS real: menu, modal quick-add, carrito, checkout y confirmacion.
- PWA: instalación Android, instrucciones iOS, actualización de versión y fallback offline sin datos personales.
- PWA: cerrar sesión, entrar con otro usuario y verificar que nunca aparece HTML del usuario anterior.
- PWA: una URL externa recibida por push debe abrir solamente la raíz del mismo origen.
- Admin: crear producto/categoria, subir imagen, ajustar stock, ver pedido y caja.
- Cocina/preparacion: tomar pedido, iniciar armado y marcar listo.
- Repartidor: salir a entregar, introducir codigo y confirmar cobro.
- Cliente: ver pedido entregado y dejar resena.
- Bot WhatsApp real: consultar catalogo, puntos y estado; solicitar humano; recibir notificaciones de estado y broadcast. No debe crear pedidos de cliente.
- POS real: cobrar venta, imprimir/ver ticket, devolver venta y verificar caja.

## Criterio de cierre

La fase 10 puede cerrarse cuando la suite completa esté verde, predeploy no tenga errores y el responsable del despliegue acepte las pruebas manuales de HTTPS, dispositivos reales, bot real y POS real.
