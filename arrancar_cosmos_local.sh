#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════
# ARRANQUE COSMOS LOCAL — El Parcerito de Carmona
# Modo: Docker  |  PostgreSQL + Redis + Evolution + nginx
# Puerto público: 8088
# Env: oxidian/.env.cosmos.local
# ══════════════════════════════════════════════════════════
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OX_DIR="$DIR/oxidian"
ENV_FILE="$OX_DIR/.env.cosmos.local"
COMPOSE="$OX_DIR/docker-compose.cosmos-local.yml"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: No se encuentra $ENV_FILE" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker no está instalado o no está en PATH" >&2
  exit 1
fi

# Detectar docker compose v2 o v1
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  echo "ERROR: docker compose no encontrado" >&2
  exit 1
fi

ACTION="${1:-up}"

case "$ACTION" in
  up)
    echo ""
    echo "  El Parcerito de Carmona — COSMOS LOCAL (Docker)"
    echo "  ─────────────────────────────────────────────────"
    echo "  Compose:  $COMPOSE"
    echo "  Env:      $ENV_FILE"
    echo "  Tienda:   http://localhost:8088"
    echo "  Ctrl+C para detener  |  'down' para limpiar"
    echo "  ─────────────────────────────────────────────────"
    echo ""
    $DC --file "$COMPOSE" --env-file "$ENV_FILE" up --build
    ;;
  down)
    echo "  Deteniendo y eliminando contenedores..."
    $DC --file "$COMPOSE" --env-file "$ENV_FILE" down
    ;;
  restart)
    $DC --file "$COMPOSE" --env-file "$ENV_FILE" down
    $DC --file "$COMPOSE" --env-file "$ENV_FILE" up --build
    ;;
  logs)
    $DC --file "$COMPOSE" --env-file "$ENV_FILE" logs -f
    ;;
  *)
    echo "Uso: $0 [up|down|restart|logs]"
    echo "  up      → arrancar la pila completa (por defecto)"
    echo "  down    → detener y eliminar contenedores"
    echo "  restart → down + up"
    echo "  logs    → ver logs en tiempo real"
    exit 1
    ;;
esac
