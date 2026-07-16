# KDXF Requirements Matrix

| 赛事要求 | 分值 | 项目实现 | 测试方法 | 当前状态 | 证据位置 |
| ---- | -: | ---- | ---- | ---- | ---- |
| 初赛：场景价值 | 20 | 聚焦多成员家庭主动空间服务；已实现舒适节能核心闭环 | 黑盒场景验收、评分自评 | IMPLEMENTING | `scripts/accept_empty_room_open_window_energy.py` |
| 初赛：功能完备程度 | 25 | 已覆盖空间感知、主动建议、确认、HA/MQTT执行、四路回读、反馈学习和假成功防护 | 单元、本地黑盒、独立容器外部黑盒 | IMPLEMENTING | `reports/external/external_20260716_063457/` |
| 初赛：技术可行性 | 20 | 新项目独立 Docker/HA/MQTT/SQLite 链路已运行并通过 6 个外部用例 | Docker健康检查、外部进程验收 | EXTERNAL_PASS_P0 | `reports/external/external_20260716_063457/` |
| 初赛：创新性 | 20 | 主动服务、结构化记忆、反馈改变后续行为、多成员温度调和 | 单元测试、黑盒验收 | IMPLEMENTING | `tests/test_spacebutler_agent.py` |
| 初赛：商业价值 | 10 | 初步定位家庭、适老空间、公寓/酒店式公寓；缺少外部市场证据 | 文档审计 | IMPLEMENTED_NOT_VERIFIED | `competition_submission/03_商业模式与落地.md` |
| 初赛：应用前景 | 5 | 规划可扩展设备生态和适配器；真实设备适配尚未完成 | 架构审计 | IMPLEMENTING | `docs/MIGRATION_MANIFEST.md` |
| 决赛：场景落地性 | 20 | 首个主动节能场景已通过真实 HA/MQTT/Docker/SQLite 外部闭环 | 外部正常与故障矩阵 | EXTERNAL_PASS_P0 | `scripts/accept_ha_mqtt_energy_external.py` |
| 决赛：Agent能力体现 | 20 | 主动服务、记忆学习、多成员协调已有原型；多轮自然语言仍有限 | 单元、黑盒 | IMPLEMENTING | `tests/test_spacebutler_agent.py` |
| 决赛：技术创新性 | 20 | 空间状态 + 主动服务 + 安全确认 + 回读验证；边缘模型未接入 | 架构和测试审计 | IMPLEMENTING | `spacebutler/` |
| 决赛：商业性 | 15 | 初稿存在；缺少可信外部市场调研 | 文档审计 | IMPLEMENTED_NOT_VERIFIED | `competition_submission/03_商业模式与落地.md` |
| 决赛：用户体验 | 10 | 交互响应说明触发原因、计划和结果；Home Assistant 可查看实体，尚无专用工作台 | 黑盒响应检查 | IMPLEMENTING | `spacebutler/interaction.py` |
| 决赛：团队情况 | 10 | 暂未补充真实团队信息 | 人工补充 | NOT_STARTED | 待补充 |
| 决赛：现场答辩 | 5 | 暂未形成答辩材料 | 人工演练 | NOT_STARTED | 待补充 |
