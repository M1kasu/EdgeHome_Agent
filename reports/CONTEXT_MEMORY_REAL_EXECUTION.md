# M1.5 正式入口真实执行记录

执行环境：本机 Docker Home Assistant、Mosquitto、独立 device-simulator、EdgeHome Agent。测试入口始终为 `POST /api/conversation/process`，Agent 为当前默认 EdgeHome Conversation；未直接调用协调器内部函数。

## 运行结果

| 场景 | 输入 | 结果 |
| --- | --- | --- |
| 首轮控制 | 把客厅主灯亮度调到 50% | 返回新 `conversation_id`，HA 与模拟器亮度均为 128（50%）。 |
| 指代续接 | 再暗一点 | 传回同一 ID，返回同一 ID，HA 与模拟器亮度为 102（40%）。 |
| 指代续接 | 关掉吧 | 传回同一 ID，返回同一 ID，真实 MQTT 灯状态为 `off`。 |
| 会话隔离 | 新会话：再暗一点 | 返回“请说明要操作哪个设备”，灯保持 `off`，未调用模型猜测或改变设备。 |
| 重启恢复 | 设为 50% → 只重启 Home Assistant → 再暗一点 | 使用同一 ID 成功恢复 SQLite 状态，亮度从 128 变为 102。 |

## 可复跑命令

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_context_memory.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_context_memory.ps1 -RestartHomeAssistant
```

已实际执行以上两条命令；两次均通过。运行证据由脚本写入被忽略的本机运行目录：

```text
reports/mqtt_light_acceptance/context_memory_results.jsonl
```

该证据每条保存测试输入、会话 ID、三轮 HA 状态与亮度、最终回复和隔离会话结果；不包含访问令牌。
