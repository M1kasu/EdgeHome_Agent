# M1：Docker MQTT 主灯验收

**状态：READY_FOR_EXTERNAL_ACCEPTANCE**
**实体：** `light.edgehome_living_room_main`
**设备类型：** Docker MQTT 虚拟设备，不是物理灯具。

## 阶段目标

验证主灯的唯一状态来源为 `device-simulator` SQLite，且每一次正常控制均走：

```text
正式 Conversation API → EdgeHome → HA Service → MQTT command
→ device-simulator → SQLite → MQTT state → HA 回读 → Agent 回复
```

## 本阶段整改

- 新增只读 MQTT 协议反馈传感器 `sensor.edgehome_living_room_main_feedback`，设备拒绝、假确认、无效状态和离线均可由 HA 回读。
- 执行器对 M1 MQTT 状态使用有界轮询；反馈超时不重试为假成功，也不重复发送待处理命令。
- 设备不可用时在调用 HA Service 前明确失败。
- 亮度 `0%` 统一按 Home Assistant 的关灯语义验证，避免把真实 OFF 误报为失败。
- 参数解析保留负号并验证范围；支持“半”和“成”这类通用百分比表达。
- 实体解析可组合房间别名与设备别名，例如房间词与“主灯”不连续时仍能安全解析；不能唯一确定时仍澄清。
- 生产代码不再从 `benchmark` 命名空间导入输入守卫或资源监视器；兼容层只服务测试/基准调用方。
- 新增 `scripts/audit_hardcoding.py` 作为提交前门禁：禁止生产代码导入验收数据、按测试 ID 分支、直接伪造设备状态或与验收输入高度相似的完整句子分支。

## 验收结果

最终一轮黑盒验收由正式 Home Assistant `POST /api/conversation/process` 触发，结果由原始 JSONL 自动汇总：

|项目|结果|
|---|---|
|计划用例 / 实际完成|31 / 31|
|通过 / 失败|31 / 0|
|假成功|0|
|随机化正常控制|16 条亮度改写 + 开/关，全部经过 MQTT command、SQLite、MQTT state 与 HA 回读|
|边界和非法参数|0%、100%、-10%、150%；非法值没有 MQTT command|
|故障注入|`reject`、`ack_without_state_change`、`invalid_state`、`delay`、`offline` 全部正确失败或超时，无成功措辞|
|重启恢复|HA、device-simulator、Mosquitto 分别重启后均恢复；设备 SQLite 状态与 HA 状态一致|
|针对性单测|23 passed|
|既有 Smoke 回归|12 passed|
|HA 配置检查|passed|
|硬编码门禁|0 error，6 条已知 warning（见下）|

## 故障行为

|故障|验收结论|
|---|---|
|`reject`|模拟器记录 `command_rejected`，反馈传感器为 `rejected`，Agent 明确报告设备拒绝。|
|`ack_without_state_change`|模拟器不改 SQLite 状态，Agent 报告“已确认但状态没有变化”，没有假成功。|
|`invalid_state`|模拟器发布无效 state，HA 保持最后有效状态，Agent 报告无效设备状态。|
|`delay`|执行器至少等待 3 秒反馈窗口后失败，未提前回复成功，也未发送重复命令。|
|`offline`|HA 显示 `unavailable`，执行器在调用服务前返回设备不可用。|
|Mosquitto 中断|控制不报告成功；恢复 broker 后模拟器和 HA 自动重新连接。|

## 原始证据

所有文件都来自验收运行器，未写入 Token、账号信息或运行数据库：

- [`summary.md`](acceptance/m1_mqtt_light/summary.md)
- [`results.jsonl`](acceptance/m1_mqtt_light/results.jsonl)
- [`mqtt_commands.jsonl`](acceptance/m1_mqtt_light/mqtt_commands.jsonl)
- [`mqtt_states.jsonl`](acceptance/m1_mqtt_light/mqtt_states.jsonl)
- [`simulator_events.jsonl`](acceptance/m1_mqtt_light/simulator_events.jsonl)
- [`ha_states.jsonl`](acceptance/m1_mqtt_light/ha_states.jsonl)
- [`conversation_states.jsonl`](acceptance/m1_mqtt_light/conversation_states.jsonl)
- [`hardcoding_audit.json`](acceptance/m1_mqtt_light/hardcoding_audit.json)

## 审计警告与未验证项

硬编码门禁的 6 条 warning 没有命中 M1 验收语句或设备控制链：1 条是仅写入 `sensor.edgehome_last_execution` 的诊断状态，1 条是受保护设备二次确认，4 条是尚待整改的能耗自然语言完整短语分支。它们已在 M0 报告中登记，且不能作为最终交付的“静态审计无高风险”结论。

M1 尚未获得独立外部验收，且仅验证 Docker 虚拟设备；不得描述为物理灯具控制。M1.5 上下文、M2–M8 均未因本阶段通过而自动完成。
