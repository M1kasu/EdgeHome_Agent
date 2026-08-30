# SpaceButler 自治智能体架构（Agent Layer）

## 总览

```
                    User Goal（自然语言）
                        │
                        ▼
      LangGraph: observe → understand → activate → plan → execute → verify → respond
                        │
                  GoalCompiler          ← LLM 语义理解（不可用时零动作退出）
                        │ 结构化目标约束（区间，非场景脚本）
                        ▼
                   Active Goal ────────────── GoalManager（生命周期 + 防震荡）
                        │
                 HomeWorldModel ◄───────── 轮询工作台清单（HA + ESPHome 实时状态）
                  │        │
                  ▼        ▼
     CandidateGenerator  PreferenceMemory ◄── OverrideDetector（人工纠正）
                  │        ▲
                  ▼        │
     CounterfactualPredictor（反事实预测，参数与 ESPHome 固件一致）
                  │
                  ▼
     PlanScorer（确定性多目标加权评分）
                  │
                  ▼
     PolicyGuard（能力白名单 / 参数边界 / 冲突检测）
                  │
                  ▼
     AgentExecutor ──► control_device ──► Home Assistant ──► ESPHome Native API ──► 设备
                  │
                  ▼
     Verifier（HA 回读验证 + 预测误差 prediction_error）
                  │
     Goal Deviation（扰动）──► 自动 Replanning
     Human Override ─────────► 偏好观测（影响下一次规划）
```

## 模块职责

| 模块 | 职责 |
|---|---|
| `agent/graph.py` | LangGraph `StateGraph` 编排；每次对话都先观察当前状态，再决定是否规划/执行；NO ACTION 直接跳到响应节点 |
| `agent/models.py` | 全部可序列化数据结构（dataclass）：目标约束、候选计划、评分、动作记录、归因枚举 |
| `agent/world_model.py` | Home World Model：感知快照、活跃目标、动作日志（归因依据）、预测历史、事件时间线 |
| `agent/capabilities.py` | Device Capability Registry：规划器唯一可见的设备能力词汇表 |
| `agent/goal_compiler.py` | 自然语言 → 结构化目标约束；仅接受模型 schema 校验通过的结果，不包含“观影模式”等关键词脚本 |
| `llm/client.py` | 统一 OpenAI 兼容客户端（`SPACEBUTLER_LLM_BASE_URL/MODEL/API_KEY`），健康探测缓存 60s |
| `agent/planner.py` | 候选生成（确定性基线 + 偏好候选 + LLM 创意候选）→ 反事实预测 → 评分选优；已满足输出 NO ACTION |
| `agent/counterfactual.py` | 环境预测模型（制冷 -12°C/min、回升 +3°C/min，与固件参数一致），可替换为学习模型 |
| `agent/scorer.py` | 确定性多目标评分：目标满足 0.35 / 舒适 0.20 / 偏好 0.20 / 安全 0.15 / 能耗 0.05 / 动作成本 0.05 |
| `agent/policy_guard.py` | 每个动作的合法性校验：设备存在、能力白名单、参数范围、重复冲突 |
| `agent/executor.py` | 经 control_device 真实下发；先写动作日志再执行（并发归因正确性）；action_id 关联 |
| `agent/verifier.py` | 逐动作 HA 回读比对 + 预测 vs 观测误差 |
| `agent/override_detector.py` | 状态变化归因：AGENT_ACTION / HUMAN_OVERRIDE / ENVIRONMENT_CHANGE；仅在目标活跃期间学习偏好 |
| `agent/preference_memory.py` | 隐式偏好观测（SQLite/内存）：近期加权均值、最少 2 样本、置信度渐进 |
| `agent/goal_manager.py` | 目标生命周期 + 防震荡：debounce 2 tick、重规划冷却 45s、上限 6 次、超时 30min |
| `agent/runtime.py` | 装配 LangGraph 与后台循环：`submit_text`（chat 入口）+ tick（感知→归因→评估→重规划）+ `/api/agent` 视图 |

## LLM 参与边界

| 环节 | LLM | 确定性代码 |
|---|---|---|
| 目标理解（Goal Compiler） | ✅ 语义 → 约束 JSON | ✅ schema/范围校验；模型失败则拒绝执行 |
| 候选计划生成 | ✅ 在能力白名单内提 1 个创意候选 | ✅ 3 个基线候选 + 偏好候选始终存在 |
| 反事实预测 | ❌ | ✅ 物理模型 |
| 多目标评分与选优 | ❌ | ✅ 加权评分 |
| 合法性与安全 | ❌ | ✅ Policy Guard |
| 执行回读验证 | ❌ | ✅ HA 状态比对 |

规划层可以在一个已经由 LLM 编译出的通用约束目标上生成确定性安全候选；但自然语言语义层没有场景 fallback。模型不可用时 `planner_mode=unavailable`，不会向设备发送命令。

## 状态变化归因（核心安全设计）

Agent 先写动作日志（action_id + expected + 时间戳）再下发命令；后台轮询检测到状态变化时：
- 时间窗（90s）内与 expected 匹配 → `AGENT_ACTION`
- 可控设备的其他变化 → `HUMAN_OVERRIDE`（暂停纠正、记偏好、优先级高于 Agent）
- 传感器漂移 → `ENVIRONMENT_CHANGE`

由此杜绝"用户调灯 45% → AI 调回 30%"的对抗行为。

## 环境扰动通道（真实物理路径）

Ubuntu `Care_Home_Agent/infra/esphome/ac.yaml` 应通过 ESPHome `api.actions` 暴露
`demo_heat_injection`；Home Assistant 的实际服务名可用
`ESPHOME_HEAT_INJECTION_SERVICE` 覆盖（默认 `living_room_ac_demo_heat_injection`）。Demo 4 经
Home Assistant 服务调用注入热量 → 固件环境模型抬升室温 → Agent 从状态轮询
中检测偏离 → 自动重规划。全程不触碰 Agent 内部状态。
