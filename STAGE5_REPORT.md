# 阶段 5 报告：受限结构化复杂任务规划

## 1. 本阶段目标

为 EdgeHome 增加“场景语义 + 任务相关状态快照 + 现有模型后端规划 + 固定计划验证”的复杂任务闭环，同时不让模型直接取得 Home Assistant 工具或任意服务权限。

## 2. 主要交付

- 场景模板：观影、离家、睡眠、回家、节能。模板仅给出规划约束，不包含默认实体或隐式服务调用。
- 任务状态快照：只收集与用户目标名称匹配、且已暴露给 Conversation 的实体；不再向规划模型发送完整家庭状态。
- 结构化计划解析：模型输出必须依次通过 JSON 对象、`TaskPlan` Pydantic Schema 和 `PlanValidator`。
- 模型适配器：复用 `LocalLLMClient._generate()`，强制非流式输出、`TaskPlan` JSON Schema 和 `llm_api=None`，没有原 Home LLM 设备控制工具。
- 复杂任务入口：仅当请求为场景或明显多动作设备请求时尝试规划；通过验证的计划才交给阶段 3 执行器。

## 3. 安全与回退策略

1. 模型不能调用 Home Assistant LLM API、REST、Shell 或 Python，只能输出文本 JSON。
2. 模型可见实体仅来自任务相关快照；虚构实体、未暴露实体、未注册工具、超范围参数和缺少验证规则的计划均会被拒绝。
3. 后端异常、空输出、非法 JSON、Pydantic 错误和计划校验错误不会调用任何服务，Conversation 回退到原 Home LLM 路径。
4. 合法计划一旦开始执行，设备执行失败会直接报告，不再回退旧模型重复控制。
5. `edgehome_enabled` 默认仍为 `false`；新增 `edgehome_planning_enabled` 默认 `true`，但只在 EdgeHome 已启用时生效。

## 4. 自动化验证

```text
pytest -q: 126 passed
ruff check custom_components/llama_conversation tests/llama_conversation: All checks passed!
mypy: Success: no issues found in 34 source files
```

新增覆盖包括：场景识别不隐式执行、任务快照裁剪、未暴露实体排除、合法/非法模型 JSON、后端异常回退、模型提示词不泄露无关实体、以及 Conversation 中多动作计划的服务执行闭环。

## 5. 真实测试边界

- 测试在 Home Assistant 官方测试状态机、服务注册表和 Conversation 测试入口中运行，验证了服务调用和状态回读的真实集成时序。
- 模型规划测试使用受控假客户端模拟后端 JSON，不代表真实 GGUF、llama.cpp server、Ollama 或远程 API 的输出质量和延迟。
- 未连接用户真实 Home Assistant 实例、实体配置或物理设备；不将自动化测试描述为真实硬件验证。

## 6. 提交与分支

分支：`agent/stage5-structured-planning`

- `6a4907c feat: 新增受限场景模板与结构化计划校验`
- `b40eff1 feat: 复用模型后端生成受限任务计划`
- `9d05a71 feat: 接入受限模型规划与复杂任务执行`

## 7. 下一阶段

阶段 6 将实现结构化多轮状态和持久化：最近房间/设备/参数、中文指代消解、澄清问题、未完成任务恢复及延时任务；仍保留 ChatLog 作为原始对话历史，并使用 Home Assistant Store 异步持久化。
