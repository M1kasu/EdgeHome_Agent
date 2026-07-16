# EdgeHome_Agent 复用审计

## 可直接复用

| 模块 | 旧项目路径 | 复用价值 |
|---|---|---|
| Home Assistant 边界 | `custom_components/llama_conversation/edgehome/execution` | 保证设备动作通过 HA 服务调用，不直接篡改状态 |
| MQTT 设备模拟器 | `device_simulator` | 支撑可复现 Demo、状态持久化和验收证据 |
| 安全与验证 | `edgehome/safety`、`edgehome/execution/verifier.py` | 白名单、受保护设备、执行后回读验证 |
| 目标规划 | `edgehome/planner` | 可转换 SpaceButler 的 `ServicePlan` 为可执行 `TaskPlan` |
| 证据脚本 | `scripts/accept_*.py`、`reports/acceptance` | 形成比赛材料中的工程可信度 |
| Docker 部署 | `deployment/docker-compose.yml` | 决赛演示可一键启动 |

## 需要改造

- 旧项目偏“命令执行可靠性”，新赛题偏“主动空间服务与产品价值”。
- 旧项目的记忆更多是会话和上下文，新项目需要面向成员、场景和偏好的长期记忆。
- 旧项目已有观影、离家、节能等能力，但需要包装成 SpaceMind 语境下的空间智能场景。
- 旧项目的验收脚本可复用，但报告需要改成返家、老人关怀、多成员、舒适节能等故事线。

## 结论

不建议直接重命名旧项目提交。更合适的方式是新建 `KDXF_SpaceButler` 作为比赛项目，把旧项目作为执行底座逐步迁移。这样既保留工程可信度，又能让方案更贴合 SpaceMind 评分点。

