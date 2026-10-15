#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_FILE=compose.yaml:compose.prod.yaml
archive="study-$(date -u +%Y%m%dT%H%M%SZ).db"
docker compose run --rm --no-deps backup python -m study.manage backup "/backups/$archive"
echo "Verified backup: $archive"
