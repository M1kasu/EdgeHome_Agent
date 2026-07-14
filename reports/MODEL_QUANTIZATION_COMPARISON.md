# 模型量化 / 推理配置对比

当前目录仅有一个真实量化文件：`Home-Llama-3.2-3B.q4_k_m.gguf`（GGUF、Q4_K_M、1.88 GiB）。没有原始高精度权重，因此本报告不虚构 Q8/Q5/Q3 模型质量结果；比较的是同一文件的两个真实上下文配置。

| 配置 | 请求数 | 上下文 | 实测加载 | 平均延迟 | P95 | 输出速度 | 容器峰值内存 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Q4_K_M ctx1024 | 20 | 1024 | 3,330.731 ms | 676.452 ms | 765.308 ms | 25.891 tok/s | 1,504.256 MiB |
| Q4_K_M ctx2048 | 20 | 2048 | 20,637.398 ms | 683.150 ms | 762.233 ms | 25.526 tok/s | 4,936.704 MiB |

容器内存和 CPU 为运行期间 `docker stats` 采样峰值；延迟、token 和吞吐量来自 llama.cpp OpenAI 端点真实响应。该表的 40 条请求要求自然语言答复，未要求 JSON Schema，因此其“任务成功”仅表示端点返回非空，不等同于结构化计划成功。独立的 30 条 `response_format=json_schema` 复杂规划探针为 27/30 合法，见 `complex_schema_q4_ctx2048_full.*`。

原始数据：[model_comparison.csv](../benchmark/results/model_comparison.csv)、两个 `quant_q4_*.jsonl/csv`、容器采样 JSON。图表：[model_memory_comparison.svg](../benchmark/figures/model_memory_comparison.svg)。
