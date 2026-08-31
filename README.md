# EdgeHome Agent

当前工作台位于 **[`KDXF_SpaceButler/`](KDXF_SpaceButler/README.md)**。本次变更主要负责 **Agent 前的流式语音识别接入**：按住 V 通过麦克风描述任务，前端实时显示识别文字，松开后才把最终文本交给已有 Agent。

## 本次重点：流式语音输入

```text
长按 V / 话筒按钮 → 麦克风采集 → 本地服务端 → 豆包流式 ASR
                                                     ↓
                         前端实时字幕 ← 持续返回识别文字
                              ↓ 松开按键，等待最终结果
                      /api/chat → 已有 LangGraph Agent
                              ↓
                    任务拆解 → HA / ESPHome → 状态回读
```

- **按住说、松开发送**：讲话期间只更新字幕，不触发任务；收到最终结果后只提交一次。
- **保留取消操作**：Esc、取消按钮、窗口失焦或切换视图会停止录音，不提交任务；权限拒绝、静音、断线和超时同样不会误发。
- **流式采集**：AudioWorklet 将麦克风音频转换为 16kHz、单声道 PCM16，以 200ms 分片上传；支持可配置的最终二遍识别。
- **服务端鉴权**：语音 API Key 只从项目 `.env` 读取，不写入前端或 Git；这条接入链路不需要通用云 AccessKeyID / SecretAccessKey。
- **独立验证**：提供静音连通性检查和 WAV 转写脚本，无需启动 HA 或 Agent，也不会控制设备。

语音识别使用云端服务，需要联网，录音音频会发送给豆包；本项目不将麦克风录音保存到磁盘。ASR 只负责把语音转为文本，任务理解、安全校验和设备执行继续由已有 Agent 负责。HA 实体匹配和只读诊断是本次联调的配套改动。

## 开始验证语音

使用 Python 3.11+，先进入子项目。**语音配置放在 `KDXF_SpaceButler/.env`，不是仓库根目录的旧 MVP `.env`。**

```powershell
Set-Location KDXF_SpaceButler
# 先确认此 python 是 3.11+；否则替换为已安装的新版解释器。
python -m venv .venv
$python = '.\.venv\Scripts\python.exe'
& $python -m pip install -e ".[dev]"
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
```

在该 `.env` 填写语音服务配置，保留已有 HA、模型和设备密钥配置：

```dotenv
SPACEBUTLER_ASR_API_KEY=<你的语音 API Key>
SPACEBUTLER_ASR_RESOURCE_ID=volc.seedasr.sauc.duration
SPACEBUTLER_ASR_ENABLE_NONSTREAM=true
```

```powershell
# 发送一秒静音，验证鉴权和流式协议；不代表人声识别准确度通过。
& $python scripts\check_voice_asr.py
# 可选：上传自己的测试音频，验证实际转写；文件需为单声道、16kHz、PCM16 WAV。
& $python scripts\check_voice_asr.py --wav 'C:\Audio\command.wav'
```

完成 [HA / ESPHome / LLM 配置](KDXF_SpaceButler/docs/SETUP.md) 后，运行 `& $python scripts\run_workbench.py --port 8765`，打开 [本地工作台](http://127.0.0.1:8765)，进入「AI 管家」并允许麦克风权限。长按 V，看到「正在聆听」后说“客厅主灯现在是什么状态？”，确认字幕更新，再松开，检查对话只提交一次。麦克风需在 localhost 或 HTTPS 页面使用。

| 文档 | 内容 |
| --- | --- |
| [语音功能与完整启动说明](KDXF_SpaceButler/README.md) | 模块边界、配置、网页验收与测试命令 |
| [语音输入说明](KDXF_SpaceButler/docs/VOICE_INPUT.md) | 二遍识别、取消行为、权限和上游错误排查 |
| [语音验证记录](KDXF_SpaceButler/docs/VOICE_VERIFICATION.md) | 合成语音调用结果、回归范围与验证限制 |
| [HA 设备匹配诊断](KDXF_SpaceButler/docs/DEVICE_DISCOVERY.md) | HA 有设备但工作台没有设备时的检查方法 |

## 历史 MVP

以下是仓库根目录 `app/` 的离线优先智能家居 MVP，与上述 8765 语音工作台是不同入口。它默认启用确定性的模拟 Home Assistant，无需账号、设备或网络即可演示核心能力；下列命令需在仓库根目录执行。

### 已实现范围

- 中文文本输入、房间/设备/亮度/温度/延时槽位提取，以及保守的简单/复杂任务路由；
- 客厅、卧室、玄关状态和 12 个模拟实体，含灯光、空调、窗帘、电视、插座及受保护设备；
- 观影、离家、睡眠场景模板与结构化计划；
- SQLite 持久化会话、任务状态和本地知识库，支持“也一样”的参数继承；
- 工具白名单、参数范围、受保护设备二次确认、冲突检查、执行后验证、离线设备降级；
- 本地说明书检索和确定性近 7 日能耗分析；
- FastAPI 接口与单元/接口测试。

该 MVP 的复杂规划使用离线模板，保证断网时可运行；`EDGEHOME_LLM_URL` 为后续 llama.cpp OpenAI 兼容服务预留，任何模型输出仍必须经过同一计划校验与执行闭环。

### 运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
uvicorn app.main:app --reload
```

打开 <http://127.0.0.1:8000/docs> 查看接口文档。

### 快速演示

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/agent/chat -ContentType 'application/json' -Body '{"session_id":"demo","text":"把客厅灯调到一半"}'
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/agent/chat -ContentType 'application/json' -Body '{"session_id":"demo","text":"我要在客厅看电影，把环境调舒服一点，空调不要低于25度"}'
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/agent/chat -ContentType 'application/json' -Body '{"session_id":"demo","text":"最近一周为什么用电量变高了？"}'
```

关闭冰箱或路由器时，系统仅保存计划并返回 `need_confirmation: true`；再次发送 `确认` 才会执行。普通离家场景会始终跳过这些设备。

### 验证

```powershell
python -m unittest discover -s tests -v
```

### 真实 Home Assistant 接入

在 `.env` 中设置 `EDGEHOME_SIMULATION=false`、`HOME_ASSISTANT_URL` 和 `HOME_ASSISTANT_TOKEN`。`HomeAssistantClient` 通过 REST 查询实体并调用灯光、空调、窗帘和通用开关服务；令牌只保存在服务端配置对象中，不会出现在计划、接口响应或日志中。默认保持模拟适配器，以保证离线演示可复现。
