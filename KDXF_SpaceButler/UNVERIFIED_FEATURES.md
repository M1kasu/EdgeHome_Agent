# Feature Verification

| 功能 | 状态 | 证据或原因 |
|---|---|---|
| Home Assistant 控制链路 | EXTERNAL_PASS | `reports/external/external_20260716_072618/` |
| MQTT 独立设备与 SQLite 状态 | EXTERNAL_PASS | MQTT 观察、设备库和 HA 回读按 request_id 交叉核对 |
| 主动节能正常与故障矩阵 | EXTERNAL_PASS | 正常、反馈、ACK 不变、拒绝、延迟、离线 6/6 |
| 家庭记忆持久化 | EXTERNAL_PASS | Agent 重建后从 SQLite 召回阈值并自动执行 |
| 服务重启恢复 | EXTERNAL_PASS | MQTT、HA、模拟器逐项重启 3/3 |
| llama.cpp 双路由 | EXTERNAL_PASS | KDXF Compose 容器 `:8081` 的真实模型输出经白名单与范围验证后持久化 |
| UI 工作台 API | EXTERNAL_PASS | 独立进程黑盒调用场景、分析、确认、故障和事件接口 |
| UI 浏览器体验 | BROWSER_PASS | 1280x720 与 390x844 无溢出/裁切，真实确认执行成功 |
| 真实家庭硬件 | NOT_VERIFIED | 当前外部边界使用独立 MQTT 设备模拟器 |
| 赛事方正式验收 | NOT_VERIFIED | 项目测试通过不代表赛事组织方验收 |
