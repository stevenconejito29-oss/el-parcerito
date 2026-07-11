#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# Bootstrap producción — Oxidian
#
# Deja el server listo con:
#   • Stack limpio en el SSD (volúmenes Docker por defecto).
#   • BD con SOLO el super_admin fresco (config placeholder).
#   • Backups automáticos al HDD via cron.
#   • Log rotation Docker (ya aplicado en compose vía anchor).
#   • Retención automática de tablas (ya aplicado vía worker).
#
# EJECUCIÓN
# =========
#   1. Editar el bloque CONFIG de abajo con tus valores reales.
#   2. Correr: bash bootstrap_production.sh
#   3. El script pregunta antes de cada acción destructiva.
#
# REQUISITOS
# ==========
#   - Docker + docker compose instalados.
#   - HDD montado en $HDD_MOUNT (por defecto /mnt/hdd).
#   - Este archivo estar en el clone del repo con `oxidian/` accesible.
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── CONFIG (edita antes de correr) ───────────────────────────────
STACK_DIR="${STACK_DIR:-/opt/oxidian-workspace/el-parcerito}"
COMPOSE_FILE="${COMPOSE_FILE:-$STACK_DIR/oxidian/cosmos-compose.yml}"
ENV_FILE="${ENV_FILE:-$STACK_DIR/oxidian/.env.cosmos.local}"

HDD_MOUNT="${HDD_MOUNT:-/mnt/hdd}"
BACKUP_DIR_HDD="${BACKUP_DIR_HDD:-$HDD_MOUNT/oxidian-backups}"

# Credenciales del super_admin nuevo (obligatorias)
SUPERADMIN_EMAIL="${SUPERADMIN_EMAIL:-}"
SUPERADMIN_PASSWORD="${SUPERADMIN_PASSWORD:-}"
SUPERADMIN_NAME="${SUPERADMIN_NAME:-}"
SUPERADMIN_PHONE="${SUPERADMIN_PHONE:-}"

# ─── helpers ──────────────────────────────────────────────────────

RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; BOLD=$'\e[1m'; RESET=$'\e[0m'
step() { echo; echo "${BOLD}${GREEN}▶ $*${RESET}"; }
warn() { echo "${YELLOW}⚠ $*${RESET}"; }
die()  { echo "${RED}✗ $*${RESET}" >&2; exit 1; }
ask()  {
  local prompt="$1"; local reply
  read -r -p "${BOLD}$prompt [y/N] ${RESET}" reply
  [[ "${reply,,}" == "y" || "${reply,,}" == "yes" ]]
}

require_env() {
  local var="$1"
  [[ -n "${!var:-}" ]] || die "$var no definida. Setéala antes de correr el script."
}

# ─── PASO 0: pre-flight ───────────────────────────────────────────
step "Pre-flight — verificaciones"

command -v docker >/dev/null || die "docker no está instalado."
docker compose version >/dev/null 2>&1 || die "docker compose no disponible."

[[ -f "$COMPOSE_FILE" ]] || die "COMPOSE_FILE no encontrado: $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]] || die "ENV_FILE no encontrado: $ENV_FILE"

require_env SUPERADMIN_EMAIL
require_env SUPERADMIN_PASSWORD
require_env SUPERADMIN_NAME
require_env SUPERADMIN_PHONE
[[ ${#SUPERADMIN_PASSWORD} -ge 12 ]] || die "SUPERADMIN_PASSWORD debe tener al menos 12 caracteres."

if [[ ! -d "$HDD_MOUNT" ]]; then
  warn "HDD_MOUNT no existe: $HDD_MOUNT"
  ask "¿Continuar sin HDD? (los backups quedarán en /var/backups)" || die "Aborta."
  BACKUP_DIR_HDD="/var/backups/oxidian"
fi
mkdir -p "$BACKUP_DIR_HDD"

# ─── PASO 1: snapshot del estado actual ───────────────────────────
step "Estado actual del disco y de los volúmenes"

df -h / | tail -n +1
echo
echo "Volúmenes Docker existentes:"
docker volume ls 2>/dev/null | grep -E "oxidian|evolution|chatbot" || echo "  (ninguno todavía)"

# ─── PASO 2: backup pre-reset ─────────────────────────────────────
step "Backup pre-reset al HDD ($BACKUP_DIR_HDD)"

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_DEST="$BACKUP_DIR_HDD/pre-reset-$TS"
mkdir -p "$BACKUP_DEST"

echo "→ Comprobando containers en marcha…"
if docker ps --format '{{.Names}}' | grep -q "^oxidian"; then
  echo "→ pg_dump oxidian…"
  docker exec oxidian-db pg_dump -U oxidian oxidian > "$BACKUP_DEST/oxidian.sql" 2>/dev/null \
    || warn "pg_dump oxidian falló (¿DB down?)"
  echo "→ pg_dump evolution…"
  docker exec evolution-db pg_dump -U evolution evolution > "$BACKUP_DEST/evolution.sql" 2>/dev/null \
    || warn "pg_dump evolution falló"

  echo "→ Tarball de volumes críticos (images, chatbot_data)…"
  for vol in oxidian_images chatbot_data; do
    full=$(docker volume ls -q | grep "_${vol}$" | head -1)
    if [[ -n "$full" ]]; then
      docker run --rm -v "$full":/data -v "$BACKUP_DEST":/backup \
        alpine tar czf "/backup/$vol.tar.gz" -C /data . 2>/dev/null || warn "tarball $vol falló"
    fi
  done
else
  warn "No hay containers oxidian corriendo — skip backup."
fi

echo "✓ Backup en: $BACKUP_DEST"
ls -lh "$BACKUP_DEST" 2>/dev/null | tail -n +2

# ─── PASO 3: teardown del stack actual ────────────────────────────
step "Teardown del stack actual"

if docker ps --format '{{.Names}}' | grep -q "^oxidian"; then
  ask "¿Bajar el stack y borrar todos los volúmenes de datos? (los backups ya están en $BACKUP_DEST)" \
    || die "Aborta antes del teardown."

  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down -v --remove-orphans
  echo "✓ Stack down + volumes borrados."
else
  echo "  (No hay stack corriendo — skip teardown)"
fi

# ─── PASO 4: cleanup de espacio Docker ────────────────────────────
step "Cleanup de espacio Docker (imágenes huérfanas, builds antiguos)"

if ask "¿Ejecutar 'docker system prune -a --volumes -f'? (LIBERA MUCHO ESPACIO PERO BORRA CACHÉ DE BUILDS)"; then
  docker system prune -a --volumes -f
  echo "✓ Docker limpio."
else
  echo "  (Skip system prune)"
fi

# ─── PASO 5: bring up stack limpio ────────────────────────────────
step "Bring up del stack limpio"

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" pull 2>/dev/null || true
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build oxidian
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d

echo "→ Esperando healthy (hasta 3 min)…"
for i in $(seq 1 90); do
  if docker inspect -f '{{.State.Health.Status}}' oxidian 2>/dev/null | grep -q healthy; then
    echo "✓ oxidian healthy."
    break
  fi
  sleep 2
done

# ─── PASO 6: reset de BD + super_admin fresco ─────────────────────
step "Reset de BD y creación de super_admin fresco"

if ask "¿Correr scripts/reset_para_produccion.py ahora? (los datos ya migrados por el bootstrap se BORRARÁN)"; then
  docker exec \
    -e OXIDIAN_CONFIRM_WIPE=YES_WIPE_DATABASE_FOR_PRODUCTION \
    -e SUPERADMIN_EMAIL="$SUPERADMIN_EMAIL" \
    -e SUPERADMIN_PASSWORD="$SUPERADMIN_PASSWORD" \
    -e SUPERADMIN_NAME="$SUPERADMIN_NAME" \
    -e SUPERADMIN_PHONE="$SUPERADMIN_PHONE" \
    oxidian python scripts/reset_para_produccion.py
else
  echo "  (Skip reset — la BD queda como está tras el bring-up)"
fi

# ─── PASO 7: cron de backup diario al HDD ─────────────────────────
step "Registrar cron de backup diario al HDD"

CRON_SCRIPT="$STACK_DIR/oxidian/scripts/backup_to_hdd.sh"
if [[ -f "$CRON_SCRIPT" ]]; then
  CRON_LINE="30 3 * * * $CRON_SCRIPT >> /var/log/oxidian-backup.log 2>&1"
  if crontab -l 2>/dev/null | grep -F "$CRON_SCRIPT" >/dev/null; then
    echo "  Cron ya registrado."
  else
    ( crontab -l 2>/dev/null; echo "$CRON_LINE" ) | crontab -
    echo "✓ Cron añadido: diario 03:30 → $CRON_SCRIPT"
  fi
else
  warn "$CRON_SCRIPT no encontrado — no se registró cron."
fi

# ─── PASO 8: resumen final ────────────────────────────────────────
step "Resumen final"

df -h / | tail -n +1
echo
echo "${BOLD}${GREEN}✓ Producción lista${RESET}"
echo
echo "  Login super_admin:  /auth/login"
echo "  Email:              $SUPERADMIN_EMAIL"
echo "  Phone:              $SUPERADMIN_PHONE"
echo
echo "  Backups en:         $BACKUP_DIR_HDD"
echo "  Backup diario:      cron 03:30"
echo "  Log rotation:       activo (10MB × 3 por servicio)"
echo "  Retención tablas:   activo (worker outbox, 1h)"
echo
echo "Próximos pasos desde el panel:"
echo "  1. /superadmin/config — datos del negocio"
echo "  2. /superadmin/zonas — zonas de entrega"
echo "  3. /admin/productos — dar de alta catálogo"
