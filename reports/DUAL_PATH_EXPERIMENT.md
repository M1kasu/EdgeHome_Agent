# 双路径效果实验

同一套 50 条自然语言简单任务在真实 Q4_K_M / ctx2048 服务上测得：

| 方案 | 请求 | 模型调用 | 模型调用率 | 平均延迟 | P95 | 路由接受率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A：所有简单任务进模型 | 50 | 50 | 100% | 643.195 ms | 742.004 ms | 不适用 |
| B：EdgeHome 双路径简单任务 | 50 | 0 | 0% | 12.652 ms | 31.156 ms | 35/50（70%） |
| B：EdgeHome 双路径复杂任务 | 30 | 30 | 100% | 1,427.183 ms | 1,534.085 ms | 复杂任务保留模型路径 |

在这组简单任务中，双路径实际减少 100% 模型调用，平均路由响应降低约 98.03%。15/50 简单语句没有被保守分类器接受，安全降级而非误执行；因此该表不把 70% 路由接受率伪称为设备操作成功率。该吞吐实验要求自然语言答复而非 TaskPlan JSON；独立 JSON Schema 复杂规划探针为 27/30 合法，原始文件为 `complex_schema_q4_ctx2048_full.*`。

原始数据：[dual_path_results.csv](../benchmark/results/dual_path_results.csv)，图表：[latency_comparison.svg](../benchmark/figures/latency_comparison.svg)。
