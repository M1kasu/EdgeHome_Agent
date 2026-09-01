# 工作流视图

工作流视图已适配当前 SpaceButler 运行时。界面不再内置模拟设备、固定场景或假执行结果；设备、动作和状态全部来自当前 Home Assistant / ESPHome 投影。

## 数据与执行链路

1. `GET /api/devices` 返回当前真实设备。每台可控制设备的 `ui.workflow_actions` 描述它此刻支持的动作、参数范围和可选模式。
2. 自然语言生成调用 `POST /api/workflows/preview`。后端 `ConversationAgent.preview()` 基于实时设备清单完成规划和校验，但不会持久化会话、也不会发送控制命令。
3. 用户保存后，只有工作流定义写入浏览器的 `spacebutler.workflows`。刷新设备清单时，会自动移除已经不存在的设备或动作，避免旧配置误操作其他设备。
4. 执行时逐步调用 `POST /api/devices/{device_id}/control`。成功与否以 Home Assistant 控制响应和刷新后的设备状态为准；时间线不会伪造成功或节能数据。

当前只支持顺序执行。步骤延迟按真实分钟计时，范围为 0–60 分钟。

## 可用能力

- Agent 根据自然语言生成可审阅的真实设备计划
- 手动添加设备、选择后端声明的动作和参数
- 拖拽调整顺序、删除步骤、设置延迟
- 使用唯一口令匹配并启动已保存工作流
- 展示实时设备状态、逐步执行状态和失败原因
- 工作流设备列表随每次 `/api/devices` 刷新同步更新

## 关键文件

- `spacebutler/conversation.py`：无副作用的计划预览
- `spacebutler/workbench.py`：预览 API、真实设备动作描述与控制路由
- `workbench/app.js`：动态编排、保存、校验和真实执行
- `workbench/index.html` / `workbench/styles.css`：工作流界面

## 验证

```powershell
python -m pytest -q
python -m ruff check .
node --check workbench/app.js
node --test tests/voice-input.test.cjs
```
