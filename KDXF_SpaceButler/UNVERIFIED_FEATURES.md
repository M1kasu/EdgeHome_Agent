# Feature Verification

| 功能 | 状态 | 证据或原因 |
|---|---|---|
| Home Assistant 控制链路 | EXTERNAL_PASS | `reports/external/external_20260716_090236/` |
| MQTT 独立设备与 SQLite 状态 | EXTERNAL_PASS | MQTT 观察、设备库和 HA 回读按 request_id 交叉核对 |
| 主动节能正常与故障矩阵 | EXTERNAL_PASS | 正常、反馈、ACK 不变、拒绝、延迟、离线 6/6 |
| 家庭记忆持久化 | EXTERNAL_PASS | Agent 重建后从 SQLite 召回阈值并自动执行 |
| 服务重启恢复 | EXTERNAL_PASS | MQTT、HA、模拟器逐项重启 3/3 |
| llama.cpp 双路由 | EXTERNAL_PASS | KDXF Compose 容器 `:8081` 的真实模型输出经白名单与范围验证后持久化 |
| 动态设备注册表 | EXTERNAL_PASS | 运行时添加、SQLite 持久化、模拟器重启恢复和删除 4/4 |
| MQTT Discovery 动态实体 | EXTERNAL_PASS | 新增设备由 HA 自动发现，删除后实体状态被清理 |
| 六类设备控制与上报 | EXTERNAL_PASS | 灯光、开关、窗帘、空调经 HA REST -> MQTT 控制；存在和门窗传感器经设备侧上报 |
| 虚拟空间传感器 | EXTERNAL_PASS | 人体存在、门窗状态经 MQTT Discovery 进入 HA，设备侧事件可上报和回读 |
| 动态主动规则绑定 | EXTERNAL_PASS | 运行时创建书房三类设备、规则改绑、条件触发和空调执行回读 3/3 |
| UI 工作台 API | EXTERNAL_PASS | 独立进程黑盒调用设备、规则绑定、场景、分析、确认、故障和事件接口 |
| UI 浏览器体验 | BROWSER_PASS | 1440x900 与 390x844 无溢出/裁切，动态信号、条件链和主动执行成功 |
| 真实家庭硬件 | NOT_VERIFIED | 当前外部边界使用独立 MQTT 设备模拟器 |
| 真实无人感知 | NOT_VERIFIED | 页面已读取 MQTT 存在传感器，但当前设备仍为虚拟设备，尚未接入毫米波/PIR |
| 赛事方正式验收 | NOT_VERIFIED | 项目测试通过不代表赛事组织方验收 |
