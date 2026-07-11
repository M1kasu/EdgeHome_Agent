# EdgeHome 部署说明

EdgeHome 是现有 Home LLM 自定义组件中的增量模块，不是独立的设备控制服务。部署时 Home Assistant 继续持有实体、权限和服务调用边界；EdgeHome 只构造经过白名单验证的固定计划。

可使用 deployment 目录中的 Docker Compose 示例运行 Home Assistant 和 llama.cpp server。模型路径、上下文大小、端口、知识库路径等均通过 .env 配置；示例文件不包含 Token。

部署前确认 Recorder 已启用（能耗查询）、计划中目标实体已暴露给 Conversation、模型后端可访问且使用本地地址。首次上线应先启用只读查询和快速路径，再验证复杂规划。
