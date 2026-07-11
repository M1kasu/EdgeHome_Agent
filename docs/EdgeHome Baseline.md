# EdgeHome 阶段 2：Home LLM 基线

## 1. 基线信息

| 项目 | 值 |
|---|---|
| 上游仓库 | `acon96/home-llm` |
| 上游分支 | `develop` |
| 基线提交 | `50cf35c9b66440f64ce45baeb3f5b39a2f382cd1` |
| Home LLM 清单版本 | `0.4.10` |
| Home Assistant | `2026.5.0` |
| Python | `3.14.6` |
| 测试平台 | Ubuntu 24.04 / WSL2 |

## 2. 环境结论

Home Assistant 2026.5.0 要求 Python 3.14.2 或更高版本，并依赖 Unix 的 `fcntl`。因此：

- Windows 原生 Python 3.13 无法安装该版本 Home Assistant；
- Windows 原生 Python 3.14 可以安装依赖，但 pytest 插件加载 HA runner 时因缺少 `fcntl` 失败；
- 本项目的可靠测试环境必须使用 Linux、WSL2 或 Linux 容器；
- 本机 Docker Desktop 的 Linux daemon 未启动，本次使用 WSL2 Ubuntu 24.04；
- WSL 测试环境位于用户缓存目录，不会提交虚拟环境或模型文件。

## 3. 依赖漂移问题

上游开发依赖原来声明：

```text
homeassistant>=2026.5.0
home-assistant-intents
```

在 2026-07-11 安装会得到 `homeassistant==2026.5.0` 和 `home-assistant-intents==2026.6.24`。后者已经移除前者仍在导入的 `FuzzyConfig`，导致全部测试在收集阶段失败。

阶段 2 将 `home-assistant-intents` 锁定到同一发布线的 `2026.5.5`。锁定后完整测试通过。该修改只影响开发测试依赖，不改变集成运行代码。

## 4. 创建 Linux 测试环境

以下命令使用 `uv` 管理 Python 3.14 和虚拟环境。虚拟环境放在用户缓存目录：

```bash
python3 -m venv ~/.cache/uv-bootstrap
~/.cache/uv-bootstrap/bin/python -m pip install -U pip uv
~/.cache/uv-bootstrap/bin/uv python install 3.14
~/.cache/uv-bootstrap/bin/uv venv --python 3.14 ~/.cache/edgehome-agent-venv
~/.cache/uv-bootstrap/bin/uv pip install \
  --python ~/.cache/edgehome-agent-venv/bin/python \
  -r custom_components/requirements-dev.txt \
  -r custom_components/requirements.txt
```

## 5. 基线验证

### 完整自动化测试

```bash
~/.cache/edgehome-agent-venv/bin/python -m pytest -q
```

结果：

```text
75 passed in 4.62s
```

其中上游原有测试 74 项，阶段 2 新增原始灯光工具闭环回归测试 1 项。

### 原始灯光工具闭环

新增基线回归测试使用原项目 `HassServiceTool`，不引入 EdgeHome 执行器：

```text
结构化 HassCallService 工具输入
→ 校验 light.turn_on 属于允许范围
→ Home Assistant blocking service call
→ 测试 HA 状态机将灯从 off 更新为 on
→ 工具返回 {"result": "success"}
```

该测试证明原项目的“工具输入 → Home Assistant 服务 → 灯光状态”底座可以运行。模型输出到工具输入的转换由原有 Conversation 和工具解析测试覆盖。

## 6. 静态检查基线

上游没有 Ruff/mypy 配置。阶段 2 采用渐进策略：

- Ruff 先检查语法错误和未定义名称等关键正确性规则；
- Ruff 全量 `F` 扫描发现 30 个上游历史问题，主要是未使用/重复导入和无占位符 f-string，本阶段不批量修改；
- mypy 暂不要求历史代码一次性修复；
- `edgehome.*` 新模块从创建开始启用更严格的函数注解和返回值检查；
- 不在功能提交中批量格式化上游文件。

执行：

```bash
ruff check custom_components/llama_conversation tests/llama_conversation
mypy
```

## 7. 测试边界

本阶段已经完成：

- 上游完整自动化测试；
- Home Assistant 测试实例中的原始灯光服务调用；
- Conversation、模型客户端和工具解析的上游回归测试；
- Python、HA 和 intents 兼容版本确认。

本阶段没有将以下结果描述为已完成：

- 未下载 GGUF 模型；
- 未在用户真实 Home Assistant 实例中控制实体；
- 未进行真实设备状态传播延迟测试；
- 未测量端侧模型推理性能。

这些验证需要模型文件、目标硬件和隔离的 Home Assistant 测试实例，将在部署与性能阶段提供明确记录。

## 8. 下一阶段入口

阶段 3 从固定 JSON 计划开始，不接大模型，依次实现：

1. 结构化计划模型；
2. EdgeHome 工具元数据注册表；
3. 真实且已暴露实体解析；
4. 受控 HA 工具执行；
5. 执行后的状态重读、验证和有限重试。
