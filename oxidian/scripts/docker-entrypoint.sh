#!/bin/sh
set -e

# Arreglar permisos de volúmenes montados por Docker (como root)
chown -R oxidian:oxidian /app/static/uploads /app/images /app/bot-data 2>/dev/null || true

PIDS=""

terminate_children() {
  for pid in $PIDS; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}

trap terminate_children INT TERM

# Compatibilidad previa: columnas que el modelo necesita para poder ejecutar
# el bootstrap sobre instalaciones que vienen de una versión anterior.
gosu oxidian python scripts/prebootstrap_schema.py

# Migraciones idempotentes antes del bootstrap para que los seeds consulten
# siempre un esquema compatible con los modelos actuales.
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  gosu oxidian python scripts/apply_schema_migrations.py
fi

# Bootstrap: seed de configuración y catálogo demo. Se puede desactivar en
# workers con RUN_BOOTSTRAP=0 para evitar trabajo repetido.
if [ "${RUN_BOOTSTRAP:-1}" = "1" ]; then
  gosu oxidian python scripts/cosmos_bootstrap.py
fi

if [ "$1" = "gunicorn" ] || [ "$#" -eq 0 ]; then
  export OXIDIAN_SKIP_STARTUP_DB="${OXIDIAN_SKIP_STARTUP_DB:-1}"

  if [ "${RUN_CHATBOT:-1}" = "1" ]; then
    echo "Arrancando chatbot Node en :${PORT:-3000}..."
    export NODE_ENV="${NODE_ENV:-production}"
    export PORT="${PORT:-3000}"
    export DB_DIR="${DB_DIR:-/app/bot-data}"
    export OXIDIAN_URL="${OXIDIAN_URL:-http://127.0.0.1:5000}"
    export TIENDA_URL="${TIENDA_URL:-${OXIDIAN_PUBLIC_URL:-http://127.0.0.1:5000}}"
    export OXIDIAN_KEY="${OXIDIAN_KEY:-${BOT_API_KEY:-}}"
    export BOT_PANEL_KEY="${BOT_PANEL_KEY:-${BOT_API_KEY:-}}"
    gosu oxidian node /app/chat/bot.js &
    PIDS="$PIDS $!"
  fi

  if [ "${RUN_OUTBOX_WORKER:-1}" = "1" ]; then
    echo "Arrancando worker outbox (long-lived, interval=${OUTBOX_INTERVAL_SECONDS:-2}s)..."
    gosu oxidian python scripts/process_notification_outbox.py \
      --loop \
      --env "${FLASK_ENV:-production}" \
      --limit "${OUTBOX_LIMIT:-25}" \
      --interval "${OUTBOX_INTERVAL_SECONDS:-2}" &
    PIDS="$PIDS $!"
  fi

  echo "Arrancando Oxidian Gunicorn en :5000..."
  gosu oxidian gunicorn \
    --workers "${WEB_CONCURRENCY:-2}" \
    --threads "${WEB_THREADS:-2}" \
    --worker-class "${GUNICORN_WORKER_CLASS:-gthread}" \
    --bind 0.0.0.0:5000 \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
    --keep-alive "${GUNICORN_KEEP_ALIVE:-5}" \
    --max-requests "${GUNICORN_MAX_REQUESTS:-1000}" \
	    --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER:-100}" \
	    --access-logfile - \
	    --access-logformat '%(h)s %(l)s %(u)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s "%(f)s" "%(a)s"' \
	    --error-logfile - \
	    --log-level info \
	    "app:create_app('production')" &
  PIDS="$PIDS $!"

  while :; do
    for pid in $PIDS; do
      if ! kill -0 "$pid" 2>/dev/null; then
        wait "$pid" 2>/dev/null
        status=$?
        terminate_children
        exit "$status"
      fi
    done
    sleep 2
  done
fi

exec gosu oxidian "$@"
