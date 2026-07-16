# Feature Verification

| 功能 | 状态 | 证据或原因 |
|---|---|---|
| Home Assistant 控制链路 | EXTERNAL_PASS | `reports/external/external_20260716_063457/` |
| MQTT 独立设备 | EXTERNAL_PASS | 正常及故障消息均由独立 MQTT 观察器捕获 |
| SQLite 设备状态 | EXTERNAL_PASS | 正常关机后与 HA/MQTT request_id 交叉核对 |
| 反馈改变后续行为 | EXTERNAL_PASS | 第二次同类场景自动执行 |
| ACK/拒绝/延迟/离线矩阵 | EXTERNAL_PASS | 外部 4/4 故障用例不误报成功 |
| 本地 timeout/partial_success | LOCAL_PASS | `scripts/accept_local_fault_matrix.py` |
| 家庭记忆持久化 | IMPLEMENTING | 尚未写入 SQLite |
| 重启恢复 | NOT_VERIFIED | 尚未逐项重启 Broker、HA 和模拟器 |
| llama.cpp 双路由 | NOT_STARTED | 尚未接入新 Agent |
| UI 工作台 | NOT_STARTED | 仅有脚本和 HA 实体界面 |
