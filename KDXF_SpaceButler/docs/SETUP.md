# SpaceButler 安装与运行指南

本文档对应 `feature/model-driven-agent-v2`。正式链路只连接 Ubuntu 上已运行的 ESPHome 节点，不启动项目自带的硬编码模拟设备。

## 1. 当前运行拓扑

```text
浏览器 → Workbench / LangGraph Agent → Home Assistant REST
                                      ↓
                              ESPHome Native API
                                      ↓
                     Ubuntu 192.168.111.134:6053–6056
```

| 角色 | 地址 | 预期实体名称 |
|---|---|---|
| light | `192.168.111.134:6053` | `Living Room Light` |
| curtain | `192.168.111.134:6054` | `Living Room Curtain` |
| ac | `192.168.111.134:6055` | `Living Room AC`、`Living Room Temperature` |
| presence | `192.168.111.134:6056` | `Living Room Presence` |

主机地址可用 `ESPHOME_HOST` 覆盖，四个端口保持固定。别名解析集中在 `spacebutler/esphome_registry.py`。

## 2. Python 环境

本机检查结果：

- `E:\Anaconda\envs\Agent-Hub\python.exe`：Python 3.12.13，可用；LangGraph 1.1.6、pytest 和 paho-mqtt 已验证。
- 默认 `python` 指向 Python 3.10.7，不满足项目的 Python 3.11+ 要求；下文均使用明确的解释器路径。
- `E:\Anaconda\envs\CareHomeAgent`：不是完整可执行环境，未找到 `python.exe`，不要使用。
- `D:\Public Project\langgraph-main`：本地 LangGraph 源码参考，`pyproject.toml` 版本 1.2.2；本项目使用相同的 `StateGraph / START / END / conditional_edges` 范式，不修改该源码仓库。

安装项目：

```powershell
Set-Location 'D:\Project\EdgeHome_Agent-main\KDXF_SpaceButler'
$python = 'E:\Anaconda\envs\Agent-Hub\python.exe'
& $python -m pip install -e ".[dev]"
```

## 3. `.env` 与 Noise PSK

所有本地凭据填写在 `KDXF_SpaceButler/.env` 中。该文件已被 Git 忽略，启动器和独立语音脚本会自动加载，终端环境变量优先。
当前机器已填好语音 Key，不要覆盖 `.env`。其他机器首次安装时才复制模板：

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
```

编辑 `.env`，复制 Ubuntu `infra/esphome/secrets.yaml` 中正在使用的 32-byte Base64 key，并填写实际主机地址：

```dotenv
ESPHOME_NOISE_PSK='<现有 api_encryption_key>'
ESPHOME_HOST=192.168.111.134
SPACEBUTLER_HA_URL=http://127.0.0.1:12900
SPACEBUTLER_LLM_BASE_URL=http://127.0.0.1:12881
SPACEBUTLER_LLM_TIMEOUT=240
```

不要把真实值写入 `.env.example`、源码、命令示例或 Git。语音接入只需要 `SPACEBUTLER_ASR_API_KEY`，不使用通用云 AK/SK。
缺少、Base64 非法或解码后不是 32 bytes 时，工作台会停止启动。代码不会自动生成新密钥。
`.env` 修改后需重启工作台；它是本地明文配置，不应上传或截图分享。

只验证语音时，先执行以下命令，无需 Docker、HA、ESPHome 或 Agent 模型：

```powershell
& $python scripts\check_voice_asr.py
# 可选：本机已有的中文合成语音，不会触发任何设备操作。
& $python scripts\check_voice_asr.py --wav deployment\runtime\voice-verification\synthetic-command.wav
```

无文件参数时发送一秒静音，成功标志是 `connected: true` 和最后的 `state: "final"`。
文件参数必须为 16kHz、单声道、PCM16 WAV；有声音时应看到 `partial` 和最终文字。

## 4. 启动 Home Assistant

本机当前 PATH 未发现 Docker CLI。若使用本机容器，需要先安装/启动 Docker Desktop，并确认 `docker version` 可连接 Docker 引擎。
如果 HA 已在其他主机运行，则不重复创建本机容器：将 `.env` 的 `SPACEBUTLER_HA_URL` 改成实际地址，
并在 `.env` 设置该实例的 `HA_TOKEN`（长期访问令牌）。

```powershell
docker compose --env-file .env -f deployment\docker-compose.yml up -d spacebutler-home-assistant
```

以下服务默认禁用：

- 四个项目内 `spacebutler-esphome-*`：只有显式指定 `--profile local-esphome` 才会启动；连接 Ubuntu 节点时不要启用。
- 旧 MQTT/Fleet/硬编码设备：只有 `--profile legacy-simulator` 才会启动；正式 Demo 不要启用。

若 Ubuntu 上此前已经运行了项目内同名容器，这里的 Compose 不会管理或重启它们。

## 5. 验证 HA 容器网络

仅在 Windows 主机上测试端口不够，必须从 HA 容器的网络命名空间测试：

```powershell
& $python scripts\check_ha_esphome_network.py
```

脚本会执行：

```text
docker exec spacebutler-home-assistant python -c socket.create_connection(...)
```

四个端口都显示 `reachable: true` 后再启动工作台。如果 Docker 不在当前 Windows 主机上，请在实际运行 HA 的 Ubuntu/主机上执行等价命令。

## 6. 配置并启动模型

已有 OpenAI-compatible 模型服务时，只需在 `.env` 填写其 `SPACEBUTLER_LLM_BASE_URL`。
如有鉴权或指定模型要求，再填写 `SPACEBUTLER_LLM_API_KEY` 和 `SPACEBUTLER_LLM_MODEL`。无需重复启动本地模型。

使用项目内 llama.cpp 容器时，必须先准备实际的 GGUF 模型文件；Compose 不会自动下载模型。
例如模型实际位于 `D:/Models/Qwen2.5-7B-Instruct-Q4_K_M.gguf`，则在 `.env` 中启用以下设置，其他文件名请相应替换：

```dotenv
SPACEBUTLER_MODELS_DIR=D:/Models
SPACEBUTLER_MODEL_PATH=/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf
SPACEBUTLER_LLM_BASE_URL=http://127.0.0.1:12881
EDGE_LLM_URL=http://127.0.0.1:12881
```

第一项是 Windows 主机上的模型目录，第二项是容器内文件路径。确认文件存在后启动：

```powershell
docker compose --env-file .env -f deployment\docker-compose.yml --profile edge-llm up -d spacebutler-edge-llm
Invoke-RestMethod http://127.0.0.1:12881/v1/models
```

没有可用模型时 Agent 会拒绝自然语言执行，不会用关键词或固定场景脚本猜测动作。模型可用性与 ASR 可用性需分别验证。

## 7. 启动工作台

```powershell
& $python scripts\run_workbench.py --port 8765
```

启动程序会：

1. 加载项目 `.env`，要求并校验现有 Noise PSK；
2. 等待 HA 就绪并获取 Token；
3. 按 `6053 / 6054 / 6055 / 6056` 创建 ESPHome config entry；
4. 启动 LangGraph Agent 和网页工作台。

打开 `http://127.0.0.1:8765`。保持终端运行，按 Ctrl+C 停止服务。前端由 Python 同时提供，无需 npm/Vite 单独启动。
可选 PowerShell 包装器：`./scripts/start_workbench_local.ps1 -Python $python -Port 8765`；它使用相对项目位置，不再读取旧机器的目录或覆盖配置。
默认仅监听本机地址，不要直接向公网开放工作台。

进入「AI 管家」，允许麦克风权限。按住 V，看到「正在聆听」后讲话，确认实时字幕更新；松开后等待最终结果，随后进入 Agent。
初次建议询问“客厅主灯现在是什么状态？”，再测试会改变设备状态的指令。Esc 取消且不提交，快速轻按 V 仍正常输入字母。
普通局域网 HTTP 无法使用麦克风，需 localhost/127.0.0.1 或 HTTPS。完整操作与排错见 [语音输入说明](VOICE_INPUT.md)。

## 8. 双轮场景验收

先恢复可控设备初始状态：

```powershell
& $python scripts\demo_reset.py
```

第一次输入：

> 我准备在客厅看会电影，有点热，灯别太亮。

Agent 先观察状态，再由模型生成环境目标，规划并执行 `关窗帘 + 调灯 + 开空调`，最后逐项回读。

紧接着输入：

> 现在适合看电影了吗？

也可以再次输入同一请求。Graph 会重新执行 observe；若窗帘已关闭、灯已 20%、空调正在向 26℃ 制冷，则计划为 `NO ACTION`，回复当前状态并说明无需重复调整。

该行为已有自动化测试：

```powershell
& $python -m pytest tests\test_langgraph_movie_replanning.py -q
```

测试断言第一轮恰好下发三类设备动作，第二轮下发零动作，并检查完整 Graph trace。

## 9. 热扰动 Demo

Ubuntu 的 `ac.yaml` 需要声明 `demo_heat_injection` action。项目内参考配置 `deployment/esphome/ac.yaml` 已包含该 action。

```powershell
$env:ESPHOME_HEAT_INJECTION_SERVICE = 'living_room_ac_demo_heat_injection'
& $python scripts\demo_disturbance.py --temperature 31
```

Home Assistant 的服务名取决于实体命名；若实际名称不同，以 Developer Tools 中的 ESPHome action 名为准覆盖环境变量。

## 10. 验证命令

```powershell
& $python -m pytest -q
& $python -m ruff check .
node --check workbench\app.js
git diff --check
```

本次回归为 `137 passed, 19 subtests passed`，前端语音测试 `node --test tests/voice-input.test.cjs` 为 10 项通过。

## 11. 故障排查

| 现象 | 处理 |
|---|---|
| 启动时报 `ESPHOME_NOISE_PSK is required` | 从 Ubuntu 复制现有密钥到 `.env`，再重启；不要生成新密钥 |
| Windows 可达但网络脚本失败 | HA 容器路由/防火墙问题；在容器内检查到 `192.168.111.134` 的路由，并放行 6053–6056 |
| HA 出现两套设备 | 确保未启用 `local-esphome` 或 `legacy-simulator` profile，清理 HA 中旧 config entries 后按真实端口重接 |
| 实体 ID 带 `_2` | 旧 HA 注册表残留；别名注册表可解析常见名称，但建议删除重复 config entry 后重建 |
| 对话提示模型不可用 | 启动 OpenAI-compatible 模型服务，检查 `/health`/`/v1/models` 与 `SPACEBUTLER_LLM_BASE_URL` |
| 第二轮仍重复动作 | 先检查 HA 状态是否真实回读为目标值；若未回读，执行器会正确重试而不是假定成功 |
