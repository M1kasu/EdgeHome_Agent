# Next Actions

继续执行前运行：

```powershell
cd E:\code\znjj\KDXF_SpaceButler
python scripts\run_iteration.py
```

下一步优先级：
1. 将 `HouseholdMemory` 持久化到 SQLite，验证 Agent 重建后仍保留“同类场景自动执行”。
2. 增加 MQTT Broker、设备模拟器和 Home Assistant 的逐项重启恢复外部门禁。
3. 接入边缘模型双路由，并保持所有模型计划都经过确定性验证和执行回读。
4. 实现紧凑的演示工作台，展示空间状态、触发理由、计划、确认、执行和学习结果。

继续标记：

```text
CONTINUE_FROM_NEXT_ACTIONS
```
