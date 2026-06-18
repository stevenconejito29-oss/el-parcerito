#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# Oxidian — Script de instalación para Cosmos OS
# Uso: bash scripts/cosmos_install.sh
# ════════════════════════════════════════════════════════════════
set -euo pipefail
VERDE="\033[0;32m" AMARILLO="\033[0;33m" ROJO="\033[0;31m" RESET="\033[0m"
ok()   { echo -e "${VERDE}✓ $*${RESET}"; }
warn() { echo -e "${AMARILLO}⚠ $*${RESET}"; }
fail() { echo -e "${ROJO}✗ $*${RESET}"; exit 1; }

echo ""
echo "════════════════════════════════════════"
echo "   OXIDIAN — Instalación en Cosmos OS"
echo "════════════════════════════════════════"
echo ""

# ── Verificar Docker ────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || fail "Docker no encontrado. Instala Docker primero."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 no encontrado."
ok "Docker y Docker Compose disponibles"

# ── Verificar que estamos en el directorio correcto ─────────────
[ -f "app.py" ] && [ -f "Dockerfile" ] || fail "Ejecuta este script desde la raíz del proyecto oxidian/"

# ── Crear .env si no existe ──────────────────────────────────────
if [ ! -f ".env.cosmos.local" ]; then
  cp .env.production.example .env.cosmos.local
  warn "Creado .env.cosmos.local desde la plantilla."
  warn "DEBES editar .env.cosmos.local antes de continuar:"
  warn "  → SECRET_KEY, contraseñas de BD, API keys, número de WhatsApp"
  echo ""
  read -p "¿Has editado .env.cosmos.local? [s/N]: " resp
  [[ "$resp" =~ ^[sS]$ ]] || { warn "Edita el archivo y vuelve a ejecutar."; exit 0; }
fi

# ── Verificar variables críticas ────────────────────────────────
set -a
source .env.cosmos.local 2>/dev/null || true
set +a

check_var() {
  local val="${1:-}" name="$2" bad="$3"
  if [ -z "$val" ] || [ "$val" = "$bad" ]; then
    fail "Variable $name no configurada o usando valor por defecto."
  fi
  ok "$name configurada"
}

check_len() {
  local val="${1:-}" name="$2" min_len="$3"
  if [ "${#val}" -lt "$min_len" ]; then
    fail "Variable $name debe tener al menos $min_len caracteres."
  fi
}

check_var "${SECRET_KEY:-}"             "SECRET_KEY"             "CAMBIA_ESTO_POR_UNA_CLAVE_ALEATORIA_DE_64_CHARS"
check_var "${SEED_PASSWORD:-}"          "SEED_PASSWORD"          "CAMBIA_ESTO_POR_CONTRASENA_FUERTE"
check_var "${OXIDIAN_DB_PASSWORD:-}"    "OXIDIAN_DB_PASSWORD"    "CAMBIA_ESTO_POR_CONTRASENA_BD_FUERTE"
check_var "${EVOLUTION_DB_PASSWORD:-}"  "EVOLUTION_DB_PASSWORD"  "CAMBIA_ESTO_CONTRASENA_EVOLUTION_BD"
check_var "${EVOLUTION_API_KEY:-}"      "EVOLUTION_API_KEY"      "CAMBIA_ESTO_API_KEY_EVOLUTION"
check_var "${BOT_API_KEY:-}"            "BOT_API_KEY"            "CAMBIA_ESTO_BOT_KEY"
check_var "${BOT_PANEL_KEY:-}"          "BOT_PANEL_KEY"          "CAMBIA_ESTO_PANEL_KEY"
check_var "${WEBHOOK_SECRET:-}"         "WEBHOOK_SECRET"         "CAMBIA_ESTO_WEBHOOK_SECRET"
check_var "${OWNER_NUMBER:-}"           "OWNER_NUMBER"           "34XXXXXXXXX"
check_var "${SUPERADMINS:-}"            "SUPERADMINS"            "34XXXXXXXXX"
check_var "${OXIDIAN_PUBLIC_URL:-}"     "OXIDIAN_PUBLIC_URL"     "https://tudominio.com"
check_var "${TIENDA_URL:-}"             "TIENDA_URL"             "https://tudominio.com"

check_len "${SECRET_KEY:-}" "SECRET_KEY" 32
check_len "${SEED_PASSWORD:-}" "SEED_PASSWORD" 12
check_len "${BOT_API_KEY:-}" "BOT_API_KEY" 16
check_len "${BOT_PANEL_KEY:-}" "BOT_PANEL_KEY" 16
check_len "${WEBHOOK_SECRET:-}" "WEBHOOK_SECRET" 32

if [ "${SIMULATE_EVO_SEND:-0}" = "1" ]; then
  warn "SIMULATE_EVO_SEND=1 → WhatsApp NO enviará mensajes reales."
  warn "Cambia a SIMULATE_EVO_SEND=0 cuando conectes WhatsApp."
fi

if [ "${SESSION_COOKIE_SECURE:-1}" != "1" ]; then
  warn "SESSION_COOKIE_SECURE no está en 1. En producción con HTTPS debe ser 1."
fi

echo ""
echo "Variables críticas: OK"
echo ""

# ── Arrancar el stack ────────────────────────────────────────────
COMPOSE_FILE="${1:-cosmos-compose.yml}"
echo ""
echo "Iniciando stack: $COMPOSE_FILE"
docker compose --env-file .env.cosmos.local -f "$COMPOSE_FILE" up -d --build \
  || fail "Error al levantar el stack"

# ── Esperar a que Oxidian esté listo ────────────────────────────
PORT="${PUBLIC_PORT:-5070}"
echo ""
echo "Esperando a que el gateway único arranque en :$PORT..."
for i in $(seq 1 45); do
  if curl -fs "http://localhost:$PORT/gateway-health" >/dev/null 2>&1; then
    ok "Gateway responde en http://localhost:$PORT"
    break
  fi
  if [ "$i" -eq 45 ]; then
    warn "Gateway no responde después de 90s. Revisa los logs:"
    warn "  docker compose --env-file .env.cosmos.local -f $COMPOSE_FILE logs gateway oxidian"
    fail "El gateway no quedó disponible; instalación detenida."
  fi
  printf "."
  sleep 2
done

# La base no publica puertos en el host. Las comprobaciones se ejecutan dentro
# de la red privada Docker, igual que en producción.
docker compose --env-file .env.cosmos.local -f "$COMPOSE_FILE" \
  exec -T oxidian python scripts/predeploy_check.py \
  || fail "Predeploy check falló dentro del contenedor."

echo ""
echo "════════════════════════════════════════"
echo "   INSTALACIÓN COMPLETADA"
echo "════════════════════════════════════════"
echo ""
echo "  Gateway local:     http://localhost:$PORT"
echo "  Tienda pública:    ${TIENDA_URL:-http://localhost:$PORT}"
echo "  Panel admin:       ${TIENDA_URL:-http://localhost:$PORT}/admin/dashboard"
echo "  Super admin:       ${TIENDA_URL:-http://localhost:$PORT}/superadmin/dashboard"
echo ""
echo "  Credenciales:"
echo "    Email:    ${ADMIN_EMAIL:-admin@oxidian.local}"
echo "    Password: [valor de SEED_PASSWORD en .env.cosmos.local]"
echo ""
echo "  Siguiente paso → Conectar WhatsApp:"
echo "    1. Abre ${TIENDA_URL:-http://localhost:$PORT}/superadmin/chatbot"
echo "    2. Escanea el QR con tu teléfono"
echo "    3. Pon SIMULATE_EVO_SEND=0 en .env.cosmos.local"
echo "    4. Reinicia: docker compose --env-file .env.cosmos.local -f $COMPOSE_FILE restart oxidian"
echo ""
