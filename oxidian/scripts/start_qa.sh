#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${QA_PORT:-5071}"
PASSWORD="${QA_PASSWORD:-Oxidian-QA-2026}"
IMAGE="${QA_IMAGE:-oxidian:latest}"
NETWORK="oxidian_oxidian-test-net"
DB="postgresql://oxidian:testpassword@oxidian-test-db:5432/oxidian_test"

cd "$ROOT"
docker compose -f docker-compose.test.yml up -d oxidian-test-db >/dev/null
until [[ "$(docker inspect -f '{{.State.Health.Status}}' oxidian-test-db 2>/dev/null || true)" == "healthy" ]]; do sleep 1; done

run_python() {
  docker run --rm --entrypoint python --network "$NETWORK" \
    -v "$ROOT:/app" -w /app \
    -e PYTHONPATH=/app -e APP_ENV=production -e SESSION_COOKIE_SECURE=0 \
    -e SECRET_KEY=qa-local-only -e DATABASE_URL="$DB" \
    -e OXIDIAN_KEY=qa-key -e BOT_API_KEY=qa-bot -e BOT_PANEL_KEY=qa-panel \
    -e OWNER_NUMBER=34600000000 -e SUPERADMINS=34600000000 \
    -e SEED_PASSWORD="$PASSWORD" "$IMAGE" "$@"
}

run_python scripts/prebootstrap_schema.py
run_python scripts/apply_schema_migrations.py
run_python scripts/cosmos_bootstrap.py
run_python scripts/seed_matrix.py
run_python scripts/seed_operational_matrix.py
run_python -c "
import os
from app import create_app
from extensions import db
from models import User
a=create_app()
with a.app_context():
    for u in User.query.filter(User.email.like('qa.%@elparcerito.local')).all():
        if u.rol in {'super_admin','admin','cocina','preparacion','repartidor'}:
            u.set_password(os.environ['SEED_PASSWORD'])
            u.activo=True
    db.session.commit()
"
run_python scripts/visual_operational_fixtures.py create

docker rm -f oxidian-qa >/dev/null 2>&1 || true
docker run -d --name oxidian-qa --entrypoint gunicorn --network "$NETWORK" \
  -p "$PORT:5000" -v "$ROOT:/app" -w /app \
  -e PYTHONPATH=/app -e APP_ENV=production -e SESSION_COOKIE_SECURE=0 \
  -e SECRET_KEY=qa-local-only -e DATABASE_URL="$DB" -e OXIDIAN_MFA_ENFORCED=0 \
  -e OXIDIAN_KEY=qa-key -e BOT_API_KEY=qa-bot -e BOT_PANEL_KEY=qa-panel \
  -e OWNER_NUMBER=34600000000 -e SUPERADMINS=34600000000 \
  "$IMAGE" --worker-class gthread --threads 8 --timeout 120 \
  -b 0.0.0.0:5000 'app:create_app()' >/dev/null

for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1 && break; sleep 1; done
printf '\nQA listo: http://127.0.0.1:%s\n' "$PORT"
printf 'Contraseña común: %s\n' "$PASSWORD"
printf 'Roles: qa.superadmin, qa.admin, qa.cocina, qa.preparacion y qa.repartidor @elparcerito.local\n'
