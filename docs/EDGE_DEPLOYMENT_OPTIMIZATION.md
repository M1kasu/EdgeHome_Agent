# 端侧部署优化

第 13 阶段不把主机 GPU 误写为已启用推理。当前 llama.cpp Docker 容器没有 GPU DeviceRequest，实际为 CPU 路径；可通过 Docker device request 和 llama.cpp `--n-gpu-layers` 另行启用并重新测量。

部署的可配置资源参数由 `ResourceSchedulerConfig` 提供：`memory_warning_percent`、`cpu_warning_percent`、`max_llm_concurrency`、`max_context_tokens`、`fallback_context_tokens`、`max_generation_tokens`。简单请求保持快速路径；模型不可用、内存或 CPU 警戒时，只对已登记场景使用模板降级。

当前 Docker 安装容器继续把知识资料和能耗数据复制到 `/config`。每次调整模型、上下文、并发或硬件后，必须重新执行 `benchmark/scripts/run_llama_workloads.py` 与 `generate_figures.py`，不能沿用本报告数字。
