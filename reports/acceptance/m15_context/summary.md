# M1.5 结构化会话黑盒验收汇总

- 生成时间：2026-07-12T10:26:23.005125+00:00
- 总用例：20
- 通过：20
- 失败：0
- 中止原因：无
- 入口：Home Assistant `/api/conversation/process`；MQTT 仅作只读订阅。
- 证据：`results.jsonl`、`mqtt_commands.jsonl`、`mqtt_states.jsonl`、`simulator_events.jsonl`、`ha_states.jsonl`、`conversation_states.jsonl`。

状态：`INTEGRATION_VERIFIED`。
