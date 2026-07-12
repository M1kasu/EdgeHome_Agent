# 未验证与未完成能力

**更新时间：2026-07-12（M1 完成后）**

|能力|状态|边界与原因|
|---|---|---|
|M1 MQTT 主灯|READY_FOR_EXTERNAL_ACCEPTANCE|31 条正式 Conversation API 黑盒用例通过，但设备是 Docker 模拟器，仍等待独立外部验收。|
|M1.5 结构化上下文|IMPLEMENTED_NOT_VERIFIED|M0 已复现历史查询与显式相对亮度失败；必须先修复再验收。|
|M2 MQTT 窗帘|NOT_STARTED|当前模板窗帘是兼容项，不是最终 MQTT 设备。|
|M3 MQTT 空调|NOT_STARTED|当前 Generic Thermostat/Helper 不是最终状态来源。|
|M4 MQTT 电视或插座|NOT_STARTED|当前模板开关不是最终状态来源。|
|M5 动态观影|NOT_STARTED|现有静态 Demo 场景不满足动态计划和多设备真实协议验收要求。|
|M6 离家保护|NOT_STARTED|保护规则已有部分实现，但尚未在真实多 MQTT 设备上验收。|
|M7 故障恢复|NOT_STARTED|只有 M1 单设备故障矩阵已验证。|
|M8 离线/重启/稳定性|NOT_STARTED|只有 M1 的单设备重启和 broker 恢复已验证，尚未完成全系统一小时稳定性。|
|能耗分析|IMPLEMENTED_NOT_VERIFIED|使用合成/本地数据，不得描述为真实家庭长期能耗。|
|硬编码审计|IMPLEMENTED_NOT_VERIFIED|门禁已运行且 M1 相关 error 为 0；能耗完整短语等 6 条 warning 仍需后续整改。|

运行时 Token、Home Assistant 私有配置、SQLite runtime 和本地模型均不属于仓库交付物。
