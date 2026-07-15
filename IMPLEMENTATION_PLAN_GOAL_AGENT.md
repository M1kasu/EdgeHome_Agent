# 智家端脑 EdgeHome 目标驱动改造实施计划

项目目标：把 EdgeHome 从“自然语言设备控制演示”升级为“目标驱动、环境感知、可审计、安全可回滚的端侧家居智能体”。

当前阶段：`INTEGRATION_VERIFIED`

原则：

- 不再新增更多设备来堆功能点，先把目标、规划、安全、证据链做实。
- 模型只能生成结构化计划，不能直接调用 Home Assistant。
- 所有控制动作必须通过白名单工具、状态回读和设备反馈验证。
- 任何失败都不能回复成功。
- 模板 fallback 不能被宣传为自主规划。
- 最终状态最多标记为 `READY_FOR_EXTERNAL_ACCEPTANCE`，除非完成独立外部黑盒验收。

## 阶段 1：基线审计

状态：`INTEGRATION_VERIFIED`

产物：

- `reports/GOAL_AGENT_BASELINE_AUDIT.md`
- `reports/HARDCODED_SCENE_AUDIT.md`
- `IMPLEMENTATION_PLAN_GOAL_AGENT.md`

已完成内容：

- 确认分支、提交、工作树与运行容器。
- 确认现有测试基线。
- 审计观影、离家、fallback、关键词入口与状态快照。
- 标出固定场景和目标驱动缺口。

## 阶段 2：目标与约束建模

状态：`NOT_STARTED`

目标：新增机器可推理的目标对象，替代单纯自然语言 `goal` 字符串。

建议新增对象：

- `GoalSpec`
- `GoalConstraint`
- `GoalPreference`
- `GoalRiskPolicy`
- `GoalSession`

最低字段：

- `goal_type`
- `target_rooms`
- `target_devices`
- `desired_outcomes`
- `constraints`
- `preferences`
- `forbidden_actions`
- `protected_entities`
- `risk_tolerance`
- `requires_confirmation`

验收：

- “准备观影但不要开空调”必须抽取出禁止空调控制。
- “主灯不低于 40%”必须成为硬约束。
- “卧室观影”必须成为目标空间，不允许静默改成客厅。
- 信息不足时输出澄清问题，不生成控制计划。

## 阶段 3：统一环境感知快照

状态：`NOT_STARTED`

目标：把设备状态、环境状态、安全状态、能耗状态整合为统一 `HomeStateSnapshot`。

建议字段：

- `snapshot_id`
- `captured_at`
- `stale_after`
- `rooms`
- `devices`
- `environment`
- `safety`
- `energy`
- `availability`
- `source`

验收：

- 观影计划能读取光照、温度、窗帘位置、灯光亮度、电视状态、空调状态。
- 离家计划能读取人员、门窗、保护设备、设备在线状态。
- snapshot 过期后必须重读或要求确认。

## 阶段 4：动态观影与离家重构

状态：`NOT_STARTED`

目标：移除生产主路径中的固定观影/离家计划骨架，改为目标驱动规划。

必须处理：

- `planner/demo_scenes.py` 只保留为测试夹具或离线演示 fallback。
- `coordinator.py` 中遗留直接场景处理函数删除或迁移。
- `SceneTemplateLibrary` 只提供 prompt hint，不作为固定执行入口。
- UI 展示 `planning_source`，明确模型计划、模型修复、模板 fallback。

观影验收：

- 高光照、低光照、高温、低温、窗帘已关闭、用户禁止空调、用户要求最低亮度等情况必须生成不同计划。
- 不允许所有观影请求输出几乎相同的四步固定计划。

离家验收：

- 受保护设备 MQTT command 数量必须为 0。
- 有人在家、门窗打开、关键设备离线时必须阻断或澄清。
- 不允许回复“已完成”但实际设备未变化。

## 阶段 5：计划评分、风险评估与重规划

状态：`NOT_STARTED`

目标：在执行前生成可解释的候选计划和风险评估。

建议对象：

- `PlanCandidate`
- `PlanScore`
- `RiskAssessment`
- `ReplanRequest`

评分维度：

- 目标满足度
- 安全风险
- 能耗成本
- 舒适度
- 设备可用性
- 执行复杂度

验收：

- 设备离线时不生成不可执行计划。
- 设备部分失败时，能给出“已完成/未完成/可补救”的清晰分解。
- 重规划最多一次，且不能绕过保护设备策略。

## 阶段 6：证据链与 UI 工作台升级

状态：`NOT_STARTED`

目标：让演示界面展示“为什么这样规划”和“每一步是否真实执行”。

UI 应展示：

- 用户目标
- 抽取出的约束
- 状态快照摘要
- 计划来源
- 任务图
- 风险评估
- HA service
- MQTT command
- 设备事件
- SQLite 前后状态
- HA 回读
- Agent 最终回复

验收：

- 任一失败都必须显示失败原因。
- `get_room_state` 等读取步骤不能长期卡在“等待回读”。
- 成功徽章只在全部必需步骤回读通过后出现。

## 阶段 7：外部验收脚本

状态：`NOT_STARTED`

目标：把验收从“看 UI 感觉通过”升级为可复查证据文件。

建议输出目录：

`reports/goal_agent_acceptance/`

建议文件：

- `test_results.jsonl`
- `mqtt_messages.jsonl`
- `simulator_events.jsonl`
- `ha_states.jsonl`
- `goal_specs.jsonl`
- `plans.jsonl`
- `acceptance_summary.md`

验收门槛：

- 隐藏表达成功或合理澄清率不低于 90%。
- 会话串话 0。
- 错误设备控制 0。
- 假成功 0。
- 受保护设备调用 0。
- 五组观影环境生成不同合理计划。
- 容器重启后可恢复。
- 干净环境可部署。

## 阶段 8：README 与参赛材料收口

状态：`NOT_STARTED`

目标：把项目叙述从“智能家居控制 demo”升级为“端侧目标驱动智能体”。

README 必须讲清：

- 一键启动方式。
- 本地模型与 Docker MQTT 设备架构。
- Home Assistant 是唯一执行边界。
- EdgeHome 不直接改 HA 状态。
- 如何运行外部验收。
- 哪些结论已经验证，哪些仍是待外部验收。

视频重点：

- 目标输入不是按钮场景。
- 系统先感知环境。
- 系统抽取约束和风险。
- 系统生成不同计划。
- 系统执行并回读。
- 故障时不假成功。

## 推荐提交节奏

每阶段完成后单独提交并推送：

- 阶段 1：`audit(edgehome): inspect fixed scenes and goal planning gaps`
- 阶段 2：`feat(edgehome): add structured goal and constraint models`
- 阶段 3：`feat(edgehome): add unified home state snapshot`
- 阶段 4：`feat(edgehome): migrate movie and leaving to goal-driven planning`
- 阶段 5：`feat(edgehome): add plan scoring and constrained replanning`
- 阶段 6：`feat(edgehome): expose goal planning evidence in workbench`
- 阶段 7：`test(edgehome): add goal agent external acceptance harness`
- 阶段 8：`docs(edgehome): document goal-driven deployment and demo flow`

## 当前下一步

进入阶段 2，先实现 `GoalSpec` 与 `GoalConstraint`，并把现有 `TaskPlan.constraints` 从自由文本逐步迁移为结构化约束。完成后再改 planner prompt 和 parser，不直接重写观影/离家。
