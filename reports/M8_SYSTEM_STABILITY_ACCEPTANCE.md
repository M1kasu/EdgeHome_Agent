# M8：MQTT 系统离线、重启与稳定性验收

**状态：READY_FOR_EXTERNAL_ACCEPTANCE**

M8 在单一串行验收进程中验证四个独立 MQTT 设备、Home Assistant、device-simulator 和 Mosquitto 的恢复链路。所有控制仍须经 Home Assistant Conversation API 发起并通过 MQTT 状态回读确认，服务调用返回本身不算成功。

正式本地用例 4/4 通过：

- 连续三轮动态观影后离家保护：四个 MQTT 设备均到达目标状态，冰箱和路由器保持开启；
- 单独重启 Home Assistant：MQTT Discovery 与 retained 状态自动重建，HA 与设备模拟器状态一致；
- 停止再启动 device-simulator：HA 临时显示 MQTT 实体 `unavailable`，模拟器从 SQLite 恢复非默认状态并自动回填；
- 停止再启动 Mosquitto：中断期间电视控制不报告成功，Broker 恢复后设备、HA 与控制自动恢复，无需手工改配置。

原始 Conversation、HA 状态、模拟器事件和 Docker 操作记录见 [`acceptance/m8_system_stability`](acceptance/m8_system_stability)。该结论仅覆盖本地 Docker MQTT 数字设备，不能替代真实物理设备或独立外部验收。
