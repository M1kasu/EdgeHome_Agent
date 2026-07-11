# 第 14 阶段完成报告：比赛材料整合、演示视频与答辩准备

## 发布信息

- 分支：`agent/phase14-competition-package`
- 主要提交：`6d66540`（比赛材料和演示包）、`56aabbc`（报告格式修正）
- 草稿 PR：[M1kasu/EdgeHome_Agent#12](https://github.com/M1kasu/EdgeHome_Agent/pull/12)
- 基线：第 13 阶段 `bc92343`

## 比赛材料清单

`competition_submission/` 已包含 12 份 Markdown 材料：

1. 三个版本的作品简介与正式推荐版；
2. 项目详细文档、技术可行性说明、三项创新与对比分析；
3. 带数据来源和边界的实验结果摘要；
4. 开源与原创说明；
5. 五分钟演示视频脚本、12 页答辩 PPT 大纲、5/8/12 分钟答辩讲稿；
6. 25 个问题的评委问答手册、现场演示脚本和故障应急预案。

`competition_submission/assets/` 同时包含 7 张由原始数据生成的 SVG 图表、10 份最小复核数据副本和截图采集说明。图表由 `scripts/generate_submission_figures.py` 生成，未手工改写数值。

## 最终验证与演示状态

| 项目 | 结果 |
| --- | --- |
| EdgeHome 全量回归 | 114 passed，5 条第三方依赖弃用警告 |
| 演示核心冒烟 | 12 passed，5 条同类弃用警告 |
| Ruff / mypy | 均通过；mypy 检查 70 个 EdgeHome 源文件 |
| Home Assistant 配置 | `check_config` 退出码 0 |
| 服务健康 | Home Assistant HTTP 200；llama.cpp HTTP 200 |
| 图表与数据生成 | PowerShell 包装脚本运行成功 |

Windows 主机的 PowerShell 执行策略禁止直接载入未签名脚本；所有 `.ps1` 已用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ...` 单次调用验证。该方式不会更改系统执行策略。`reset_demo` 不保存任何凭证，要求操作者在当前会话显式设置 `HA_TOKEN`。

## 关键实验数据

- 简单任务快速路径：平均 12.652 ms；同一 50 条任务直接模型：643.195 ms（Q4_K_M、ctx2048、CPU）。
- 复杂规划原始 Schema 合法：27/30；3 条非法计划均未进入设备执行层。
- 隔离故障注入：最终状态完成率 90% → 100%，假成功率 10% → 0%。
- 安全攻击：20 条输入的受保护设备服务调用为 0。
- 本地知识任务：19/20 有证据；能耗分析调用：15/15 成功。
- 稳定性：3,600 秒配置窗口、60 个样本；模型健康、检索、能耗失败均为 0。

完整审计见 `FINAL_EVIDENCE_AUDIT.md`，复杂规划失败归因及第 14 阶段复测见 `SCHEMA_FAILURE_ANALYSIS.md`。

## 已知限制

1. 仅有 Q4_K_M、CPU 实测；没有 GPU/NPU 实测、跨量化质量比较或流式 TTFT 数据。
2. 27/30 是结构化 Schema 合法率，不是端到端设备执行成功率；原第 13 阶段三条失败的原文未留存，不能倒推或伪造。
3. 安全、故障恢复与场景验证使用虚拟实体或隔离 HA；合成资料和能耗数据不代表真实家庭长期效果。
4. 未完成完整八模块统一端到端消融；比赛材料没有把部分探针写为完整消融。
5. 本地断网结论仅覆盖 EdgeHome/llama.cpp 核心链路，不覆盖所有外部 Home Assistant 集成。

## 开源与原创边界

Home Assistant、Home LLM、llama.cpp、SQLite FTS5、Pydantic、Docker 等为开源基础；中文快速路径、受限场景规划与安全闭环、实体边界、知识检索、确定性能耗分析、资源调度、虚拟演示与评价体系为本项目新增。模型权重未随仓库发布，资料与能耗数据为原创测试资料和合成数据。详见 `competition_submission/06_开源与原创说明.md` 与 `THIRD_PARTY_NOTICES.md`。

## 尚需人工完成的事项

1. 使用无个人身份信息的测试/访客账户录制视频和截图，并在预录片段上清晰标注；当前包刻意未保存本机 Home Assistant 页面截图。
2. 按比赛规则复核模型、依赖和数据许可，确认提交格式、时长和匿名要求。
3. 在目标开发板及真实授权设备上重跑性能、功耗、稳定性、安全和完整消融实验。
4. 人工审阅草稿 PR #12 后再决定是否转为可评审 PR 或合并；本阶段没有合并 `main`。
