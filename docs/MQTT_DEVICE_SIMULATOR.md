# MQTT 独立设备仿真（M1–M4）

## 范围与真实性边界

M1–M4 迁移的实体不是 Home Assistant Helper，也不是物理家电；它们是运行在 Docker 中、带 MQTT 协议、独立 SQLite 状态和故障行为的数字设备仿真器。

| 里程碑 | MQTT 实体 | 设备标识 | 独立状态 |
| --- | --- | --- | --- |
| M1 | `light.edgehome_living_room_main` | `living_room_main_light` | 电源、亮度 |
| M2 | `cover.edgehome_living_room_curtain` | `living_room_curtain` | 位置、目标位置、移动状态 |
| M3 | `climate.edgehome_living_room_ac` | `living_room_ac` | 模式、目标温度、当前温度 |
| M4 | `switch.edgehome_living_room_tv` | `living_room_tv` | 电源、瞬时功耗 |

其正常控制路径固定为：

```text
EdgeHome Agent → Home Assistant light 服务 → MQTT command → device-simulator
→ SQLite 状态变更 → MQTT state → Home Assistant 实体更新 → EdgeHome 回读验证
```

Agent 没有设备管理 API 权限，也不会直接写入模拟器数据库。`http://127.0.0.1:8090` 上的管理 API 仅用于测试人员和测试脚本注入故障或复位状态。

## 组件

- `mqtt`：Eclipse Mosquitto 2，监听本机 `127.0.0.1:1883`。
- `device-simulator`：Python 容器，监听本机 `127.0.0.1:8090`；状态位于 `deployment/runtime/device-simulator/device_state.db`。
- `home-assistant`：通过 MQTT 配置条目连接 `mqtt`，由 retained Discovery 消息创建同名灯实体。

首次迁移时，`scripts/migrate_template_light_to_mqtt.py`、`scripts/migrate_template_curtain_to_mqtt.py`、`scripts/migrate_generic_thermostat_to_mqtt.py` 与 `scripts/migrate_template_tv_to_mqtt.py` 仅在 Home Assistant 停止时删除对应旧实体的归属。Discovery 使用 `default_entity_id` 保持实体 ID 不变；迁移脚本重复执行会报告 `already_migrated`，不会删除现有 MQTT 注册记录。

## 启动与配置

```powershell
docker compose -f deployment/docker-compose.yml up -d mqtt device-simulator home-assistant
$env:HA_TOKEN = '<本机长期访问令牌>'
python scripts/configure_mqtt_integration.py --url http://127.0.0.1:8123 --broker mqtt --port 1883
```

配置脚本调用 Home Assistant 的配置条目 REST 流程，不写入认证信息，也不直接编辑 `.storage`。重复执行会报告 `already_configured`。

## MQTT 主题与状态

每台设备都使用 `edgehome/devices/{device_id}` 命名空间。例如电视为 `living_room_tv`：

- command：`edgehome/devices/living_room_tv/command`
- state：`edgehome/devices/living_room_tv/state`
- availability：`edgehome/devices/living_room_tv/availability`
- discovery：`homeassistant/switch/edgehome_living_room_tv/config`

Discovery 与状态消息均为 retained 消息。设备也监听 `homeassistant/status`，当 Home Assistant 上线时重新发布 Discovery、availability 与当前状态。容器收到 `SIGTERM` 时会为**所有**模拟设备发布 retained `offline` availability；重新启动后再从 SQLite 恢复并发布状态。

## 故障注入与验收

管理 API 支持 `GET /devices`、`GET /devices/{id}`、`GET /events`，以及：

```text
POST /admin/devices/living_room_main_light/fault
{"mode":"offline"}

POST /admin/devices/living_room_main_light/fault
{"mode":"ack_without_state_change"}

POST /admin/devices/living_room_main_light/reset
```

支持的 mode 包括 `none`、`offline`、`delay`、`reject`、`ack_without_state_change`、`invalid_state`、`random_failure` 与 `stuck`。M1 已实际验证：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_mqtt_light.ps1
```

验收脚本会验证亮度 50% 的完整 MQTT 状态回读，并注入 `ack_without_state_change`，确认 Home Assistant 状态不变、EdgeHome 最终轨迹为 `failed`。`offline` 故障会把 HA 实体显示为 `unavailable`。

为适配设备 200 ms 命令延迟与 MQTT/HA 状态传播，执行器仅对已迁移的 MQTT 主灯施加最多 1 秒的反馈等待；等待后仍以回读状态为准，绝不因服务调用返回而宣布成功。
