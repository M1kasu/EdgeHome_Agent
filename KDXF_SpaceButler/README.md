# SpaceButler 家庭空间主动管家

SpaceButler 是面向科大讯飞 SpaceMind 家庭应用场景赛题的 AI Agent。项目采用“新 Agent 外壳 + 旧项目设备底座迁移”，聚焦主动理解、家庭记忆、自学习、多设备协同和多成员个性化服务。

## 赛题定位

- **场景方向**：家庭智能管家 + 老人关怀安全守护 + 家庭能源管理 + 多成员个性化服务。
- **核心价值**：从“用户命令设备”升级为“空间主动理解家庭需求并提出可解释服务”。
- **技术关键词**：空间状态建模、家庭记忆、自学习偏好、主动服务触发、多设备计划、多成员冲突调和。

## 已实现原型

- 统一空间状态 `SpatialSnapshot`：成员位置、活动、设备、环境、时间段。
- 家庭记忆 `HouseholdMemory`：显式反馈写入偏好，按成员和场景召回。
- 主动服务引擎 `ProactiveServiceEngine`：返家舒适、夜间老人安全、舒适节能平衡、多成员冲突调和。
- 计划编排 `SpaceButlerAgent`：输入空间快照，输出可解释 `ServicePlan`。
- 主动节能闭环：客厅连续无人、窗户打开、空调运行且功率偏高时，首次建议确认，执行后四路回读；用户授权后同类场景自动执行。
- 真实执行底座：独立 Docker Compose、Home Assistant REST、MQTT Discovery、设备模拟器和 SQLite 设备状态。
- 故障防护：设备拒绝、ACK 但状态不变、延迟、离线、超时和多动作部分成功均不误报为全部成功。

## 快速运行

```powershell
cd E:\code\znjj\KDXF_SpaceButler
python -m unittest discover -s tests -v
python scripts\run_iteration.py
```

运行示例：

```powershell
python -m spacebutler.demo
```

## 外部验收

Docker Desktop 运行时执行：

```powershell
python scripts\run_external_acceptance.py
```

该门禁在独立进程中验证 `Agent -> Home Assistant REST -> MQTT -> device-simulator -> SQLite -> MQTT/HA 回读`，报告写入 `reports/external/`。也可用 `python scripts\run_iteration.py --external` 同时执行本地回归与外部门禁。
