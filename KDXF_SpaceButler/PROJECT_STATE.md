# Project State

当前分支：`agent/kdxf-continuous-optimization`

当前提交：以 `git rev-parse HEAD` 输出为准。

当前阶段：`ENGINEERING_EXTERNAL_GATE_PASS`

已完成能力：
- 读取 KDXF 赛题文档并按 `docs/REUSE_OR_REBUILD_DECISION.md` 落实“新 Agent 外壳 + 旧设备底座迁移”。
- 完成“客厅连续无人、窗户打开、空调运行、功率偏高”的主动节能闭环。
- 首次建议确认、执行、HA/MQTT/SQLite 回读、反馈学习及后续同类场景自动执行。
- 家庭偏好 SQLite 持久化，Agent 重建后仍可召回学习结果。
- MQTT Broker、Home Assistant、设备模拟器逐项重启后重新完成真实闭环。
- llama.cpp 边缘语言路由；模型输出只生成候选偏好，必须经过字段白名单和数值范围验证。
- 本地工作台支持场景重置、主动分析、确认执行、故障注入、偏好清除和事件查看。
- 假成功防护：ACK 不变、拒绝、延迟、离线均不会被报告为验证成功。

最终验证：
- 15 项单元测试通过。
- `python scripts\run_iteration.py --external` 通过，本地门禁 7/7、外部门禁 10/10。
- 节能闭环正常/反馈/ACK 不变/拒绝/延迟/离线 6/6 通过。
- MQTT、Home Assistant、设备模拟器重启恢复 3/3 通过。
- 真实 llama.cpp 路由、工作台黑盒 API、桌面与移动浏览器交互均通过。
- 最终本地证据：`reports/iterations/iteration_20260716_072711/`。
- 最终外部证据：`reports/external/external_20260716_072618/`。

当前服务：
- Home Assistant：`http://127.0.0.1:8900`
- MQTT：`127.0.0.1:1884`
- 设备模拟器：`http://127.0.0.1:8091`
- KDXF llama.cpp：`http://127.0.0.1:8081`
- SpaceButler 工作台：`http://127.0.0.1:8765`

边界声明：
- 当前结论是项目自带外部黑盒门禁通过，不等同于科大讯飞赛事方验收或获奖结果。
- 真实硬件接入、现场网络环境、团队信息和商业数据仍需在正式提交或现场阶段验证。

阻塞问题：无工程阻塞。
