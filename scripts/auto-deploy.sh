#!/usr/bin/env bash
# Pull-based production deployment. Intended to run from cron on the server.

set -uo pipefail

REPO_URL="${OXIDIAN_REPO_URL:-https://github.com/stevenconejito29-oss/el-parcerito.git}"
REPO_SLUG="${OXIDIAN_REPO_SLUG:-stevenconejito29-oss/el-parcerito}"
DEPLOY_DIR="${OXIDIAN_DEPLOY_DIR:-/opt/oxidian-workspace/el-parcerito}"
ENV_FILE="${OXIDIAN_ENV_FILE:-$DEPLOY_DIR/oxidian/.env.cosmos.local}"
COMPOSE_FILE="$DEPLOY_DIR/oxidian/cosmos-compose.yml"
STATE_DIR="${OXIDIAN_DEPLOY_STATE_DIR:-$HOME/.local/state/oxidian-deploy}"
LOG_FILE="${OXIDIAN_DEPLOY_LOG:-$STATE_DIR/deploy.log}"
LOCK_FILE="$STATE_DIR/deploy.lock"
DEPLOYED_FILE="$STATE_DIR/deployed-sha"

mkdir -p "$STATE_DIR"
touch "$LOG_FILE"
exec >>"$LOG_FILE" 2>&1
exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

log() {
    printf '[%s] %s\n' "$(date -Is)" "$*"
}

compose() {
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

ci_conclusion() {
    local sha="$1"
    curl -fsSL \
        -H "Accept: application/vnd.github+json" \
        -H "User-Agent: oxidian-production-deployer" \
        "https://api.github.com/repos/$REPO_SLUG/actions/runs?event=push&head_sha=$sha&per_page=20" |
        jq -r '
            [.workflow_runs[] | select(.name == "CI")]
            | sort_by(.created_at)
            | last
            | if . == null then "missing" else (.conclusion // .status) end
        '
}

wait_healthy() {
    local attempt app gateway database app_id gateway_id database_id
    for attempt in $(seq 1 90); do
        app_id="$(compose ps -q oxidian 2>/dev/null | head -n 1)"
        gateway_id="$(compose ps -q gateway 2>/dev/null | head -n 1)"
        database_id="$(compose ps -q oxidian-db 2>/dev/null | head -n 1)"
        app="$(docker inspect -f '{{.State.Health.Status}}' "$app_id" 2>/dev/null || true)"
        gateway="$(docker inspect -f '{{.State.Health.Status}}' "$gateway_id" 2>/dev/null || true)"
        database="$(docker inspect -f '{{.State.Health.Status}}' "$database_id" 2>/dev/null || true)"
        if [ "$app" = "healthy" ] && [ "$gateway" = "healthy" ] && [ "$database" = "healthy" ]; then
            compose exec -T oxidian curl -fsS --max-time 10 http://127.0.0.1:5000/health >/dev/null
            return $?
        fi
        sleep 2
    done
    return 1
}

deploy_revision() {
    compose config --quiet &&
        compose up -d --build --remove-orphans &&
        wait_healthy
}

if [ ! -d "$DEPLOY_DIR/.git" ]; then
    log "ERROR: $DEPLOY_DIR no es un clon Git."
    exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
    log "ERROR: falta $ENV_FILE."
    exit 1
fi

cd "$DEPLOY_DIR" || exit 1
if ! git fetch --quiet --prune origin main; then
    log "GitHub no esta disponible; se conserva la version actual."
    exit 0
fi

target_sha="$(git rev-parse origin/main)"
current_sha="$(git rev-parse HEAD)"
deployed_sha="$(cat "$DEPLOYED_FILE" 2>/dev/null || true)"
if [ "$target_sha" = "$current_sha" ] && [ "$target_sha" = "$deployed_sha" ]; then
    exit 0
fi

conclusion="$(ci_conclusion "$target_sha" 2>/dev/null || echo unavailable)"
case "$conclusion" in
    success) ;;
    failure|cancelled|timed_out|action_required|startup_failure)
        log "Commit $target_sha rechazado: CI=$conclusion."
        exit 0
        ;;
    *)
        log "Commit $target_sha en espera: CI=$conclusion."
        exit 0
        ;;
esac

log "Iniciando despliegue $current_sha -> $target_sha."
if ! bash "$DEPLOY_DIR/scripts/backup.sh"; then
    log "ERROR: no se pudo crear el backup previo."
    exit 1
fi

git reset --hard "$target_sha"
if deploy_revision; then
    printf '%s\n' "$target_sha" >"$DEPLOYED_FILE"
    if [ -f "$DEPLOY_DIR/scripts/auto-deploy.sh" ]; then
        install -m 0755 "$DEPLOY_DIR/scripts/auto-deploy.sh" "$STATE_DIR/auto-deploy.sh"
    fi
    log "Despliegue $target_sha completado y saludable."
    exit 0
fi

log "ERROR: $target_sha no quedo saludable; restaurando codigo $current_sha."
git reset --hard "$current_sha"
if deploy_revision; then
    printf '%s\n' "$current_sha" >"$DEPLOYED_FILE"
    log "Rollback de aplicacion completado en $current_sha. El backup previo queda disponible."
else
    log "CRITICO: el rollback tampoco quedo saludable; se requiere intervencion."
fi
exit 1
