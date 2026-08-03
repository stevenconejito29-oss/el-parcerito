#!/usr/bin/env bash
# Monitor operativo sin secretos: salud web, dependencias, backup y capacidad.
# Sale con código 1 si encuentra un problema para integrarse con cron/systemd.

set -u

BASE_URL="${OXIDIAN_MONITOR_URL:-https://elparcerito.com}"
BACKUP_DIR="${OXIDIAN_BACKUP_DIR:-$HOME/oxidian-backups}"
BACKUP_MAX_AGE_HOURS="${OXIDIAN_BACKUP_MAX_AGE_HOURS:-30}"
DISK_USED_MAX_PERCENT="${OXIDIAN_DISK_USED_MAX_PERCENT:-85}"
REQUIRED_CONTAINERS="${OXIDIAN_REQUIRED_CONTAINERS:-oxidian oxidian-db oxidian-redis evolution-api evolution-db evolution-redis oxidian-gateway}"
failures=0

log() { printf '[%s] %s\n' "$(date -Is)" "$*"; }
fail() { log "ERROR: $*" >&2; failures=$((failures + 1)); }

check_url() {
    local path="$1" body
    if ! body="$(curl --fail --silent --show-error --max-time 8 "$BASE_URL$path")"; then
        fail "$path no responde correctamente"
        return
    fi
    log "$path OK · ${body:0:240}"
}

check_url "/health/live"
check_url "/health/ready"
check_url "/health/integrations"

for container in $REQUIRED_CONTAINERS; do
    state="$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || true)"
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || true)"
    if [[ "$state" != "running" || "$health" == "unhealthy" || -z "$state" ]]; then
        fail "contenedor $container state=${state:-missing} health=${health:-unknown}"
    fi
done

latest="$(find -L "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -nr | awk 'NR==1 {$1=""; sub(/^ /, ""); print; exit}')"
if [[ -z "$latest" || ! -s "$latest/BACKUP_STATUS" ]] || ! grep -q 'status=verified' "$latest/BACKUP_STATUS"; then
    fail "no existe un backup reciente marcado como verificado"
else
    age_seconds=$(( $(date +%s) - $(stat -c %Y "$latest/BACKUP_STATUS") ))
    if (( age_seconds > BACKUP_MAX_AGE_HOURS * 3600 )); then
        fail "último backup verificado supera ${BACKUP_MAX_AGE_HOURS}h"
    else
        log "backup OK · $(basename "$latest")"
    fi
fi

disk_used="$(df -P / | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
if [[ ! "$disk_used" =~ ^[0-9]+$ ]] || (( disk_used > DISK_USED_MAX_PERCENT )); then
    fail "uso de disco raíz=${disk_used:-unknown}% (máximo ${DISK_USED_MAX_PERCENT}%)"
else
    log "disco OK · usado=${disk_used}%"
fi

if (( failures > 0 )); then
    log "MONITOR DEGRADED · fallos=$failures"
    exit 1
fi
log "MONITOR OK"
