# EdgeHome 工具规范

允许工具由 ToolRegistry 静态注册。每项工具包含参数 Pydantic Schema、允许实体域、风险等级、超时、最大重试次数、服务数据映射和是否要求状态验证。当前控制工具包括开关设备、灯光亮度、空调温度和窗帘位置；未知工具或额外参数在服务调用前被拒绝。

第 12 阶段的只读知识工具为 `query_device_manual`、`query_household_rule`、`query_energy_guide`，统一返回来源、章节、内容、分数及错误码。能耗工具为 `query_energy_data`、`analyse_home_energy`、`generate_energy_plan`、`apply_energy_plan`；最后一项必须传入已选建议和明确确认。它们由专用运行时实现，不开放给固定计划执行器解析为任意 HA 服务调用。
