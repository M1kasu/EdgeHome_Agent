# 麦克风语音输入

在「AI 管家」中按住 **V** 或「按住说话」话筒按钮，看到「正在聆听」后说话。
实时识别文字显示在语音面板和输入框中。松开后停止采集，等待最终识别结果，
再通过已有的 `/api/chat` 入口调用 LangGraph Agent，进入意图理解、任务拆解与执行验证。
讲话中的停顿或服务商的 `definite` 分句不会提前提交任务。

- 第一次使用需要允许浏览器的麦克风权限；如果已松开按键，再按住一次开始录音。
- 使用 `http://localhost:8765`、`http://127.0.0.1:8765` 或 HTTPS；普通局域网 HTTP 不支持麦克风。
- 按 Esc、点击取消、切换工作台视图、离开窗口或隐藏页面，会取消本次输入，不提交 Agent。
- 空白录音、断线、最终结果超时均不提交；失败时恢复原来的文字草稿。
- 单次上限 90 秒；识别结果超过 Agent 的 500 字限制时，请手动精简后发送。
- 在输入框中快速轻按 V 仍输入 `v`；长按才录音。中文输入法组字、Ctrl/Alt/Meta 组合键和其他表单输入不拦截。

## 服务端配置

依赖 `websockets` 和 `python-dotenv` 已加入 `pyproject.toml`。在 PowerShell 中使用 Python 3.11+：

```powershell
Set-Location 'D:\Project\EdgeHome_Agent-main\KDXF_SpaceButler'
$python = 'E:\Anaconda\envs\Agent-Hub\python.exe'
& $python -m pip install -e ".[dev]"
# 已有 .env 时绝不覆盖；新安装需自行填写语音 Key。
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
```

在 `KDXF_SpaceButler/.env` 中配置以下变量，不要把真实值写入 `.env.example`：

```dotenv
SPACEBUTLER_ASR_API_KEY='<语音 API Key>'
SPACEBUTLER_ASR_RESOURCE_ID=volc.seedasr.sauc.duration
SPACEBUTLER_ASR_ENABLE_NONSTREAM=true
```

工作台和独立语音测试脚本自动读取项目目录的 `.env`，从其他工作目录启动也有效。
进程环境变量优先于 `.env`，显式空值也会覆盖文件配置；不对密钥中的 `$` 做变量插值。
`.env`、`.env.local`、`.env.production` 等已被 Git 忽略；只有不含真实密钥的 `.env.example` 可提交。
`.env` 位于静态资源目录之外，前端 URL 无法访问。旧的 `deployment/voice.local.json` 已删除，代码不再读取它。
不需要通用云账户 AccessKeyID / SecretAccessKey，也不要把它们当作语音 Access Token。
配置在工作台启动时读取，修改后需重启服务。

`enable_nonstream` 控制二遍识别。当前本机已分别验证关闭/开启两种模式，
使用已开通的 2.0 小时版；`.env` 和示例配置均开启二遍识别。
如需排查基础流式链路，将 `SPACEBUTLER_ASR_ENABLE_NONSTREAM` 改为 `false`，或给独立脚本传入 `--no-second-pass`。

| ASR 2.0 资源 | Resource ID |
| --- | --- |
| 小时版 | `volc.seedasr.sauc.duration` |
| 并发版 | `volc.seedasr.sauc.concurrent` |

模型“已开通”、当前 Key 的资源授权、可用额度是不同条件，必须同时满足。
只配置 Key 不代表调用已验证成功；页面开始录音时会实际连接服务商并显示错误。

## 启动与测试

语音服务与原工作台共用端口，无需额外的 WebSocket 端口。
按原来的 HA / ESPHome / LLM 配置启动工作台：

```powershell
& $python scripts\run_workbench.py --port 8765
```

语音入口最终使用同一个 Agent，因此完整的设备任务仍需要 Home Assistant、ESPHome 和 Agent LLM 在线。
这项功能不会启动或替代这些服务。完整准备步骤见 [安装与运行指南](SETUP.md)。
启动前需配置已有 ESPHome 节点的 `ESPHOME_NOISE_PSK`。如果 HA 已在 Ubuntu 虚拟机部署，填写其实际地址并复用，不需要另建 Windows 本地 HA。

先独立验证上游（不调用 Agent，不控制任何设备）：

```powershell
& $python scripts\check_voice_asr.py
& $python scripts\check_voice_asr.py --wav '<16kHz、单声道、PCM16 WAV 文件>'
& $python scripts\check_voice_asr.py --wav '<WAV 文件>' --no-second-pass
& $python scripts\check_voice_asr.py --wav '<WAV 文件>' --second-pass
```

不带 `--wav` 时发送一秒静音，只检查鉴权、音频传输与最终包，不代表人声识别准确度通过。
带文件时按 200ms 分片实时发送，并打印最终转写；测试文件的音频会发送给豆包。
成功标志为 `connected: true`，最后返回 `state: "final"`；有声音时还应看到不断更新的 `partial` 和最终 `text`。
本机保留的合成语音可用 `--wav deployment\runtime\voice-verification\synthetic-command.wav` 复测，此文件不提交 Git。

网页麦克风验收：

1. 启动完整工作台，打开 `http://127.0.0.1:8765`，进入「AI 管家」。
2. 允许麦克风权限。按住 V，等「正在聆听」后说“客厅主灯现在是什么状态？”。
3. 确认讲话中字幕持续更新，没有提前发起 Agent 任务。
4. 松开 V，等待最终识别结果；对话应只发送一次，然后显示 Agent 的处理过程。
5. 再次按住 V 说话，按 Esc 取消；应停止录音且不提交。之后再测试需要的设备指令。

错误排查：

- HTTP 403 / `requested resource not granted`：该 Key 没有目标资源的调用权限。核对同一应用/项目及实际开通的 Resource ID。
- `45000292` / `quota exceeded for types: concurrency`：连接鉴权已通过，但没有可用并发额度；检查额度、有效期和其他占用会话。
- `NotAllowedError`：浏览器未允许麦克风，或页面不在安全上下文中。
- 识别成功但 Agent 无法执行：单独检查 HA、模型连接以及设备能力，不要把部分识别文字当作成功任务。

## 数据与实现

浏览器使用 AudioWorklet 将麦克风实际采样率转换为 16kHz、单声道 PCM16，小端编码。
每 200ms 向本地服务端顺序上传一个分片；字幕轮询间隔为 150ms。
服务端使用 `X-Api-Key` 鉴权，按官方二进制协议建立长连接，发送到
`wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async`，可配置二遍识别。
音频只保存在内存缓冲中，不落盘；服务端从不将 Key 返回给浏览器。
最终文字提交后，沿用现有 Agent 的会话/记忆行为；音频的服务商处理方式以服务商政策为准。

测试：

```powershell
& $python -B -m pytest -c pyproject.toml tests -q
node --test tests/voice-input.test.cjs
& $python -m ruff check --no-cache .
node --check workbench/voice-input.js
node --check workbench/voice-capture.js
```

协议参考：[火山引擎官方大模型流式语音识别 API](https://www.volcengine.com/docs/6561/1354869)。
