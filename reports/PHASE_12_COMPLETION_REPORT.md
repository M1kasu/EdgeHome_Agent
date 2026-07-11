# EdgeHome Agent 第 12 阶段完成报告

分支：`agent/phase12-knowledge-energy`  
基线：`ff83041`（第 11 阶段）

## 交付内容

- 新增 `knowledge/` 本地资料库：5 类虚拟设备、3 类家庭规则、3 类节能指南和来源策略元数据；共 27 个可引用段。
- 新增 SQLite FTS5 优先、纯 Python BM25 回退的离线检索。返回结构化来源、章节、证据、分数和错误码；无证据不诊断。
- 新增实体到测试型号的受限说明书查询，以及家庭规则/节能指南工具运行时。
- 新增固定种子的正常周、异常周能耗 CSV、严格装载器、确定性统计/异常检测/建议引擎。
- 新增分析、方案、选择、调整、撤销的多轮能耗会话；“第二条先不要”会被排除。
- 新增必须明确确认、仅面向虚拟白名单实体的 `EdgeHome Demo` 自动化。支持查询、停用、删除和撤销；冰箱与路由器不在任何节能目标中。
- Docker 安装容器会同步知识资料和能耗数据至 Home Assistant `/config` 目录。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 第 11 阶段基线回归 | 90 passed, 5 warnings |
| 第 12 阶段全量回归 | 103 passed, 5 warnings in 4.30s |
| Ruff | All checks passed |
| mypy（EdgeHome） | Success: no issues found in 61 source files |
| Home Assistant `check_config` | 退出码 0 |
| Docker 部署 | `docker compose up -d --build` 成功，HA 重启后运行；llama.cpp 服务 healthy |
| 自动化验证 | 测试内真实 Home Assistant 服务调用仅命中 `climate.edgehome_living_room_ac`，并验证确认、查询、删除流程 |

Windows 测试通过仓库既有无自动加载 HA 夹具执行，5 条警告均为第三方依赖弃用提示。性能数据见 `PHASE12_PERFORMANCE_REPORT.md`，检索测试证据见 `KNOWLEDGE_RETRIEVAL_TEST_REPORT.md`。

## 运行与安全结论

系统未新增云端检索、向量数据库或外部资料下载。知识和能耗结论由本地确定性模块生成，模型没有获取原始 CSV 或事实补全权限。长期动作必须由用户明确确认，并经过 Pydantic 白名单校验后才可调用 Home Assistant 服务。
