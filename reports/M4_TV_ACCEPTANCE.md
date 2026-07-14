# M4：Docker MQTT 电视与功耗验收

**状态：READY_FOR_EXTERNAL_ACCEPTANCE**

`switch.edgehome_living_room_tv` 已从模板开关迁移为 MQTT Discovery 实体。电视的开关状态与功耗只由 `device-simulator` 的 SQLite 数据库维护；Home Assistant 仅消费 MQTT state、availability 与只读协议反馈。`sensor.edgehome_living_room_tv_power` 展示设备上报的瞬时功耗：开启约 95 W，关闭待机约 1.5 W。

## 本地集成验收

- 正式 Home Assistant Conversation API 黑盒用例 12/12 通过。
- 正常打开、关闭均验证 Agent → HA → MQTT → SQLite → MQTT state → HA 回读；状态和功耗必须同时匹配。
- `reject`、`ack_without_state_change`、`invalid_state`、`delay`、`offline` 五类故障均不允许假成功。
- HA、设备模拟器、Mosquitto 分别重启后，电视恢复为 SQLite 中的开启状态与 95 W；停止模拟器时 HA 先显示 `unavailable`。
- 模拟器收到 Docker `SIGTERM` 时会显式发布所有设备的 retained `offline` availability，避免只有首个设备离线而其他实体残留为可用。
- 路由器和冰箱没有纳入电视 MQTT 控制范围，仍是受保护实体；离家策略将在 M6 单独验证。

原始证据位于 [`acceptance/m4_mqtt_tv`](acceptance/m4_mqtt_tv)：`results.jsonl`、`simulator_events.jsonl`、`ha_states.jsonl`、`hardcoding_audit.json` 和 `summary.md`。本结论是本地集成验收，不替代独立外部验收。
