# M0：会话记忆失败根因

**审计状态：READY_FOR_EXTERNAL_ACCEPTANCE**
**复现时间：2026-07-12 09:05 UTC**
**调用入口：** Home Assistant `POST /api/conversation/process`
**Agent：** `conversation.home_llama_3_2_3b_edgehome_home_llama_3_2_3b_edgehome`
**会话：** `m0-context-repro-20260712`

## 黑盒复现结果

测试前通过模拟器管理接口重置 M1 主灯。三轮均使用相同的 `conversation_id`，没有使用测试替身或直接修改 HA 实体状态。

|轮次|输入|预期|实际结果|协议/状态证据|
|---|---|---|---|---|
|1|`把客厅主灯调到35%`|调用主灯并回读 35%。|成功回复“已设置亮度客厅主灯”。|模拟器收到 `brightness: 89, state: ON`，随后发布 `brightness: 89, power: ON`；HA 从 204/255 变为 89/255。|
|2|`刚才设置的是多少？`|基于同会话返回最近一次已验证设置；此例应说明请求值为 35%，并可同时给出当前回读值。|错误澄清“没有找到已开放给对话控制的设备，请确认设备名称”。|HA 仍为 `on, brightness=89`；模拟器没有新 command/state 事件。|
|3|`再调高10%`|以最新实际亮度为基准，生成 `45%` 的受限计划，执行并回读约 115/255。|再次错误澄清“没有找到已开放给对话控制的设备”。|HA 与模拟器继续保持 89/255；没有第二次 MQTT command。|

第 1 轮确实经过了 M1 链路；第 2、3 轮不是设备故障或 MQTT 超时，而是对话语义路由错误。因此不能把现有 M1.5 标记为已验收。

## 数据库核验

对 HA 容器内 `/runtime/edgehome_context.db` 的只读查询证明，带 HA 用户和 Agent 身份的会话记录已经正确保存：

```text
last_entity_id = light.edgehome_living_room_main
last_action    = set_light_brightness
last_parameters = {entity_id: light.edgehome_living_room_main, brightness: 35}
recent_results[0].actual.brightness = 89
recent_results[0].success = true
```

因此根因**不是** SQLite 未持久化，也不是首轮状态回读失败。

同时发现了一个同 `conversation_id`、但没有 `user_id` 与 `assistant_id` 的额外会话行。这是身份参数在部分澄清写入路径丢失的旁证，虽然它不是第 2 轮首发失败的唯一原因。

## 根因链

1. `coordinator.py:203-248` 正确读到了带身份的会话状态，并把文本交给 `ReferencePlanResolver`。
2. `reference_resolver.py:23-44` 没有“查询最近一次已验证设置”的语义；它只识别少量关闭、隐式加减亮度、复制等短语。因此第 2 轮返回 `None`。
3. 随后 `ChineseFastRouter` 把问号/“多少”归类为 `QUERY_STATE`（`intent_classifier.py:16,32-35`），但原句没有设备别名，实体解析在 `router.py:48-55` 失败，输出 `entity_name_not_found`。
4. `coordinator.py:298-308` 将这个本应由历史状态回答的问题登记成“缺少 value 的设备控制澄清”。第 299 行未传递会话身份，先创建了匿名状态；第 301 行才写入带身份状态。
5. 第 3 轮先被 `_async_resume_clarification()` 当作第 2 轮的槽位补充，合并成“刚才设置的是多少？ 再调高10%”。随后引用解析仍不支持“调高 + 显式百分比”，再次回落到无实体的查询澄清。
6. `_relative_brightness()` 仅支持固定 `±10` 的“再亮/再暗一点”短语，既不提取 `10%`，也不把最近**实际**亮度作为一个显式、可审计的会话查询结果返回。

## 必须修复的行为契约

- 历史查询必须只读取同一 `(conversation_id, user_id, assistant_id)` 下最近一次**已验证**动作，回答中区分“请求值”和“实际回读值”。
- 相对亮度命令必须使用结构化解析：方向、显式百分比增量、指代实体和当前实际亮度；缺任一关键条件时澄清，绝不猜测。
- 所有 `DialogueStateStore`/`ConversationStateStore` 的读写都必须携带同一身份参数；删除匿名写入分支。
- 待澄清状态必须携带 intent、缺失槽位和原子输入；不应把信息查询错误地当作控制槽位补充，也不应把两个用户回合拼成一个不可解析命令。
- 修复后用正式 Conversation API 覆盖：成功链、失败不覆盖、会话隔离、HA 重启恢复、过期、同义改写、无历史会话和显式增量边界值。
