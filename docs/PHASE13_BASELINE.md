# 第 13 阶段基线记录

记录时间：2026-07-12（Asia/Shanghai）
实施分支：`agent/phase13-edge-benchmark`
起始提交：`8052c85 Merge pull request #11 from M1kasu/agent/phase12-knowledge-energy`

## 工作区与回归

- 从已经合并第 12 阶段的 `main` 创建分支。
- 创建分支前工作区无未提交改动。
- Windows Home Assistant 测试夹具回归：`103 passed, 5 warnings in 4.12s`。
- 5 条警告均为第三方依赖弃用提示；无失败。

## 当前运行配置

| 项目 | 实测值 |
| --- | --- |
| Home Assistant | `2026.7.2` |
| llama.cpp server | `9967 (4f37f5197)`，Linux x86_64 构建 |
| 模型文件 | `Home-Llama-3.2-3B.q4_k_m.gguf` |
| 模型格式 / 量化 | GGUF / `Q4_K_M`（文件名和部署配置） |
| 模型大小 | 2,019,378,080 bytes（约 1.88 GiB） |
| 上下文长度 | 2048，服务日志显示 4 slots |
| CPU 线程限制 | 未配置；容器 CPU 配额与 cpuset 均为 0（不限制） |
| GPU / NPU 卸载 | 未配置；Docker `DeviceRequests` 与 `Devices` 均为 `null` |
| 容器 | Home Assistant 运行；llama.cpp server healthy |

本阶段所有新结果均必须以 `benchmark/results/` 的实际 JSONL/CSV 为依据；本文件不含预估延迟、吞吐量或成功率。
