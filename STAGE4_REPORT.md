# 阶段 4 报告：中文快速路径

## 1. 本阶段目标

为高频、简单的中文智能家居指令建立不依赖大模型的确定性路径，并保持 Home LLM 原有 Conversation 行为可完全回退。

## 2. 主要交付

- 中文意图识别：开关、状态查询、亮度、温度、窗帘位置。
- 中文数值槽位：支持阿拉伯数字和常见中文数字，严格限制亮度/位置 `0..100`、温度 `16..30`。
- 实体名称解析：只匹配已暴露给 Conversation 的实际实体，支持友好名称、实体别名和区域组合名称；同名或不存在时要求澄清。
- 快速路由器：只把单设备、单动作、高置信请求转换为阶段 3 的单步 `TaskPlan`；多动作、条件、延时和未知请求不直接执行。
- 协调器：执行固定计划并把成功、查询、失败转为中文结果；已接管的服务调用失败后不会回退到旧模型路径重复控制。
- Conversation 窄入口：在模型提示词、聊天记录和工具循环之前运行，且受配置开关保护。

## 3. 配置兼容性

| 配置 | 默认值 | 兼容策略 |
|---|---:|---|
| `edgehome_enabled` | `false` | 缺少此字段的既有 Conversation 子条目完全走原 Home LLM 流程。 |
| `edgehome_fast_path_enabled` | `true` | 仅在 EdgeHome 启用后生效；关闭时保持原流程。 |

因此，默认安装和现有配置不会请求新协调器、不会跳过模型、不会改变原始工具调用行为。

## 4. 验证结果

WSL2 Ubuntu / Python 3.14.6 / Home Assistant 2026.5.0：

```text
pytest -q: 117 passed
ruff check custom_components/llama_conversation tests/llama_conversation: All checks passed!
mypy: Success: no issues found in 28 source files
```

测试覆盖了：中文单设备指令、中文百分比与温度、设备别名、同名冲突、复杂指令回退、状态查询、执行失败不回退、以及启用快速路径后在模型调用前执行服务。

## 5. 真实测试边界

- 自动化测试在 Home Assistant 官方测试状态机和服务注册表中运行，已验证实际服务注册、状态变更和 Conversation 入口时序。
- 未连接用户真实 Home Assistant 实例、真实硬件或真实 GGUF 模型；不将单元/集成测试结果描述为物理设备验证。
- 快速路径有意不处理多设备、条件、延时、历史分析或低置信请求，这些请求仍进入原流程，后续由受限规划功能覆盖。

## 6. 提交与分支

分支：`agent/stage4-chinese-fast-route`

- `e5406fc feat: 新增中文意图槽位与设备别名解析`
- `1e89bb6 feat: 实现中文快速路径固定计划路由`
- `349dcd4 feat: 接入可配置的 EdgeHome 中文快速控制`

## 7. 下一阶段

阶段 5 将新增场景模板、任务相关状态快照和受限模型规划：模型只能生成 `TaskPlan`，所有工具、实体、参数和计划限制仍由阶段 3 校验器执行，模型不可取得任意 Home Assistant 服务或 REST 权限。
