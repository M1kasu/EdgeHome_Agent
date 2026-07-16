# SpaceButler 家庭空间主动管家

SpaceButler 是面向科大讯飞 SpaceMind 家庭应用场景赛题的 AI Agent 原型。项目基于既有 `EdgeHome_Agent` 的 Home Assistant、MQTT、Docker、本地模型和执行验证经验，重新聚焦“主动理解、真实记忆、自学习、多设备协同和多成员个性化服务”。

当前版本先提供一个可运行、可测试的核心 Agent 内核，后续可把旧项目的 Home Assistant 插件、MQTT 设备模拟器、Docker Compose 和验收脚本迁移到本目录。

## 赛题定位

- **场景方向**：家庭智能管家 + 老人关怀安全守护 + 家庭能源管理 + 多成员个性化服务。
- **核心价值**：从“用户命令设备”升级为“空间主动理解家庭需求并提出可解释服务”。
- **技术关键词**：空间状态建模、家庭记忆、自学习偏好、主动服务触发、多设备计划、多成员冲突调和。

## 已实现原型

- 统一空间状态 `SpatialSnapshot`：成员位置、活动、设备、环境、时间段。
- 家庭记忆 `HouseholdMemory`：显式反馈写入偏好，按成员和场景召回。
- 主动服务引擎 `ProactiveServiceEngine`：返家舒适、夜间老人安全、舒适节能平衡、多成员冲突调和。
- 计划编排 `SpaceButlerAgent`：输入空间快照，输出可解释 `ServicePlan`。
- 单元测试覆盖核心比赛场景。

## 快速运行

```powershell
cd E:\code\znjj\KDXF_SpaceButler
python -m unittest discover -s tests -v
```

运行示例：

```powershell
python -m spacebutler.demo
```

## 后续迁移路线

1. 将 `EdgeHome_Agent` 的 MQTT 设备模拟器迁移为 SpaceButler 的执行沙箱。
2. 复用 Home Assistant 服务调用边界和执行后状态回读验证。
3. 把 `ServicePlan` 映射为旧项目的 `TaskPlan`，保留安全白名单与受保护设备规则。
4. 补充初赛 PDF/PPT、演示视频脚本、商业计划书和原创性声明。

