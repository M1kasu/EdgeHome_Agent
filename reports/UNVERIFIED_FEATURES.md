# 未验证与未完成能力

**更新时间：2026-07-13（M8 完成后）**

|能力|状态|边界与原因|
|---|---|---|
|M1 MQTT 主灯|READY_FOR_EXTERNAL_ACCEPTANCE|31 条正式 Conversation API 黑盒用例通过；设备是 Docker 模拟器，仍等待独立外部验收。|
|M1.5 结构化上下文|READY_FOR_EXTERNAL_ACCEPTANCE|20 条正式 Conversation API 黑盒用例通过，含 HA 重启、会话隔离、TTL 过期和失败动作不污染；仍等待独立外部验收。|
|M2 MQTT 窗帘|READY_FOR_EXTERNAL_ACCEPTANCE|Docker MQTT 窗帘已通过开合、定位、移动、卡住、拒绝、超时、离线及三项重启验证；等待独立外部验收。|
|M3 MQTT 空调|READY_FOR_EXTERNAL_ACCEPTANCE|已迁出 Generic Thermostat；Docker MQTT Climate 的温度、模式、故障和重启闭环通过，等待独立外部验收。|
|M4 MQTT 电视或插座|READY_FOR_EXTERNAL_ACCEPTANCE|电视已迁为 Docker MQTT Switch，功耗、故障与重启矩阵通过；路由器和冰箱不在该控制范围。|
|M5 动态观影|READY_FOR_EXTERNAL_ACCEPTANCE|仅使用四个 MQTT 设备，动态参数、幂等与部分失败场景已通过本地验收。|
|M6 离家保护|READY_FOR_EXTERNAL_ACCEPTANCE|已在四个 MQTT 设备上完成正常离家、门窗/人员阻断与电视拒绝 4/4 本地验收；冰箱和路由器保持开启。|
|M7 故障恢复|READY_FOR_EXTERNAL_ACCEPTANCE|3 条正式 Conversation API 用例验证跨设备恢复、拒绝后的同会话重试和失败场景恢复点清理；仍等待独立外部验收。|
|M8 离线/重启/稳定性|READY_FOR_EXTERNAL_ACCEPTANCE|4 条正式系统用例覆盖三轮场景循环、HA 重启、模拟器 SQLite 恢复和 Broker 中断/恢复；仍等待独立外部验收。|
|能耗分析|IMPLEMENTED_NOT_VERIFIED|使用合成/本地数据，不得描述为真实家庭长期能耗。|
|硬编码审计|IMPLEMENTED_NOT_VERIFIED|M1 相关错误为 0；已知自然语言分支 warning 仍须在后续阶段继续收敛。|

运行 Token、Home Assistant 私有配置、SQLite runtime 数据和本地模型均不是仓库交付物。
