# M6：MQTT 离家保护验收

**状态：READY_FOR_EXTERNAL_ACCEPTANCE**

离家计划只读取并控制四个已验收 MQTT 设备：主灯、窗帘、空调和电视。它在执行前检查在场和门窗传感器；任一风险存在时只返回阻断说明，不发布设备控制。冰箱和路由器保持受保护注册状态，既不属于离家场景设备，也不会生成关闭步骤。

正式 Conversation API 用例 4/4 通过：正常离家、窗户打开阻断、有人在家阻断、电视拒绝。所有用例中路由器与冰箱均保持 `on`；电视拒绝不产生成功回复。原始证据见 [`acceptance/m6_leaving_protection`](acceptance/m6_leaving_protection)。
