# 第 13 阶段使用指南

运行 `benchmark/scripts/generate_workloads.py` 生成任务集。真实模型调用可通过：

```powershell
.\.venv\Scripts\python.exe benchmark\scripts\run_llama_workloads.py `
  --workload benchmark\workloads\simple_commands.json --name local_run
```

本地知识、能耗、并发、安全与执行验证运行器也在 `benchmark/scripts/`。执行后运行 `generate_figures.py`，结果在 `benchmark/results/`，图表在 `benchmark/figures/`。

资源调度阈值可在创建 `ResourceSchedulerConfig` 时显式传入。超过输入长度时，EdgeHome 会在模型和工具前拒绝请求；知识文档里的文本始终作为数据，不会变成系统指令。
