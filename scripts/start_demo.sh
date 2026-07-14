#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
docker compose -f deployment/docker-compose.yml up -d --build
"$ROOT/scripts/healthcheck.sh"
