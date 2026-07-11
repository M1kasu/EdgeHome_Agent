# 第 14 阶段最终质量报告

执行日期：2026-07-12（Asia/Shanghai）  
执行分支：`agent/phase14-competition-package`

| 检查项 | 命令/方式 | 结果 |
| --- | --- | --- |
| 全量 EdgeHome 回归 | 项目既有 Windows pytest 命令，含第14阶段留存测试 | 114 passed，5 条第三方依赖弃用警告 |
| 现场核心冒烟 | `run_demo_tests.ps1 -SmokeOnly` | 12 passed，5 条同类弃用警告 |
| Ruff | EdgeHome、基准脚本、比赛图表生成脚本和新测试 | 通过 |
| mypy | `custom_components/llama_conversation/edgehome` | 70 个源文件无问题 |
| Git 差异 | `git diff --check` | 通过；仅有 Windows 换行符转换提示 |
| 证据审计 | `FINAL_EVIDENCE_AUDIT.md` | 已完成；过时稳定性表述已修正 |
| 图表生成 | `generate_submission_figures.ps1` | 成功；图表与最小复核数据已更新 |
| Home Assistant 配置 | `python -m homeassistant --script check_config --config /config` | 退出码 0 |
| 服务健康 | PowerShell 健康检查 | Home Assistant HTTP 200；llama.cpp HTTP 200 |

## 现场演示状态

- Docker 中 Home Assistant 正在运行；llama.cpp 为 healthy。
- 图表、原始数据副本、视频脚本、答辩材料、问答与应急预案已生成。
- 本机 PowerShell 执行策略禁止直接载入未签名 `.ps1`；脚本经 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ...` 验证可运行。该调用仅对单次进程生效，不修改系统执行策略。
- `reset_demo` 为避免保存凭证，要求操作者仅在当前会话设置 `HA_TOKEN`；未设置时会安全失败并说明原因。

## 未完成的人工事项

1. 使用无个人信息的测试/访客 Home Assistant 账户，按 `11_现场演示脚本.md` 采集并审核视频与截图；当前提交包不保存本机页面截图，以避免账户信息泄露。
2. 在目标开发板和真实授权设备上重跑性能、功耗、安全与长稳测试。
3. 依据比赛规则复核模型与依赖许可证，并在分发前复核开源通知。
4. 若比赛要求完整八模块端到端消融，应在固定模型、工作负载和硬件上补测，不能用本报告替代。
