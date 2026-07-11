# 比赛指标汇总（真实数据索引）

所有数值均链接至原始 CSV/JSON；没有可靠测量的项目写“未测”，不以 0 填充。

## 智能体能力

| 指标 | 当前实测 | 证据 |
| --- | --- | --- |
| 简单请求快速路径接受率 | 35/50（70%） | `dual_b_edgehome_simple_q4_ctx2048.csv` |
| 复杂模型端点非空响应率 | 30/30 | `dual_b_edgehome_complex_q4_ctx2048.csv` |
| 复杂模型 Schema 合法率 | 27/30（独立 `response_format=json_schema` 探针） | `complex_schema_q4_ctx2048_full.*` |
| 工具执行最终完成率 | 50/50（故障注入 + 验证/恢复） | `execution_verification_comparison.csv` |
| 无验证最终完成率 / 假成功率 | 45/50 / 5/50 | 同上 |
| 安全拦截（受保护设备服务未调用） | 20/20 | `safety_workloads.json` |
| 本地知识任务证据成功率 | 19/20 | `knowledge_workloads.csv` |
| 能耗分析成功率 | 15/15 | `energy_workloads.csv` |
| 多轮状态准确率 | 未在第 13 阶段重新量化；第 12 阶段回归仍覆盖 | `tests/test_energy_multi_turn.py` |

## 端侧性能

| 指标 | 当前实测 | 证据 |
| --- | --- | --- |
| 模型文件 | Q4_K_M GGUF，1.88 GiB | `docs/PHASE13_BASELINE.md` |
| 加载时间 | ctx1024 3.331s；ctx2048 20.637s | `model_comparison.csv` 与 llama.cpp 日志摘要 |
| 容器峰值内存 | ctx1024 1,504.256 MiB；ctx2048 4,936.704 MiB | `*.container_stats.json` |
| 首 Token 延迟 | 未测（非流式端点） | 每条 `BenchmarkMetric.model_ttft_ms=null` |
| 生成速度 | 约 25.5 tok/s（两种上下文） | `model_comparison.csv` |
| 简单任务平均延迟 | 全模型 643.195ms；快速路由 12.652ms | `dual_path_results.csv` |
| 复杂模型平均 / P95 | 1,427.183 / 1,534.085ms | `dual_path_results.csv` |
| 并发吞吐 | 1/2/4 并发：1.685 / 2.837 / 3.939 req/s | `concurrency_results.csv` |
| 断网本地模型可用 | 是（内部健康检查 200，外网 DNS 失败） | `offline_network_test.json` |
| 稳定运行时长 | 1 小时结果将在稳定性采集完成后写入 | `stability_1h.jsonl` |

## 场景价值

| 指标 | 当前实测 | 证据 |
| --- | --- | --- |
| 验证闭环避免的假成功 | 5/50 | `execution_verification_comparison.csv` |
| 能耗异常类别 | 3 类 | `datasets/energy/README.md`、第 12 阶段测试 |
| 可生成节能方案 | 15/15 能耗分析调用成功 | `energy_workloads.csv` |

自然语言吞吐实验不要求 JSON；独立 Schema 探针使用 TaskPlan JSON Schema 并测得 27/30 合法。工具准确率和端到端场景执行仍应在后续模型校准后扩大样本重跑。
