# 第 13 阶段性能方法

原始结果位于 `benchmark/results/`。`BenchmarkMetric` 使用 `perf_counter_ns()` 计时，记录路由、上下文、检索、模型、工具、验证与队列等待；未可靠采集的 GPU、温度、功耗和 TTFT 写为 `null`，不写 0。

真实 llama.cpp 请求经 `http://127.0.0.1:8080/v1/chat/completions` 执行，模型端点返回的输入/输出 token 与生成速率直接写入 CSV。该端点在本轮使用非流式兼容调用，无法从网络流可靠获得 TTFT，因此 `model_ttft_ms` 为 `null`。容器内存和 CPU 由运行期间 `docker stats --no-stream` 采样。

工作负载由 `benchmark/scripts/generate_workloads.py` 生成并版本控制：简单 50、复杂 30、多轮 20、知识 20、能耗 15、安全 20、失败恢复 15。图表 SVG 只由已有 CSV 生成，脚本为 `benchmark/scripts/generate_figures.py`。
