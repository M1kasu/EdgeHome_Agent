# 阶段 2 报告：Home LLM 可运行基线

## 1. 本阶段目标

在不实现 EdgeHome 新功能的前提下，建立可复现的 Home LLM 测试环境，验证原项目测试、模型对话底座和单灯工具执行链路。

## 2. 修改文件

- `custom_components/requirements-dev.txt`
  - 锁定 `home-assistant-intents==2026.5.5`；
  - 增加 Ruff 和 mypy 开发工具。

## 3. 新增文件

- `pyproject.toml`：渐进式 Ruff/mypy 基线；
- `tests/llama_conversation/test_home_llm_tool.py`：原始 `HassServiceTool` 单灯调用回归测试；
- `docs/EdgeHome Baseline.md`：环境、问题、命令和测试边界说明；
- `STAGE2_REPORT.md`：本阶段交付报告。

## 4. 核心实现说明

本阶段没有修改 Home LLM 运行代码。

完成的基线工作：

1. 安装 GitHub CLI 并配置目标仓库；
2. 保留 `acon96/home-llm` 上游完整提交历史；
3. 使用 Python 3.14.6 和 Home Assistant 2026.5.0；
4. 确认 Windows 原生测试受 `fcntl` 限制，迁移到 WSL2 Ubuntu 24.04；
5. 发现并修复开发依赖的版本漂移；
6. 使用原 `HassServiceTool` 调用 `light.turn_on`，验证 HA 测试状态机从 `off` 更新为 `on`；
7. 建立不会迫使上游代码一次性重写的静态检查门禁。

依赖问题根因：`homeassistant==2026.5.0` 仍导入 `FuzzyConfig`，而未锁定依赖会安装已经删除该符号的 `home-assistant-intents==2026.6.24`。锁定到 `2026.5.5` 后测试恢复正常。

## 5. 测试结果

```text
pytest: 75 passed in 4.62s
ruff:  All checks passed!
mypy:  Success: no issues found in 12 source files
```

测试平台：WSL2 Ubuntu 24.04，Python 3.14.6。

测试真实性边界：

- 75 项测试在真实 Home Assistant Python 测试框架中执行；
- 单灯闭环使用 HA 测试状态机和已注册服务，不是伪造成功返回；
- 模型客户端由上游测试中的 DummyClient/Mock 隔离，不代表真实 GGUF 模型性能；
- 未连接用户真实 HA 实例或物理灯具。

## 6. 当前遗留问题

1. 尚未下载和加载 GGUF 模型；
2. 尚未在真实 Home Assistant 实例中执行设备控制；
3. Docker Desktop Linux daemon 未启动，本次测试使用 WSL2；
4. 上游全量 Ruff `F` 规则存在 30 个历史问题，本阶段未批量修改；
5. mypy 对旧模块仅建立可运行基线，严格检查从 EdgeHome 新模块开始；
6. 真机状态传播延迟、模型推理速度和资源占用留待部署/性能阶段验证。

## 7. 下一阶段计划

阶段 3 先不接入大模型，按小提交实现固定 JSON 计划的真实执行与验证：

1. Pydantic 任务计划和步骤模型；
2. EdgeHome 工具元数据注册表；
3. 已暴露实体解析和实体存在校验；
4. 受控 Home Assistant 工具执行器；
5. 每步状态重读、容差验证、超时和有限重试；
6. 成功、状态未变化、设备离线和非法实体测试。
