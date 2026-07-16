# Next Actions

继续执行前先运行：

```powershell
cd E:\code\znjj\KDXF_SpaceButler
python scripts\run_iteration.py
```

下一步优先级：

1. 实现执行结果状态枚举和假成功防护测试：命令返回 success 但状态不变必须失败。
2. 增加本地故障注入：设备离线、设备拒绝、响应延迟。
3. Docker Desktop 启动后运行：

```powershell
docker version
```

4. Docker 可用后迁移 `EdgeHome_Agent/device_simulator` 和 `deployment/docker-compose.yml` 的最小子集。

继续标记：

```text
CONTINUE_FROM_NEXT_ACTIONS
```

