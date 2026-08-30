# Known Issues

- 当前 Windows 主机没有 Docker CLI，无法在本机完成“从 HA 容器访问 Ubuntu 6053–6056”的最终网络验证；须在实际 HA Docker 主机运行 `scripts/check_ha_esphome_network.py`。
- 当前主机的 `127.0.0.1:12881` 与 `127.0.0.1:8080` 均无模型服务监听，常用目录也未找到 GGUF；自然语言 Agent 因此会安全拒绝执行，不会进入场景脚本 fallback。
- Ubuntu ESPHome 节点已从 Windows 宿主侧验证 6053–6056 可达，但该结果不能替代 HA 容器网络验证。
- 热扰动演示依赖 Ubuntu `ac.yaml` 的 `demo_heat_injection` action；实际 Home Assistant service 名可能需要通过 `ESPHOME_HEAT_INJECTION_SERVICE` 覆盖。
- 团队情况、真实商业数据和现场答辩信息必须由用户提供，不能臆造。
