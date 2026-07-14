# 资源感知调度

`ResourceAwareScheduler` 不让模型决定资源策略。它以 `SystemResourceMonitor` 的真实可用指标和可注入阈值返回结构化决策。

| 条件 | 决策 |
| --- | --- |
| 简单任务 | `fast_path` |
| 紧急安全任务 | `deterministic_safety` |
| 模型不可用 | `template_fallback` |
| 内存/CPU 达警戒 | `template_fallback` |
| 资源充足的复杂任务 | `llm_path` |

复杂任务在模型队列中等待，实际等待时间可由基准记录；模板降级仅调用已有虚拟演示场景，不编造新动作。当前真实资源采样与阈值探针见 `benchmark/results/resource_scheduler_decisions.json`。
