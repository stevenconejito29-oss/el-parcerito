# Observabilidad — Umami

Analítica web con Umami (self-hosted en LAN). La app Oxidian está preparada para inyectar el snippet de tracking de forma condicional.

## Estado actual

- **Umami:** desplegado en LAN → `http://192.168.1.32:3001`.
- **Config Oxidian:** clave `UMAMI_WEBSITE_ID` en SiteConfig (default vacío) — visible en `/superadmin/config`.
- **Snippet:** inyectado en `<head>` de `base.html` sólo si `UMAMI_WEBSITE_ID` no está vacío. Default seguro: sin snippet, sin analítica.

## Mixed content — **PENDIENTE**

El snippet actual apunta a `http://192.168.1.32:3001/script.js`. La web pública (`https://elparcerito.com`) es HTTPS. Los navegadores modernos bloquean cargar recursos HTTP desde una página HTTPS ("mixed content active"). **Con la configuración actual, si se rellena `UMAMI_WEBSITE_ID` en producción el script quedará bloqueado por el navegador y no habrá tracking**.

Por eso el default es vacío: la infraestructura está lista, pero la analítica queda oficialmente desactivada hasta que se exponga Umami por HTTPS.

## Pasos para activar en producción

1. Exponer Umami detrás de Cloudflare Tunnel con un subdominio, ej. `https://umami.elparcerito.com`.
2. Editar `templates/base.html` y cambiar `http://192.168.1.32:3001/script.js` por la URL pública HTTPS.
3. Crear un "website" dentro de Umami y copiar su UUID en `/superadmin/config → UMAMI_WEBSITE_ID`.
4. Recargar la web y verificar en DevTools que `script.js` carga con status 200.
5. Confirmar en el dashboard de Umami que llegan hits.

## Extensión futura

Umami acepta `data-domains` para restringir el tracking a dominios concretos y evitar tráfico de preview/dev. Si en algún momento tenemos más de un site (bot dashboard, admin) se pueden crear websites separados en Umami y varias claves de configuración.

## Otros servicios de observabilidad

- **Logs de la app:** `docker logs oxidian --tail 100`.
- **Métricas de sistema:** el compose sirve health-checks básicos; no hay Prometheus/Grafana montado.
