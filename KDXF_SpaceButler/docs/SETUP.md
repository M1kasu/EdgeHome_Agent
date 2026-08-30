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
- `E:\Anaconda\envs\CareHomeAgent`：不是完整可执行环境，未找到 `python.exe`，不要使用。
- `D:\Public Project\langgraph-main`：本地 LangGraph 源码参考，`pyproject.toml` 版本 1.2.2；本项目使用相同的 `StateGraph / START / END / conditional_edges` 范式，不修改该源码仓库。

安装项目：

```powershell
cd KDXF_SpaceButler
$python = 'E:\Anaconda\envs\Agent-Hub\python.exe'
& $python -m pip install -e ".[dev]"
```

## 3. Noise PSK

必须复制 Ubuntu `infra/esphome/secrets.yaml` 中正在使用的 32-byte Base64 key，并通过环境变量传入。不要把真实密钥写入 `.env.example`、源码或 Git。

```powershell
$env:ESPHOME_NOISE_PSK = '<现有 api_encryption_key>'
$env:ESPHOME_HOST = '192.168.111.134'
```

缺少、Base64 非法或解码后不是 32 bytes 时，工作台会停止启动。代码不会自动生成新密钥。

## 4. 启动 Home Assistant

```powershell
docker compose -f deployment\docker-compose.yml up -d spacebutler-home-assistant
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

## 6. 启动模型

推荐 `Qwen2.5-7B-Instruct-Q4_K_M.gguf`。7B 指令模型能够完成当前中文目标解析、结构化 JSON 生成和候选任务规划；真实效果仍应通过场景测试确认。

```powershell
docker compose -f deployment\docker-compose.yml --profile edge-llm up -d spacebutler-edge-llm
$env:SPACEBUTLER_LLM_BASE_URL = 'http://127.0.0.1:12881'
$env:SPACEBUTLER_LLM_TIMEOUT = '240'
```

本次检查时 `127.0.0.1:12881` 与 `127.0.0.1:8080` 均无监听，常用目录也未发现 GGUF，因此“当前实际运行模型”为无。没有模型时 Agent 会安全拒绝自然语言执行，不会用关键词或固定场景脚本猜测动作。

## 7. 启动工作台

```powershell
& $python scripts\run_workbench.py --port 8765
```

启动程序会：

1. 要求并校验现有 Noise PSK；
2. 等待 HA 就绪并获取 Token；
3. 按 `6053 / 6054 / 6055 / 6056` 创建 ESPHome config entry；
4. 启动 LangGraph Agent 和网页工作台。

打开 `http://127.0.0.1:8765`。

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

当前回归基线为 `113 passed, 19 subtests passed`。

## 11. 故障排查

| 现象 | 处理 |
|---|---|
| 启动时报 `ESPHOME_NOISE_PSK is required` | 从 Ubuntu 复制现有密钥到当前终端环境变量；不要生成新密钥 |
| Windows 可达但网络脚本失败 | HA 容器路由/防火墙问题；在容器内检查到 `192.168.111.134` 的路由，并放行 6053–6056 |
| HA 出现两套设备 | 确保未启用 `local-esphome` 或 `legacy-simulator` profile，清理 HA 中旧 config entries 后按真实端口重接 |
| 实体 ID 带 `_2` | 旧 HA 注册表残留；别名注册表可解析常见名称，但建议删除重复 config entry 后重建 |
| 对话提示模型不可用 | 启动 OpenAI-compatible 模型服务，检查 `/health`/`/v1/models` 与 `SPACEBUTLER_LLM_BASE_URL` |
| 第二轮仍重复动作 | 先检查 HA 状态是否真实回读为目标值；若未回读，执行器会正确重试而不是假定成功 |
