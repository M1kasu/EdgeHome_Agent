# 一设备一容器架构与平台调研

## 结论

SpaceButler 将“调用一个设备就是调用一个 Docker”定义为：**每个设备实例对应一个常驻设备容器，设备命令通过 MQTT 投递到该容器；Docker 负责生命周期和故障隔离，不参与每次命令的临时启动。**

这与“收到开灯请求后执行一次 `docker run`”不同。临时容器会引入秒级启动延迟、状态丢失和重复执行问题，不适合设备控制。

## 官方平台调研

### 小米 IoT

小米的 MIoT-Spec 把设备能力组织为 Service Tree，每个服务包含 Properties、Actions 和 Events。不同产品实例通过 URN 绑定同一套可验证的规格。小米的 SPEC 测试工具会根据 instance 自动生成属性和动作测试，并提供设备调试；Wi-Fi 设备可走云端通信，BLE/BLE Mesh 设备则通过网关通信。

可借鉴点：

- 产品规格与设备实例分离，同型号设备共享能力定义。
- 属性、动作、事件是协议层对象，不把 UI 按钮直接等同于设备 API。
- instance 与设备实现可以自动做一致性测试。
- 直连设备与网关子设备采用不同运行拓扑。

官方来源：

- [小米 IoT 开发者平台](https://iot.mi.com/)
- [MiEcosystem MIoT-Spec 官方仓库](https://github.com/MiEcosystem/miot-spec-doc)
- [小米 AIoT SPEC 测试工具说明](https://autotest.iot.mi.com/acsLanding/%E6%B5%8B%E8%AF%95%E5%B7%A5%E5%85%B7/SPEC%E6%B5%8B%E8%AF%95%E5%B7%A5%E5%85%B7/%E5%B8%AE%E5%8A%A9%E6%96%87%E6%A1%A3.html)
- [小米 AIoT 认证测试平台通信能力](https://autotest.iot.mi.com/acsLanding/%E6%8C%87%E5%8D%97/%E5%B9%B3%E5%8F%B0%E4%BB%8B%E7%BB%8D.html)

### 阿里云生活物联网平台

阿里云先定义产品物模型，再注册具体设备。物模型使用属性、事件、服务描述能力，支持标准和自定义功能；设备通过 ProductKey、DeviceName、DeviceSecret 标识，支持一机一密。平台同时提供真实设备和虚拟设备调试、消息轨迹、设备影子、OTA，以及直连设备和网关子设备两种接入方式。子设备可以复用网关的 MQTT 连接。

可借鉴点：

- 产品模型、设备身份、设备状态和消息路由分层。
- 每台设备有稳定唯一身份，消息携带 ID/trace ID 便于追踪。
- 非标准协议在边界适配为统一物模型，不污染上层 Agent。
- 虚拟设备和真实设备使用同一能力定义，可先仿真再换硬件。
- 低功耗子设备无需强制独立连接，可由网关代理。

官方来源：

- [阿里云生活物联网平台](https://cn.aliyun.com/product/livinglink?from_alibabacloud=)
- [设备使用物模型通信](https://help.aliyun.com/zh/iot/user-guide/use-tsl-models-for-communication)
- [设备身份注册](https://help.aliyun.com/zh/iot/user-guide/register-devices)
- [设备接入协议与网关子设备](https://help.aliyun.com/zh/iot/user-guide/overview-of-device-connection-1)
- [虚拟设备与真实设备调试](https://help.aliyun.com/zh/document_detail/610971.html)

## 与 SpaceButler 对比

| 能力 | 小米 / 阿里云 | 改造前 SpaceButler | 当前落地 |
| --- | --- | --- | --- |
| 产品模型 | Service/TSL 与实例分离 | YAML 同时承担型号和实例 | 保留 YAML，下一步拆为 product spec + device instance |
| 能力语义 | 属性、动作、事件/服务 | 类型分支和 MQTT Topic | 现有能力保持兼容，计划升级统一 Thing Model |
| 设备身份 | 独立 DID 或设备证书 | 多设备共用 MQTT Client ID | 每个容器独立 `DEVICE_ID` 和 MQTT Client ID |
| 运行拓扑 | 直连和网关子设备并存 | 一个进程托管所有设备 | 控制设备一设备一容器；后续为简单传感器增加网关模式 |
| 虚拟调试 | 虚拟/真实设备共用模型 | 模拟器与动态设备混在同一进程 | 每台虚拟设备拥有独立容器、SQLite 和故障域 |
| 控制面 | 注册、路由、状态、轨迹 | Workbench 直连一个模拟器 | Fleet Gateway 聚合、路由和受限动态编排 |

## 运行架构

```text
Workbench / Agent
        |
        | Home Assistant REST
        v
Home Assistant <------ MQTT ------> device container A (light + SQLite)
        ^                  |-------> device container B (presence + SQLite)
        |                  `-------> device container C (illuminance + SQLite)
        |
        `---- Fleet Gateway :12891 ---- Docker lifecycle / inventory / fault API
```

关键边界：

- 设备命令仍走 HA/MQTT，不通过 Docker API，保证低延迟和现有状态回读语义。
- 每个静态设备容器只加载一个 `DEVICE_ID`，无法通过管理 API继续添加第二台设备。
- 每台设备使用独立 SQLite 卷、MQTT Client ID、运行时在线 Topic 和资源上限。
- Fleet Gateway 保留原 `12891` 接口，Workbench 不需要知道容器 IP。
- 动态新增设备时，Fleet Gateway 校验定义并创建一个新容器；删除时只允许回收动态设备。
- 设备容器不映射宿主端口，仅 Fleet Gateway 暴露管理端口。

## 夜间安全场景

“老人夜间起身安全路径”现在至少跨三个独立运行时：

1. `bedroom_presence_sensor` 容器上报起身事件。
2. `bedroom_illuminance_sensor` 容器上报实时照度。
3. `bedroom_reading_light` 容器接收柔光动作并持久化状态。

停止照度容器时，只有照度实体变为不可用，存在传感器和灯继续在线；规则因输入不完整而拒绝执行。恢复照度容器后，其 SQLite 状态保留，MQTT Discovery 和状态会重新发布。

## 两种部署模式

一设备一容器适合演示、数字孪生、协议适配器和高价值控制设备，但不应成为永远不可变的教条。

| 模式 | 适用对象 | 优点 | 代价 |
| --- | --- | --- | --- |
| 独立设备容器 | 门锁、空调、摄像头、机器人、仿真设备 | 故障隔离、独立升级、状态清晰 | Python 进程和内存开销随设备数增长 |
| 网关子设备 | BLE、Zigbee、PIR、温湿度等简单传感器 | 复用连接、资源开销低、贴近真实组网 | 网关成为共享故障域 |

当前演示全部采用独立容器，以便验证隔离。生产扩展建议采用混合模式：控制设备独立运行，低功耗传感器由协议网关容器代理。

本机 Docker Desktop 实测九个静态 Python 设备运行时各约占用 16 至 18 MiB 内存，Fleet Gateway 约 35 MiB；镜像层由所有设备共享，不会按设备重复占用完整镜像磁盘空间。

## 安全边界

本地原型的 Fleet Gateway 挂载 Docker Socket，以支持动态设备容器创建。它只暴露固定镜像、固定网络、固定资源上限和经过校验的设备定义，但 Docker Socket 本身仍是高权限入口。

生产环境需要进一步完成：

- 将编排器从 Web 工作台进程彻底分离，使用 rootless Docker、containerd 代理或受限远程编排 API。
- 为 MQTT 开启 TLS、ACL 和真实的一机一密；当前 Mosquitto 仍允许匿名访问，`DEVICE_SECRET` 只是身份预留。
- 为命令增加统一 `request_id`、幂等窗口和设备级审计轨迹。
- 把 YAML 拆成可版本化产品模型与仅含身份/空间/凭据的设备实例。

## 验证命令

```powershell
docker compose -f deployment\docker-compose.yml up -d --build
docker compose -f deployment\docker-compose.yml ps
Invoke-RestMethod http://127.0.0.1:12891/health
Invoke-RestMethod http://127.0.0.1:12891/devices
```

故障隔离验证：

```powershell
docker compose -f deployment\docker-compose.yml stop spacebutler-device-bedroom-illuminance
Invoke-RestMethod http://127.0.0.1:12891/devices
docker compose -f deployment\docker-compose.yml start spacebutler-device-bedroom-illuminance
```

设备清单中的 `runtime.container`、`runtime.status` 和 `runtime.isolation` 可用于确认请求实际落到哪一个容器。
