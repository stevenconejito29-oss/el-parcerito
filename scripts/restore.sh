#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# Restauración del stack Oxidian desde un directorio de backup
# ════════════════════════════════════════════════════════════════════════════
# Uso:
#   bash restore.sh /ruta/al/backup [--dry-run]
#
# Procedimiento:
#   1. Verifica SHA256SUMS.
#   2. Detiene oxidian + bot worker (deja PG en marcha).
#   3. Restaura las DBs con pg_restore --clean --if-exists.
#   4. Restaura volúmenes (images, chatbot_data) sobrescribiendo el contenido.
#   5. Re-levanta oxidian.
#   6. Smoke test HTTP.
#
# --dry-run: solo verifica integridad y muestra qué haría, sin tocar nada.
# ════════════════════════════════════════════════════════════════════════════

set -euo pipefail

BACKUP_DIR="${1:-}"
DRY_RUN=0
for arg in "$@"; do
    [ "$arg" = "--dry-run" ] && DRY_RUN=1
done

if [ -z "$BACKUP_DIR" ] || [ ! -d "$BACKUP_DIR" ]; then
    echo "Uso: $0 <backup_dir> [--dry-run]" >&2
    echo "Ej:  $0 ~/oxidian-backups/20260617-173731" >&2
    exit 2
fi

cd "$BACKUP_DIR"

# ── 1. Verificación de integridad ───────────────────────────────────────────
if [ ! -f SHA256SUMS ]; then
    echo "ERROR: SHA256SUMS no existe en $BACKUP_DIR" >&2
    exit 3
fi
echo "▸ Verificando hashes..."
if ! sha256sum -c SHA256SUMS --quiet; then
    echo "ERROR: hashes corruptos. Aborta." >&2
    exit 4
fi
echo "  ✓ integridad OK"

cat manifest.txt 2>/dev/null || true

if [ "$DRY_RUN" = "1" ]; then
    echo ""
    echo "DRY RUN — no se modifica nada."
    exit 0
fi

read -rp "Vas a SOBREESCRIBIR las BDs y volúmenes activos. ¿Continuar? (escribe SI): " ans
if [ "$ans" != "SI" ]; then
    echo "Cancelado."
    exit 1
fi

OXIDIAN_DB_CONTAINER="${OXIDIAN_DB_CONTAINER:-oxidian-db}"
OXIDIAN_DB_USER="${OXIDIAN_DB_USER:-oxidian}"
OXIDIAN_DB_NAME="${OXIDIAN_DB_NAME:-oxidian}"
EVOLUTION_DB_CONTAINER="${EVOLUTION_DB_CONTAINER:-evolution-db}"
EVOLUTION_DB_USER="${EVOLUTION_DB_USER:-evolution}"
EVOLUTION_DB_NAME="${EVOLUTION_DB_NAME:-evolution}"
IMAGES_VOLUME="${OXIDIAN_IMAGES_VOLUME:-oxidian_oxidian_images}"
CHATBOT_VOLUME="${OXIDIAN_CHATBOT_VOLUME:-oxidian_chatbot_data}"

COMPOSE_FILE="${OXIDIAN_COMPOSE_FILE:-/opt/oxidian-workspace/oxidian/cosmos-compose.yml}"
ENV_FILE="${OXIDIAN_ENV_FILE:-/opt/oxidian-workspace/oxidian/.env.cosmos.local}"
DC=(docker compose --file "$COMPOSE_FILE" --env-file "$ENV_FILE")

# ── 2. Parada controlada (deja PG en marcha) ────────────────────────────────
echo "▸ Parando oxidian (mantengo PG arriba)..."
"${DC[@]}" stop oxidian gateway 2>&1 | tail -3 || true

# ── 3. Restore PostgreSQL ───────────────────────────────────────────────────
echo "▸ pg_restore oxidian (drop + recreate)..."
docker exec -i "$OXIDIAN_DB_CONTAINER" pg_restore \
    --clean --if-exists --no-owner --no-acl \
    -U "$OXIDIAN_DB_USER" -d "$OXIDIAN_DB_NAME" < oxidian.dump

echo "▸ pg_restore evolution..."
docker exec -i "$EVOLUTION_DB_CONTAINER" pg_restore \
    --clean --if-exists --no-owner --no-acl \
    -U "$EVOLUTION_DB_USER" -d "$EVOLUTION_DB_NAME" < evolution.dump

# ── 4. Restore volúmenes ────────────────────────────────────────────────────
echo "▸ Restaurando volumen $IMAGES_VOLUME..."
docker run --rm -i -v "$IMAGES_VOLUME:/data" alpine:3.20 \
    sh -c 'rm -rf /data/* /data/.[!.]* 2>/dev/null; tar -xzf - -C /data' \
    < images.tar.gz

echo "▸ Restaurando volumen $CHATBOT_VOLUME..."
docker run --rm -i -v "$CHATBOT_VOLUME:/data" alpine:3.20 \
    sh -c 'rm -rf /data/* /data/.[!.]* 2>/dev/null; tar -xzf - -C /data' \
    < chatbot_data.tar.gz

# ── 5. Re-levantar oxidian ──────────────────────────────────────────────────
echo "▸ Levantando oxidian + gateway..."
"${DC[@]}" up -d oxidian gateway 2>&1 | tail -5

# ── 6. Smoke ────────────────────────────────────────────────────────────────
echo "▸ Esperando healthy..."
for i in $(seq 1 60); do
    if [ "$(docker inspect -f '{{.State.Health.Status}}' oxidian 2>/dev/null)" = "healthy" ]; then
        break
    fi
    sleep 2
done
STATUS="$(docker inspect -f '{{.State.Health.Status}}' oxidian 2>/dev/null || echo unknown)"
echo "  oxidian health: $STATUS"

CODE="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:5070/ || true)"
echo "  GET / → HTTP $CODE"

if [ "$STATUS" = "healthy" ] && [ "$CODE" = "200" ]; then
    echo "✓ Restauración completada con éxito."
else
    echo "El stack no quedó healthy. Revisa logs: docker logs oxidian"
    exit 5
fi
