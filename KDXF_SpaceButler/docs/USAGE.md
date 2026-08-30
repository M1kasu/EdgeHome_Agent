# SpaceButler 使用指南

详细安装与排障见 [SETUP.md](SETUP.md)。本页只保留日常操作。

## 启动

```powershell
cd KDXF_SpaceButler
$python = 'E:\Anaconda\envs\Agent-Hub\python.exe'
$env:ESPHOME_HOST = '192.168.111.134'
$env:ESPHOME_NOISE_PSK = '<Ubuntu 正在使用的现有密钥>'
$env:SPACEBUTLER_LLM_BASE_URL = 'http://127.0.0.1:12881'

docker compose -f deployment\docker-compose.yml up -d spacebutler-home-assistant
& $python scripts\check_ha_esphome_network.py
& $python scripts\run_workbench.py --port 8765
```

访问：

- Workbench：`http://127.0.0.1:8765`
- Home Assistant：`http://127.0.0.1:12900`

不要为正式运行添加 `--profile local-esphome` 或 `--profile legacy-simulator`，否则可能制造重复设备。

## 对话 Agent

正式对话入口由 LangGraph 编排：

```text
observe → understand → activate → plan → execute → verify → respond
```

它不是“场景名映射固定脚本”。每次输入都会重新读取 HA 状态：

1. 第一次说“我准备在客厅看会电影，有点热，灯别太亮。”时，当前状态若不满足目标，会规划窗帘、灯和空调动作。
2. 立即再问“现在适合看电影了吗？”或重复原请求时，若状态已满足，Agent 返回 `NO ACTION`，不会重复下发命令。
3. 模型不可用或结构化语义输出失败时，Agent 返回不可用说明并保持零设备命令。

## Demo 脚本

```powershell
& $python scripts\demo_start.py
& $python scripts\demo_reset.py
& $python scripts\demo_disturbance.py --temperature 31
& $python scripts\demo_status.py
```

`demo_reset.py` 和 `demo_start.py` 使用稳定实体 ID 加名称别名解析，不再依赖旧 `SpaceButler Living Room ...` 固定名称。热扰动 action 可通过 `ESPHOME_HEAT_INJECTION_SERVICE` 覆盖。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ESPHOME_HOST` | `192.168.111.134` | Ubuntu ESPHome 主机 |
| `ESPHOME_NOISE_PSK` | 无，必填 | 已有 32-byte Base64 Noise PSK |
| `SPACEBUTLER_HA_URL` | `http://127.0.0.1:12900` | HA REST 地址 |
| `SPACEBUTLER_LLM_BASE_URL` | 自动探测本机服务 | OpenAI-compatible 模型地址 |
| `SPACEBUTLER_LLM_MODEL` | 空 | 可选模型名 |
| `SPACEBUTLER_LLM_API_KEY` | 空 | 可选 API key |
| `SPACEBUTLER_LLM_TIMEOUT` | `40` | 单次模型调用超时；7B CPU 可设 `240` |
| `ESPHOME_HEAT_INJECTION_SERVICE` | `living_room_ac_demo_heat_injection` | ESPHome 热扰动 action 名 |

## 验证

```powershell
& $python -m pytest -q
& $python -m ruff check .
node --check workbench\app.js
```

网络验证必须从 HA 容器执行 `scripts/check_ha_esphome_network.py`；宿主机端口可达不能替代这一步。
