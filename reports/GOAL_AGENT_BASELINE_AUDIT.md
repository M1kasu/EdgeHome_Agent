# 智家端脑 EdgeHome 阶段一基线审计

审计日期：2026-07-15

分支：`agent/goal-driven-home-agent`

基线提交：`573d745 feat(edgehome): 增加受约束自主规划与安全回读`

当前状态：`INTEGRATION_VERIFIED`

说明：本状态只表示阶段一审计、代码基线与现有自动化测试已完成核对，不表示“目标驱动自主智能体”已经外部验收通过。按照需求文档，本阶段不新增设备、不改核心执行逻辑、不宣称最终交付。

## 1. 仓库与运行基线

- 当前工作分支已切换到 `agent/goal-driven-home-agent`，用于目标驱动改造审计与后续实施；未直接在主分支上修改。
- 审计前基线提交为 `573d745`。
- 自动化测试基线：`244 passed, 5 warnings`。
- Docker 实际运行容器由 `docker ps` 确认：
  - `deployment-home-assistant-1`：`127.0.0.1:8899->8123`
  - `deployment-device-simulator-1`：`127.0.0.1:8090->8090`
  - `deployment-llama-server-1`：`0.0.0.0:8080->8080`，healthy
  - `deployment-mqtt-1`：`127.0.0.1:1883->1883`
- 注意：在仓库根目录执行 `docker compose ps` 只返回表头，不能作为容器健康证据。真实运行栈来自 `deployment-*` 容器。

## 2. 已具备的可复用能力

### 2.1 模型被限制在“生成计划”边界内

`custom_components/llama_conversation/edgehome/planner/llm_planner.py:93-126` 中，`LLMTaskPlanner.async_plan()` 构建 JSON-only prompt，并把 Home-LLM 工具调用前后缀替换为禁用标记。模型只产出结构化计划，不直接调用 HA service。

`custom_components/llama_conversation/edgehome/planner/llm_planner.py:343-362` 支持两类输入：旧版完整 `TaskPlan`，以及较小的语义草稿 `ModelPlanDraft`，最终都要经过 `StructuredPlanParser` 解析。

价值：这是目标驱动架构的基础，后续可以保留“模型只规划，执行器才执行”的安全边界。

### 2.2 执行器已有状态回读与诚实失败

`custom_components/llama_conversation/edgehome/execution/executor.py:62-112` 在执行前验证完整计划，然后按拓扑顺序逐步执行。遇到失败会根据 `on_failure` 决定停止、跳过或重规划。

`custom_components/llama_conversation/edgehome/execution/executor.py:178-199` 在调用 HA service 前抓取 `state_before`，执行后通过 verifier 获取 `state_after`。

`custom_components/llama_conversation/edgehome/execution/executor.py:248-259` 要求关键步骤必须提供 `verify`，否则返回失败。

`custom_components/llama_conversation/edgehome/execution/executor.py:262-281` 已把设备反馈映射为明确错误：`device_rejected`、`state_not_changed`、`invalid_device_state`、`device_unavailable`、`device_stuck`、`feedback_timeout`。

价值：这部分已经接近“不能假成功”的底座，后续应扩大证据链输出，而不是重写执行器。

### 2.3 受保护设备已有安全守卫

`custom_components/llama_conversation/edgehome/entity_registry.py:153-154` 将路由器、冰箱注册为 `protected=True`。

`custom_components/llama_conversation/edgehome/coordinator.py:315-330` 对 fast-router 计划中的受保护关机动作要求二次确认。

`custom_components/llama_conversation/edgehome/coordinator.py:492-507` 对复杂计划中的受保护动作同样要求确认。

价值：安全边界已经存在，但目标驱动阶段还需要把“用户禁止项、保护策略、风险等级”升级为结构化约束，而不是只靠实体标记与二次确认。

### 2.4 对话上下文已有持久化雏形

`custom_components/llama_conversation/edgehome/context/models.py` 已包含 `ConversationState`、`current_goal`、`pending_plan`、`last_plan`、`recent_results`、`pending_protected_action` 等字段。

`custom_components/llama_conversation/edgehome/coordinator.py:245-293` 支持基于当前会话状态解析“上一条”“刚才”等引用，并在失败时记录失败上下文。

价值：可以作为后续“GoalSession / PlanRevision / ConstraintSet”的迁移基础。

## 3. 关键缺口

### 3.1 目标对象不够结构化

`custom_components/llama_conversation/edgehome/models.py:87-98` 中 `TaskPlan` 只有 `goal`、`scene`、`constraints`、`steps`。

`custom_components/llama_conversation/edgehome/models.py:60-67` 中 `PlanConstraint` 只是 `statement/source/hard` 文本，不包含：

- `constraint_type`
- `scope`
- `target`
- `operator`
- `value`
- `priority`
- `conflict_policy`

影响：当前能记录“用户约束”，但很难可靠表达“主灯不低于 40%”“不要开空调”“最多功耗 800W”“有人在家不得启动离家保护”等机器可判定规则。

### 3.2 计划步骤缺少目标驱动审计字段

`custom_components/llama_conversation/edgehome/models.py:70-84` 中 `PlanStep` 包含 `id/tool/arguments/depends_on/preconditions/verify/on_failure/optional`，但没有显式：

- `reason`
- `expected_outcome`
- `risk_level`
- `constraint_refs`
- `evidence_required`

影响：UI 能展示工具与回读，但不能完整解释“为什么做这一步、它满足哪个用户目标、失败后为何重规划”。

### 3.3 状态快照不够完整

`custom_components/llama_conversation/edgehome/context/home_state.py:12-20` 的 `TaskHomeStateSnapshot` 仅包含 `goal/scene/entities/truncated`。

`custom_components/llama_conversation/edgehome/context/environment.py:14-23` 的 `EnvironmentContextSnapshot` 增加了 `captured_at/cache_hit`，但还缺少：

- `snapshot_id`
- `stale_after`
- `source`
- `room_groups`
- `safety_facts`
- `environment_facts`
- `energy_facts`
- `device_capability_summary`

影响：目前模型能看到相关实体，但不能稳定形成“感知 -> 约束 -> 计划 -> 执行 -> 回读”的完整证据对象。

### 3.4 观影/离家仍保留模板计划

`custom_components/llama_conversation/edgehome/planner/demo_scenes.py:29-58` 的 `DemoScenePlanFactory` 针对 `movie/leaving/sleeping` 返回固定结构计划。

`custom_components/llama_conversation/edgehome/planner/demo_scenes.py:82-98` 观影默认做窗帘、主灯、电视、空调四类动作，具体数值由简单正则或默认值决定。

`custom_components/llama_conversation/edgehome/planner/demo_scenes.py:123-140` 离家默认关闭主灯、电视、空调、窗帘并启用安防。

影响：它不是完整句子硬编码，但仍属于“场景语义 -> 固定计划骨架”。目标驱动阶段必须把它降级为兜底测试夹具或移除生产主路径，不能作为核心创新能力展示。

### 3.5 场景识别仍靠关键词

`custom_components/llama_conversation/edgehome/planner/templates.py:33-58` 通过关键词识别观影、离家、睡眠、回家、节能。

`custom_components/llama_conversation/edgehome/planner/templates.py:61-67` 使用字符串包含判断。

影响：这能稳定演示，但还不是“理解复杂目标”。下一阶段应让模型输出 `GoalSpec`，场景名只能是推理结果的一部分，不能是固定入口开关。

### 3.6 静态场景 fallback 仍存在

`custom_components/llama_conversation/edgehome/planner/llm_planner.py:240-282` 在模型异常或输出校验失败时会调用 `_scene_fallback()`。

好的一面：`llm_planner.py:253-258` 已经避免在含显式约束时静默替换；`llm_planner.py:271-279` 会标记 `source=DEMO_SCENE_FALLBACK`。

风险：如果演示中大部分“复杂场景”实际来自 fallback，创新点会退化成模板执行。后续必须在 UI 和报告中明确显示 `planning_source`，并让目标驱动路径默认失败或澄清，而不是套模板。

### 3.7 仍有遗留直接场景处理函数

`custom_components/llama_conversation/edgehome/coordinator.py:788-858` 中保留 `_async_handle_mqtt_movie_scene()`。

`custom_components/llama_conversation/edgehome/coordinator.py:860-920` 中保留 `_async_handle_deterministic_leaving_scene()`。

当前主路径在 `coordinator.py:303-313` 将命名场景交给复杂规划入口，未看到这些函数在主流程中被调用；但它们仍然是维护风险。

影响：后续应删除或明确标为测试/兼容代码，避免未来误接回生产路径。

## 4. 第一阶段结论

当前项目不是“Helper 假节点 + 固定句子回复”的早期状态，已经具备 Docker MQTT 设备闭环、计划校验、状态回读、受保护设备守卫、会话上下文与部分失败语义。

但它也还不能直接宣称为“目标驱动边缘自主智能体”。主要原因是：

- 目标与约束没有形成机器可推理的 `GoalSpec`。
- 观影/离家仍存在固定计划骨架。
- 场景识别仍依赖关键词。
- 状态快照还不是统一的环境认知对象。
- UI 证据链仍偏执行结果展示，不能完整展示规划推理、约束冲突、风险评估和重规划路径。

建议下一阶段从 `GoalSpec`、`ConstraintSet`、`HomeStateSnapshot` 三个对象开始改造，先把“用户想达到什么”和“系统为什么这样做”建起来，再迁移观影和离家。
