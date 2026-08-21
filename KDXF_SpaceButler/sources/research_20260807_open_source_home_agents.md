# Open-source smart-home agent research evidence

检索日期：2026-08-07

检索方式：先通过 GitHub REST API 核对仓库和 README，再将候选仓库固定到提交并检查实现代码。Star 数仅记录检索时快照，不作为方案优劣的主要依据。

## 1. Home Assistant Core

- 仓库：https://github.com/home-assistant/core
- API：https://api.github.com/repos/home-assistant/core
- 检索快照：89,785 stars，Apache-2.0。
- README 关键表述：`Open source home automation that puts local control and privacy first.`
- 可学习点：本地优先、隐私优先、以集成为核心的设备生态。

## 2. Home LLM

- 仓库：https://github.com/acon96/home-llm
- API：https://api.github.com/repos/acon96/home-llm
- 检索快照：1,408 stars。
- README 关键表述：`completely local`、`AI Task Automation`、`Flexible Backends`。
- 可学习点：模型在本地执行，兼容 Home Assistant 内置推理与 OpenAI-compatible 外部后端；模型负责理解，设备执行仍落在家庭系统边界内。
- 源码快照：`acon96/home-llm@50cf35c`。
- 代码证据：`custom_components/llama_conversation/utils.py` 的 `get_home_llm_tools` 先过滤 `SERVICE_TOOL_ALLOWED_DOMAINS` 和 `SERVICE_TOOL_ALLOWED_SERVICES`，再仅暴露 `ALLOWED_SERVICE_CALL_ARGUMENTS` 交集；工具参数转换为 OpenAPI schema。

## 3. Extended OpenAI Conversation

- 仓库：https://github.com/jekalmin/extended_openai_conversation
- API：https://api.github.com/repos/jekalmin/extended_openai_conversation
- 检索快照：1,423 stars。
- README 关键表述：支持实体历史检索；使用 function calling 调用 Home Assistant 服务；提供 `Maximum Function Calls Per Conversation` 限制。
- 可学习点：显式函数规格、复合函数、历史上下文和调用次数上限，适合约束语言模型的工具使用边界。
- 源码快照：`jekalmin/extended_openai_conversation@f2ccd03`。
- 代码证据：`custom_components/extended_openai_conversation/entity.py` 读取 `max_function_calls_per_conversation`，并在请求次数达到上限后停止继续选择工具；README 明确说明该限制用于避免重复调用甚至死循环。

## 4. Adaptive Lighting

- 仓库：https://github.com/basnijholt/adaptive-lighting
- API：https://api.github.com/repos/basnijholt/adaptive-lighting
- 检索快照：3,407 stars，Apache-2.0。
- README 关键表述：`sleep mode` 使用低亮度暖光；灯被用户操作后标记为 `manually controlled`，自动调光暂停，直到重置或再次开关。
- 可学习点：把“夜间舒适参数”和“人工接管优先”作为一等规则，而不是普通自动化脚本里的例外分支。
- 源码快照：`basnijholt/adaptive-lighting@4a87b5e`。
- 代码证据：`custom_components/adaptive_lighting/switch.py` 维护每盏灯的 `manual_control` 与 `auto_reset_manual_control_timers`；状态事件使用 Home Assistant `context.id` 判断变更是否由集成自身发起，并在关灯、服务调用或定时器到期时复位。

## 5. openHAB Core

- 仓库：https://github.com/openhab/openhab-core
- API：https://api.github.com/repos/openhab/openhab-core
- 检索快照：1,134 stars，EPL-2.0。
- README 关键表述：`core bundles of the openHAB runtime`；其本身是用于构建智能家居产品的框架，而非最终产品。
- 可学习点：核心能力按模块和运行时边界拆分，便于替换设备协议、规则和上层体验。

## 结论边界

- 本次是面向场景设计的定向调研，不是对整个智能家居开源生态的穷尽性排名。
- 对比结论基于公开仓库在检索日可见的 README 和项目结构；上游功能可能继续变化。
- SpaceButler 未复制上游代码，仅吸收公开设计思想并在现有模型、记忆、Home Assistant/MQTT 执行与回读边界中重新实现。
- 本次没有仅凭 README 推断“人工接管”和“调用限制”的细节；上述结论均对应固定提交中的实现位置。
