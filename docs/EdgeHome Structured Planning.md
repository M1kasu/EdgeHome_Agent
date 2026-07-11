# EdgeHome 受限结构化规划

阶段 5 将复杂智能家居请求转换为阶段 3 的 `TaskPlan`，而不是让模型直接获得 Home Assistant 服务工具。

## 规划输入

模型只会获得：用户目标、匹配到的场景语义、任务相关且已暴露的实体快照、以及 EdgeHome 注册工具和计划 JSON Schema。模型不会获得 Home Assistant Token、任意 REST 地址、Python/Shell 能力或完整家庭状态。

场景模板（观影、离家、睡眠、回家、节能）只提供规划约束，不带任何默认实体或隐式动作；没有明确实体时，规划器必须拒绝猜测。

## 三道验证门

1. 输出必须是一个 JSON 对象；代码块、自然语言和数组不会被修复后执行。
2. JSON 必须通过 `TaskPlan` 的 Pydantic Schema，拒绝未知字段和不合法步骤。
3. `PlanValidator` 必须验证工具白名单、实体暴露、实体域、参数范围和验证规则，之后才可交给执行器。

任一道门失败时均不会调用 Home Assistant 服务，调用方可安全回退到原 Home LLM 对话路径。
