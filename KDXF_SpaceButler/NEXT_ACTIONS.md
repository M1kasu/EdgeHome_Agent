# Next Actions

继续执行前运行：

```powershell
cd E:\code\znjj\KDXF_SpaceButler
python scripts\run_iteration.py
python scripts\run_external_acceptance.py
```

下一步优先级：
1. 将真实毫米波/PIR 与照度硬件接入现有实体绑定，补充现场噪声、离线和抖动门禁。
2. 将工作台的一秒实体轮询升级为 Home Assistant WebSocket 事件订阅，并增加重复触发抑制。
3. 将主动场景注册为独立策略包，减少场景 API 对工作台控制器的耦合。

继续标记：

```text
CONTINUE_FROM_NIGHT_SAFETY_WEBSOCKET_GATE
```
