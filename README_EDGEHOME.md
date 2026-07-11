# EdgeHome Agent

EdgeHome 是基于 acon96/home-llm 的增量 Home Assistant 自定义组件扩展。它保留原有 Conversation、模型后端和工具调用能力，并增加中文快速路径、受控复杂规划、多轮状态、环境快照、本地知识检索、能耗分析、安全确认、失败重规划与端侧资源调度。

快速开始请阅读 docs/deployment.md；架构和安全边界见 docs/architecture.md 与 docs/safety_design.md。

第 12 阶段新增离线 FTS5 本地知识库、合成能耗分析、可确认的 EdgeHome Demo 节能自动化和多轮方案选择。详见 docs/KNOWLEDGE_BASE_DESIGN.md、docs/ENERGY_ANALYSIS_DESIGN.md、docs/ENERGY_AUTOMATION_SAFETY.md 与 docs/PHASE12_USER_GUIDE.md。

第 13 阶段新增端侧请求指标、资源感知调度、版本控制工作负载、真实 llama.cpp 配置对比、并发/断网/稳定性采样及 SVG 图表。详见 docs/BENCHMARK_METHODOLOGY.md 与 docs/PHASE13_USER_GUIDE.md。
