# M1 MQTT 主灯黑盒验收汇总

- 生成时间：2026-07-13T02:39:13.927671+00:00
- 总用例：31
- 计划用例：31
- 通过：31
- 失败：0
- 假成功：0
- 中止原因：无
- 入口：Home Assistant `/api/conversation/process`；MQTT 仅作只读订阅。
- 证据：`results.jsonl`、`mqtt_commands.jsonl`、`mqtt_states.jsonl`、`simulator_events.jsonl`、`ha_states.jsonl`、`conversation_states.jsonl`。

状态：`INTEGRATION_VERIFIED`（只有通过后才可等待外部独立验收）。
