# M5：动态 MQTT 观影场景验收

**状态：READY_FOR_EXTERNAL_ACCEPTANCE**

M5 不再使用模板氛围灯或静态实体清单。场景从注册表中取得明确标注为 `movie` 且协议为 MQTT 的主灯、窗帘、空调和电视，读取实时状态后只生成必要动作。每个控制步骤仍走 Home Assistant 服务、MQTT、设备 SQLite 与 HA 回读验证。

## 本地集成验收

- 正式 Conversation API 用例 6/6 通过。
- 用户参数可覆盖场景默认值：`主灯调到20%` 与 `空调设为26度` 被写入受限计划并回读确认。
- 已经达到目标时，计划只保留房间读取，不发布新的设备 command。
- 空调处于 `off` 时，场景通过 `set_hvac_mode(cool)` 后再设置目标温度。
- 窗帘 `stuck` 使用明确的可跳过策略：其余 MQTT 设备完成后，场景报告 `partial_success`，不会称为完成。
- 电视 `reject` 会停止后续关键动作并报告失败，假成功为 0。

原始结果见 [`acceptance/m5_mqtt_movie`](acceptance/m5_mqtt_movie)。场景仍是 Docker 数字设备集成验收，不等同于物理家庭设备或独立外部验收。
