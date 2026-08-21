# 同类开源项目源码对比与场景落地

## 调研目标

本次不追求再造一个 Home Assistant，而是回答两个问题：SpaceButler 在“家庭主动 Agent”方向缺什么，以及哪一个上游优点能在当前真实链路中形成可演示、可验证的闭环。

原始来源、固定提交和源码检查位置见 `sources/research_20260807_open_source_home_agents.md`。本次学习的是运行机制，不是复制页面或拼接功能名。

## 项目对比

| 项目 | 核心优势 | 相对 SpaceButler 的启发 | 不直接照搬的部分 |
| --- | --- | --- | --- |
| Home Assistant Core | 本地控制、实体状态、Discovery 与集成生态 | HA 实体状态作为传感器和执行回读的权威来源 | SpaceButler 不承担通用设备平台职责 |
| Home LLM | 本地模型后端；服务 domain、service、argument 分层白名单 | 模型只给候选；设备、动作、参数仍经过注册表和范围校验 | 不让模型绕过计划层直接控制设备 |
| Extended OpenAI Conversation | 显式函数 schema；每轮函数调用上限防止循环 | SpaceButler 保留有限计划：最多 12 步，失败即中止后续动作 | 当前场景不引入云端 OpenAI 依赖 |
| Adaptive Lighting | 用 HA context 区分自身与外部控制；人工接管可自动过期或复位 | 人工接管成为带来源、开始时间和 TTL 的持久化上下文 | 不做全天候色温曲线，先聚焦老人安全路径 |
| openHAB Core | 模块化运行时和可扩展框架 | 保持策略、记忆、设备适配、执行验证分层 | 不引入 OSGi 级框架复杂度 |

## SpaceButler 的位置

SpaceButler 当前的优势不是设备数量，而是主动决策的可信闭环：

- `SpatialSnapshot` 对成员、活动、环境和设备状态做统一建模。
- `HouseholdMemory` 将显式偏好与授权持久化，而不是只依赖对话上下文。
- `ServicePlan` 先解释和校验动作，再交给 Home Assistant/MQTT 执行。
- `execute_and_verify` 读取真实设备状态，拒绝 ACK 不变、离线和超时造成的假成功。

改造前的主要问题不是主动场景数量少，而是场景输入和控制权语义不够真实：照度来自页面滑块，“灯已经亮”被误当成人工接管，执行只能靠按钮发起。这样的演示能跑，却不能说明 Agent 真正理解了环境或尊重了用户控制权。

## 选择场景：老人夜间起身安全路径

### 触发条件

1. 后台监听已绑定的 Home Assistant 人体存在实体，并只接受 `off -> on` 上升沿。
2. 照度来自已绑定的 MQTT Discovery `sensor` 实体，不来自规则配置值。
3. 当前照度不高于家庭配置门槛，默认 50 lux。
4. 路径灯与两路传感器均在线、已发现，且路径至少包含一盏灯。

### 执行策略

- 默认使用 18% 柔光，用户可设置 5% 到 40%，并写入成员偏好。
- 按卧室、走廊、卫生间等配置顺序点亮路径。
- 由家庭成员直接控制的路径灯写入 `ControlContextRegistry`，记录来源并在 30 分钟后自动过期；灯关闭时立即释放接管。
- 人工接管、受保护或不可用的灯保持不变；“自动执行后处于开启状态”不会被反推成人工控制。
- 动作为低风险、可逆的照明调整，因此无需二次确认。
- 每个动作通过 Home Assistant REST、MQTT、设备模拟器状态和 HA 回读验证。

### 为什么这个场景合适

- 安全价值明确，比普通“回家开灯”更符合家庭主动管家定位。
- 同时用到成员活动、环境照度、偏好记忆、多设备顺序和执行回读，不是单条自动化。
- 能直接吸收 Adaptive Lighting 的 sleep mode 与 manual takeover 思路。
- 与 Home LLM 的本地执行边界一致：模型可以理解偏好，但关键触发和设备动作保持确定性。

## 已落地实现

| 层次 | 实现 |
| --- | --- |
| 规则标识 | `spacebutler/proactive.py` 中的 `night_elder_safety` |
| 主动策略 | `spacebutler/services.py` 中的新鲜度、照度、顺序、偏好和显式人工接管判断 |
| 控制权上下文 | `spacebutler/control_context.py` 中的 SQLite 持久化来源、TTL、过期清理和释放 |
| 实体输入 | 设备模拟器新增 `illuminance` 类型，通过 MQTT Discovery 注册数值型 HA `sensor` |
| 自动触发 | 工作台后台线程轮询绑定实体，以启动基线消除误触，仅在人体存在上升沿执行 |
| 工作台 API | `/api/night-safety`、`/config`、`/prepare`、`/trigger`；`/run` 保留为直接分析入口 |
| 可操作界面 | 可绑定人体/照度实体，查看两项实时条件、有序路径、接管剩余时间和执行回读 |
| 验证 | 计划动作经 `HomeAssistantRuntime` 执行并逐项回读；页面展示动作数、执行状态与 PASS/FAIL |
| 测试 | 覆盖 HA 状态优先、上升沿只执行一次、接管持久化/过期、路径顺序、照度抑制、偏好和 MQTT Discovery |

## 后续可学习方向

1. 用真实毫米波/PIR 与照度硬件替换模拟实体，验证现场噪声、离线与抖动。
2. 将当前一秒轮询升级为 HA WebSocket 事件订阅，并加入事件去抖和短时间重复触发抑制。
3. 借鉴 openHAB，将主动场景做成可注册策略包，避免工作台接口随场景增长而膨胀。
