# 基线对比实验

执行闭环在隔离的真实 Home Assistant Core 中运行 50 次；服务每 10 次故意不更新状态，模拟“服务调用返回但设备未生效”。

| 方案 | 调用报告成功率 | 最终状态完成率 | 假成功率 | 平均延迟 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| C：EdgeHome 不含反馈验证 | 100% | 90% | 10% | 0.005 ms | 0.010 ms |
| D：完整 EdgeHome（验证 + 一次受限恢复） | 100% | 100% | 0% | 0.067 ms | 0.086 ms |

完整路径在同一服务故障注入下通过固定重试和状态回读恢复全部 5 个失效动作；无验证路径把它们全数报告为成功。该结果只比较可隔离、可重复的执行闭环，不把文本模型“非空回复”冒充为已完成设备操作。

基线 A（固定规则）和 B（直接 Home LLM）保留为接口级参考：固定规则不能理解未预置的新自然语言；直接模型端点的 50 个简单请求平均 643.195 ms。该组请求要求自然语言答复，未要求 `TaskPlan` JSON，因此不能把“非空回复”写成真实设备操作成功率。独立的 `response_format=json_schema` 复杂规划探针得到 27/30 合法 JSON；仍有 3 条不合法输出，故不能绕过结构校验、伪造 A/B 与 C/D 的统一端到端任务完成率。

原始数据：[execution_verification_comparison.csv](../benchmark/results/execution_verification_comparison.csv)、[dual_a_all_llm_simple_q4_ctx2048.csv](../benchmark/results/dual_a_all_llm_simple_q4_ctx2048.csv)。
