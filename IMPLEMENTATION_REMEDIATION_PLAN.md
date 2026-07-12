# EdgeHome 实施整改计划

**计划版本：M0 审计后，2026-07-12**
**当前分支：** `agent/phase14-competition-package`
**总体规则：** 每个里程碑先实现、再以正式 Home Assistant Conversation API 做黑盒验收、保存原始证据、独立提交并推送；PR 保持 Draft，不合并。状态只可使用本文件定义的枚举。

## 当前状态

|里程碑|状态|说明|
|---|---|---|
|M0 硬编码/虚假完成/根因审计|READY_FOR_EXTERNAL_ACCEPTANCE|本次报告已生成；发现阻断项，尚未进行业务代码整改。|
|M1 MQTT 主灯|READY_FOR_EXTERNAL_ACCEPTANCE|31 条正式 API 黑盒用例通过；已保存 MQTT、SQLite、HA、会话和重启证据，等待独立外部验收。|
|M1.5 结构化会话记忆|IMPLEMENTED_NOT_VERIFIED|SQLite 与部分引用已实现；M0 已复现历史查询和显式相对亮度失败。|
|M2 MQTT 窗帘|NOT_STARTED|不得使用现有模板窗帘作为最终来源。|
|M3 MQTT 空调|NOT_STARTED|不得使用 Generic Thermostat/Helper 作为最终来源。|
|M4 MQTT 电视/智能插座|NOT_STARTED|不得使用模板开关作为最终来源。|
|M5 动态观影场景|NOT_STARTED|须等待 M1、M2、M3、M4 协议闭环验收。|
|M6 离家保护|NOT_STARTED|须等待设备真实性和保护设备策略可验证。|
|M7 故障恢复|NOT_STARTED|依赖 M1–M6 的真实协议设备。|
|M8 离线/重启/稳定性|NOT_STARTED|跨设备最终验收。|

允许状态：`NOT_STARTED`、`IMPLEMENTING`、`IMPLEMENTED_NOT_VERIFIED`、`INTEGRATION_VERIFIED`、`READY_FOR_EXTERNAL_ACCEPTANCE`、`EXTERNALLY_ACCEPTED`、`REJECTED`、`BLOCKED`。自动化过程不得把任何项目自标为 `EXTERNALLY_ACCEPTED`。

## M1.5 先行整改（阻塞后续设备）

1. 新建会话语义层，统一解析历史查询、指代、相对调节和显式百分比；不得增加完整验收句子到结果的映射。
2. 修改会话状态模型，明确保存最近一次已验证动作的请求参数、实际回读、时间和结果；读取函数必须按会话/用户/Agent 完整隔离。
3. 将所有状态写入调用补齐 `user_id` 与 `assistant_id`；修复澄清合并逻辑，信息查询不可进入控制槽位补充。
4. 为“再调高 N%/再调低 N%”从真实 HA 当前亮度计算目标值，检查 0–100 边界；缺少历史、实体不可用或语义含混时明确澄清。
5. 使用正式 Conversation API 运行多轮、隔离、失败不污染、HA 重启、TTL 过期与同义改写测试，保存原始 JSONL 证据。
6. 新增 `scripts/audit_hardcoding.py`，将 M0 审计规则纳入回归门禁。

完成条件：所有 M1.5 黑盒案例通过，失败路径无控制、无成功措辞、无跨会话泄漏；状态可提升至 `READY_FOR_EXTERNAL_ACCEPTANCE`。

## M1 MQTT 主灯验收与收口

1. 执行至少 10 条未写入生产代码的隐藏表达，成功率至少 9/10；不完整表达只能澄清。
2. 三轮重复验证 MQTT command → 模拟器 SQLite 前后状态 → MQTT state → HA → Agent 回读。
3. 分别重启 HA、模拟器、Mosquitto；验证实体恢复、SQLite 恢复、offline/unavailable、恢复后自动重连。
4. 注入 `delay`、`reject`、`ack_without_state_change`、`offline`、非法亮度、错误 state 消息；任一情形假成功数必须为 0。
5. 统一输出到 `reports/acceptance/m1_mqtt_light/`：`results.jsonl`、`mqtt_commands.jsonl`、`mqtt_states.jsonl`、`simulator_events.jsonl`、`ha_states.jsonl`、`conversation_states.jsonl`、`summary.md`。汇总必须由原始 JSON 生成。

## 后续设备与场景顺序

### M2：MQTT 窗帘

实现独立 Docker 设备、SQLite、移动中/完成/卡住/延迟/拒绝/离线/假确认状态；必须支持位置 0–100 和部分失败回读。模板窗帘只保留兼容说明，不参与验收。

### M3：MQTT 空调

实现模式、目标温度、上下限、持续状态、离线与延迟；严格验证参数和实际状态。

### M4：MQTT 电视与智能插座

实现开关、功耗、离家关闭；冰箱与路由器保护策略必须在真实协议设备上验证。

### M5：动态观影场景

仅使用通过协议验收的设备。先读取当前状态，再生成最小计划；每步回读，失败时报告实际完成/未完成状态，不能把静态模板当成动态协同。

### M6：离家保护

明确保护设备白名单，禁止关闭冰箱与路由器；发生任一步失败时报告并保持安全设备状态。

### M7/M8：故障恢复与稳定性

覆盖服务重启、broker 中断、设备离线、消息异常、连续多轮控制与稳定性；汇总真实失败率、恢复时间和假成功次数。

## 全程证据与提交规则

- 生产模块不得导入测试/验收/基准夹具；测试不得决定生产分支。
- 所有控制完成回复都需要验证报告；不以 API 200 或卡片 `action_done` 判定。
- 每个里程碑完成一个独立提交并推送到 Draft PR 分支；提交前运行对应单测、配置检查与黑盒脚本。
- 私有仓库可以上传报告、源码、演示素材；绝不提交 HA Token、配置认证文件、私有模型、SQLite 运行数据库或 Docker runtime 数据。
