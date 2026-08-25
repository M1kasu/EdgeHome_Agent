# Next Actions

继续执行前运行：

```powershell
cd E:\code\znjj\KDXF_SpaceButler
python scripts\run_iteration.py
python scripts\run_external_acceptance.py
```

下一步优先级：
1. 将当前 YAML 拆为版本化 Product Spec 与 Device Instance，统一属性、动作、事件模型。
2. 为 Mosquitto 开启 TLS、设备级 ACL 和真实一机一密，再接入小米/阿里云协议网关适配器。
3. 为大量低功耗传感器增加网关子设备模式，与独立设备容器组成混合部署。
4. 将工作台的一秒实体轮询升级为 Home Assistant WebSocket 事件订阅，并增加重复触发抑制。

继续标记：

```text
CONTINUE_FROM_PRODUCT_SPEC_AND_DEVICE_IDENTITY
```
