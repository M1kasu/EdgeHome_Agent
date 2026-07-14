#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
HA_URL=${HA_URL:-http://127.0.0.1:8123}
SIMULATOR_URL=${EDGEHOME_SIMULATOR_URL:-http://127.0.0.1:8090}
"$PYTHON" "$ROOT/scripts/install_edgehome_demo.py" --config-dir "$ROOT/deployment/homeassistant"
: "${HA_TOKEN:?请设置仅当前会话有效的 HA_TOKEN 后运行 reset_demo.sh}"
curl --fail --silent --show-error -X POST "$HA_URL/api/services/script/turn_on" \
  -H "Authorization: Bearer $HA_TOKEN" -H 'Content-Type: application/json' \
  --data '{"entity_id":"script.edgehome_reset_demo_environment"}' >/dev/null
for device in living_room_main_light living_room_curtain living_room_ac living_room_tv; do
  curl --fail --silent --show-error -X POST "$SIMULATOR_URL/admin/devices/$device/reset" \
    -H 'Content-Type: application/json' --data '{}' >/dev/null
done
printf '%s\n' '已请求重置 EdgeHome 虚拟演示环境。'
