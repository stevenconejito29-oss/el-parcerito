#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# Backup diario Oxidian al HDD
#
# Diseñado para cron: no depende de vars interactivas, todo por env con
# defaults sensatos. Escribe log en stdout/stderr — cron lo pipea al log.
#
# CONFIG via env:
#   BACKUP_DIR       — destino en el HDD (default /mnt/hdd/oxidian-backups)
#   RETAIN_DAYS      — días que se conservan backups antiguos (default 30)
#   OXIDIAN_DB       — container postgres oxidian (default oxidian-db)
#   EVOLUTION_DB     — container postgres evolution (default evolution-db)
#   OXIDIAN_APP      — container app (default oxidian)
#
# INSTALACIÓN en cron:
#   30 3 * * * /opt/oxidian-workspace/el-parcerito/oxidian/scripts/backup_to_hdd.sh \
#     >> /var/log/oxidian-backup.log 2>&1
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/mnt/hdd/oxidian-backups}"
RETAIN_DAYS="${RETAIN_DAYS:-30}"
OXIDIAN_DB="${OXIDIAN_DB:-oxidian-db}"
EVOLUTION_DB="${EVOLUTION_DB:-evolution-db}"
OXIDIAN_APP="${OXIDIAN_APP:-oxidian}"

TS="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_DIR/$TS"

log() { printf '[%s] %s\n' "$(date -Is)" "$*"; }
warn() { log "WARN: $*" >&2; }

mkdir -p "$DEST"

log "Backup destino: $DEST"

# ─── pg_dump oxidian ──────────────────────────────────────────────
if docker ps --format '{{.Names}}' | grep -q "^${OXIDIAN_DB}$"; then
  log "pg_dump oxidian…"
  if docker exec "$OXIDIAN_DB" pg_dump -U oxidian -Fc oxidian > "$DEST/oxidian.dump"; then
    log "  OK ($(du -h "$DEST/oxidian.dump" | cut -f1))"
  else
    warn "pg_dump oxidian falló"
  fi
else
  warn "$OXIDIAN_DB no está corriendo"
fi

# ─── pg_dump evolution ────────────────────────────────────────────
if docker ps --format '{{.Names}}' | grep -q "^${EVOLUTION_DB}$"; then
  log "pg_dump evolution…"
  if docker exec "$EVOLUTION_DB" pg_dump -U evolution -Fc evolution > "$DEST/evolution.dump"; then
    log "  OK ($(du -h "$DEST/evolution.dump" | cut -f1))"
  else
    warn "pg_dump evolution falló"
  fi
else
  warn "$EVOLUTION_DB no está corriendo"
fi

# ─── volumes críticos: images (fotos de productos) + chatbot_data ─
for vol_name in oxidian_images chatbot_data; do
  full=$(docker volume ls -q | grep "_${vol_name}$" | head -1)
  if [[ -n "$full" ]]; then
    log "tar.gz $vol_name (volumen $full)…"
    if docker run --rm -v "$full":/data -v "$DEST":/backup alpine \
         tar czf "/backup/${vol_name}.tar.gz" -C /data . 2>/dev/null; then
      log "  OK ($(du -h "$DEST/${vol_name}.tar.gz" | cut -f1))"
    else
      warn "tarball $vol_name falló"
    fi
  else
    warn "volumen $vol_name no encontrado"
  fi
done

# ─── Integridad: sha256 de todos los archivos ─────────────────────
log "SHA256SUMS…"
( cd "$DEST" && sha256sum ./* > SHA256SUMS 2>/dev/null || true )

# ─── Retención: borrar backups más viejos que RETAIN_DAYS ─────────
log "Retención: borrando >${RETAIN_DAYS} días…"
BORRADOS=0
if [[ -d "$BACKUP_DIR" ]]; then
  while IFS= read -r -d '' d; do
    log "  → rm -rf $d"
    rm -rf "$d"
    BORRADOS=$((BORRADOS + 1))
  done < <(find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d \
             -mtime "+$RETAIN_DAYS" -print0 2>/dev/null)
fi
log "  $BORRADOS backups viejos eliminados."

# ─── Resumen ──────────────────────────────────────────────────────
TOTAL_MB=$(du -sm "$DEST" 2>/dev/null | cut -f1)
FREE_HDD=$(df -h "$BACKUP_DIR" | tail -1 | awk '{print $4}')
log "Backup $TS OK · tamaño=${TOTAL_MB}MB · HDD libre=$FREE_HDD"
