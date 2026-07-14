# 复杂规划 Schema 失败分析

## 范围与证据完整性

第 13 阶段的 `complex_schema_q4_ctx2048_full.outcomes.json` 记录了 30 条任务中的 3 条 `schema_valid=false`：`complex-010`、`complex-019`、`complex-025`。原运行器只保存了布尔结果和性能指标，**没有保存原始模型文本**。这是一项证据留存缺口，因此本报告不伪造原始输出。

第 14 阶段已修复 `benchmark/scripts/run_llama_workloads.py`：对 `--task-plan-schema` 请求保存 `raw_model_output` 与 Pydantic `schema_error`；随后以同一模型、ctx2048、256 token 上限复测三条原任务，并对 `complex-010` 以 384 token 做单条恢复验证。复测具有可审计原文，但不覆盖第 13 阶段的正式 27/30 统计。

| 任务 | 用户输入 | 第 13 阶段原始证据 | 原始输出 | 错误分类 | 是否被安全拦截 / 是否影响设备 | 可否恢复 | 建议 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `complex-010` | 请帮我晚饭后看电影，请把客厅调整舒适，空调温度不得低于 25 度。 | `schema_valid=false`；256 output token；12,731.728 ms | 未留存，无法恢复 | 高置信度：JSON 截断 | 是。该基准只调用模型端点，`tool_execution_ms=null`，未调用 HA 服务 | 是。第14阶段 256-token 复测再次在第 256 token 截断；384-token 单条复测以 119 token 合法 | 对规划模型设置与任务规模匹配的生成预算；在执行层继续保留 Schema、工具白名单和实体验证 |
| `complex-019` | 能否晚饭后看电影，请把客厅调整舒适，空调温度不得低于 25 度。 | `schema_valid=false`；256 output token；13,572.319 ms | 未留存，无法恢复 | 只能判为疑似输出上限截断；无法确定是否另有字段错误 | 是。原测试没有工具执行 | 第14阶段同配置复测为 91 token 合法；不能倒推原错误文本 | 修复留存后扩大样本；失败时重试一次并仅将通过完整执行校验的计划交给设备层 |
| `complex-025` | 请帮我晚饭后看电影，请把客厅调整舒适，空调温度不得低于 25 度。 | `schema_valid=false`；256 output token；14,535.666 ms | 未留存，无法恢复 | 只能判为疑似输出上限截断；无法确定是否另有字段错误 | 是。原测试没有工具执行 | 第14阶段同配置复测为 119 token 合法；不能倒推原错误文本 | 同上；对重复或异常膨胀步骤设置计划长度上限并重试 |

## 可审计复测原文

### `complex-010`，256 token，仍非法

原文保存在 `benchmark/results/phase14_schema_failure_recheck_ctx2048_256.outcomes.json`。输出从合法 JSON 开始，但在 `"CURTAIN13", "tool"` 中断；Pydantic 错误为 `Invalid JSON: EOF while parsing a string`。这直接证实本条的截断分类。该请求只做模型规划验证，不会执行任何设备服务。

### `complex-010`，384 token，恢复为合法 Schema

`phase14_schema_failure_recovery_ctx2048_384.outcomes.json` 保存完整原文；该次输出 119 token、耗时 5,573.770 ms，通过 `TaskPlan` Schema。它说明扩大输出预算可以恢复**结构合法性**，不代表工具名或实体已经通过完整 EdgeHome 执行白名单校验，也不代表已执行设备动作。

### `complex-019` 与 `complex-025`，256 token 复测

同一复测文件保存二者的完整原文，分别以 91 与 119 token 通过 `TaskPlan` Schema。模型输出具有采样非确定性；这只能说明复测未复现原始非法文本，不能替代或改写第 13 阶段的原始失败记录。

## 结论

三条原始非法计划均未进入设备执行层，因而没有造成错误设备操作。第 14 阶段完成了低风险的可观测性修复和受控验证；正式比赛材料仍使用原始 27/30（90%）结果，并公开原始文本缺失与完整消融未完成的边界。
