# EdgeHome 虚拟家居测试环境

本目录保存可提交、可复用的 Home Assistant 演示环境定义。实际运行时配置仍位于被 Git 忽略的 `deployment/homeassistant/`，避免把 `.storage`、认证信息或用户数据提交到仓库。

安装到本地 Docker 部署：

```powershell
python scripts/install_edgehome_demo.py --config-dir deployment/homeassistant
```

安装脚本会复制 YAML 片段、向 `configuration.yaml` 添加所需 `!include`，并添加（或更新）`script.edgehome_reset_demo_environment`。客厅主灯不再是模板 Helper：它由 `device-simulator` 经 MQTT Discovery 创建。当前 Home Assistant 版本通过配置条目管理 MQTT Broker，首次部署后运行 `scripts/configure_mqtt_integration.py` 创建该条目。

运行前与每个场景测试前都应执行 `scripts/reset_demo.ps1`，它会同时复位 Home Assistant 演示状态和设备容器中的客厅主灯 SQLite 状态，使虚拟家庭回到文档约定的晚间初始状态。

故障恢复验证可先开启未公开的 `input_boolean.edgehome_simulate_curtain_failure`。此时调用窗帘服务会正常返回，但窗帘位置不会改变；执行器必须经回读校验后报告失败。重置脚本会自动关闭该故障注入开关。
