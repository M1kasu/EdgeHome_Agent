# M7：跨设备故障恢复验收

**状态：READY_FOR_EXTERNAL_ACCEPTANCE**

M7 将动态观影场景的恢复点限定为实际进入固定计划的 MQTT 设备。恢复计划逐步回读主灯亮度、窗帘位置、电视及功耗，并额外恢复空调运行模式和目标温度；恢复失败时不清除恢复点，设备恢复后同一会话可重试。若原观影场景未完整成功，则恢复点会被清除，避免后续执行过期补偿。

正式 Home Assistant Conversation API 用例 3/3 通过：

- 完整观影后恢复主灯、窗帘、空调模式/温度及电视功耗；
- 主灯拒绝恢复命令时不得回复成功，恢复点保留，清除故障后可重试成功；
- 电视拒绝导致观影场景部分失败时，不得遗留可执行的恢复点。

原始 Conversation、HA 状态和模拟器 MQTT 事件证据见 [`acceptance/m7_fault_recovery`](acceptance/m7_fault_recovery)。这仅代表本地 Docker MQTT 模拟设备验收，仍等待独立外部验收。
