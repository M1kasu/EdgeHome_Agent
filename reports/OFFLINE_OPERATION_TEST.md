# 断网运行验证

真实隔离步骤：创建 Docker `--internal` 网络；将现有 Home Assistant 和 llama.cpp 容器以原别名接入；断开 `deployment_default`；执行检查；恢复默认网络并删除临时网络。

- 本地 `http://llama-server:8080/health`：HTTP 200。
- 外部 `https://example.com`：DNS `Errno -3`，访问失败。
- 测试后两个容器均恢复至 `deployment_default`，Home Assistant 正常运行，llama.cpp healthy。

本测试没有发现本地模型健康检查的外部请求。它只覆盖 EdgeHome/llama.cpp 本地链路；在线天气、翻译等 Home Assistant 外部集成不在“完全离线”结论内。原始记录：[offline_network_test.json](../benchmark/results/offline_network_test.json)。
