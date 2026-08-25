# Feature Verification

| 功能 | 状态 | 证据或原因 |
|---|---|---|
| Home Assistant 控制链路 | EXTERNAL_PASS | `reports/external/external_20260716_090236/` |
| MQTT 独立设备与 SQLite 状态 | EXTERNAL_PASS | MQTT 观察、设备库和 HA 回读按 request_id 交叉核对 |
| 主动节能正常与故障矩阵 | EXTERNAL_PASS | 正常、反馈、ACK 不变、拒绝、延迟、离线 6/6 |
| 家庭记忆持久化 | EXTERNAL_PASS | Agent 重建后从 SQLite 召回阈值并自动执行 |
| 服务重启恢复 | EXTERNAL_PASS | 设备运行时、Fleet Gateway、MQTT、HA 重启后完整执行 4/4 |
| llama.cpp 双路由 | EXTERNAL_PASS | 历史外部门禁已验证真实模型输出经白名单与范围验证后持久化；当前主机未挂载 GGUF，工作台为确定性降级 |
| 动态设备注册表 | EXTERNAL_PASS | 专属容器注册、HA Discovery、控制、自身重启状态恢复和删除 4/4 |
| MQTT Discovery 动态实体 | EXTERNAL_PASS | 新设备容器由 HA 自动发现，删除容器后实体状态被清理 |
| 一设备一容器运行时 | EXTERNAL_PASS | 54 项测试、10 项子测试；独立 SQLite/故障矩阵外部门禁 6/6 |
| Fleet Gateway 动态容器编排 | EXTERNAL_PASS | 固定镜像、网络、资源上限、动态创建和回收外部门禁 4/4 |
| 单设备容器故障隔离 | EXTERNAL_PASS | 目标空调容器 SIGKILL 后其实体 unavailable，卧室阅读灯保持在线 |
| 六类设备控制与上报 | EXTERNAL_PASS | 灯光、开关、窗帘、空调经 HA REST -> MQTT 控制；存在和门窗传感器经设备侧上报 |
| 虚拟空间传感器 | EXTERNAL_PASS | 人体存在、门窗状态经 MQTT Discovery 进入 HA，设备侧事件可上报和回读 |
| 动态主动规则绑定 | EXTERNAL_PASS | 运行时创建书房三类设备、规则改绑、条件触发和空调执行回读 3/3 |
| UI 工作台 API | EXTERNAL_PASS | 独立进程黑盒调用设备、规则绑定、场景、分析、确认、故障和事件接口 |
| UI 浏览器体验 | BROWSER_PASS | 1440x900 与 390x844 无溢出/裁切，动态信号、条件链和主动执行成功 |
| 真实家庭硬件 | NOT_VERIFIED | 当前外部边界使用独立 MQTT 设备模拟器 |
| 真实夜间感知 | NOT_VERIFIED | 已完成 HA 人体上升沿与 MQTT 照度实体闭环，但当前来源仍是虚拟设备，尚未接入毫米波/PIR 和真实照度硬件 |
| 赛事方正式验收 | NOT_VERIFIED | 项目测试通过不代表赛事组织方验收 |
