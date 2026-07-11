#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"
. ./.env
curl --fail --silent --show-error "http://127.0.0.1:${HA_PORT:-8123}/"
curl --fail --silent --show-error "http://127.0.0.1:${MODEL_PORT:-8080}/health"
