# EdgeHome 实施整改计划

**计划版本：M1.5 完成后，2026-07-12**
**当前分支：** `agent/phase14-competition-package`

每个里程碑均须先实现、再通过正式 Home Assistant Conversation API 做黑盒验收、保存原始证据、独立提交并推送。PR 始终保持 Draft，只有独立验收方可以标记 `EXTERNALLY_ACCEPTED`。

## 当前状态

|里程碑|状态|说明|
|---|---|---|
|M0 硬编码、虚假完成和根因审计|READY_FOR_EXTERNAL_ACCEPTANCE|审计报告、复现记录与整改计划已保存。|
|M1 MQTT 主灯|READY_FOR_EXTERNAL_ACCEPTANCE|31 条正式 API 黑盒用例通过；MQTT、SQLite、HA、重启和故障证据已保存。|
|M1.5 结构化会话记忆|READY_FOR_EXTERNAL_ACCEPTANCE|20 条正式 API 黑盒用例通过，覆盖查询、相对调节、失败不污染、HA 重启、会话隔离和 TTL。|
|M2 MQTT 窗帘|READY_FOR_EXTERNAL_ACCEPTANCE|Docker MQTT 窗帘与 SQLite 状态闭环已验收，覆盖移动、卡住、故障和独立重启；等待外部验收。|
|M3 MQTT 空调|READY_FOR_EXTERNAL_ACCEPTANCE|Docker MQTT Climate、SQLite 状态、温度/模式控制、故障矩阵和独立重启已通过本地验收。|
|M4 MQTT 电视/智能插座|NOT_STARTED|不得使用模板开关作为最终状态来源。|
|M5 动态观影场景|NOT_STARTED|等待 M1 至 M4 协议闭环验收。|
|M6 离家保护|NOT_STARTED|等待真实设备和保护设备策略可验证。|
|M7 故障恢复|NOT_STARTED|依赖 M1 至 M6 的真实协议设备。|
|M8 离线、重启与稳定性|NOT_STARTED|跨设备最终验收。|

允许状态：`NOT_STARTED`、`IMPLEMENTING`、`IMPLEMENTED_NOT_VERIFIED`、`INTEGRATION_VERIFIED`、`READY_FOR_EXTERNAL_ACCEPTANCE`、`EXTERNALLY_ACCEPTED`、`REJECTED`、`BLOCKED`。自动化过程不得自行标记 `EXTERNALLY_ACCEPTED`。

## M1.5 整改与完成条件

1. 以结构化意图而不是完整测试句子映射解析历史查询、指代、相对调节与显式百分比。
2. 在 SQLite 中保存最近一次已验证动作的请求参数、实际回读、时间和结果，并按会话、用户和 Assistant 隔离。
3. 所有状态写入都必须传递 `user_id` 和 `assistant_id`；查询不得进入控制或槽位补全路径。
4. 相对亮度必须从实时 HA 状态计算，范围限定为 0 至 100；没有历史、实体不可用或歧义时必须澄清。
5. 使用正式 Conversation API 验证多轮、失败不污染、HA 重启、会话隔离、TTL 过期和同义改写，并保留 JSONL 证据。
6. 静态硬编码审计必须阻止生产代码导入测试数据、按测试 ID 分支、伪造设备状态或对验收句子做完整匹配。

## 后续设备顺序

### M2：MQTT 窗帘

实现独立 Docker 设备、SQLite、移动中/完成/卡住/延迟/拒绝/离线/假确认状态，支持位置 0 至 100 和部分失败回读。

### M3：MQTT 空调

实现模式、目标温度、上下限、持续状态、离线与延迟，并严格验证参数和实际状态。

### M4：MQTT 电视与智能插座

实现开关、功耗、离家关闭；冰箱与路由器保护策略必须在真实协议设备上验证。

### M5 至 M8

仅使用已协议验收的设备生成动态场景；每步均回读。之后覆盖离家保护、服务和 broker 中断恢复、设备离线、消息异常和长时间稳定性。

## 证据与提交规则

- 生产模块不得导入测试、验收或基准夹具；测试不得决定生产分支。
- 控制成功必须有状态验证，不以 HTTP 200 或 `action_done` 卡片判断。
- 每个里程碑独立提交并推送到 Draft PR 分支；提交前运行对应单元测试、配置检查、黑盒脚本和静态审计。
- 私有仓库可上传源代码、报告和演示素材；绝不提交 HA Token、认证配置、私有模型、SQLite 运行库或 Docker runtime 数据。
