<!-- edgehome: {"id":"tv-demo","title":"EdgeHome 测试电视 TV-Demo-1 与测试插座","source":"原创测试设备资料，2026-07","category":"device_manual","device_type":"switch","device_model":"EdgeHome TV-Demo-1"} -->
# EdgeHome 测试电视 TV-Demo-1

## 设备状态
类型为 switch，测试型号为 TV-Demo-1。`off` 仅表示电视测试实体关闭，不代表测试插座已断电，也不代表真实电视的待机功率为零。

## 测试插座断电
需要切断电视测试插座时，必须明确目标为 `switch.edgehome_test_socket`，并由用户确认。不得将“电视关闭”自动扩展为关闭冰箱、路由器或其他插座。

## 异常待机处理
电视为 off 而测试插座功率异常偏高时，建议先提示；若用户选择执行，可创建可查询、可停用、可删除的 EdgeHome Demo 自动化。
