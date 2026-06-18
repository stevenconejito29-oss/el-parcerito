#!/usr/bin/env bash
# Installs the pull-based deploy job for the current user.

set -euo pipefail

DEPLOY_DIR="${OXIDIAN_DEPLOY_DIR:-/opt/el-parcerito}"
STATE_DIR="${OXIDIAN_DEPLOY_STATE_DIR:-$HOME/.local/state/oxidian-deploy}"
SOURCE="$DEPLOY_DIR/scripts/auto-deploy.sh"
TARGET="$STATE_DIR/auto-deploy.sh"
CRON_LINE="*/2 * * * * /usr/bin/flock -n /tmp/oxidian-auto-deploy-cron.lock $TARGET"

if [ ! -f "$DEPLOY_DIR/oxidian/.env.cosmos.local" ]; then
    echo "Falta $DEPLOY_DIR/oxidian/.env.cosmos.local" >&2
    exit 1
fi

mkdir -p "$STATE_DIR"
install -m 0755 "$SOURCE" "$TARGET"
git -C "$DEPLOY_DIR" rev-parse HEAD >"$STATE_DIR/deployed-sha"

{
    crontab -l 2>/dev/null | grep -vF "$TARGET" || true
    echo "$CRON_LINE"
} | crontab -

echo "Despliegue automatico instalado cada 2 minutos."
echo "Log: $STATE_DIR/deploy.log"
