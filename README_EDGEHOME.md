# EdgeHome Agent

EdgeHome 是基于 acon96/home-llm 的增量 Home Assistant 自定义组件扩展。它保留原有 Conversation、模型后端和工具调用能力，并增加中文快速路径、受控复杂规划、多轮状态、环境快照、本地知识检索、能耗分析、安全确认、失败重规划与端侧资源调度。

快速开始请阅读 docs/deployment.md；架构和安全边界见 docs/architecture.md 与 docs/safety_design.md。

第 12 阶段新增离线 FTS5 本地知识库、合成能耗分析、可确认的 EdgeHome Demo 节能自动化和多轮方案选择。详见 docs/KNOWLEDGE_BASE_DESIGN.md、docs/ENERGY_ANALYSIS_DESIGN.md、docs/ENERGY_AUTOMATION_SAFETY.md 与 docs/PHASE12_USER_GUIDE.md。

第 13 阶段新增端侧请求指标、资源感知调度、版本控制工作负载、真实 llama.cpp 配置对比、并发/断网/稳定性采样及 SVG 图表。详见 docs/BENCHMARK_METHODOLOGY.md 与 docs/PHASE13_USER_GUIDE.md。

## 比赛材料与演示入口

第 14 阶段的可提交材料、视频脚本、答辩大纲、问答、现场流程、应急预案及可复核图表位于 `competition_submission/`。所有数字先经过 `reports/FINAL_EVIDENCE_AUDIT.md` 审计；复杂规划失败归因见 `reports/SCHEMA_FAILURE_ANALYSIS.md`。

Windows 演示常用命令：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_demo.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\healthcheck.ps1
# 设置仅当前会话有效的 HA_TOKEN 后，才可调用本地重置服务：
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\reset_demo.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_demo_tests.ps1 -SmokeOnly
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\generate_submission_figures.ps1
```

对应 POSIX shell 脚本使用同名 `.sh` 文件。重置脚本不会写入 Token；演示不得把 CPU/Q4 结果说成 GPU/NPU 或把 Schema 合法率说成设备执行成功率。
