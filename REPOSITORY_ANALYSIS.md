# EdgeHome Agent 仓库分析

## 1. 分析范围与结论

本分析基于 `acon96/home-llm` 的 `develop` 分支，基线提交为：

```text
50cf35c9b66440f64ce45baeb3f5b39a2f382cd1
```

分析日期：2026-07-11。

目标仓库 `M1kasu/EdgeHome_Agent` 当前为空的私有仓库，默认分支为 `main`，当前账号具备管理和推送权限。后续应保留 Home LLM 的 Git 历史，将该基线作为 EdgeHome Agent 的上游基础，而不是将独立 Agent 项目复制进来。

核心结论：Home LLM 已经具备 Home Assistant Conversation 接入、多个本地/远程模型后端、暴露实体建模、工具调用循环、会话历史、提示词模板、配置页面和较完整的集成测试。EdgeHome Agent 应在这些能力之上新增可配置的编排层，不应替换现有模型后端或重写现有 Conversation 集成。

## 2. 仓库概览

```text
home-llm/
├── custom_components/llama_conversation/  # Home Assistant 自定义集成
│   ├── __init__.py                         # 集成生命周期、后端工厂、兼容工具 API
│   ├── conversation.py                     # Conversation Agent 入口和 Agent 循环
│   ├── entity.py                           # 模型客户端基类、工具解析、实体上下文、提示词
│   ├── ai_task.py                          # Home Assistant AI Task 实体
│   ├── config_flow.py                      # 配置流、选项流和子条目配置
│   ├── const.py                            # 配置项、默认值、提示词和后端常量
│   ├── utils.py                            # 工具/消息格式、JSON 修复、模型安装等工具函数
│   ├── backends/                           # llama.cpp、Ollama、OpenAI、Anthropic 等后端
│   ├── translations/                       # 配置页面翻译
│   └── *.gbnf / in_context_examples*.csv  # 输出约束和 ICL 示例
├── tests/llama_conversation/               # Home Assistant 集成测试
├── docs/                                   # 安装、后端、提示词、性能和 AI Task 文档
├── data/                                   # 训练/合成数据生成工具及多语言数据
├── train/                                  # 模型训练和评估脚本
├── scripts/                                # 模型转换、量化和构建脚本
├── docker-compose.yml                      # 上游开发/推理相关容器配置
└── LICENSES.txt                            # MIT 代码许可和数据/模型第三方许可
```

集成清单版本为 `0.4.10`，要求 Home Assistant `2026.5.0` 或更高版本。集成类型为本地轮询服务，依赖 Conversation 和 AI Task 平台。

## 3. 原项目关键执行链路

### 3.1 集成启动与后端创建

入口位于 `custom_components/llama_conversation/__init__.py`：

1. `async_setup_entry()` 注册旧版 Home LLM 模型兼容 API `HomeLLMAPI`；
2. 根据 `CONF_BACKEND_TYPE` 从 `BACKEND_TO_CLS` 创建模型客户端；
3. 校验后端启动条件；
4. 转发 Conversation 和 AI Task 平台；
5. 使用 Config Entry/Subentry 保存后端连接与模型配置；
6. 通过迁移函数兼容旧配置版本。

当前后端映射包括：

- 内嵌 `llama-cpp-python`；
- Generic OpenAI Chat Completions；
- Generic OpenAI Responses；
- llama.cpp server；
- text-generation-webui；
- Ollama；
- Anthropic Messages API。

### 3.2 对话入口

对话实体位于 `custom_components/llama_conversation/conversation.py`：

- `async_setup_entry()` 为每个 Conversation 子条目创建 `LocalLLMAgent`；
- `LocalLLMAgent.async_process()` 是自然语言请求的主入口；
- 使用 Home Assistant `chat_session` 与 `ChatLog` 管理会话；
- 根据配置选择 Home Assistant LLM API；
- 读取或裁剪聊天历史；
- 生成/刷新系统提示词；
- 调用模型客户端；
- 在最多 `max_tool_call_iterations` 次循环内处理工具调用和工具结果；
- 将最后一条有效 Assistant 文本转换为 `ConversationResult`。

这条链路是 EdgeHome 编排层最合适的接入点，但应通过小型门面或协调器接入，避免继续扩大 `async_process()`。

### 3.3 模型客户端与输出解析

`custom_components/llama_conversation/entity.py` 中的 `LocalLLMClient` 是所有模型后端的公共抽象：

- 统一流式/非流式生成；
- 解析原生工具调用及带前后缀的旧式工具调用；
- 将模型增量输出交给 Home Assistant `ChatLog`；
- 读取 Home Assistant 暴露实体；
- 解析 Entity、Device、Area 和实体别名；
- 渲染 Jinja 系统提示词；
- 生成 ICL 示例；
- 根据配置暴露实体属性。

`utils.py` 负责：

- OpenAI 工具与消息格式转换；
- 旧版 Home LLM 工具格式转换；
- 模型输出 JSON 修复；
- 原始工具调用解析；
- 思考块清理；
- llama-cpp-python 安装与模型文件处理。

现有输出约束包括工具调用前后缀、`json.gbnf`、`output.gbnf`、后端原生 tool calling 和 JSON 修复。它们可以继续用于“生成 EdgeHome 结构化计划”，但模型不得直接生成任意 Home Assistant REST 请求。

### 3.4 工具调用

原项目存在两类工具路径：

1. Home Assistant 自带或其他集成提供的 `llm.APIInstance` 工具；
2. `HomeLLMAPI` 暴露的旧模型兼容工具 `HassCallService`。

`HassCallService` 当前会：

- 校验服务格式；
- 限定允许的 domain 和 service；
- 限定允许透传的参数；
- 以 `blocking=True` 调用 Home Assistant 服务；
- 返回 `success`、`failed` 或 `unknown service`。

现有工具调用由 Home Assistant `ChatLog` 在模型循环中执行并将结果反馈给模型。EdgeHome 的计划执行不能直接复用“模型输出后立即执行”的时序，而应在执行前插入计划 Schema、实体、参数、安全、冲突和用户约束校验。

### 3.5 Home Assistant 实体与家庭状态

`LocalLLMClient._async_get_exposed_entities()` 已经具备：

- 仅读取对 Conversation 暴露的实体；
- Entity Registry 查询；
- Device Registry 查询；
- Area Registry 查询；
- 设备/实体 Area 归属解析；
- 中文友好名称和别名读取；
- 状态、单位和额外属性读取。

但 `_generate_system_prompt()` 会将所有暴露实体组织进提示词。它适合通用聊天，不满足 EdgeHome “只向模型传入与当前任务有关的房间和设备”的要求。需要在原方法之外新增任务相关状态快照构建器，而不是改变通用提示词行为。

### 3.6 会话状态

原项目通过 Home Assistant `chat_session` 和 `ChatLog` 保存消息历史，并支持：

- 是否记忆会话；
- 保留最近若干轮交互；
- OpenAI Responses 后端按时间保留对话；
- 每轮刷新系统提示词。

当前没有结构化任务状态，无法可靠表示当前目标、已确认槽位、待澄清问题、延时任务、未完成步骤和场景恢复点。EdgeHome 需要新增结构化状态存储，但应继续保留 ChatLog 作为原始对话历史。

### 3.7 配置页面

`config_flow.py` 已采用 Home Assistant Config Entry + Config Subentry：

- 父条目保存后端连接配置；
- Conversation/AI Task 子条目保存模型和提示词配置；
- `runtime_options` 合并父条目 options 与子条目 data；
- 配置迁移已经覆盖多个历史版本；
- 配置页面支持后端选择、模型选择、提示词、LLM API、历史、流式、上下文、采样和 llama.cpp 性能选项。

EdgeHome 配置应作为 Conversation 子条目的可选字段追加，并全部提供默认值。不开启 EdgeHome 模式时，执行行为必须与原项目一致。

### 3.8 测试现状

仓库当前包含 57 个测试函数，覆盖：

- 集成启动和卸载；
- 配置迁移；
- 配置流和模型子条目；
- Conversation Agent 生成、异常、工具格式错误和历史裁剪；
- 工具调用解析；
- 暴露实体和系统提示词；
- llama.cpp、Ollama、OpenAI 等后端基础行为；
- AI Task 结构化输出。

当前机器未安装 `homeassistant`、`voluptuous` 和 `pytest-homeassistant-custom-component`。执行 `pytest --collect-only` 时在收集阶段出现 8 个依赖导入错误，因此尚不能把当前基线标记为“测试通过”。这属于环境缺失，不是已确认的上游代码失败。完整基线测试应在实施阶段 2 使用隔离虚拟环境按 `custom_components/requirements-dev.txt` 安装依赖后执行。

仓库目前没有 Ruff 或 mypy 配置。后续需先建立与现有代码兼容的最小检查范围，不能在功能提交中顺便格式化整个上游仓库。

## 4. 可直接复用的能力

以下模块应原样保留或通过窄接口复用：

| 能力 | 复用位置 | 复用方式 |
|---|---|---|
| Home Assistant Conversation | `conversation.py` | 保留实体注册、ChatLog 和 ConversationResult |
| 本地/远程模型后端 | `backends/` | 不改变公共生成接口；复杂规划复用现有客户端 |
| llama.cpp 量化模型 | `backends/llamacpp.py` | 复用模型下载、加载、采样、缓存和流式输出 |
| OpenAI/Ollama/Anthropic | `backends/` | 继续支持原有配置和工具格式 |
| Entity/Device/Area 映射 | `entity.py` | 抽取公共实体快照能力，EdgeHome 增加任务过滤 |
| 工具调用协议 | HA `llm.APIInstance`、`utils.py` | EdgeHome 注册独立工具，继续使用 HA 类型和序列化 |
| 会话历史 | `chat_session`、`ChatLog` | 保留原始历史，旁路增加结构化任务状态 |
| 提示词和 ICL | `const.py`、`entity.py` | 原模式不变；EdgeHome 使用单独规划提示词 |
| 配置与迁移 | `config_flow.py`、`__init__.py` | 追加默认关闭的 EdgeHome 选项并补迁移测试 |
| AI Task | `ai_task.py` | 不侵入 EdgeHome 编排，保证原功能回归 |
| 原有测试夹具 | `tests/` | 不删除，新增 EdgeHome 单元/场景测试 |

## 5. 需要修改的现有模块

修改应保持很小，并由配置开关保护：

1. `conversation.py`
   - 在 `async_process()` 入口调用 EdgeHome 协调器；
   - EdgeHome 关闭时完全走原流程；
   - EdgeHome 无法处理或安全降级时允许回退原对话流程；
   - 不在此文件堆放识别、计划、执行等实现。
2. `entity.py`
   - 提供可复用的暴露实体读取接口；
   - 不改变原系统提示词的全量暴露语义；
   - 复杂规划通过新状态构建器获取相关子集。
3. `const.py`
   - 增加 EdgeHome 配置键和向后兼容默认值；
   - 增加中文提示词/语言选项时保留原语言配置。
4. `config_flow.py` 与 `translations/*.json`
   - 增加 EdgeHome 可选配置；
   - 旧配置缺少字段时使用默认值，禁止启动报错。
5. `__init__.py`
   - 注册 EdgeHome 所需的独立 HA LLM API/运行时服务；
   - 不扩大旧版 `HassCallService` 的权限。
6. `manifest.json`
   - 只在确有新增运行依赖时更新；
   - 保留许可证、domain 和原组件兼容性。

## 6. 需要新增的模块

建议在真实集成目录下新增：

```text
custom_components/llama_conversation/edgehome/
├── coordinator.py
├── models.py
├── router.py
├── understanding/
│   ├── intent_classifier.py
│   └── slot_extractor.py
├── context/
│   ├── home_state.py
│   ├── dialogue_state.py
│   └── store.py
├── planner/
│   ├── templates.py
│   ├── llm_planner.py
│   ├── validator.py
│   └── replanner.py
├── tools/
│   ├── registry.py
│   ├── ha_tools.py
│   ├── task_tools.py
│   ├── knowledge_tools.py
│   └── energy_tools.py
├── safety/
│   ├── action_guard.py
│   ├── conflict_detector.py
│   └── permissions.py
├── execution/
│   ├── executor.py
│   ├── verifier.py
│   ├── failure_analyzer.py
│   └── reporter.py
├── knowledge/
│   ├── indexer.py
│   └── retriever.py
└── resource/
    ├── monitor.py
    └── scheduler.py
```

目录会按实际实现逐步创建；不为尚未实现的功能预先生成空文件。

## 7. 当前能力缺口

与 EdgeHome 目标相比，原项目尚缺：

- 可记录和可测试的简单/复杂双路径路由；
- 中文意图和槽位的确定性快速路径；
- 与任务相关的家庭状态裁剪与缓存；
- 结构化任务计划、模板规划和计划 Schema 校验；
- 结构化多轮任务状态和持久化；
- 工具元数据注册表、业务参数约束和细粒度权限；
- 关键设备保护、高风险确认和完整冲突检测；
- 每步执行后的状态重读、容差验证、重试和重规划；
- 延时任务、状态保存和恢复；
- 本地说明书检索；
- 确定性能耗分析；
- 资源监控和资源感知调度；
- 完整任务轨迹、脱敏日志和调试展示；
- EdgeHome 数据集、部署编排、专项文档和比赛演示材料。

## 8. 主要工程风险

1. **工具执行时序**：原 `ChatLog` 会执行模型工具调用。EdgeHome 复杂规划必须先生成计划对象、完成全部校验，再由受控执行器调用工具，不能让规划模型直接拿到任意设备控制工具。
2. **兼容旧模型**：旧 Home LLM 模型依赖 `HassCallService` 和一次性工具调用模式。EdgeHome 不能改变其提示词、工具名称或默认配置。
3. **Home Assistant API 演进**：基线依赖 HA 2026.5.0，ChatLog、Config Subentry 和 LLM API 都是强耦合接口，必须以 HA 测试夹具做集成测试。
4. **上下文体积**：现有提示词会暴露全部已授权实体。EdgeHome 应构建任务子集，同时保证未开启 EdgeHome 时行为不变。
5. **安全边界**：旧兼容工具的白名单只到 domain/service 层。EdgeHome 应注册独立工具并校验实体是否真实、暴露且授权，不能直接扩大旧工具。
6. **真实设备验证**：单元测试只能使用 HA 测试状态机。真实设备服务调用和状态延迟必须在独立测试实例中验证，不能用模拟结果冒充真机结果。
7. **数据与许可证**：代码为 MIT；仓库内部分训练数据和模型涉及非商业许可。必须保留 `LICENSES.txt`，并在第三方声明中区分代码、数据和模型许可。
8. **静态检查基线**：上游无 Ruff/mypy 配置，直接全仓开启严格检查会产生大量无关改动。应先只覆盖 EdgeHome 新模块，再逐步扩大。

## 9. 分析阶段验收结果

- 已读取 README、安装文档、后端文档、提示词文档、贡献说明和许可证；
- 已定位对话入口、模型后端、工具调用、实体获取、会话历史、输出约束和配置页面；
- 已明确可复用、需修改和需新增模块；
- 已确认目标仓库为空且有推送权限；
- 已尝试收集原项目测试，确认本机缺少 HA 测试依赖；
- 本阶段未修改任何上游 Python 代码，未推送远端。

