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

echo "[$(date -Is)] rebuild oxidian..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build oxidian

echo "[$(date -Is)] recreate oxidian..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d oxidian

echo "[$(date -Is)] esperando health..."
for i in {1..15}; do
    sleep 1
    code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:5070/health || true)
    if [[ "$code" == "200" ]]; then
        echo "[$(date -Is)] ✅ HTTP 200 tras ${i}s"
        exit 0
    fi
    # Si a los 5s seguimos en 502, restart del gateway suele repararlo
    # (bind mount + DNS cachea IPs viejas).
    if [[ "$i" == "5" && "$code" == "502" ]]; then
        echo "[$(date -Is)] 502 persistente → restart gateway..."
        docker restart oxidian-gateway
    fi
done

echo "[$(date -Is)] ❌ health check falló: última respuesta HTTP $code" >&2
docker logs oxidian --tail 20 >&2
exit 1
