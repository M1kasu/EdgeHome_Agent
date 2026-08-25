# 小米 IoT 与阿里云生活物联网平台调研记录

- 调研日期：2026-08-25
- 调研范围：设备模型、设备身份、接入拓扑、消息语义、虚拟调试
- 来源要求：厂商官网、官方文档和厂商官方 GitHub 组织

## 小米

1. 小米 IoT 官网需要浏览器 JavaScript 才能完整显示：<https://iot.mi.com/>
2. MiEcosystem 官方仓库保留 MIoT-Spec 文档入口和参考实现：<https://github.com/MiEcosystem/miot-spec-doc>
3. 公开 MIoT 技术规格按 Service Tree 展示 Properties、Actions、Events，并使用 `urn:miot-spec-v2` 标识产品实例：<https://home.miot-spec.com/spec/miot.lock.1>
4. 官方 SPEC 测试工具根据 instance 自动生成 properties/actions 测试，验证设备实现与 instance 一致：<https://autotest.iot.mi.com/acsLanding/%E6%B5%8B%E8%AF%95%E5%B7%A5%E5%85%B7/SPEC%E6%B5%8B%E8%AF%95%E5%B7%A5%E5%85%B7/%E5%B8%AE%E5%8A%A9%E6%96%87%E6%A1%A3.html>
5. 官方认证测试平台说明 Wi-Fi 产品支持云端通信，BLE/BLE Mesh 产品通过中枢网关通信：<https://autotest.iot.mi.com/acsLanding/%E6%8C%87%E5%8D%97/%E5%B9%B3%E5%8F%B0%E4%BB%8B%E7%BB%8D.html>

## 阿里云

1. LivingLink 产品流程为功能定义、设备调试、人机交互、批量投产；支持标准/自定义物模型与一机一密：<https://cn.aliyun.com/product/livinglink?from_alibabacloud=>
2. 物模型通过属性、事件、服务和 Alink JSON 通信，提供设备影子、在线调试、消息解析和 traceID：<https://help.aliyun.com/zh/iot/user-guide/use-tsl-models-for-communication>
3. 一机一密使用 ProductKey、DeviceName、DeviceSecret 作为设备唯一身份，同时支持一型一密动态注册：<https://help.aliyun.com/zh/iot/user-guide/register-devices>
4. 直连设备支持 MQTT/CoAP/HTTPS；网关子设备通过网关代理并复用 MQTT 连接：<https://help.aliyun.com/zh/iot/user-guide/overview-of-device-connection-1>
5. 生活物联网平台支持虚拟设备与真实设备调试产品物模型：<https://help.aliyun.com/zh/document_detail/610971.html>

## 采用结论

- 使用产品模型和设备实例两层结构，不再让一个 YAML 对象同时承担两种职责。
- 内部统一为属性、动作、事件，并保留协议适配边界。
- 一台模拟设备对应一个稳定运行时身份、状态存储和故障域。
- 同时保留独立设备和网关子设备两种部署能力；本轮先实现独立容器模式。
- Docker 负责设备运行时生命周期，MQTT 负责命令和状态；不按命令临时启动容器。
