# EdgeHome 部署说明

根目录 [README](../README.md) 是 Windows 首次部署、Home Assistant 首次配置和验收命令的唯一入口。本页补充部署边界，避免将开发机运行目录误作安装包。

## 可提交内容与本机运行内容

- 可提交：Compose 文件、Mosquitto 配置、`deployment/demo/homeassistant` 的 YAML 模板、组件源码和验收脚本。
- 不可提交：`deployment/.env`、`deployment/homeassistant/`、`deployment/runtime/`、模型、Token、SQLite、`.storage` 和浏览器会话。

首次部署先执行：

```powershell
Copy-Item .\deployment\.env.example .\deployment\.env
python .\scripts\install_edgehome_demo.py --config-dir .\deployment\homeassistant
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_demo.ps1
```

`install_edgehome_demo.py` 是幂等的：它复制已跟踪 YAML 片段并更新 `configuration.yaml` 的 include，不会复制另一台机器的 `.storage` 或数据库。MQTT Broker 由 Home Assistant 配置条目管理，因此需要在创建本机长期访问令牌后运行 `configure_mqtt_integration.py`；这是支持的配置流，不直接编辑 `.storage`。

## 部署完成条件

1. `docker compose -f deployment/docker-compose.yml ps` 中 Home Assistant、Mosquitto、模拟器和 llama.cpp 均为运行状态；
2. `scripts/healthcheck.ps1` 返回两个 HTTP 200；
3. MQTT Discovery 自动出现四个 `edgehome_living_room_*` 实体；
4. `accept_m1_mqtt_light.py` 能通过真实 Conversation API 产生 MQTT command、模拟器 state 和 HA 回读证据。

如果上述任一步失败，先修复部署条件，不要通过手工改数据库或伪造实体状态绕过。
