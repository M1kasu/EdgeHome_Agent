# M3：Docker MQTT 空调验收

**状态：READY_FOR_EXTERNAL_ACCEPTANCE**

`climate.edgehome_living_room_ac` 已从 Generic Thermostat 迁移为 MQTT Climate。设备模式、目标温度和当前温度只由模拟器 SQLite 维护，Home Assistant 消费 MQTT 状态，Agent 经正式 Conversation API 发起受限计划。

## 验收结果

- 修复“空调调到 25 度”未进入快速路径的问题：现在按通用的空调、数值与温度单位语法识别，不依赖完整句子。
- 25°C Agent 指令：HA 和 SQLite 的目标温度均为 25°C。
- `heat` 模式：HA 与 SQLite 均为 `heat`。
- 正式 API 故障矩阵 5/5：正常设置、拒绝、确认但不变、延迟、离线；故障下假成功为 0。
- Home Assistant 重启后保持 24°C；模拟器重启后从 SQLite 恢复 26°C；Mosquitto 重启后自动重连并保持 26°C。
- 演示部署固定为公制 °C，避免中文温度请求被错误解释为华氏度。

原始 API 结果见 [`results.jsonl`](acceptance/m3_mqtt_climate/results.jsonl) 和 [`summary.md`](acceptance/m3_mqtt_climate/summary.md)。

该结论仅为本地集成验收，不替代独立外部验收，也不表示 M4 至 M8 已完成。
