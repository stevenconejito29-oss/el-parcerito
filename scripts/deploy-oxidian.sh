#!/usr/bin/env bash
# Deploy manual de oxidian tras un git pull en el server productivo.
#
# ¿Por qué existe este script?
# ─────────────────────────────
# En producción hay DOS compose files coexistiendo:
#   * cosmos-compose.yml           — define `container_name: oxidian` (correcto)
#   * docker-compose.cosmos-local.yml — NO define container_name → Docker crea
#     `oxidian-oxidian-1` (con prefijo del project) y el gateway falla porque
#     su nginx.conf apunta al hostname `oxidian`, no `oxidian-oxidian-1`.
#
# Deploys anteriores usando el segundo compose file causaban HTTP 502 tras
# el `up -d` porque el gateway resolvía un hostname distinto. Este script
# asegura que SIEMPRE se use el compose file "cosmos" que preserva los
# nombres esperados.
#
# Uso:
#   ./scripts/deploy-oxidian.sh
# Idempotente. Sale con código != 0 si la health check falla tras 15s.

set -euo pipefail

DEPLOY_DIR="${OXIDIAN_DEPLOY_DIR:-/opt/oxidian-workspace/el-parcerito}"
ENV_FILE="$DEPLOY_DIR/oxidian/.env.cosmos.local"
COMPOSE_FILE="$DEPLOY_DIR/oxidian/cosmos-compose.yml"

if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "[ERROR] cosmos-compose.yml no encontrado en $COMPOSE_FILE" >&2
    exit 1
fi

echo "[$(date -Is)] pull código..."
cd "$DEPLOY_DIR"
git pull --ff-only origin main

echo "[$(date -Is)] validando configuración de producción..."
python3 "$DEPLOY_DIR/oxidian/scripts/predeploy_check.py" --env-file "$ENV_FILE" --deployment cosmos

echo "[$(date -Is)] rebuild oxidian..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build oxidian

echo "[$(date -Is)] recreate oxidian + redis..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d oxidian-redis oxidian

echo "[$(date -Is)] esperando health..."
for i in {1..15}; do
    if docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T oxidian curl -fsS http://localhost:5000/health/ready >/dev/null 2>&1; then
        echo "[$(date -Is)] ✅ readiness OK tras ${i}s"
        break
    fi
    sleep 1
    # Si a los 5s seguimos en 502, restart del gateway suele repararlo
    # (bind mount + DNS cachea IPs viejas).
    if [[ "$i" == "5" ]]; then
        if docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps oxidian --format json 2>/dev/null | grep -q '"Health":"healthy"'; then
            echo "[$(date -Is)] ✅ servicio reporta healthy"
            break
        fi
        echo "[$(date -Is)] 502/health pendiente → restart gateway..."
        docker restart oxidian-gateway || true
    fi
done

if ! docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T oxidian curl -fsS http://localhost:5000/health/ready >/dev/null 2>&1; then
    echo "[$(date -Is)] ❌ readiness falló: app o base de datos no están disponibles" >&2
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs oxidian --tail 40 >&2 || true
    exit 1
fi

echo "[$(date -Is)] verificando PWA y enlaces públicos..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T oxidian \
    curl -fsS http://localhost:5000/health/live >/dev/null
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T oxidian \
    curl -fsS http://localhost:5000/sw.js >/dev/null
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T oxidian \
    curl -fsS http://localhost:5000/manifest.webmanifest >/dev/null

echo "[$(date -Is)] estado de integraciones (bot/WhatsApp puede figurar degraded si la sesión no está conectada):"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T oxidian \
    curl -fsS http://localhost:5000/health/integrations || true
echo
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
echo "[$(date -Is)] ✅ deploy verificado"
