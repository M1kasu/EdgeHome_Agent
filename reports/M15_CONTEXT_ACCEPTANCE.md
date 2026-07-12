# M1.5：结构化会话上下文验收

**状态：READY_FOR_EXTERNAL_ACCEPTANCE**

本阶段将 EdgeHome 的会话记忆从保存原始文本后猜测，改为可持久化、可隔离的结构化状态。它只在设备动作经过验证成功后更新；查询、澄清和失败动作均不把未验证的目标写成历史事实。

## 交付内容

- `ConversationState` 记录最近一次已验证动作、请求参数和实际 HA 回读状态。
- 会话状态按 `conversation_id`、`user_id`、`assistant_id` 隔离并写入 SQLite；TTL 由 `EDGEHOME_CONTEXT_TTL_SECONDS` 配置，默认 1,800 秒。
- 通用语义规则支持历史设置、当前状态、最近实体、最近成功结果查询，以及“再调高 N%”“再调低 N%”“关掉吧”等引用表达；相对亮度始终从实时 HA 状态计算。
- 所有澄清写入和能耗多轮状态写入均传播用户与 Assistant 身份，避免匿名上下文混用。
- 新增 `scripts/accept_m15_context.py`。脚本只通过正式 Home Assistant `POST /api/conversation/process` 调用系统，对 MQTT 仅只读订阅，并保存原始 JSONL 证据。

## 黑盒验收结果

运行命令：

```powershell
& .\.venv\Scripts\python.exe .\scripts\accept_m15_context.py
```

最终结果为 **20/20 通过，0 失败**：

|类别|覆盖项|结论|
|---|---|---|
|同一会话链|35% 设置、历史设置、当前亮度、最近实体、最近成功、提高 10%、引用关灯|全部正确；查询未发送 MQTT 命令|
|相对调节|62% 后查询、再降低 7%|最终实际亮度 55%，基于实时状态计算|
|会话隔离|新会话历史查询；会话 A 设置后会话 B 相对调暗|均澄清且未发控制命令|
|失败不污染|70% 成功后注入 `ack_without_state_change` 的 30% 失败，再调暗|失败没有覆写历史；下一步从实际 70% 调至 60%|
|HA 重启|50% 成功后仅重启 Home Assistant，再调暗|状态恢复且调至 40%|
|TTL|临时 TTL=2 秒，48% 成功后等待 3 秒再查询|上下文过期、请求澄清、未发送控制命令|

验收产生 12 条 MQTT command、12 条 MQTT state、24 条模拟器事件和 20 条结构化会话状态快照。任何查询、隔离或过期场景都没有 MQTT 控制命令；故障场景未报告成功。

## 回归与部署检查

- Windows / Python 3.14 全量测试：`215 passed`（5 条第三方弃用警告）。Windows 测试 shim 已纳入 `tests/windows_test_shims/`，不再依赖临时目录。
- 演示 smoke：`12 passed`。
- `docker compose -f deployment/docker-compose.yml config -q` 通过。
- `scripts/audit_hardcoding.py`：0 error、6 条既有 warning；完整结果见证据目录。

## 原始证据

- [`summary.md`](acceptance/m15_context/summary.md)
- [`results.jsonl`](acceptance/m15_context/results.jsonl)
- [`mqtt_commands.jsonl`](acceptance/m15_context/mqtt_commands.jsonl)
- [`mqtt_states.jsonl`](acceptance/m15_context/mqtt_states.jsonl)
- [`simulator_events.jsonl`](acceptance/m15_context/simulator_events.jsonl)
- [`ha_states.jsonl`](acceptance/m15_context/ha_states.jsonl)
- [`conversation_states.jsonl`](acceptance/m15_context/conversation_states.jsonl)
- [`hardcoding_audit.json`](acceptance/m15_context/hardcoding_audit.json)（0 error、6 条既有 warning）

该结论只代表本地集成验收通过，不替代独立外部验收，也不表示 M2 至 M8 已完成。
