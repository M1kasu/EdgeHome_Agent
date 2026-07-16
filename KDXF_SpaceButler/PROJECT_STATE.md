# Project State

当前分支：`agent/kdxf-continuous-optimization`

当前提交：以 `git rev-parse HEAD` 输出为准。

当前阶段：P0_EXTERNAL_ACCEPTED_CONTINUING

已完成能力：
- 读取 KDXF 真实赛题文档并建立评分矩阵。
- SpaceButler 核心 Agent、空间快照、主动服务和结构化偏好记忆。
- “客厅连续无人、窗户打开、空调运行、功率偏高”主动节能闭环。
- 首次确认、执行回读、用户反馈学习及后续自动执行。
- Home Assistant REST 执行适配器、MQTT Discovery、SQLite 设备状态和独立 Docker Compose。
- 假成功防护：ACK 不变、拒绝、延迟和离线均不会被报告为验证成功。
- 本地超时与多动作 `partial_success` 语义。
- 多成员温度偏好调和和夜间老人安全照明原型。

真实验证能力：
- `python scripts\run_iteration.py` PASS，11 项单元测试和 3 类本地故障黑盒通过。
- `python scripts\run_external_acceptance.py` PASS。
- 外部边界：独立进程 -> Home Assistant REST -> MQTT -> device-simulator -> SQLite -> MQTT/HA 回读。
- 外部用例：正常确认执行、反馈改变下次行为、ACK 不变、拒绝、延迟、离线，共 6/6 通过。
- 外部证据：`reports/external/external_20260716_063457/`。

未完成或未验证能力：
- MQTT Broker、Home Assistant、设备模拟器逐项重启恢复矩阵。
- 家庭偏好记忆 SQLite 持久化与重启恢复。
- 边缘 llama.cpp 双路由和模型输出计划验证。
- 面向现场演示的完整 UI 工作台。
- 真实团队信息、现场答辩和外部商业数据。

当前 Docker 状态：
- Docker Desktop daemon 正常运行。
- `spacebutler-mqtt`、`spacebutler-device-simulator`、`spacebutler-home-assistant` 容器正常运行。
- Home Assistant：`http://127.0.0.1:8900`。

当前赛事保守自评：
- 初赛：74/100。
- 决赛：50/100。
- 首个 P0 场景已通过外部验收，不代表整个参赛项目已完成或赛事方已验收。

阻塞问题：无工程阻塞；团队资料和真实商业信息最终需要用户提供。
