# Project State

当前分支：根目录仓库初始化中；目标分支 `agent/kdxf-continuous-optimization`

当前提交：待提交

当前阶段：IMPLEMENTING

已完成能力：
- 读取 KDXF 真实赛题文档并建立评分矩阵。
- SpaceButler 核心 Agent 内核。
- 舒适节能主动服务：连续无人、窗户打开、空调运行、功率偏高。
- 中风险确认：首次触发需要用户确认。
- 执行回读：本地 runtime 执行后读取设备状态。
- 反馈学习：用户说“以后这种情况直接执行”后，下一次同类场景自动执行。
- 多成员温度偏好调和原型。
- 夜间老人安全照明原型。

真实验证能力：
- `python scripts\run_iteration.py` 通过。
- `python scripts\accept_empty_room_open_window_energy.py` 返回 PASS。
- 硬编码审计 `python scripts\audit_hardcoding.py` 返回 PASS。

未验证能力：
- Home Assistant -> MQTT -> device-simulator -> SQLite -> MQTT state -> HA 回读真实链路。
- Docker 环境测试。
- Home Assistant 黑盒测试。
- MQTT Broker 中断、HA 重启、设备离线等故障矩阵。
- 边缘 llama.cpp 双路由。
- 完整 UI 工作台。

当前 Docker 状态：
- Docker CLI 可用。
- Docker Desktop daemon 未运行：`failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`。

最近一次完整测试结果：
- `scripts/run_iteration.py` PASS。
- 报告目录：`reports/iterations/iteration_20260716_060242/`。

当前赛事评分：
- 初赛自评：58/100。
- 决赛自评：38/100。
- 评分依据见 `competition/SCORECARD_CURRENT.md`。

阻塞问题：
- Docker daemon 未运行，无法完成 Docker/HA/MQTT 真实黑盒验收。
- 根目录 `.git` 原为空目录，需要初始化后才能按要求创建分支和提交。
