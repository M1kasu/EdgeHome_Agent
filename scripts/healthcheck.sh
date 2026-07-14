#!/usr/bin/env sh
set -eu

HA_URL=${HA_URL:-http://127.0.0.1:8123}
MODEL_URL=${MODEL_URL:-http://127.0.0.1:8080}
curl --fail --silent --show-error "$HA_URL/" >/dev/null
curl --fail --silent --show-error "$MODEL_URL/health" >/dev/null
printf '%s\n' 'Home Assistant: HTTP 200'
printf '%s\n' 'llama.cpp: HTTP 200'
