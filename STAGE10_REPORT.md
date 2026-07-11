# 阶段 10 报告：端侧优化与部署交付

## 交付内容

- PlanningContextBudgeter：将模型提示限制为最多 12 个相关实体和 6000 个字符，属性白名单只保留控制与环境需要的字段；完整环境快照仍服务于执行前重校验。
- ResourceMonitor：限制复杂规划的默认并发数为 1，记录聚合耗时、进程 CPU 时间、Python 峰值内存增量和并发数，不记录输入内容或密钥。
- 部署：提供 Home Assistant、组件初始化服务、llama.cpp server 的 Docker Compose、环境变量示例、启动脚本和健康检查脚本。
- 性能：提供可重复的固定计划 Schema 基准脚本，并明确真实模型推理性能必须在目标硬件实测。

## 验证

    pytest -q: 153 passed
    ruff: All checks passed
    mypy: Success: no issues found in 45 source files
    docker compose config --quiet: passed
    scripts/benchmark_edgehome.py --iterations 10: passed
    edgehome-agent 初始化容器: passed
    Home Assistant 容器 HTTP 健康检查: 200

## 部署边界

Compose 文件已做静态解析，Shell 脚本已做语法检查；本机已成功构建 EdgeHome 初始化镜像、启动 Home Assistant，并验证 HTTP 200。尚未加载真实 GGUF 模型，因此 llama.cpp 服务未启动。部署者必须填入本机模型路径，完成 Home Assistant 初始配置，并在上线前执行真实设备验收。
