#!/usr/bin/env bash
# Contrato único de pruebas para desarrollo y CI.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${OXIDIAN_PYTHON:-python3}"

if ! "$PYTHON_BIN" -c 'import flask_sqlalchemy, flask_login, flask_wtf' >/dev/null 2>&1; then
    echo "[ERROR] Faltan dependencias Python." >&2
    echo "Crea .venv e instala: .venv/bin/pip install -r oxidian/requirements.txt" >&2
    exit 2
fi

echo "[1/4] Compilación Python"
"$PYTHON_BIN" -m compileall -q "$ROOT_DIR/oxidian"

echo "[2/4] Suite Flask y reglas de negocio"
(
    cd "$ROOT_DIR/oxidian"
    "$PYTHON_BIN" -m unittest discover -s tests -p 'test_*.py'
)

echo "[3/4] Sintaxis del bot"
node --check "$ROOT_DIR/chat/bot.js"

echo "[4/4] Suite del bot WhatsApp"
(
    cd "$ROOT_DIR/chat"
    npm test
)
