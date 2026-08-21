# EdgeHome Agent

根据《需求规格说明书》和《开发计划》实现的离线优先智能家居 Agent MVP。默认启用确定性的模拟 Home Assistant，因此无需账号、设备或网络即可演示核心能力。

## 已实现范围

- 中文文本输入、房间/设备/亮度/温度/延时槽位提取，以及保守的简单/复杂任务路由；
- 客厅、卧室、玄关状态和 12 个模拟实体，含灯光、空调、窗帘、电视、插座及受保护设备；
- 观影、离家、睡眠场景模板与结构化计划；
- SQLite 持久化会话、任务状态和本地知识库，支持“也一样”的参数继承；
- 工具白名单、参数范围、受保护设备二次确认、冲突检查、执行后验证、离线设备降级；
- 本地说明书检索和确定性近 7 日能耗分析；
- FastAPI 接口与单元/接口测试。

该 MVP 的复杂规划使用离线模板，保证断网时可运行；`EDGEHOME_LLM_URL` 为后续 llama.cpp OpenAI 兼容服务预留，任何模型输出仍必须经过同一计划校验与执行闭环。

## 运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

打开 <http://127.0.0.1:8000/docs> 查看接口文档。

## 快速演示

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/agent/chat -ContentType 'application/json' -Body '{"session_id":"demo","text":"把客厅灯调到一半"}'
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/agent/chat -ContentType 'application/json' -Body '{"session_id":"demo","text":"我要在客厅看电影，把环境调舒服一点，空调不要低于25度"}'
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/agent/chat -ContentType 'application/json' -Body '{"session_id":"demo","text":"最近一周为什么用电量变高了？"}'
```

关闭冰箱或路由器时，系统仅保存计划并返回 `need_confirmation: true`；再次发送 `确认` 才会执行。普通离家场景会始终跳过这些设备。

## 验证

```powershell
python -m unittest discover -s tests -v
```

## 真实 Home Assistant 接入

在 `.env` 中设置 `EDGEHOME_SIMULATION=false`、`HOME_ASSISTANT_URL` 和 `HOME_ASSISTANT_TOKEN`。`HomeAssistantClient` 通过 REST 查询实体并调用灯光、空调、窗帘和通用开关服务；令牌只保存在服务端配置对象中，不会出现在计划、接口响应或日志中。默认保持模拟适配器，以保证离线演示可复现。
