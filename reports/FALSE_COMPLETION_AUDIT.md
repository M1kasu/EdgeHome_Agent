# M0：虚假完成审计

**审计状态：READY_FOR_EXTERNAL_ACCEPTANCE**
**审计时间：2026-07-12**

## 审计标准

一次控制只能在以下条件全部满足后向用户称为“完成”：计划有效、通过安全校验、实际调用 Home Assistant 服务、对目标实体重新读取状态、状态满足 `verify` 规则。HTTP 200、服务调用未抛异常、或 Home Assistant 对话 API 的 `action_done` 外观均不足以证明完成。

## 已确认的正向控制链

|位置|观察|结论|
|---|---|---|
|`edgehome/execution/executor.py:56-100`|先验证整份计划；任一步失败即为 `FAILED`/`PARTIAL_SUCCESS`/`NEEDS_REPLAN`。|不以服务返回值直接判成功。|
|`edgehome/execution/executor.py:155-231`|服务调用后必须执行 `_verify_step()`；M1 MQTT 主灯额外等待 1 秒设备反馈。|只有 HA 回读满足声明状态和属性才产生 `StepStatus.SUCCESS`。|
|`edgehome/execution/verifier.py:19-47`|重新获取实体快照，逐项比较 state/attributes/tolerance。|回读校验逻辑存在且在执行器主路径上。|
|`edgehome/tools/runtime.py:168-224`|工具运行时仅在 `TaskStatus.SUCCESS` 时返回 `success=True`；失败返回错误码和实际快照。|工具接口没有把“已调用”伪装为“已完成”。|
|`edgehome/context/service.py:31-83`|只有验证成功的报告可以更新最后成功上下文；失败只进入 `recent_results`。|会话记忆不应把失败操作覆盖成成功目标。|

## 风险与缺口

|级别|位置|问题|为何不能据此宣称交付完成|整改要求|
|---|---|---|---|---|
|高|`deployment/demo/homeassistant/edgehome_demo_templates.yaml`、`edgehome_demo_climate.yaml`|除 MQTT 主灯外，电视、窗帘、空调等状态来自模板/Helper。|这些实体即使通过 HA 回读，也只证明 Helper 变化，不证明独立设备或协议闭环。|M2 起逐一替换为独立 Docker MQTT 设备；未替换前禁止用于最终验收。|
|中|`conversation.py:300-307`|API 对“澄清/未控制”响应也显示 `response_type: action_done`；M0 复现中卡片同时写明“本次未触发设备控制”。|客户端外观可能被误读成操作已完成。|在 trace/card 中增加明确 `outcome=clarification|failed|verified_success` 字段；验收脚本只能按执行报告和状态证据判定。|
|中|`coordinator.py:547-558`|`sensor.edgehome_last_execution` 使用 `hass.states.async_set` 写诊断轨迹。|它不是设备状态，但若后续被当作控制证据会造成自证循环。|只保留为诊断传感器；M1 证据必须来自 MQTT、模拟器 SQLite 和 HA 实体，不得以此传感器判成功。|
|中|`planner/demo_scenes.py` 与 `llm_planner.py:155-186`|模型失败会回退到静态演示场景计划。|计划可被验证为“模板状态已变”，但设备物理/协议真实性未被证明。|按设备验收状态禁用未迁移实体的 fallback；复杂场景在 M5 前不得标记完成。|
|低|`coordinator.py:419-424`|复杂计划澄清写入未传递 `user_id`/`assistant_id`。|这是会话隔离风险，可能错误恢复上下文，但不会直接使执行器报成功。|M1.5 统一把身份传给所有状态存取调用。|

## 直接状态写入结果

在生产 Python 路径中，仅发现 `coordinator.py:558` 对 `sensor.edgehome_last_execution` 的诊断写入；没有发现对 `light.edgehome_living_room_main` 或其他受控设备的 `hass.states.async_set`/`hass.states.set`。M1 主灯状态来自 MQTT Discovery 的 state topic，而非集成直接写回。

这项静态结论尚不能替代协议级验收。M1 仍必须保存 Agent 请求、MQTT command/state、模拟器 SQLite 前后状态、HA 前后状态和最终回复，且任一故障模式的假成功次数必须为 0。
