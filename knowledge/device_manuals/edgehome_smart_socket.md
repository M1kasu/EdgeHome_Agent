<!-- edgehome: {"id":"socket-demo","title":"EdgeHome 测试智能插座 Socket-Demo-1 安全指引","source":"原创测试设备资料，2026-07","category":"device_manual","device_type":"switch","device_model":"EdgeHome Socket-Demo-1"} -->
# EdgeHome 测试智能插座 Socket-Demo-1

## 设备与额定限制
类型为 switch，测试型号为 Socket-Demo-1。测试额定负载为 10A / 2200W；功率高于该阈值视为过载风险。过载时先关闭测试插座，再检查接入负载，不应反复合闸。

## 电视待机异常
电视关闭而测试插座仍长期高待机功率时，先提示用户检查电视和外围设备；仅在用户确认并且目标是 `switch.edgehome_test_socket` 时，允许创建或执行关闭动作。

## 安全步骤
不要把本测试插座的结论套用于冰箱、路由器或未知插座。它们可能为受保护设备，离家策略不得自动断电。
