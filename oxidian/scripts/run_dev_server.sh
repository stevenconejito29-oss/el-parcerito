#!/usr/bin/env bash

# Levanta el servidor Flask en localhost:5000 para testing real
# Usa testing mode (en-memoria con check_same_thread=False)
# Uso: ./scripts/run_dev_server.sh [--port 5000]

PORT=5000

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2;;
    *) echo "Uso: $0 [--port 5000]"; exit 1;;
  esac
done

echo "Iniciando servidor Flask en http://localhost:${PORT}"
echo "Modo: testing (SQLite en memoria con check_same_thread=False)"
echo "Para detenerlo: Ctrl+C"

cd "$(dirname "$0")/.."

# Activar venv si existe
if [ -d ".venv" ]; then
  source .venv/bin/activate
fi

# Crear servidor en modo testing (en memoria, sin necesidad de fichero DB)
python - "$PORT" << 'EOFPYTHON'
import sys
import os
sys.path.insert(0, '.')
os.environ["OXIDIAN_SKIP_STARTUP_DB"] = "1"
os.environ["ADMIN_EMAIL"] = os.environ.get("SUPERADMIN_EMAIL", "superadmin@oxidian.com")

from app import create_app, _seed_admin

app = create_app("testing")

# Seed only super admin and basic site defaults
with app.app_context():
    from extensions import db
    db.drop_all()
    db.create_all()
    _seed_admin()
    print("✓ Base de datos limpia: solo super admin sembrado")

app.run(host="0.0.0.0", port=int(sys.argv[1] if len(sys.argv) > 1 else 5000), debug=True)
EOFPYTHON
