#!/usr/bin/env bash
# ══════════════════════════════════════════════════════
# ARRANQUE LOCAL — El Parcerito de Carmona
# Modo: Flask + SQLite  |  Puerto: 5055  |  Debug: ON
# ══════════════════════════════════════════════════════
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OX_DIR="$DIR/oxidian"
VENV="$OX_DIR/.venv"
PORT="${OXIDIAN_PORT:-5055}"

kill_port() {
  local pids=""
  if command -v fuser >/dev/null 2>&1; then
    pids="$(fuser -n tcp "$1" 2>/dev/null || true)"
  fi
  if [ -z "$pids" ] && command -v ss >/dev/null 2>&1; then
    pids="$(ss -ltnp 2>/dev/null | awk -v p=":$1 " '$4~p{print}' \
            | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u)"
  fi
  if [ -n "$pids" ]; then
    echo "  Puerto $1 ocupado (PID $pids) — liberando..."
    kill $pids 2>/dev/null || true; sleep 1
    kill -9 $pids 2>/dev/null || true
  fi
}

# Crear venv si no existe
if [ ! -x "$VENV/bin/python" ]; then
  echo "  Creando entorno virtual Python..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r "$OX_DIR/requirements.txt"
  echo "  Entorno listo."
fi

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

echo ""
echo "  El Parcerito de Carmona — LOCAL"
echo "  ────────────────────────────────"
echo "  BD:   $DIR/db/oxidian.db  (SQLite)"
echo "  URL:  http://localhost:$PORT"
[ -n "$LAN_IP" ] && echo "  LAN:  http://$LAN_IP:$PORT"
echo "  Modo: development (debug ON)"
echo "  Ctrl+C para detener"
echo "  ────────────────────────────────"
echo ""

kill_port "$PORT"
cd "$OX_DIR"
export OXIDIAN_PORT="$PORT"
exec "$VENV/bin/python" app.py
