# M1.5 结构化会话记忆：实施说明

## 已实现

- `custom_components/llama_conversation/edgehome/context/models.py`
  - 强类型 `ConversationState`、`PendingClarification`、`ExecutionResult`；
  - 只保存最近房间、实体、设备类型、动作、参数、待澄清项、待执行计划及最多 5 条结构化结果；
  - 默认 TTL 为 30 分钟。
- `custom_components/llama_conversation/edgehome/context/store.py`
  - SQLite 参数化 SQL；
  - 表 `conversation_state` 支持加载、保存、单会话删除与过期清理；
  - 会话键由 `conversation_id + assistant_id + user_id（可得时）` 的 SHA-256 计算；原始三项同时写入并复核；
  - 默认路径由 `EDGEHOME_CONTEXT_DB_PATH` 配置，Docker 使用 `/runtime/edgehome_context.db`。
- `custom_components/llama_conversation/edgehome/context/resolver.py`
  - “对”仅在候选实体明确且不存在缺失槽位时恢复原目标；否则重复澄清，不猜测。
- `custom_components/llama_conversation/edgehome/context/service.py`
  - 只有 `TaskStatus.SUCCESS` 且执行器返回真实状态回读时更新最近成功上下文；
  - 失败仅追加审计结果，绝不替换最近成功实体、动作或参数；
  - 过滤常见敏感字段名，避免 Token、密码和 API Key 进入状态库。
- `custom_components/llama_conversation/conversation.py`
  - 从 `ConversationInput` 传递稳定的 `conversation_id`、`agent_id` 与可得的 HA 用户 ID。
- `custom_components/llama_conversation/edgehome/coordinator.py`
  - 无会话上下文的代词或相对指令会明确澄清，不再回退模型猜测；
  - 成功、失败和受保护操作确认均写入对应隔离会话。
- `custom_components/llama_conversation/edgehome/understanding/reference_resolver.py`
  - “再亮一点 / 再暗一点 / 关掉吧”只在有最近成功实体时工作；相对亮度优先读取当前 HA 实际亮度。
- `deployment/docker-compose.yml`
  - Home Assistant 挂载 `deployment/runtime` 到 `/runtime`，持久化 SQLite 数据库。
- `scripts/verify_context_memory.ps1`
  - 通过 Home Assistant Assist 正式 HTTP 入口执行真实 MQTT 灯多轮测试；`-RestartHomeAssistant` 覆盖容器重启恢复；结果追加写入 `reports/mqtt_light_acceptance/context_memory_results.jsonl`。

## 不采用的方案

- 不把无限原始聊天记录拼接给本地模型；
- 不使用跨会话或全局 `last_entity` 回退；
- 不在执行失败时写入假成功状态；
- 不把延时任务仅保存在短期会话记忆中。

## 单元测试覆盖

新增并执行：

- `tests/test_context_store.py`
- `tests/test_context_resolver.py`
- `tests/test_slot_inheritance.py`
- `tests/test_reference_resolution.py`
- `tests/test_session_isolation.py`
- `tests/test_context_expiry.py`
- `tests/test_failed_action_context.py`
- `tests/test_context_restart_recovery.py`

结果：9 passed。
