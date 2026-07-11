# 失败恢复测试报告

故障机制：开启内部 `input_boolean.edgehome_simulate_curtain_failure` 后，模板窗帘服务正常返回但不更新位置。执行器会重新读取 `current_position`，最多重试一次，再返回 `verification_failed`，不会宣称成功。

自动化执行结果：`tests/test_failure_recovery.py` 通过；断言服务调用后窗帘状态保持 open、失败步骤尝试 2 次，并在前置读取成功后产生 `partial_success`。

状态：故障注入 YAML 已加载且配置检查通过。本轮未通过 Home Assistant 开发者工具前端切换内部开关，因此该项标记为“自动化执行器通过、前端现场触发待补”。
