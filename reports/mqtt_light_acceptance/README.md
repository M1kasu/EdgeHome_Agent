# MQTT 灯光与会话记忆运行证据

本目录存放不含凭据的真实运行证据。当前提交的 `context_memory_results.jsonl` 由 `scripts/verify_context_memory.ps1` 产生，每行记录一次正式 Assist HTTP 入口测试：

- 用户输入；
- 返回和延续的 `conversation_id`；
- 三轮 Home Assistant 实体状态与亮度；
- 会话隔离结果；
- 是否执行了 Home Assistant 重启恢复测试。

该文件不包含 Home Assistant Token、密码、API Key、模型文件或原始 Home Assistant 配置。

M1 的完整外部黑盒验收将来应在此目录补充 MQTT command/state、模拟器事件、SQLite 前后状态和 HA 前后状态等独立证据。当前证据不等同于 M1 最终通过。
