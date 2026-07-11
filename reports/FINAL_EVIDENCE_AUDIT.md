# 最终证据审计

审计日期：2026-07-12（Asia/Shanghai）  
审计范围：第 11、12、13 阶段完成报告、指定实验报告、原始 CSV/JSONL、工作负载和自动化测试。  
审计原则：代码能力、自动化测试、隔离仿真验证和真实本地运行验证分别表述；没有原始记录的内容不提升为比赛结论。

| 宣称 | 数据来源 | 是否真实测试 | 是否可复现 | 是否存在限制 |
| --- | --- | --- | --- | --- |
| 虚拟家庭 17 个受控目标可完成中文控制、场景、保护与回读闭环 | `reports/PHASE_11_COMPLETION_REPORT.md`、第 11 阶段测试与 `deployment/demo/homeassistant/` | 是，本地 HA + 虚拟实体；部分为自动化测试 | 是，部署/重置脚本与测试在仓库 | 不是物理家庭设备；故障注入开关未在当轮前端手工端到端验证 |
| 本地知识检索和能耗分析可用 | `benchmark/results/knowledge_workloads.csv`、`energy_workloads.csv`、第 12 阶段报告 | 是，本地数据与运行时 | 是，资料、CSV、脚本和测试均在仓库 | 知识资料与能耗数据集为演示/合成数据；知识任务 19/20 有证据，1 条按 `NO_EVIDENCE` 正确拒答 |
| 双路径简单请求平均 12.652 ms，直接模型为 643.195 ms | `benchmark/results/dual_path_results.csv`、两组 JSONL | 是，真实 llama.cpp 端点 | 是，工作负载、脚本、结果均已版本控制 | Q4_K_M、ctx2048、CPU、50 条自然语言任务；12.652 ms 是路由响应，35/50 路由接受不等同于设备执行成功 |
| 双路径减少简单任务模型调用 | 同上 | 是 | 是 | 仅限该 50 条任务；15/50 保守降级而非误执行 |
| 复杂规划 Schema 合法率 27/30 | `complex_schema_q4_ctx2048_full.outcomes.json`、CSV/JSONL | 是，真实模型端点 | 是，原工作负载与配置均保留 | 第 13 阶段没有保存原始模型文本；失败归因见 `SCHEMA_FAILURE_ANALYSIS.md`；不等同于真实工具执行成功率 |
| 状态验证与一次受限恢复消除本次注入的假成功 | `execution_verification_comparison.csv/json`、`reports/BASELINE_COMPARISON.md` | 是，隔离真实 HA Core 的故障注入 | 是，脚本与数据已保留 | 50 次隔离虚拟服务调用；不代表所有真实厂商设备 |
| 安全攻击未调用受保护设备 | `safety_workloads.json`、`reports/SAFETY_ROBUSTNESS_TEST.md` | 是，隔离 HA Core + 虚拟注册表 | 是，工作负载、脚本和测试已保留 | 20 条攻击输入、虚拟实体；不能表述为对所有提示攻击绝对安全 |
| 本地链路可在断网网络中运行 | `offline_network_test.json`、`reports/OFFLINE_OPERATION_TEST.md` | 是，Docker `--internal` 网络 | 是，隔离步骤与恢复结果已记录 | 只覆盖 EdgeHome/llama.cpp 本地链路；外部 HA 集成不包含在“完全离线”范围 |
| 一小时核心只读稳定性无三类失败 | `stability_1h.jsonl`、`stability_1h_summary.json`、`reports/STABILITY_TEST.md` | 是，3,600 秒配置窗口 | 是，稳定性脚本与日志已保留 | 60 个一分钟样本，样本跨度 3,540.417 秒；不是所有容器和驱动的长期内存泄漏证明 |
| 112 项 Python 回归、Ruff、mypy、HA 配置检查通过 | `reports/PHASE_13_COMPLETION_REPORT.md`，第 13 阶段执行记录 | 是 | 是，命令在 `docs/PHASE13_USER_GUIDE.md` | 测试环境为 Windows；5 条为第三方依赖弃用警告 |
| GPU/NPU 加速 | 无 | 否 | 不适用 | 不得宣称。实际为 CPU 推理，compose 未配置 GPU 直通 |
| 多量化模型质量对比 | 仅 `model_comparison.csv` 的同一 Q4_K_M ctx1024/ctx2048 | 否，未拥有其他量化权重 | 不适用 | 不得写成 Q3/Q5/Q8 或高精度权重比较 |
| 完整八模块端到端消融 | `ablation_results.csv` | 否，仅完成部分可控探针 | 部分 | 不得宣称完成；场景状态、结构化校验、安全冲突等未在统一可靠 Schema 端到端负载上逐项重跑 |

## 审计修正与材料使用规则

1. `reports/COMPETITION_METRICS_SUMMARY.md` 中原“稳定运行时长结果将在采集完成后写入”的过时表述已在本阶段更正为实际一小时结果。
2. 对模型的“成功”只能指端点返回非空内容，除非另有执行验证证据；比赛材料不得将其改写为已完成设备操作。
3. 对复杂规划的非法输出，原第 13 阶段记录只保存布尔结果，不能倒推或伪造原文。第 14 阶段已补充原文/验证错误留存并完成一条受控复测；细节见 `reports/SCHEMA_FAILURE_ANALYSIS.md`。
4. 所有比赛材料的性能数字均注明 Q4_K_M、ctx2048、CPU、本地 llama.cpp 与对应工作负载；未测项保持“未测”。
