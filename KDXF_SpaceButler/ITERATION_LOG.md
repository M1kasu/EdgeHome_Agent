# Iteration Log

## Iteration 1

迭代目标：
- 完成舒适与节能平衡主动服务闭环：客厅连续无人 + 空调运行 + 窗户打开 + 功率偏高。
- 加入确认、执行回读、用户反馈学习和下一次行为改变。

修改文件：
- `spacebutler/models.py`
- `spacebutler/memory.py`
- `spacebutler/services.py`
- `spacebutler/runtime.py`
- `spacebutler/interaction.py`
- `scripts/accept_empty_room_open_window_energy.py`
- `scripts/audit_hardcoding.py`
- `scripts/run_iteration.py`
- `tests/test_spacebutler_agent.py`

测试结果：
- 编译检查通过。
- 单元测试通过。
- 黑盒节能验收通过。
- 硬编码审计通过。
- 迭代脚本通过，最新报告在 `reports/iterations/iteration_20260716_060242/`。

失败分析：
- 初次反馈解析未识别“以后这种情况直接执行”，属于泛化不足。

修复结果：
- 改为基于未来范围词 + 直接执行/自动关闭动作词组合识别，不依赖完整句子。

赛事评分变化：
- 初赛当前 58/100。
- 决赛当前 38/100。

下一轮目标：
- 在 Docker 可用前，先补设备故障假成功防护和执行结果状态枚举。
- Docker 可用后迁移旧项目 HA/MQTT/device-simulator 执行链路。

## Iteration 2

迭代目标：
- 处理 P0 假成功风险：命令 ACK 但设备状态不变不能报告成功。

修改文件：
- `spacebutler/models.py`
- `spacebutler/runtime.py`
- `spacebutler/__init__.py`
- `tests/test_spacebutler_agent.py`
- `scripts/accept_false_success_prevention.py`
- `scripts/run_iteration.py`

测试结果：
- 单元测试 9 项通过。
- 主动节能黑盒验收通过。
- 假成功防护黑盒验收通过。
- 硬编码审计通过。
- 迭代脚本通过，最新报告在 `reports/iterations/iteration_20260716_060559/`。

失败分析：
- 未出现测试失败。新增测试覆盖 `ack_without_state_change` 和 `available=False` 两类故障。

修复结果：
- 新增 `ExecutionStatus`，执行报告可区分 `success`、`validation_failed`、`device_unavailable` 等状态。
- `execute_and_verify` 以执行后状态回读决定最终状态，避免 HTTP/ACK 式假成功。

赛事评分变化：
- 初赛 58 -> 60。
- 决赛 38 -> 40。

下一轮目标：
- 本地故障矩阵扩展到设备拒绝和 partial_success。
- Docker 可用后迁移真实 HA/MQTT/device-simulator 链路。
