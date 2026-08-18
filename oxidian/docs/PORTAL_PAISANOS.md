# Portal /paisanos

Vista pública dentro de la app Oxidian con recursos en vivo para la comunidad colombiana en España.

## Arquitectura

- **Ruta:** `/paisanos` — servida por `routes/paisanos.py` (blueprint `paisanos_bp`).
- **Servicio:** `paisanos_service.py` — refresco de datos externos + snapshot para la vista.
- **Cache:** Redis del contenedor `oxidian-redis` (misma instancia que rate limiting).
- **Refresco:** APScheduler (`BackgroundScheduler`) cada 15 min, más un tick síncrono al arrancar la app para que Redis nunca esté vacío en el minuto 0.
- **Template:** `templates/public/paisanos.html`, extiende `base.html`.

## Fuentes externas

| Widget | API / Feed | Observación |
|---|---|---|
| COP/EUR | `https://api.exchangerate.host/latest?base=EUR&symbols=COP` | Gratis, sin API key |
| USD/COP | `https://api.exchangerate.host/latest?base=USD&symbols=COP` | Gratis, sin API key |
| Clima Carmona | `https://api.open-meteo.com/v1/forecast?latitude=37.4691&longitude=-5.6420&current_weather=true` | Gratis |
| Clima Bogotá | `https://api.open-meteo.com/v1/forecast?latitude=4.7110&longitude=-74.0721&current_weather=true` | Gratis |
| Noticias CO | RSS El Tiempo, Semana, El Espectador | 8 items combinados |
| BOE extranjería | RSS `https://www.boe.es/rss/canal.php?c=10` filtrado por keywords | 5 items |

**Keywords BOE:** `extranjería`, `extranjeros`, `residencia`, `arraigo`, `nacionalidad`, `asilo`.

## Claves Redis

| Key | Contenido |
|---|---|
| `paisanos:tasas` | `{cop_eur, usd_cop, updated_at}` |
| `paisanos:clima` | `{carmona:{temperatura,viento,codigo,hora}, bogota:{...}, updated_at}` |
| `paisanos:noticias_co` | `{items:[{titulo,enlace,fuente,publicado,resumen}], updated_at}` |
| `paisanos:tramites_boe` | `{items:[...], updated_at}` |

## Política de fallback

Si una fuente externa falla (timeout, 5xx, feed inválido):
- **Snapshot previo se conserva.** Nunca borramos la clave Redis por un error puntual.
- Si nunca ha respondido → la vista muestra "Actualizando…" en ese widget.
- Los timeouts HTTP son de 8s y se registran como `WARNING` en el log.

## Refresh cycle

- **Startup:** `paisanos_service.refrescar_todo()` se llama una vez tras `db.create_all()` en `create_app()`.
- **Cron:** `BackgroundScheduler.add_job(refrescar_todo, "interval", minutes=15)`.
- **Scope:** ambos guardas están protegidos con try/except para no romper el arranque de la app aunque una API externa esté caída.

## Verificar en producción

```
docker exec oxidian-redis redis-cli KEYS 'paisanos:*'
docker exec oxidian-redis redis-cli GET paisanos:tasas | jq
curl -sk https://elparcerito.com/paisanos | head -50
```
