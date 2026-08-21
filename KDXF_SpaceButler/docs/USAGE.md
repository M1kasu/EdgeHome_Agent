# SpaceButler 使用指南

## 1. 环境要求

- Windows 10/11 与 PowerShell 7；核心 Python 代码也可在 Linux/macOS 运行。
- Python 3.11 或更高版本。
- Docker Desktop，使用 Linux containers。
- 可选：Node.js，用于运行前端 JavaScript 语法检查。
- 可选：兼容 llama.cpp 的 GGUF 模型。没有模型时系统自动使用确定性语言降级。

## 2. 安装依赖

在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example deployment\.env
```

`deployment/.env` 用于覆盖 Docker 宿主端口和模型目录，不应提交到 Git。

## 3. 启动服务

先启动 MQTT、设备模拟器和 Home Assistant：

```powershell
docker compose -f deployment\docker-compose.yml up -d --build
docker compose -f deployment\docker-compose.yml ps
```

再启动工作台：

```powershell
python scripts\run_workbench.py --port 8765
```

首次运行时，脚本会等待 Home Assistant 就绪、完成本地 onboarding、获取 Token，并幂等配置 MQTT。浏览器访问：

- SpaceButler：`http://127.0.0.1:8765`
- Home Assistant：`http://127.0.0.1:12900`

默认宿主端口：

| 环节 | 端口 | 环境变量 |
| --- | ---: | --- |
| MQTT | `2884` | `SPACEBUTLER_MQTT_PORT` |
| 设备模拟器 | `12891` | `SPACEBUTLER_SIMULATOR_PORT` |
| Home Assistant | `12900` | `SPACEBUTLER_HA_PORT` |
| llama.cpp | `12881` | `SPACEBUTLER_MODEL_PORT` |
| 工作台 | `8765` | 命令行参数 `--port` |

## 4. 启用边缘模型

将 `Home-Llama-3.2-3B.q4_k_m.gguf` 放到 `deployment/models/`，然后执行：

```powershell
docker compose -f deployment\docker-compose.yml --profile edge-llm up -d spacebutler-edge-llm
```

也可在 `deployment/.env` 中指定外部目录：

```dotenv
SPACEBUTLER_MODELS_DIR=E:/models
SPACEBUTLER_MODEL_PATH=/models/Home-Llama-3.2-3B.q4_k_m.gguf
```

模型不可用时，AI 管家顶部会显示“确定性降级”。此状态不会关闭主动规则、设备控制或 HA/MQTT 回读。

## 5. 体验夜间安全路径

1. 打开“主动服务”，找到“老人夜间起身安全路径”。
2. 选择起身空间、人体存在传感器和照度传感器。
3. 勾选 1 至 6 盏路径灯；勾选顺序就是执行顺序。
4. 设置路径亮度和触发照度，点击“保存路径”。
5. 在“保持手动点亮”中选择一盏灯，点击“准备状态”。系统会写入测试照度，并为该灯建立 30 分钟人工接管上下文。
6. 点击“模拟起身”。页面应显示动作数、执行状态以及 HA 回读 `PASS`。

规则运行时只接受人体存在实体的 `off -> on` 上升沿。环境照度高于阈值时不补光；人工接管、受保护、离线或不可用灯具保持不变。真实传感器通过同一 HA 实体上报后，无需点击模拟按钮也会自动触发。

## 6. 设备与故障测试

- “空间与设备”可新增灯光、开关、空调、窗帘、人体存在、门窗和照度传感器。
- 新设备通过 MQTT Discovery 自动注册到 Home Assistant。
- 设备卡片可注入离线、拒绝、延迟、ACK 不变和无效状态等故障。
- “执行事件”显示设备事件和协议反馈，用于核对是否真实到达目标状态。

## 7. 验证

本地回归：

```powershell
python -m pytest tests -q
python -m ruff check .
node --check workbench\app.js
python scripts\run_iteration.py
```

完整 Docker 外部门禁需要 GGUF 模型：

```powershell
python scripts\run_external_acceptance.py
```

报告分别写入 `reports/iterations/` 和 `reports/external/`。

## 8. 常见问题

### Docker 提示端口不可用

编辑 `deployment/.env`，把冲突端口改为未占用的高位端口。若同时手工运行验收脚本，需要设置对应 URL，例如：

```powershell
$env:SPACEBUTLER_HA_URL = "http://127.0.0.1:13900"
$env:SPACEBUTLER_SIMULATOR_URL = "http://127.0.0.1:13891"
```

### 页面显示“来源不完整”

先检查容器状态，再等待 MQTT Discovery 完成：

```powershell
docker compose -f deployment\docker-compose.yml ps
Invoke-RestMethod http://127.0.0.1:12891/health
```

确认人体存在和照度传感器与“起身空间”在同一房间，然后重新保存路径。

### 页面显示“监听异常”

确认 Home Assistant 和设备模拟器均可访问。工作台后台监听会在短暂故障后继续重试，不需要重建规则。

### 停止服务

先结束工作台进程，再停止容器：

```powershell
docker compose -f deployment\docker-compose.yml down
```
