#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# Backup completo del stack Oxidian (PostgreSQL oxidian + evolution + volúmenes)
# ════════════════════════════════════════════════════════════════════════════
# Genera un directorio con timestamp dentro de $OXIDIAN_BACKUP_DIR conteniendo:
#   - oxidian.dump          (pg_dump -Fc de la BD principal)
#   - evolution.dump        (pg_dump -Fc de la BD de Evolution API)
#   - images.tar.gz         (volumen oxidian_oxidian_images)
#   - chatbot_data.tar.gz   (volumen oxidian_chatbot_data — SQLite del bot)
#   - manifest.txt          (sizes + comando ejecutado)
#   - SHA256SUMS            (hashes de todos los archivos del backup)
#
# Conserva los últimos RETENTION_DAYS (default 7) y borra el resto.
#
# Uso: bash /home/panzeta/Documentos/scripts/backup.sh
# ════════════════════════════════════════════════════════════════════════════

set -euo pipefail

OXIDIAN_BACKUP_DIR="${OXIDIAN_BACKUP_DIR:-$HOME/oxidian-backups}"
RETENTION_DAYS="${OXIDIAN_BACKUP_RETENTION:-7}"
TS="$(date +%Y%m%d-%H%M%S)"
DEST="$OXIDIAN_BACKUP_DIR/$TS"
LOG="$OXIDIAN_BACKUP_DIR/backup.log"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${OXIDIAN_DEPLOY_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
COMPOSE_FILE="${OXIDIAN_COMPOSE_FILE:-$DEPLOY_DIR/oxidian/cosmos-compose.yml}"
ENV_FILE="${OXIDIAN_ENV_FILE:-$DEPLOY_DIR/oxidian/.env.cosmos.local}"

compose() {
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

service_container() {
    local service="$1" fallback="$2" container_id
    container_id="$(compose ps -q "$service" 2>/dev/null | head -n 1)"
    if [ -n "$container_id" ]; then
        printf '%s\n' "$container_id"
    else
        printf '%s\n' "$fallback"
    fi
}

OXIDIAN_DB_CONTAINER="${OXIDIAN_DB_CONTAINER:-$(service_container oxidian-db oxidian-db)}"
OXIDIAN_DB_USER="${OXIDIAN_DB_USER:-oxidian}"
OXIDIAN_DB_NAME="${OXIDIAN_DB_NAME:-oxidian}"
EVOLUTION_DB_CONTAINER="${EVOLUTION_DB_CONTAINER:-$(service_container evolution-db evolution-db)}"
EVOLUTION_DB_USER="${EVOLUTION_DB_USER:-evolution}"
EVOLUTION_DB_NAME="${EVOLUTION_DB_NAME:-evolution}"

IMAGES_VOLUME="${OXIDIAN_IMAGES_VOLUME:-oxidian_oxidian_images}"
CHATBOT_VOLUME="${OXIDIAN_CHATBOT_VOLUME:-oxidian_chatbot_data}"

mkdir -p "$DEST"
echo "[$(date -Is)] backup → $DEST" | tee -a "$LOG"

# ── 1. Dump PostgreSQL ──────────────────────────────────────────────────────
echo "  · pg_dump oxidian..."
docker exec "$OXIDIAN_DB_CONTAINER" pg_dump -U "$OXIDIAN_DB_USER" -Fc "$OXIDIAN_DB_NAME" \
    > "$DEST/oxidian.dump"

echo "  · pg_dump evolution..."
docker exec "$EVOLUTION_DB_CONTAINER" pg_dump -U "$EVOLUTION_DB_USER" -Fc "$EVOLUTION_DB_NAME" \
    > "$DEST/evolution.dump"

# ── 2. Volúmenes (tar.gz) ──────────────────────────────────────────────────
# Usamos un contenedor efímero que monta el volumen Docker y devuelve un tar
# por stdout — así no dependemos de los permisos del host.
echo "  · tar volumen $IMAGES_VOLUME..."
docker run --rm -v "$IMAGES_VOLUME:/data:ro" alpine:3.20 \
    tar -czf - -C /data . > "$DEST/images.tar.gz"

echo "  · tar volumen $CHATBOT_VOLUME..."
docker run --rm -v "$CHATBOT_VOLUME:/data:ro" alpine:3.20 \
    tar -czf - -C /data . > "$DEST/chatbot_data.tar.gz"

# ── 3. Manifest y hashes ───────────────────────────────────────────────────
{
    echo "Backup Oxidian — $TS"
    echo "Generado por: $(whoami)@$(hostname) en $(date -Is)"
    echo ""
    echo "Origen:"
    echo "  oxidian DB     ← $OXIDIAN_DB_CONTAINER:$OXIDIAN_DB_NAME"
    echo "  evolution DB   ← $EVOLUTION_DB_CONTAINER:$EVOLUTION_DB_NAME"
    echo "  images         ← volumen $IMAGES_VOLUME"
    echo "  chatbot_data   ← volumen $CHATBOT_VOLUME"
    echo ""
    echo "Tamaños:"
    du -h "$DEST"/*.dump "$DEST"/*.tar.gz 2>/dev/null
} > "$DEST/manifest.txt"

(cd "$DEST" && sha256sum *.dump *.tar.gz manifest.txt > SHA256SUMS)

# ── 4. Rotación ─────────────────────────────────────────────────────────────
find "$OXIDIAN_BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d \
    -mtime "+$RETENTION_DAYS" -print -exec rm -rf {} \; | \
    sed 's/^/  · purgado: /' | tee -a "$LOG"

TOTAL="$(du -sh "$DEST" | awk '{print $1}')"
echo "[$(date -Is)] OK  — $DEST ($TOTAL)" | tee -a "$LOG"
echo "$DEST"
