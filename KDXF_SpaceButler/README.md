# SpaceButler 家庭空间主动管家

SpaceButler 是面向科大讯飞 SpaceMind 家庭应用场景赛题的 AI Agent。项目采用“新 Agent 外壳 + 旧项目设备底座迁移”，聚焦主动理解、家庭记忆、自学习、多设备协同和多成员个性化服务。

## 赛题定位

- **场景方向**：家庭智能管家 + 老人关怀安全守护 + 家庭能源管理 + 多成员个性化服务。
- **核心价值**：从“用户命令设备”升级为“空间主动理解家庭需求并提出可解释服务”。
- **技术关键词**：空间状态建模、家庭记忆、自学习偏好、主动服务触发、多设备计划、多成员冲突调和。

## 已实现原型

- 统一空间状态 `SpatialSnapshot`：成员位置、活动、设备、环境、时间段。
- 家庭记忆 `HouseholdMemory`：显式反馈写入偏好，按成员和场景召回。
- SQLite 持久化记忆：Agent 重建后仍能召回用户授权和节能等待阈值。
- 主动服务引擎 `ProactiveServiceEngine`：返家舒适、夜间老人安全、舒适节能平衡、多成员冲突调和。
- 老人夜间起身安全路径：监听 HA 人体存在实体的上升沿，从 MQTT 照度实体取值，按成员偏好顺序点亮柔光；人工接管有显式来源和自动过期，并逐项完成 HA/MQTT 状态回读。
- 计划编排 `SpaceButlerAgent`：输入空间快照，输出可解释 `ServicePlan`。
- 主动节能闭环：任意已绑定空间连续无人、窗户打开、空调运行且功率偏高时，首次建议确认，执行后四路回读；用户授权后同类场景自动执行。
- 真实执行底座：独立 Docker Compose、Home Assistant REST、MQTT Discovery、设备容器和 SQLite 设备状态。
- 一设备一容器：每台虚拟设备使用独立 MQTT Client ID、SQLite 卷、资源上限和故障域，Fleet Gateway 保持统一设备 API。
- 动态设备编排：运行时新增设备会创建专属 Docker 容器，容器重启后设备定义和状态仍然存在。
- 设备中心：按空间管理灯光、智能开关、空调、窗帘、人体存在、门窗和照度传感器，支持添加、控制/上报、故障注入和删除。
- 动态 MQTT Discovery：新增设备立即进入 Home Assistant，删除设备同步清理 Discovery 配置。
- 动态主动规则：选择空间及三路设备来源，绑定持久化；实时展示设备实体、上报时间和每项触发条件。
- 故障防护：设备拒绝、ACK 但状态不变、延迟、离线、超时和多动作部分成功均不误报为全部成功。
- 边缘语言路由：调用 llama.cpp 理解自然语言偏好，模型候选必须通过字段白名单和范围校验。
- 现场工作台：AI 管家、空间与设备、主动服务、执行事件四个视图；夜间安全场景可绑定起身/照度实体、查看实时条件和接管 TTL，并模拟真实上升沿与执行回读。

## 快速运行

准备 Python 3.11+、Docker Desktop 和 PowerShell，然后在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example deployment\.env
docker compose -f deployment\docker-compose.yml up -d --build
python scripts\run_workbench.py --port 8765
```

浏览器打开 `http://127.0.0.1:8765`。工作台启动时会自动完成本地 Home Assistant 初始化和 MQTT 配置；默认端口如下：

| 服务 | 默认地址 |
| --- | --- |
| 工作台 | `http://127.0.0.1:8765` |
| Home Assistant | `http://127.0.0.1:12900` |
| 设备 Fleet Gateway | `http://127.0.0.1:12891` |
| MQTT | `127.0.0.1:18884` |
| 可选 llama.cpp | `http://127.0.0.1:12881` |

没有 GGUF 模型时，工作台会明确使用确定性语言降级；主动规则、设备控制和状态回读仍可完整运行。完整安装、夜间安全场景操作、端口覆盖和故障排查见 [使用指南](docs/USAGE.md)。

运行本地验证：

```powershell
python -m pytest tests -q
python -m ruff check .
node --check workbench\app.js
python scripts\run_iteration.py
```

首页可直接添加设备并按类型控制；“主动服务”可绑定节能规则来源，也可配置老人夜间起身的有序灯光路径、照度门槛和人工接管灯。新增设备通过 MQTT Discovery 自动进入 Home Assistant。工作台使用项目内置前端资源，不依赖公网 CDN；Home Assistant Token 仅保留在服务端。

同类开源项目调研、差距分析与场景选择见 `docs/OPEN_SOURCE_PROJECT_COMPARISON.md`；小米 IoT、阿里云生活物联网平台对比及一设备一容器设计见 `docs/DEVICE_CONTAINER_ARCHITECTURE.md`。

## 外部验收

Docker Desktop 运行时执行：

```powershell
python scripts\run_external_acceptance.py
```

将 `Home-Llama-3.2-3B.q4_k_m.gguf` 放入 `deployment/models/`，或通过 `SPACEBUTLER_MODELS_DIR` 指向模型目录。当前工作区存在旧项目模型时，验收脚本会迁移复用该 GGUF 制品，但始终启动 KDXF 自己的 llama.cpp 容器。

该门禁在独立进程中验证 `Agent -> Home Assistant REST -> MQTT -> 目标设备容器 -> 独立 SQLite -> MQTT/HA 回读`，并覆盖单设备容器故障隔离、服务重启、KDXF Compose llama.cpp、动态设备容器注册/控制/删除、动态主动规则绑定和工作台 API。报告写入 `reports/external/`。也可用 `python scripts\run_iteration.py --external` 同时执行本地回归与外部门禁。
