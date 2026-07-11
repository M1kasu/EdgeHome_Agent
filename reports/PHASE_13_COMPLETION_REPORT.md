# 第 13 阶段完成报告：端侧优化、性能评测与对比实验

## 交付范围

本阶段在 `agent/phase13-edge-benchmark` 分支实现了可复用的端侧评测基础设施、资源感知调度与降级路径，并保留了原始数据、CSV/JSONL 结果和 SVG 图表，便于复跑与审查。核心实现提交为 `9caa7b5`。运行基线、实验方法、部署步骤和限制分别记录在 `docs/PHASE13_BASELINE.md`、`docs/BENCHMARK_METHODOLOGY.md`、`docs/EDGE_DEPLOYMENT_OPTIMIZATION.md` 与 `docs/PHASE13_USER_GUIDE.md`。

## 已实现能力

- `edgehome.benchmark`：统一工作负载、单调时钟延迟记录、P50/P95、资源采样、JSONL/CSV 结果写出与配置脱敏。
- `edgehome.resource.ResourceAwareScheduler`：基于内存、CPU、模型健康和并发排队时间，在模型路径、模板降级和确定性安全路径之间选择；上下文与生成长度带有上限。
- 协调器接入输入长度保护、资源调度、排队等待时间和模板降级；规划器可在模型不可用时返回已注册场景的安全模板计划。
- 可复现脚本覆盖工作负载生成、真实 llama.cpp 请求、并发、离线网络、隔离 HA 执行验证、资源决策、知识/能耗、安全攻击和稳定性采样。

## 真实结果摘要

| 项目 | 结果 | 原始数据 |
| --- | --- | --- |
| 直接模型简单任务 | 50 请求，平均 643.195 ms，P95 742.004 ms | `dual_a_all_llm_simple_q4_ctx2048.*` |
| 双路径简单任务 | 50 请求，35 条走快速路径；0 次模型调用；平均 12.652 ms，P95 31.156 ms | `dual_b_edgehome_simple_q4_ctx2048.*` |
| 双路径复杂任务 | 30 请求均保留模型路径；平均 1,427.183 ms，P95 1,534.085 ms | `dual_b_edgehome_complex_q4_ctx2048.*` |
| Schema 复杂规划探针 | 27/30 合法 `TaskPlan` JSON | `complex_schema_q4_ctx2048_full.*` |
| 执行验证 / 恢复 | 注入 5 次未生效服务调用；无验证最终完成 90%、假成功 10%，完整路径最终完成 100%、假成功 0% | `execution_verification_comparison.*` |
| 并发压力 | 1/2/4 并发吞吐为 1.685/2.837/3.939 req/s；P95 为 712.125/963.577/1,378.077 ms | `concurrency_results.*` |
| 离线网络 | 内部 Docker 网络下本地模型健康检查 200；外网解析失败，网络随后恢复 | `offline_network_test.json` |
| 安全攻击 | 20 条攻击输入均安全收敛；受保护设备调用为 0 | `safety_workloads.*` |

量化比较仅涉及本机实际存在的 `Home-Llama-3.2-3B.q4_k_m.gguf`。Q4_K_M 的 ctx1024/ctx2048 两个真实推理配置各完成 20 请求；完整数字见 `reports/MODEL_QUANTIZATION_COMPARISON.md`。没有高精度或其他量化权重，故未编造跨量化质量结论。

## 稳定性结论

配置的 3,600 秒只读稳定性窗口已自然结束。共记录 60 个一分钟周期；首末样本覆盖 3,540.417 秒，稳定性执行器内存为 188.109–188.352 MiB（首末变化 +0.211 MiB），系统 CPU 峰值为 75.5%。模型不健康、知识检索失败和能耗分析失败均为 0。最后一个样本在 3,540.504 秒写入，程序随后完成该周期的剩余休眠并在 3,600 秒时退出，故不把缺失的最后一分钟伪造成采样数据。详细报告为 `reports/STABILITY_TEST.md`。

## 质量与部署校验

- Python 回归：112 passed（5 条来自第三方依赖的弃用警告）。
- 静态检查：Ruff 与 mypy 均通过。
- 容器配置：重新构建 `edgehome-agent` 镜像后，Home Assistant `check_config` 以退出码 0 通过；重启后 Home Assistant 首页与 llama.cpp `/health` 均返回 HTTP 200。一次性 `edgehome-agent` 容器按 compose 的 `service_completed_successfully` 依赖关系以退出码 0 结束。

## 结论边界与后续建议

- 当前只有一个 Q4_K_M 模型，且 compose 未配置 GPU 设备直通；本阶段实测为 CPU 路径，不能将主机 RTX 4070 当作实际推理加速结果。
- llama.cpp 的本轮请求使用非流式 OpenAI 兼容端点，只有完整请求延迟与 token 吞吐，TTFT 记为不可用而非估算值。
- 50 条自然语言简单任务的“非空模型回复”和 35 条快速路径“路由接受”均不等同于真实设备操作成功；执行闭环指标只来自隔离 HA 的故障注入实验。
- 已完成快速路径、执行验证/恢复、知识检索和资源降级的可复核探针。场景状态、结构化校验和安全冲突模块尚未在同一可靠 Schema 模型的完整端到端工作负载上逐项重跑；`reports/ABLATION_STUDY.md` 将其明确标记为未测，不用推测数字替代。
