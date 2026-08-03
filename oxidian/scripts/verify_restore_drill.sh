#!/usr/bin/env bash
# Verifica integridad y restaura ambos dumps en bases temporales aisladas.
# No toca las bases activas. Uso: ./scripts/verify_restore_drill.sh /ruta/backup

set -euo pipefail

BACKUP_DIR="${1:-}"
OXIDIAN_DB="${OXIDIAN_DB:-oxidian-db}"
EVOLUTION_DB="${EVOLUTION_DB:-evolution-db}"

if [[ -z "$BACKUP_DIR" || ! -d "$BACKUP_DIR" ]]; then
  echo "Uso: $0 /ruta/al/backup" >&2
  exit 2
fi

for required in SHA256SUMS oxidian.dump evolution.dump chatbot_data.tar.gz; do
  if [[ ! -s "$BACKUP_DIR/$required" ]]; then
    echo "Falta o está vacío: $BACKUP_DIR/$required" >&2
    exit 1
  fi
done

if [[ -s "$BACKUP_DIR/images.tar.gz" ]]; then
  images_archive="$BACKUP_DIR/images.tar.gz"
elif [[ -s "$BACKUP_DIR/oxidian_images.tar.gz" ]]; then
  images_archive="$BACKUP_DIR/oxidian_images.tar.gz"
else
  echo "Falta el archivo de imágenes del catálogo" >&2
  exit 1
fi

(cd "$BACKUP_DIR" && sha256sum --check SHA256SUMS)
tar -tzf "$images_archive" >/dev/null
tar -tzf "$BACKUP_DIR/chatbot_data.tar.gz" >/dev/null

suffix="$(date +%s)_$$"
ox_restore="oxidian_restore_${suffix}"
ev_restore="evolution_restore_${suffix}"

cleanup() {
  docker exec "$OXIDIAN_DB" dropdb -U oxidian --if-exists "$ox_restore" >/dev/null 2>&1 || true
  docker exec "$EVOLUTION_DB" dropdb -U evolution --if-exists "$ev_restore" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker exec "$OXIDIAN_DB" createdb -U oxidian -T template0 "$ox_restore"
docker exec -i "$OXIDIAN_DB" pg_restore -U oxidian --no-owner --no-privileges \
  -d "$ox_restore" < "$BACKUP_DIR/oxidian.dump"

core_tables="users products orders site_config schema_migrations"
for table in $core_tables; do
  exists="$(docker exec "$OXIDIAN_DB" psql -U oxidian -d "$ox_restore" -Atc \
    "SELECT to_regclass('public.$table') IS NOT NULL")"
  [[ "$exists" == "t" ]] || { echo "Restauración incompleta: falta $table" >&2; exit 1; }
done
docker exec "$OXIDIAN_DB" psql -U oxidian -d "$ox_restore" -v ON_ERROR_STOP=1 -Atc \
  "SELECT 'users=' || count(*) FROM users; SELECT 'products=' || count(*) FROM products; SELECT 'migrations=' || count(*) FROM schema_migrations;"

docker exec "$EVOLUTION_DB" createdb -U evolution -T template0 "$ev_restore"
docker exec -i "$EVOLUTION_DB" pg_restore -U evolution --no-owner --no-privileges \
  -d "$ev_restore" < "$BACKUP_DIR/evolution.dump"
evolution_tables="$(docker exec "$EVOLUTION_DB" psql -U evolution -d "$ev_restore" -Atc \
  "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')")"
[[ "${evolution_tables:-0}" -gt 0 ]] || { echo "Evolution restauró sin tablas" >&2; exit 1; }

echo "OK: checksums, archivos y restauración temporal de Oxidian/Evolution verificados."
