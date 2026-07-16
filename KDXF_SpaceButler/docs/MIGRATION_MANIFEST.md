# 迁移清单

| 新模块 | 旧项目来源 | 来源提交 | 迁移方式 | 重构内容 | 原创增量 |
| --- | ----- | ---- | ---- | ---- | ---- |
| `spacebutler/runtime.py` | `EdgeHome_Agent/custom_components/.../execution` 设计经验 | `1de1d6a0cdab344321b75b87bf8de9a959d03bdd` | 重新实现最小本地执行沙箱 | 从 HA 异步调用改为本地可验收状态读写 | 执行报告、期望值回读验证 |
| `spacebutler/services.py` | `EdgeHome_Agent/edgehome/energy` 和场景策略经验 | `1de1d6a0cdab344321b75b87bf8de9a959d03bdd` | 新写主动服务规则 | 从用户命令驱动改为事件驱动 | 连续无人+开窗+功率高节能机会检测 |
| `spacebutler/memory.py` | 旧项目会话/上下文记忆设计经验 | `1de1d6a0cdab344321b75b87bf8de9a959d03bdd` | 新写结构化偏好记忆 | 添加 sample_count/confidence/source 时间字段 | 反馈改变后续行为 |
| `scripts/accept_empty_room_open_window_energy.py` | 旧项目 `scripts/accept_*.py` 验收风格 | `1de1d6a0cdab344321b75b87bf8de9a959d03bdd` | 新写黑盒验收 | 聚焦 SpaceMind 舒适节能 Demo | 建议-确认-执行-回读-学习链路 |

## 待迁移：第一批演示闭环

- 迁移 `device_simulator`，保留 MQTT Discovery、SQLite 状态和故障注入能力。
- 迁移 `deployment/docker-compose.yml`，改名服务为 `spacebutler-*`。
- 新增 `ServicePlan -> TaskPlan` 适配层。
- 迁移执行后状态回读验证。

## 第二批：SpaceMind 场景

- 返家舒适预备：空调、灯光、窗帘。
- 老人夜间安全：人体活动、路径灯、紧急提醒占位。
- 多成员观影：灯光、窗帘、电视、空调偏好调和。
- 峰电节能：电价、温度、在家状态、能耗建议。

## 第三批：提交材料

- 项目方案书 PDF/PPT。
- 演示视频脚本和录屏素材。
- 技术架构图、商业模式页、原创性声明。
- 压缩包命名：`【团队名+SpaceButler】.zip`。
