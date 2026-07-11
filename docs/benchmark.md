# EdgeHome 基准说明

运行 python scripts/benchmark_edgehome.py --iterations 1000 可以获得固定计划 Pydantic 校验的本地耗时和 Python 峰值内存。它不调用模型、不调用 Home Assistant 服务，也不把结果冒充为端到端推理性能。

在目标设备上还应记录 llama.cpp 模型名称、GGUF 量化级别、上下文长度、CPU/GPU 设置、并发数，以及典型复杂任务的端到端时间。EdgeHome 的 ResourceMonitor 会记录复杂规划的 CPU 时间、峰值内存增量、耗时和起始并发数，但不记录用户输入或密钥。
