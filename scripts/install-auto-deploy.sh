#!/usr/bin/env bash
# Installs the pull-based deploy job for the current user.

set -euo pipefail

DEPLOY_DIR="${OXIDIAN_DEPLOY_DIR:-/opt/oxidian-workspace/el-parcerito}"
STATE_DIR="${OXIDIAN_DEPLOY_STATE_DIR:-$HOME/.local/state/oxidian-deploy}"
SOURCE="$DEPLOY_DIR/scripts/auto-deploy.sh"
TARGET="$STATE_DIR/auto-deploy.sh"
CRON_LINE="*/2 * * * * /usr/bin/flock -n /tmp/oxidian-auto-deploy-cron.lock $TARGET"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_FILE="$UNIT_DIR/oxidian-auto-deploy.service"
TIMER_FILE="$UNIT_DIR/oxidian-auto-deploy.timer"

if [ ! -f "$DEPLOY_DIR/oxidian/.env.cosmos.local" ]; then
    echo "Falta $DEPLOY_DIR/oxidian/.env.cosmos.local" >&2
    exit 1
fi

mkdir -p "$STATE_DIR"
install -m 0755 "$SOURCE" "$TARGET"
git -C "$DEPLOY_DIR" rev-parse HEAD >"$STATE_DIR/deployed-sha"

# El timer de usuario no comparte el crontab con backups u otros instaladores,
# por lo que resulta más resistente a reemplazos accidentales.
mkdir -p "$UNIT_DIR"
cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=Despliegue automatico de Oxidian desde GitHub

[Service]
Type=oneshot
ExecStart=$TARGET
EOF

cat >"$TIMER_FILE" <<EOF
[Unit]
Description=Comprobar actualizaciones de Oxidian cada dos minutos

[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
RandomizedDelaySec=15
Persistent=true
Unit=oxidian-auto-deploy.service

[Install]
WantedBy=timers.target
EOF

{
    crontab -l 2>/dev/null | grep -vF "$TARGET" || true
} | crontab -

if systemctl --user daemon-reload \
    && systemctl --user enable --now oxidian-auto-deploy.timer; then
    echo "Despliegue automatico instalado con systemd cada 2 minutos."
else
    {
        crontab -l 2>/dev/null | grep -vF "$TARGET" || true
        echo "$CRON_LINE"
    } | crontab -
    echo "Systemd de usuario no disponible; instalado con cron cada 2 minutos."
fi

echo "Log: $STATE_DIR/deploy.log"
