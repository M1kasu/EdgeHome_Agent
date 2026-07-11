#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
HA_URL=${HA_URL:-http://127.0.0.1:8123}
"$PYTHON" "$ROOT/scripts/install_edgehome_demo.py" --config-dir "$ROOT/deployment/homeassistant"
: "${HA_TOKEN:?请设置仅当前会话有效的 HA_TOKEN 后运行 reset_demo.sh}"
curl --fail --silent --show-error -X POST "$HA_URL/api/services/script/turn_on" \
  -H "Authorization: Bearer $HA_TOKEN" -H 'Content-Type: application/json' \
  --data '{"entity_id":"script.edgehome_reset_demo_environment"}' >/dev/null
printf '%s\n' '已请求重置 EdgeHome 虚拟演示环境。'
