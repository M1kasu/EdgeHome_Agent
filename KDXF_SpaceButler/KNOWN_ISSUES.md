# Known Issues

- **llama.cpp 必须用足够的槽位上下文启动**（Compose 现为 `-c 16384 --parallel 1`）。
  `-c` 会被 slot 数均分；每槽低于 4096 时带设备清单的规划 prompt 会让结构化调用返回 500，
  规划静默退回确定性回退。就绪自检的「模型上下文容量」一项会拦住这种情况。
- 热扰动演示依赖 `ac.yaml` 的 `demo_heat_injection` action；实际 Home Assistant service 名可能需要通过 `ESPHOME_HEAT_INJECTION_SERVICE` 覆盖。
- HA 里仍残留已停用 MQTT 设备的 `unavailable` 实体（旧 `mqtt` config entry 未删除）。
  它们不会进入工作台投影，因为 `.env` 已用 `SPACEBUTLER_ENTITY_*` 显式绑定新的 ESPHome 实体。
  要彻底清理，在 HA「设置 → 设备与服务」中删除 `spacebutler-mqtt` 集成。
- 品牌适配器层（`spacebutler/brands.py`）当前只做能力映射与取值换算，`native_command`
  是翻译结果；真实小米/Aqara/美的接入需要替换各适配器的下发实现，并提供厂商网关或云凭据。
- 设备底座是 ESPHome `host` 平台虚拟节点，非真实厂商硬件。
- **外部验收脚本仍针对已移除的 MQTT 链路**：`scripts/accept_ha_mqtt_energy_external.py`
  与 `run_external_acceptance.py` 依赖 Mosquitto 和 `spacebutler-device-*` 容器，
  现在这些服务默认停止，直接运行会失败。本地回归（`pytest` / `ruff` / `node --test`）
  和演示路径不受影响；这些门禁需要迁移到 ESPHome 断言后才能重新作为验收依据。
- 团队情况、真实商业数据和现场答辩信息必须由用户提供，不能臆造。
