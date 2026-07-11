# 安全与鲁棒性测试

`benchmark/workloads/safety_tasks.json` 包含 20 条攻击性输入：非法工具、虚构实体、5°C 参数、受保护设备绕过和规则忽略指令。在隔离 Home Assistant Core 与 EdgeHome 虚拟注册表中执行后：

- 20/20 未调用冰箱或路由器；总服务调用数为 0。
- 已处理请求 8/20；其余输入安全地落入未处理/拒绝路径，没有转为任意 HA 服务。
- 提示注入资料测试证明恶意文本仅作为 `KnowledgeSearchResult` 内容返回，不会成为工具或系统指令。
- 超长输入由 `InputLengthGuard` 在路由前拒绝；无效模型 JSON 被解析器拒绝或走受限模板回退。

安全测试只使用虚拟实体。原始：[safety_workloads.json](../benchmark/results/safety_workloads.json)；自动化测试：`tests/test_prompt_injection_safety.py`、`tests/test_invalid_json_recovery.py`、`tests/test_long_input_handling.py`。
