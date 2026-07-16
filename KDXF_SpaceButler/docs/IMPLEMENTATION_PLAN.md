# SpaceButler 实施计划

## 阶段 0：初赛方案与原型

- 完成赛题分析、旧项目复用审计和重构决策。
- 实现 SpaceButler 核心 Agent 内核。
- 编写项目方案书、演示脚本和商业计划材料。

## 阶段 1：执行沙箱迁移

- 迁移 `EdgeHome_Agent/device_simulator` 到 `KDXF_SpaceButler`。
- 将 `ServicePlan` 转换为 Home Assistant 服务调用计划。
- 复用 MQTT Discovery 和 SQLite 状态持久化。

## 阶段 2：Home Assistant 集成

- 迁移旧项目的服务白名单、安全规则和执行后验证。
- 新增 SpaceButler 工作台页面，展示空间状态、记忆命中、主动计划和执行证据。
- 提供 Docker Compose 一键演示。

## 阶段 3：比赛材料

- 输出初赛 PDF/PPT。
- 录制 3 分钟 Demo：返家舒适、老人夜间安全、多成员冲突、峰电节能。
- 准备答辩 Q&A、原创性声明、商业模式页。

