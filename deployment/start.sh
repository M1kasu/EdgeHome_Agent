#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"
test -f .env || cp .env.example .env
docker compose --env-file .env up -d --build
