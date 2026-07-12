# 未验证与未完成能力

**更新时间：2026-07-12（M1.5 完成后）**

|能力|状态|边界与原因|
|---|---|---|
|M1 MQTT 主灯|READY_FOR_EXTERNAL_ACCEPTANCE|31 条正式 Conversation API 黑盒用例通过；设备是 Docker 模拟器，仍等待独立外部验收。|
|M1.5 结构化上下文|READY_FOR_EXTERNAL_ACCEPTANCE|20 条正式 Conversation API 黑盒用例通过，含 HA 重启、会话隔离、TTL 过期和失败动作不污染；仍等待独立外部验收。|
|M2 MQTT 窗帘|READY_FOR_EXTERNAL_ACCEPTANCE|Docker MQTT 窗帘已通过开合、定位、移动、卡住、拒绝、超时、离线及三项重启验证；等待独立外部验收。|
|M3 MQTT 空调|READY_FOR_EXTERNAL_ACCEPTANCE|已迁出 Generic Thermostat；Docker MQTT Climate 的温度、模式、故障和重启闭环通过，等待独立外部验收。|
|M4 MQTT 电视或插座|NOT_STARTED|当前模板开关不是最终状态来源。|
|M5 动态观影|NOT_STARTED|现有静态 Demo 不满足动态计划与多设备真实协议验收。|
|M6 离家保护|NOT_STARTED|规则已有部分实现，但尚未在真实多 MQTT 设备上验收。|
|M7 故障恢复|NOT_STARTED|仅 M1 单设备故障矩阵已验证。|
|M8 离线/重启/稳定性|NOT_STARTED|仅 M1 单设备重启和 broker 恢复已验证，尚未完成系统级稳定性验收。|
|能耗分析|IMPLEMENTED_NOT_VERIFIED|使用合成/本地数据，不得描述为真实家庭长期能耗。|
|硬编码审计|IMPLEMENTED_NOT_VERIFIED|M1 相关错误为 0；已知自然语言分支 warning 仍须在后续阶段继续收敛。|

运行 Token、Home Assistant 私有配置、SQLite runtime 数据和本地模型均不是仓库交付物。
