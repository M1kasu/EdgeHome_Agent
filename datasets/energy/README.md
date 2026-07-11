# EdgeHome 能耗测试数据

`generator.py` 使用固定随机种子 `20260711` 生成两个 7 天、小时级 CSV，每个文件包含 1176 条记录（7 设备 × 168 小时）。

- `normal_week.csv`：稳定对照数据。
- `abnormal_week.csv`：含客厅无人空调运行 4 小时、电视关闭后的高待机插座、高温日空调功率增长、冰箱和路由器稳定基线、夜间无人长亮测试灯。

字段为 `timestamp/entity_id/device_name/room/power_w/energy_kwh/state/occupancy/temperature/scene`。资料只用于测试，所有数值均为合成数据；分析模块会拒绝负数、缺列、缺状态、重复时间实体或无时区时间戳。
