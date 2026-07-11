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
    llama.cpp /health、/v1/models、/v1/chat/completions: 200

## 部署边界

Compose 文件已做静态解析，Shell 脚本已做语法检查；本机已成功构建 EdgeHome 初始化镜像、启动 Home Assistant 和 llama.cpp，并验证 HTTP 200。使用 Home-Llama-3.2-3B Q4_K_M 的短推理实测 prompt 约 93.8 tokens/s、生成约 44.7 tokens/s；该数字仅代表本机这一次 42-token 低负载探针。部署者仍需完成 Home Assistant 初始配置并在上线前执行真实设备验收。
