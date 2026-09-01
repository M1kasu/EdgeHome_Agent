# SpaceButler 家庭空间主动管家

SpaceButler 是面向科大讯飞 SpaceMind 家庭应用场景赛题的 AI Agent。项目采用“新 Agent 外壳 + 旧项目设备底座迁移”，聚焦主动理解、家庭记忆、自学习、多设备协同和多成员个性化服务。

## 本次主要交付：Agent 前的语音识别

本次开发重点是语音输入层：**用户长按 V 说话 → 实时字幕 → 松开后提交最终文本 → 复用已有 Agent 进行任务拆解**。语音模块不代替 Agent 规划，也不直接调用设备服务；HA 实体匹配修复与诊断用于配合完整链路联调。

### 使用行为

1. 在「AI 管家」长按 **V** 或「按住说话」按钮，允许麦克风权限，看到「正在聆听」后讲话。
2. 讲话时字幕和输入框持续更新；停顿或服务商确认某一分句，不会提前提交任务。
3. 松开后停止采集，发送最后的音频分片，等待最终识别结果，再通过 `/api/chat` 提交一次。
4. 按 Esc、点击取消、窗口失焦、隐藏页面或切换视图均取消本次输入；静音、权限拒绝、网络失败和超时不提交任务，并恢复原草稿。

输入框中轻按 V 仍输入字符，长按才触发录音；中文输入法组字和修饰组合键不会触发。单次录音上限 90 秒，超过 Agent 500 字限制的文本需手动精简后发送。

### 语音链路与实现位置

```text
浏览器麦克风 → AudioWorklet（16kHz / 单声道 / PCM16，200ms 分片）
    → 本地 HTTP 语音会话 → 服务端 WebSocket → 豆包流式识别
    ← 实时文字轮询 ← partial / final
松开 V → 等待 final → /api/chat → LangGraph → HA → ESPHome → 状态回读
```

| 模块 | 负责内容 |
| --- | --- |
| [`workbench/voice-capture.js`](workbench/voice-capture.js) | 麦克风音频重采样与 PCM16 分片 |
| [`workbench/voice-input.js`](workbench/voice-input.js) | 长按、实时字幕、取消和最终文本提交 |
| [`spacebutler/voice.py`](spacebutler/voice.py) | ASR 二进制协议、服务端鉴权、会话与最终结果处理 |
| [`spacebutler/workbench.py`](spacebutler/workbench.py) | `/api/voice/*` 接口，与原有 `/api/chat` 共用端口 |
| [`spacebutler/configuration.py`](spacebutler/configuration.py) | 从项目 `.env` 加载配置，进程环境变量优先 |
| [`scripts/check_voice_asr.py`](scripts/check_voice_asr.py) | 不依赖 Agent 或 HA 的上游语音验证 |

语音音频会发送给云端豆包服务，因此语音入口需要联网；麦克风录音仅在内存中缓冲，本项目不落盘。最终文字提交后沿用原有 Agent 的会话与记忆行为。

### 配置语音 API Key

依赖 Python 3.11+。在本目录创建虚拟环境并安装依赖；已有可用环境时可直接使用，不必重复创建：

```powershell
# 从仓库根目录进入子项目，先确认 python 指向 Python 3.11+。
Set-Location KDXF_SpaceButler
python -m venv .venv
$python = '.\.venv\Scripts\python.exe'
& $python -m pip install -e ".[dev]"
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
```

编辑 **本目录的 `.env`**，不要覆盖已有配置，也不要把真实值写入 `.env.example`：

```dotenv
SPACEBUTLER_ASR_API_KEY=<你的语音 API Key>
SPACEBUTLER_ASR_RESOURCE_ID=volc.seedasr.sauc.duration
SPACEBUTLER_ASR_ENABLE_NONSTREAM=true
```

服务端连接 `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async`，使用 `X-Api-Key` 鉴权。本接入不需要通用云 AccessKeyID / SecretAccessKey。示例开启最终二遍识别；排查基础流式链路时可改为 `false`。Key、资源授权和可用额度都必须有效。

`.env` 及 `.env.*` 已被 Git 忽略，仅保留无真实密钥的 `.env.example`。密钥不会返回浏览器；修改配置后需重启工作台。`.env` 是本机明文文件，曾在聊天中分享的临时凭据建议轮换。

### 先独立验证语音，再验证完整任务

```powershell
# 不需要 HA、ESPHome 或 LLM，也不会调用 Agent / 控制设备。
& $python scripts\check_voice_asr.py
# 可选：测试文件将上传到语音服务商，需为单声道 / 16kHz / PCM16 WAV。
& $python scripts\check_voice_asr.py --wav 'C:\Audio\command.wav'
# 比较关闭二遍识别时的基础流式结果。
& $python scripts\check_voice_asr.py --wav 'C:\Audio\command.wav' --no-second-pass
```

不带 `--wav` 时只发送一秒静音，检查鉴权、音频传输和最终包；成功标志为 `connected: true` 和最终 `state: "final"`，不代表人声准确度验证通过。带语音文件时还应检查 `partial` 更新与最终 `text`。脚本会消耗少量上游语音额度。

完整网页验收需按下方启动工作台，在 [localhost 页面](http://127.0.0.1:8765) 允许麦克风，先问“客厅主灯现在是什么状态？”，检查字幕实时更新、松开后仅提交一次，再测试需要的设备控制。设备执行成功还需 HA 回读验证，不能仅凭字幕判断。

| 检查项 | 地址或操作 |
| --- | --- |
| 语音配置是否被读取 | `/api/voice/status`；`configured: true` 只表示已配置 Key，不表示上游调用通过 |
| HA、模型和 Agent 状态 | `/api/health` |
| 设备实体匹配 | `/api/devices/diagnostics`，或运行 `scripts/check_ha_devices.py` |
| 麦克风权限问题 | 使用 localhost 或 HTTPS，检查浏览器权限，首次授权后重新长按 |
| 字幕正常但任务失败 | 分别检查模型、HA 和实体绑定；不要归因于语音转写 |

本地自动化验证不调用真实语音服务或设备：

```powershell
& $python -B -m pytest -c pyproject.toml tests -q
node --test tests/voice-input.test.cjs
& $python -m ruff check --no-cache .
node --check workbench/voice-input.js
node --check workbench/voice-capture.js
```

2026-09-01 回归结果：155 项 Python 测试、10 项前端语音测试通过。真实 API 的合成语音验证与限制见 [验证记录](docs/VOICE_VERIFICATION.md)；麦克风交互、错误码和更多选项见 [语音输入说明](docs/VOICE_INPUT.md)。真实麦克风到设备执行的现场验收仍需在你的部署环境完成。

## 赛题定位

- **场景方向**：家庭智能管家 + 老人关怀安全守护 + 家庭能源管理 + 多成员个性化服务。
- **核心价值**：从“用户命令设备”升级为“空间主动理解家庭需求并提出可解释服务”。
- **技术关键词**：空间状态建模、家庭记忆、自学习偏好、主动服务触发、多设备计划、多成员冲突调和。

## 已实现原型

- 统一空间状态 `SpatialSnapshot`：成员位置、活动、设备、环境、时间段。
- 家庭记忆 `HouseholdMemory`：显式反馈写入偏好，按成员和场景召回。
- SQLite 持久化记忆：Agent 重建后仍能召回用户授权和节能等待阈值。
- 主动服务引擎 `ProactiveServiceEngine`：返家舒适、夜间老人安全、舒适节能平衡、多成员冲突调和。
- 老人夜间起身安全路径：监听 HA 人体存在实体的上升沿，从 ESPHome 照度实体取值，按成员偏好顺序点亮柔光；人工接管有显式来源和自动过期，并逐项完成 HA/ESPHome 状态回读。
- 计划编排 `SpaceButlerAgent`：输入空间快照，输出可解释 `ServicePlan`。
- 主动节能闭环：任意已绑定空间连续无人、窗户打开、空调运行且功率偏高时，首次建议确认，执行后四路回读；用户授权后同类场景自动执行。
- 真实执行底座：Home Assistant REST → ESPHome Native API → 六个虚拟节点共 10 台设备；MQTT 链路已整体移除。
- 品牌适配器层：10 台设备分属小米米家 / Aqara / 美的 / 鸿蒙智联 / Philips Hue 五个生态，同一意图按品牌翻译成 MIoT siid/piid、Zigbee 簇命令、Hue 0-254 亮度等原生报文（见 `spacebutler/brands.py`）。
- LangGraph 编排：`observe → understand → plan → execute → verify → respond`，将状态观察、语义目标、任务拆解、真实执行和回读验证显式分离。
- 实体注册表：稳定实体 ID 与 `Living Room Light`、`Living Room Curtain`、`Living Room AC`、`Living Room Presence` 等名称别名统一解析。
- 动态主动规则：选择空间及三路设备来源，绑定持久化；实时展示设备实体、上报时间和每项触发条件。
- 故障防护：设备拒绝、ACK 但状态不变、延迟、离线、超时和多动作部分成功均不误报为全部成功。
- 边缘语言路由：调用 llama.cpp 理解自然语言偏好，模型候选必须通过字段白名单和范围校验。
- 现场工作台：AI 管家、空间与设备、主动服务、执行事件四个视图；夜间安全场景可绑定起身/照度实体、查看实时条件和接管 TTL，并模拟真实上升沿与执行回读。

## 完整工作台运行（HA / ESPHome / LLM）

麦克风入口支持长按 **V** 说话、实时显示字幕、松开后提交 Agent。
语音 Key 配置、权限排查和测试步骤见 [语音输入说明](docs/VOICE_INPUT.md)。
HA 已连接但工作台显示 0 台时，见 [设备匹配与只读诊断](docs/DEVICE_DISCOVERY.md)。

ESPHome 的六个节点必须已经运行，并把其现有 Noise PSK 填入本目录 `.env` 的 `ESPHOME_NOISE_PSK`；程序不会生成或覆盖密钥。HA 已部署在虚拟机时，复用该实例，不需要再启动 Windows 本地 HA。

例如 HA 和 ESPHome 同在 Ubuntu、HA 使用 Docker host 网络时：

```dotenv
SPACEBUTLER_HA_URL=http://192.168.111.134:8123
HA_TOKEN=<该 HA 实例的长期访问令牌>
ESPHOME_HOST=127.0.0.1
ESPHOME_NOISE_PSK=<现有 ESPHome API 加密密钥>
SPACEBUTLER_LLM_BASE_URL=http://127.0.0.1:12881
EDGE_LLM_URL=http://127.0.0.1:12881
```

以上地址需按实际部署调整。`ESPHOME_HOST` 是 **HA 访问 ESPHome 的地址**，这里的 `127.0.0.1` 指 HA 所在 Ubuntu；`SPACEBUTLER_HA_URL` 则是 Windows 工作台访问 HA 的地址。

```powershell
# 在子项目目录使用前面选定的 $python；HA、ESPHome 和模型需先就绪。
& $python scripts\run_workbench.py --port 8765
```

浏览器打开 `http://127.0.0.1:8765`。启动脚本会检查配置的 HA、获取令牌并确认 ESPHome 集成；已有集成会复用。以下是项目本地 Compose 的默认值，虚拟机部署以 `.env` 为准：

| 服务 | 默认地址 |
| --- | --- |
| 工作台 | `http://127.0.0.1:8765` |
| Home Assistant | `http://127.0.0.1:12900` |
| Light 节点（客厅主灯） | `192.168.111.134:6053` |
| Curtain 节点（客厅窗帘） | `192.168.111.134:6054` |
| AC 节点（客厅空调 + 温度） | `192.168.111.134:6055` |
| Presence 节点（客厅人体存在） | `192.168.111.134:6056` |
| Bedroom 节点（阅读灯 + 起身 + 照度） | `192.168.111.134:6057` |
| Living Room Aux 节点（电视 + 功耗 + 窗户） | `192.168.111.134:6058` |
| 可选 llama.cpp | `http://127.0.0.1:12881` |

自然语言入口必须连接 OpenAI-compatible 本地模型；模型不可用时会明确拒绝执行，绝不退回“观影模式→固定脚本”。推荐 `Qwen2.5-7B-Instruct-Q4_K_M.gguf`，该规模足以完成当前结构化语义解析和任务规划。完整配置见 [安装指南](docs/SETUP.md)。

## 客厅舒适观影 Demo（ESPHome 真实设备链路 + 自治智能体）

客厅主灯、电动窗帘、空调温控（含室温环境模型）和人体存在传感器由 Ubuntu `Care_Home_Agent/infra/esphome` 中已运行的节点提供，经 ESPHome Native API 接入 Home Assistant。Compose 内同名 `spacebutler-esphome-*` 服务位于 `local-esphome` profile，默认不会启动，避免出现两套重复设备。

在既有设备链路之上，SpaceButler 现在是一个**自治家庭智能体**（架构详见 [docs/AGENT_ARCHITECTURE.md](docs/AGENT_ARCHITECTURE.md)），三个核心机制全部在真实执行链路中：

1. **LangGraph + 反事实规划**：图节点先观察实时状态，再由模型把自然语言编译为环境约束，生成候选任务、预测未来状态并评分选优。模型不可用或语义输出不合法时零命令退出；确定性代码只负责能力约束、安全、评分与验证，不负责猜测用户场景。
2. **人工纠正的隐式偏好学习**：目标活跃期间用户手动调整设备 → 识别为 Human Override（与 Agent 自身动作、环境漂移严格归因区分）→ 写入偏好观测 → 至少两次相近纠正后，后续规划自动生成并偏向"偏好匹配"候选。用户从不需要说"记住我喜欢 45%"。
3. **物理反馈驱动的持续自治**：说出目标后无需继续对话——后台运行时持续感知环境；通过 ESPHome 固件服务注入热量等真实扰动会触发 目标偏离 → 自动重规划 → 重新执行验证（含防震荡：debounce、冷却期、重试上限）。

按交付方案演示（工作台 AI 管家对话框 + "Agent Activity" 决策链面板）：

```powershell
# 复用已配置的 HA、ESPHome 和模型，不重复创建设备。
& $python scripts\run_workbench.py --port 8765
```

打开工作台的 **「演示控制台」** 视图，全部操作在页面内完成，不需要另开终端：
先点「就绪自检」确认 7 项全绿，再点「一键重置」（约 12 秒，窗帘是真实行程），
然后逐个点场景卡片。每张卡片带预期现象和可直接照读的旁白。

| Demo | 操作 | 预期现象 |
|---|---|---|
| 1. AI 动态规划 | 场景卡片 ①，或输入 `我准备在客厅看会电影，有点热，灯别太亮。` | 模型按当前状态动态生成计划（通常是调灯 + 开空调，是否关窗帘取决于实时光照与温度），执行并逐项 HA 回读验证 |
| 2. 已满足则不动 | 场景卡片 ②，或再问 `现在适合看电影了吗？` | 判定目标已满足，零设备命令，回复"无需重复调整" |
| 3. 人工纠正学习 | 场景卡片 ③ ×3（等价于 `scripts\demo_override.py --brightness 45`） | 时间线出现 Human Override；偏好观测置信度上升；再说"我要看电影，别太暗"时选中偏好匹配候选（≈45%） |
| 4. 扰动自治重规划 | 场景卡片 ④，之后不要说话 | 固件注入热量 → 目标偏离 → 自动重规划 → 新计划执行验证（区分 Agent 与 Chatbot 的关键） |

完整录制流程、旁白逐句和边界声明见 [演示视频录制手册](docs/VIDEO_DEMO_SCRIPT.md)。

最关键的验收是同一场景连续两次：第一次产生 3 个动作；第二次提问或重复同一请求时，Graph 会重新观察状态并输出零动作，明确说明窗帘、灯光和空调已经满足目标。测试见 `tests/test_langgraph_movie_replanning.py`。

运行本地验证：

```powershell
python -m pytest tests -q
python -m ruff check .
node --check workbench\app.js
python scripts\run_iteration.py
```

首页可直接添加设备并按类型控制；“主动服务”可绑定节能规则来源，也可配置老人夜间起身的有序灯光路径、照度门槛和人工接管灯。新增设备在 ESPHome 节点侧注册后由 Home Assistant 自动发现。工作台使用项目内置前端资源，不依赖公网 CDN；Home Assistant Token 仅保留在服务端。

同类开源项目调研、差距分析与场景选择见 `docs/OPEN_SOURCE_PROJECT_COMPARISON.md`；小米 IoT、阿里云生活物联网平台对比及一设备一容器设计见 `docs/DEVICE_CONTAINER_ARCHITECTURE.md`。

## 外部验收

Docker Desktop 运行时执行：

```powershell
python scripts\run_external_acceptance.py
```

将 `Home-Llama-3.2-3B.q4_k_m.gguf` 放入 `deployment/models/`，或通过 `SPACEBUTLER_MODELS_DIR` 指向模型目录。当前工作区存在旧项目模型时，验收脚本会迁移复用该 GGUF 制品，但始终启动 KDXF 自己的 llama.cpp 容器。

该门禁在独立进程中验证 `Agent -> Home Assistant REST -> MQTT -> 目标设备容器 -> 独立 SQLite -> MQTT/HA 回读`，并覆盖单设备容器故障隔离、服务重启、KDXF Compose llama.cpp、动态设备容器注册/控制/删除、动态主动规则绑定和工作台 API。报告写入 `reports/external/`。也可用 `python scripts\run_iteration.py --external` 同时执行本地回归与外部门禁。
