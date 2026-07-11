# EdgeHome 当前运行状态

记录时间：2026-07-11（Asia/Shanghai）

## 已通过真实运行验证

| 项目 | 状态与证据 |
| --- | --- |
| Git 基线 | `agent/stage10-edge-optimization`，基线提交 `b7fd265` |
| Home Assistant | 2026.7.2 Docker 容器运行，端口 8123；`check_config` 通过 |
| 本地模型 | llama.cpp Server 健康，Home-Llama-3.2-3B 可响应 |
| 虚拟环境 | 17 个指定 `edgehome_*` 目标实体已加载，区域为客厅、卧室、全屋 |
| 公开边界 | 运行时公开表仅写入 17 个目标；HA 通用 Assist 层显示 16 个，专用虚拟安防由 EdgeHome 注册表白名单处理 |
| 简单控制 | Assist 实测：客厅主灯关闭/打开、亮度 50%、安防模式开启均返回成功 |
| 复杂场景 | Assist 实测观影、离家场景均返回“已完成任务” |
| 保护设备 | 离家后 Assist 回读：`switch.edgehome_router=on`、`switch.edgehome_refrigerator=on` |
| 多轮睡眠 | 实测先澄清、再继承目标并创建 30 秒延时任务；任务后客厅空调回读为 `off`、温度 26.0 |

## 已部署但尚未完成现场触发的项目

- `input_boolean.edgehome_simulate_curtain_failure` 和模板窗帘故障注入已经加载；自动化测试已验证“服务成功、状态不变、重试后失败/部分成功”。本轮未通过开发者工具手动切换该内部开关，因此不将其标记为 Home Assistant 前端端到端通过。
- 性能报告记录实际观察到的端到端时间区间和容器瞬时资源，不把它们误称为稳定基准。

## 代码已覆盖并通过自动化测试

- 中文快速路径、实体别名/歧义处理、结构化计划、工具注册、服务后回读与重试。
- 受保护冰箱/路由器拦截，观影/离家/睡眠受限场景计划，延时任务与澄清上下文继承。
- 虚拟实体注册表是 EdgeHome 唯一的实体边界；未登记的购物清单、真实设备和内部 helper 都不能进入计划或工具调用。

## 通过绕过前端问题完成的配置

Home Assistant 2026.7.2 的自定义集成条目界面曾出现 `ha-list-virtualized` 错误。相关 Assist/Conversation 配置及本阶段实体公开配置均遵循：停止 HA、完整备份 `.storage`、JSON 解析写入、原子替换、重启校验。运行时备份文件在被 Git 忽略的 `deployment/homeassistant/.storage/`，不包含秘密或账户信息。

## 安全边界

- 只操作 `edgehome_*` 虚拟实体；真实门锁、燃气、总电源、摄像头和隐私实体不在注册表中。
- 冰箱与路由器是虚拟但受保护的实体，关闭前必须二次确认；离家计划从不包含它们。
- 内部状态 helper 与故障注入 helper 不可被自然语言解析或公开给 Agent。
