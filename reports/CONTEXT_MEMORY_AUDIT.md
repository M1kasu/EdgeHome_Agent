# M1.5 会话上下文链路审计

审计日期：2026-07-12
范围：Home Assistant Assist 正式 REST 入口、`llama_conversation` EdgeHome 快速路径和当前持久化实现。

## 结论

当前项目不是完全没有上下文代码，但尚不具备可交付的结构化会话记忆能力。

Home Assistant 不会在后端主动丢失 `conversation_id`：调用方必须在下一轮把上一轮响应中的 ID 传回。当请求省略该字段时，EdgeHome 会安全地新建会话，因而不会继承旧上下文。这是开发者工具或一次性 REST 调用表现为“没有记忆”的直接原因；不能通过全局 `last_entity` 补救，否则会造成跨用户串话。

当前实现还存在以下交付缺口：状态保存到 Home Assistant JSON Store 而非 SQLite；TTL 为 12 小时而非 30 分钟；没有用户/助手维度；澄清状态不包含意图、候选实体和缺失槽位；成功结果只保留计划，没有保存设备回读后的结构化实际结果；测试没有通过正式入口覆盖重启、隔离和失败不污染上下文。

## 已执行的正式入口复现

请求入口：`POST /api/conversation/process`，使用当前默认 EdgeHome Agent。

1. 首轮发送“把客厅主灯亮度调到”（省略 `conversation_id`）。
2. 响应得到 `edgehome-29a1e8f47677474c813d6fb93dc9102c`，并要求补充目标数值。
3. 第二轮发送“50%”并传回该 ID。
4. 响应的 `conversation_id` 仍为 `edgehome-29a1e8f47677474c813d6fb93dc9102c`；主灯实际回读为 `on`、亮度 `128`（50%）。
5. 设备随后已重置，未把该审计操作作为验收证据。

因此：会话 ID 在 HA 协议中可稳定传递；无 ID 请求被视为新的、隔离的一次性会话。后续实现应记录该事实，并让状态读取严格绑定 `conversation_id + assistant_id + user_id（可得时）`。

## 当前调用链与定位

| 环节 | 当前实现 | 证据 | 结论 |
| --- | --- | --- | --- |
| HA 输入 | `ConversationInput.conversation_id` 进入 Agent | `custom_components/llama_conversation/conversation.py:104-116` | 普通模型路径会将它传给 HA ChatLog。 |
| EdgeHome 入口 | 读取原 ID；缺失时生成 `edgehome-<uuid>` | `conversation.py:278-301` | 防止 `None` 和跨一次性请求串话，但调用方不回传时必然成为新会话。 |
| 会话处理 | 用 ID 查找澄清、引用和能耗状态 | `edgehome/coordinator.py:141-240` | 快速路径已有单轮到下一轮的有限承接。 |
| 澄清恢复 | 简单拼接“原句 + 本轮文本” | `edgehome/coordinator.py:507-511` | 无意图/槽位模型，无法可靠处理“对”、冲突和多候选设备。 |
| 当前状态模型 | `last_plan`、`last_entity_id`、参数和少量 pending 字段 | `edgehome/context/dialogue_state.py:43-63` | 缺少 `user_id`、房间、设备类型、动作、最近真实执行结果、当前目标和待执行计划。 |
| 当前持久化 | HA `Store` JSON 文件，内存缓存，12 小时 TTL | `edgehome/context/store.py:16-60, 137-149` | 容器重启可恢复，但不满足 SQLite、30 分钟、可审计查询的要求。 |
| 成功写入 | 仅在报告成功后记录计划 | `edgehome/coordinator.py:176-199, 225-240` | “成功后再写入”方向正确，但没有保存 MQTT/HA 回读的实际值。 |
| 引用解析 | 使用上一计划推导“它 / 也一样 / 再亮或暗一点” | `edgehome/understanding/reference_resolver.py:18-90` | 能覆盖窄场景，但相对亮度以旧请求参数计算，未优先读取当前真实亮度。 |
| 原有测试 | 直接调用协调器并手工传入固定 ID | `tests/llama_conversation/edgehome/test_coordinator.py:157-195` | 不能证明 Assist 正式入口、ID 回传、重启恢复或会话隔离。 |
| M1 验证脚本 | 手工写死 `conversation_id` | `scripts/verify_mqtt_light.ps1:13-20` | 仅覆盖 MQTT 闭环，不覆盖真实多轮会话协议。 |

## 存储核查

运行配置内的 `.storage/edgehome_dialogue_state` 是有效 JSON，且已存在按会话 ID 保存的历史记录。此前 PowerShell 的默认编码无法解析 UTF-8 文件是本地读取方式问题，不是文件损坏。仓库源码与运行中已部署组件的关键文件 SHA-256 一致。

这说明问题不是“部署了旧代码”，而是当前设计的能力边界与实际客户端是否回传 ID 均不可观测，且无法满足 M1.5 的持久化和安全要求。

## M1.5 实施边界

1. 新增 `edgehome/context/models.py`，以强类型 `ConversationState`、`PendingClarification` 和 `ExecutionResult` 保存有限结构化状态；不保存 Token、密码、账户信息、模型原始输出或无限聊天记录。
2. 将 `edgehome/context/store.py` 替换为参数化 SQL 的 SQLite 存储，默认 30 分钟 TTL，支持加载、保存、清除和过期清理；Docker 默认数据库位于 `deployment/runtime/edgehome_context.db`。
3. 新增 `edgehome/context/resolver.py` 与 `service.py`：把代词、槽位继承和澄清恢复限制为唯一且类型兼容的实体；不唯一、冲突、失败或过期时只澄清，不猜测。
4. 仅在工具执行成功且 Home Assistant 已回读到目标状态时更新“最近成功操作”；失败只追加失败记录，绝不覆盖最近成功上下文。
5. 在 Agent 入口从 `ConversationInput` 提取 `conversation_id`、`agent_id` 和可得的 `context.user_id`，形成隔离会话键；缺失 ID 的请求继续新建隔离会话，不引入全局回退。
6. 增加单元测试、容器重启恢复测试和通过 `/api/conversation/process` 的真实 MQTT 端到端测试；输出验证与未验证边界报告。

## 当前状态

`M1` 仍为“实现完成，等待外部黑盒验收”；PR #12 不应合并。

本审计完成后开始实施 M1.5；在测试和真实入口验证完成前，不宣称系统已具备通用多轮对话能力。
