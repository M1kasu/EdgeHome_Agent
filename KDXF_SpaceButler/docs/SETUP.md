# SpaceButler 安装与运行指南（交接版）

本文档面向接手本项目的开发者，覆盖从零到完整演示的全部步骤、已知坑与日常开发命令。

## 1. 项目是什么

SpaceButler 是一个目标驱动的家庭自治智能体：用户只表达环境目标（"客厅看会电影，有点热，灯别太亮"），系统由本地大模型完成目标理解与多候选规划，经 Home Assistant / ESPHome 执行真实设备，并逐项回读验证、持续监控、自动重规划、从人工纠正中学习偏好。

核心链路：

```text
自然语言 → Goal Compiler(LLM) → World Model → 候选计划(LLM+确定性)
→ 反事实预测 → 多目标评分 → Policy Guard → Home Assistant
→ ESPHome Native API → 真实设备 → 状态回读验证 → 监控/重规划/偏好学习
```

架构细节见 `docs/AGENT_ARCHITECTURE.md`。

## 2. 前置条件

| 依赖 | 版本 | 用途 |
|---|---|---|
| Windows 10/11 | — | 开发/演示机（Linux 同理，路径自行调整） |
| Python | 3.11+ | 工作台、agent、脚本 |
| Docker Desktop | Linux 引擎 | HA / MQTT / 设备容器 / ESPHome 节点 / llama.cpp |
| Node.js | 18+（可选） | 前端 JS 语法检查 |
| GPU | 可选 | RTX 4070 8GB 实测可用；无 GPU 时 CPU 推理可用但较慢 |

## 3. 获取代码

```powershell
git clone https://github.com/M1kasu/EdgeHome_Agent.git
cd EdgeHome_Agent
git checkout feature/model-driven-agent-v2
```

> 注意：KDXF_SpaceButler 位于该仓库的 `KDXF_SpaceButler/` 子目录，后续命令如无说明均在 `KDXF_SpaceButler/` 下执行。

## 4. Python 环境

```powershell
cd KDXF_SpaceButler
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## 5. 模型准备（二选一）

### 方案 A：默认 3B（资源占用小，开箱即用）

模型文件：`Home-Llama-3.2-3B.q4_k_m.gguf` 放入 `deployment/models/`。
若内网有旧项目制品可直接复制；否则从 HuggingFace 搜索同名 GGUF。

### 方案 B：Qwen2.5-7B-Instruct（效果显著更好，推荐演示机）

```powershell
curl.exe -L -C - -o deployment\models\Qwen2.5-7B-Instruct-Q4_K_M.gguf `
  "https://hf-mirror.com/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/Qwen2.5-7B-Instruct-Q4_K_M.gguf"
```

> 实测 4.36GB，`-C -` 支持断点续传（网络抖动时重复执行即可）。

实测对比（同代码零改动）：3B 的 full_llm_chain ≈ 0.79 / generalization ≈ 0.65–0.71，且存在语义判定波动；7B 在 held-out 10 条上 compile 0.9 / plan 0.9 / satisfaction 0.892——**模型越强系统越强，业务代码无需改动**。

## 6. 启动 LLM 服务（二选一）

### 方案 A：docker 运行 Qwen 7B（推荐）

```powershell
docker run -d --name spacebutler-qwen-7b `
  -p 127.0.0.1:12882:8080 `
  -v (Resolve-Path .\deployment\models):/models:ro `
  ghcr.io/ggml-org/llama.cpp:server `
  -m /models/Qwen2.5-7B-Instruct-Q4_K_M.gguf -c 4096 --host 0.0.0.0 --port 8080 --threads 8
```

### 方案 B：3B（compose profile）

```powershell
docker compose -f deployment\docker-compose.yml --profile edge-llm up -d spacebutler-edge-llm
```

CPU 推理说明：7B 每次结构化调用约 1–3 分钟（4070 笔记本 CPU 实测），演示前建议预热一次；3B 秒级响应。

## 7. 启动家庭设备栈

```powershell
Copy-Item .env.example deployment\.env   # 按需改端口；默认端口开箱可用
# 首次必须先生成 ESPHome 密钥（工作台启动时也会自动生成，但先生成可避免容器重启循环）
python -c "import sys; sys.path.insert(0, 'scripts'); from pathlib import Path; from ha_bootstrap import ensure_esphome_secrets; ensure_esphome_secrets(Path('deployment/esphome/secrets.yaml'))"

docker compose -f deployment\docker-compose.yml up -d
```

首次启动 ESPHome 四节点需要编译固件（10–20 分钟，之后有缓存秒起）。
此栈包含：MQTT、9 个 MQTT 设备容器（客厅四件套已由 ESPHome 替代，处于
`legacy-mqtt-living` profile 默认不启动）、Fleet Gateway(12891)、
Home Assistant(12900)、4 个 ESPHome Host 节点（内部 6053）。

## 8. 启动工作台

```powershell
$env:SPACEBUTLER_LLM_BASE_URL = 'http://127.0.0.1:12882'   # Qwen；3B 用 12881
$env:SPACEBUTLER_LLM_TIMEOUT = '240'                        # 7B CPU 推理建议放宽
python scripts\run_workbench.py --port 8765
```

工作台会自动完成：HA onboarding / Token 获取 / MQTT 配置 / 4 个 ESPHome 集成接入
（首次会等待固件编译，脚本内置重试）。看到
`SpaceButler workbench: http://127.0.0.1:8765` 即成功。

## 9. 一键演示检查（每次演示前必跑）

```powershell
python scripts\demo_start.py
```

全部 `[OK]` 输出 `SpaceButler Demo Ready` 才开始演示。检查项：Workbench、HA、
五个 ESPHome 设备实体、LLM（可选，未运行时如实提示 fallback 模式）、Agent runtime。

## 10. 演示流程（四幕）

```powershell
python scripts\demo_reset.py    # 恢复初始状态：灯80%/窗帘全开/空调关/30.2°C
```

打开 `http://127.0.0.1:8765` → 左侧"AI 管家"，按顺序：

| 幕 | 输入/操作 | 看什么 |
|---|---|---|
| 1 目标理解与规划 | 输入"我准备在客厅看会电影，有点热，灯别太亮，但是别弄得完全黑。" | Agent Activity 面板：模型语义理解 → 多候选评分 → 选优 → 三项动作执行 → 逐项 verified |
| 2 已满足不折腾 | 立刻再问"现在适合看电影了吗？" | 零设备命令，回复"已满足，无需重复调整" |
| 3 人工纠正学习 | 手动把主灯调到 45%（或 `python scripts\demo_override.py --brightness 45`）重复 2–3 次 | 时间线出现 Human Override；偏好观测置信度上升；再说"我要看电影，别太暗"时规划会偏向 45% 左右 |
| 4 扰动自治重规划 | `python scripts\demo_disturbance.py --temperature 31`，之后**不要说话** | 固件注入热量 → 目标偏离 → 自动重规划 → 执行验证（不说话证明不是 AI 客服） |

状态总览随时可用：`python scripts\demo_status.py`；系统健康：`http://127.0.0.1:8765/api/health`。

## 11. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `SPACEBUTLER_LLM_BASE_URL` | 自动探测 12881/8080 | OpenAI 兼容 LLM 地址 |
| `SPACEBUTLER_LLM_MODEL` | 空 | 模型名（llama.cpp 可留空） |
| `SPACEBUTLER_LLM_API_KEY` | 空 | 可选 |
| `SPACEBUTLER_LLM_TEMPERATURE` | 0.1 | 结构化输出建议低温 |
| `SPACEBUTLER_LLM_TIMEOUT` | 40 | 单次调用超时秒数（7B CPU 建议 240） |
| `SPACEBUTLER_HA_PORT` | 12900 | HA 端口 |
| `SPACEBUTLER_SIMULATOR_PORT` | 12891 | Fleet Gateway |
| `SPACEBUTLER_MQTT_PORT` | 18884 | MQTT |

## 12. 端口总表

| 端口 | 服务 |
|---|---|
| 8765 | 工作台 UI + Agent API |
| 12900 | Home Assistant |
| 12891 | Fleet Gateway |
| 18884 | MQTT |
| 12881 | llama.cpp（edge-llm profile） |
| 12882 | Qwen 7B（docker run 方式时） |
| 8899 | EdgeHome_Agent 项目的 HA（**另一个项目**，勿混淆） |

## 13. 常见故障排查

| 现象 | 原因与处理 |
|---|---|
| `demo_start` 报 ESPHome 实体 unavailable | 四节点容器未 healthy：`docker ps --filter name=spacebutler-esphome`；首次编译 10–20 分钟，看 `docker logs spacebutler-spacebutler-esphome-light-1` |
| 端口 8765 被占用 / 页面是旧版 | `Get-NetTCPConnection -LocalPort 8765 \| %% { Stop-Process -Id $_.OwningProcess -Force }` 后重启工作台 |
| chat 无响应很久 | LLM 推理慢：确认 `SPACEBUTLER_LLM_TIMEOUT`；LLM 挂了会自动确定性 fallback（UI 显示 fallback，不是故障） |
| 规划动作打不到设备 / 实体带 `_2` | HA 注册表残留旧条目所致；工作台按 friendly_name 匹配已兼容，勿手工改实体 ID |
| ESPHome 容器重启循环 | `deployment/esphome/secrets.yaml` 缺失或损坏——删掉后重启工作台会自动重新生成 |
| 想恢复 MQTT 客厅设备做对照 | `docker compose --profile legacy-mqtt-living up -d`（注意与 ESPHome 设备同 device_id，勿同时用于演示） |

## 14. 日常开发验证

```powershell
python -m pytest tests -q        # 101 passed 预期
python -m ruff check .           # All checks passed 预期
node --check workbench\app.js    # 前端语法
python scripts\run_agent_benchmark.py --limit 6 --ticks 3   # smoke（需 LLM）
```

完整 benchmark（34 条）只在重大版本后跑一次：

```powershell
python scripts\run_agent_benchmark.py --split all --ticks 3
python scripts\run_agent_benchmark.py --split dev  --ticks 3   # 前 24 条调优用
python scripts\run_agent_benchmark.py --split test --ticks 3   # 后 10 条 held-out
```

## 15. 关键脚本清单

| 脚本 | 用途 |
|---|---|
| `run_workbench.py` | 启动工作台（含全部 bootstrap） |
| `demo_start.py` | 演示就绪检查 |
| `demo_reset.py` | 恢复演示初始状态 |
| `demo_override.py` | 模拟人工纠正（真实经 HA） |
| `demo_disturbance.py` | 注入热扰动（真实经 ESPHome 固件服务） |
| `demo_status.py` | 命令行查看 Agent 决策链 |
| `diagnose_planner.py` | planner fallback 长尾失败分布 |
| `diagnose_satisfaction.py` | 满意度未达标样本 ABCDEF 分类 |
| `run_agent_benchmark.py` | 完整 benchmark（scene/llm_direct/agent + ablation） |
| `ha_bootstrap.py` | HA onboarding/Token/MQTT/ESPHome 自动接入 |

## 16. 已知限制（如实）

1. 3B 模型语义判定存在波动（full_chain 0.77–0.82 区间震荡）；7B 明显更稳但 CPU 推理慢，演示机建议 GPU。
2. compile 偶发 3–4 例长尾（3B 对"恢复日常状态"类抽象表述提取约束失败），fallback 兜底且 UI 如实显示。
3. HA 事件为 2 秒轮询桥接（非 WebSocket 推送）。
4. 旧 HA 注册表残留会让新实体带 `_2` 后缀——系统已按 friendly_name 匹配兼容，勿手工改实体 ID。
5. 偏好学习当前覆盖亮度维度（温度/窗帘的观测结构已建，加一行映射即可扩展）。

## 17. Git 工作流

- 基线：`v0.2-agent-baseline` tag（`spacebutler/agent-baseline` 分支）
- 开发分支：`feature/model-driven-agent-v2`
- 优化全程记录：`results/optimization_history.jsonl`
- 提交前必跑：pytest + ruff + node check；禁止 force push；禁止提交 secrets（`.gitignore` 已覆盖 `secrets.yaml`/`.storage`/`runtime`）
