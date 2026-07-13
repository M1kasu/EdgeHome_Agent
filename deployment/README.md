# Compose 部署目录

请从仓库根目录的 [README](../README.md) 按“首次部署”执行。此目录只包含容器定义和模板，不包含可复制的 Home Assistant 运行目录。

Linux 主机可执行：

```sh
cd deployment
cp .env.example .env
python3 ../scripts/install_edgehome_demo.py --config-dir ./homeassistant
./start.sh
```

随后在浏览器完成本机 Home Assistant 与 Local LLM 首次配置，并按 README 创建 MQTT 配置条目。请勿用其他机器的 `.storage`、Token 或 SQLite 数据替代该流程。
