# 第 12 阶段节能自动化安全边界

能耗分析和生成方案均为只读操作。`EnergyPlan.requires_confirmation` 固定为 `true`；只有用户明确选择条目后，`apply_energy_plan` 才会创建规则。对“应用第一条和第三条，第二条先不要”会显式排除第二条。

允许的规则与目标是代码常量，不能由模型替换：

| 规则 | 目标 | 动作 |
| --- | --- | --- |
| 无人空调 | `climate.edgehome_living_room_ac` | 连续无人指定分钟后关闭 |
| 电视待机 | `switch.edgehome_test_socket` | 电视 off 且待机功率达阈值后关闭 |
| 夜间灯光 | `light.edgehome_living_room_main` | 夜间无人时关闭 |

`EnergyRecommendation` Pydantic 校验会拒绝冰箱、路由器和任何未知实体。`EnergyAutomationManager` 只安装上述事件监听器，调用固定 Home Assistant 服务，所有名称以 `EdgeHome Demo` 开头，且支持查询、停用、删除和撤销最近一次创建。不会解析模型输出 YAML、任意服务名或任意实体 ID。

自动化只控制演示虚拟设备。冰箱与路由器继续受既有保护设备二次确认机制保护，节能流程不提供它们作为目标。
