#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker rm -f oxidian-qa >/dev/null 2>&1 || true
cd "$ROOT"
docker compose -f docker-compose.test.yml down -v
