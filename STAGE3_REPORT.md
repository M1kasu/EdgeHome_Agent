# 阶段 3 报告：固定计划的受控执行与验证

## 目标

在不接入大模型、不修改 Home LLM 既有对话路径的前提下，建立可独立测试的 EdgeHome 执行内核：只接受固定 JSON 计划，只调用显式注册的工具，并以 Home Assistant 的真实状态重读作为执行结果依据。

## 本阶段交付

- `edgehome/models.py`：Pydantic 计划、步骤、前置条件、验证规则和结构化执行报告模型；外部字段默认拒绝。
- `edgehome/tools/registry.py`：6 个显式白名单工具，覆盖状态读取、开关设备、亮度、温度和窗帘位置；每个工具都有参数范围、允许域、超时、重试和验证需求。
- `edgehome/entity_resolver.py`：仅解析 Home Assistant 中实际存在、且已暴露给 `conversation` 的实体，并保留实体/设备/区域快照。
- `edgehome/plan_validator.py`：在首个服务调用前一次性验证步骤数、工具名、Pydantic 参数、实体域、实体暴露、前置条件和验证实体一致性。
- `edgehome/execution/`：按顺序调用 Home Assistant 服务，保存调用前后状态，按声明规则重读验证，并对超时、服务异常、验证不通过最多重试一次。

## 安全边界

1. 工具名称不在注册表中，或参数不符合 Pydantic 模型时，计划直接失败，不会调用服务。
2. 未暴露给 Home Assistant 对话能力的实体、错误域实体和不存在实体都会在执行前被拒绝。
3. 控制类工具必须提供验证规则，验证对象必须与被操作实体相同。
4. 执行器不接受任意服务域或服务名；服务信息只能由工具注册表提供。
5. 失败会返回机器可读的错误码、状态快照和验证结果，不会伪造成功。

## 自动化验证

在 WSL2 Ubuntu 的 Python 3.14.6 / Home Assistant 2026.5.0 环境运行：

```text
pytest -q: 90 passed
ruff check custom_components/llama_conversation tests/llama_conversation: All checks passed!
mypy: Success: no issues found in 22 source files
```

新增测试覆盖：非法计划字段、重复步骤、未注册工具、参数越界、未暴露/错误域实体、前置条件失败、状态属性容差、成功服务调用、验证失败重试、服务超时和重新规划信号。

## 明确未做的事项

- 尚未把大模型输出接入此计划内核，也未开放自然语言到计划的路由。
- 尚未执行真实用户 Home Assistant 实例或真实设备；测试使用 Home Assistant 官方测试状态机和服务注册表。
- 仅支持初始白名单中的设备控制；危险能力、场景编排和更复杂的回滚策略留给后续阶段。

## 下一阶段入口

将把受限模型输出解析为 `TaskPlan`，先执行同一套校验与风险分级，再引入澄清提问、会话状态及可审计的自然语言交互，而不绕过本阶段的工具和验证边界。
