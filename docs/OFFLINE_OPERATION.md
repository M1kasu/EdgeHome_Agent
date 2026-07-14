# 断网运行

第 13 阶段采用受控 Docker 内部网络隔离：两个现有容器临时从 `deployment_default` 移到 Docker `--internal` 网络，保留 `home-assistant` 与 `llama-server` 别名。隔离期间本地 `http://llama-server:8080/health` 返回 200；`https://example.com` 因 DNS 不可达失败。测试后已恢复默认网络。

原始结果为 `benchmark/results/offline_network_test.json`。该测试证明 Home Assistant 与本地模型服务不依赖外网通信；它没有声称任何云翻译、在线天气或第三方集成功能可离线使用。
