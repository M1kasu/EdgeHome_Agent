# 第 12 阶段知识检索测试报告

测试环境：Windows 11、Python 3.14、SQLite 内置 FTS5；资料为仓库 `knowledge/` 的原创测试资料。

| 查询 | 实体/型号约束 | 命中文档证据 | 结论边界 |
| --- | --- | --- | --- |
| `E3` | `climate.edgehome_living_room_ac` / AC-Demo-1 | 《测试空调》“故障代码 E3” | 仅说明测试型号的风机反馈异常与恢复步骤 |
| `为什么离家不能关闭冰箱和路由器` | 家庭规则 | 《家庭受保护设备规则》“冰箱和路由器” | 明确不自动断电，不推断其他设备 |
| `空调高温用电` | 节能指南 | 《空调用电解读指南》“高温与用电” | 说明需连续数据，不诊断真实故障 |
| `不存在的 Z99 故障` | 无 | 无 | 返回 `NO_EVIDENCE`，不生成事实 |

自动化测试记录实体/查询/命中文档/证据字段/最终回答；`tests/test_knowledge_indexer.py`、`tests/test_knowledge_retriever.py`、`tests/test_device_manual_tool.py` 均断言无证据分支。检索时间见 `PHASE12_PERFORMANCE_REPORT.md`；本阶段未调用模型生成事实，因此模型回答时间为 0 ms（确定性证据返回）。
