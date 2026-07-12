# M2：Docker MQTT 窗帘验收

**状态：READY_FOR_EXTERNAL_ACCEPTANCE**

`cover.edgehome_living_room_curtain` 已从模板实体迁移为 MQTT Discovery 实体。唯一设备状态来源为 `device-simulator` 的 SQLite；Home Assistant 只消费 MQTT 状态，Agent 只经 Home Assistant 服务发起命令。

## 已验证闭环

```text
Conversation API → EdgeHome 固定计划 → cover.set_cover_position
→ MQTT set_position → 模拟器移动中状态 → SQLite 最终/部分状态
→ MQTT state 与 feedback → HA 回读 → Agent 诚实回复
```

正式 Conversation API 验收共 8 项、全部通过：关闭、打开、设置 35%、卡住、设备拒绝、确认但不变、延迟超时、离线。前三项均记录 `moving` 后达到目标；卡住时设备保留 50% 位置并返回失败，所有故障场景假成功为 0。

独立重启验证：

- 仅重启 Home Assistant：窗帘恢复，位置与模拟器一致。
- 设为 42% 后仅重启 `device-simulator`：SQLite 恢复为 42%，HA 自动同步为 42%。
- 仅重启 Mosquitto：短暂 `unavailable` 后无需人工干预恢复，HA 与模拟器均为 42%。

## 迁移与证据

- `scripts/migrate_template_curtain_to_mqtt.py` 在 HA 停止时备份并删除旧模板窗帘定义及其注册记录。
- `scripts/accept_m2_mqtt_curtain.py` 使用正式 Conversation API 生成原始结果。
- [`results.jsonl`](acceptance/m2_mqtt_curtain/results.jsonl) 与 [`summary.md`](acceptance/m2_mqtt_curtain/summary.md) 保存 8 项故障矩阵结果。

该状态不代表物理设备验收，也不代表 M3 至 M8 已完成。
