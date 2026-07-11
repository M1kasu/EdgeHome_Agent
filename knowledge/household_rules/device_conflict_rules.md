<!-- edgehome: {"id":"device-conflicts","title":"设备冲突与运行前检查","source":"原创 EdgeHome 家庭安全规则，2026-07","category":"household_rule"} -->
# 设备冲突与运行前检查

## 空调与窗户
空调制冷且窗户打开是能耗冲突。建议先关闭窗户或暂停制冷，并在执行前重新读取窗户和空调状态；不能只根据历史状态断言冲突已消除。

## 自动化边界
自动化仅允许 EdgeHome Demo 白名单实体。模型不能输出 YAML、任意服务名、任意实体 ID 或未验证条件；所有规则必须经代码 schema 校验。
