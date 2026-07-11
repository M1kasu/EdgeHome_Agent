# EdgeHome 开发

在兼容 Home Assistant 的 Python 环境中运行 pytest -q、ruff check custom_components/llama_conversation/edgehome tests/llama_conversation/edgehome，以及 mypy custom_components/llama_conversation/edgehome。

新增执行能力必须先扩展注册工具和 Pydantic 参数模型，并添加计划验证、执行验证与失败路径测试。禁止让模型拼接任意服务名、REST URL 或 shell 命令。
