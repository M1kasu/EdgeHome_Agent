# EdgeHome 容器部署

该 Compose 组合包含 Home Assistant、将 EdgeHome 组件复制进 Home Assistant 配置卷的一次性初始化服务，以及 llama.cpp server。它适合 Linux Docker 主机的演示和本地验收。

1. 复制 .env.example 为 .env，填写本机模型目录和 GGUF Q4/Q8 模型路径；不要填写或提交真实 Home Assistant Token。
2. 执行 ./start.sh。
3. 打开 http://localhost:8123，完成 Home Assistant 初始设置，并在 Local LLM 集成中将 OpenAI 兼容后端地址设为 http://llama-server:8080。
4. 执行 ./healthcheck.sh 检查两个 HTTP 服务。

默认建议使用 1B-3B、Q4 或 Q8 的 GGUF 模型，并从 2048 context 起步。资源不足时先降低模型大小或上下文长度，不要关闭 EdgeHome 的计划验证、状态验证和保护规则。
