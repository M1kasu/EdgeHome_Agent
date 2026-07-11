# 阶段 8 报告：本地知识检索与能耗分析

## 1. 本阶段目标

在不把本地资料上传给模型、也不扩大设备控制权限的前提下，增加可追溯的设备知识检索，以及基于 Home Assistant Recorder 统计的确定性能耗异常分析。

## 2. 主要交付

- `LocalKnowledgeIndex`：使用本地 SQLite 保存小规模说明书、设备笔记和政策文档；所有阻塞数据库操作都经由 Home Assistant executor job，并带有异步超时边界。
- `DeviceManualRegistry`：将 Home Assistant 实体关联到设备型号，使该实体的说明书查询只检索已关联的本地资料。
- `HomeAssistantEnergyStatistics` 与 `EnergyAnalyzer`：读取 Recorder 的累计 `sum` kWh 统计，计算相邻样本能耗区间，以历史正区间中位数为基线识别最新异常，并生成带具体测量值的检查建议。
- `ReadOnlyInformationHandler`：接入 EdgeHome 对话快速路径，支持 `查询说明书 vacuum.demo 如何清理滤网`、`查询知识库 如何清理滤网` 和 `分析 sensor.energy 最近24小时能耗`。
- 中文使用文档：[EdgeHome Knowledge](docs/EdgeHome%20Knowledge.md)。

## 3. 安全边界

1. 知识检索仅访问本地 SQLite 文档，返回说明书标题、来源和命中文本；没有证据时返回“无法据此回答”，不由模型补全答案。
2. 实体限定查询必须先存在设备型号关联，避免错误引用其他型号说明书。
3. 能耗查询仅读取 Recorder 累计统计，绝不调用 Home Assistant 设备控制服务。
4. 建议仅陈述观测到的 kWh、基线和待检查方向，不自动关闭、调节或安排设备。
5. Recorder 不可用、样本无效或统计不足时，明确说明无法生成结论；不会推测能耗原因。

## 4. 自动化验证

```text
pytest -q: 147 passed
ruff check custom_components/llama_conversation/edgehome tests/llama_conversation/edgehome: All checks passed!
mypy custom_components/llama_conversation/edgehome: Success: no issues found in 40 source files
```

覆盖了本地说明书命中与无结果、实体—型号关联范围、Recorder 样本过滤、累计能耗区间、异常与建议、统计不足，以及通过对话入口进行的只读查询。

## 5. 真实部署边界

- 自动化测试使用 Home Assistant 的测试状态机、Store 和模拟 Recorder 读取器；部署前仍须在目标实例确认 Recorder 已启用、对应统计 ID 存在且单位为 kWh。
- 本阶段提供知识索引与关联 API；资料导入和实体—型号关联应由受控配置或后续管理界面完成，不能把不受信任的文本直接当作设备控制指令。
- 未以真实设备、真实说明书或生产 Recorder 数据执行长期能耗验证；建议先在只读观测期检查阈值是否适合家庭负载。

## 6. 提交与分支

分支：`agent/stage8-knowledge-and-energy`

- `8ae2b61 feat: 新增本地知识检索与设备说明书关联`
- `8ba8b0c feat: 实现 Home Assistant 能耗统计与异常分析`
- `316ec2c feat: 接入本地知识与能耗只读查询`

## 7. 下一阶段

阶段 9 将在不放宽固定计划验证的基础上，处理安全冲突、设备竞争与失败后的受限重规划。
