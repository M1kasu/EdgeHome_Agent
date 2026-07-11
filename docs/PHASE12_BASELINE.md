# 第 12 阶段基线记录

记录时间：2026-07-11（Asia/Shanghai）  
实施分支：`agent/phase12-knowledge-energy`  
基线提交：`ff83041 feat(edgehome): add virtual device control and verified execution loop`

## 工作区

- 仓库：`E:\\code\\znjj\\EdgeHome_Agent`
- 创建第 12 阶段分支前工作区无未提交改动。
- 分支从已推送的第 11 阶段分支创建，未合并到 `main`。

## 运行环境

| 服务 | 容器 | 状态 | 端口 |
| --- | --- | --- | --- |
| Home Assistant | `deployment-home-assistant-1` | Up | 8123 |
| 本地 llama.cpp 服务 | `deployment-llama-server-1` | Up (healthy) | 8080 |

## 基线验证

在 Windows / Python 3.14 测试环境中，为避免 Home Assistant 的 Windows 事件循环兼容性问题，使用仓库既有的无自动加载测试夹具执行：

```powershell
$env:PYTHONPATH='C:\\tmp\\edgehome_test_shims'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\\.venv\\Scripts\\python.exe -m pytest -p pytest_asyncio.plugin -p edgehome_windows_hass -q `
  tests\\llama_conversation\\edgehome tests\\test_entity_registry.py `
  tests\\test_failure_recovery.py tests\\test_fast_path.py `
  tests\\test_leave_home_scene.py tests\\test_movie_scene.py `
  tests\\test_multi_turn_sleep.py tests\\test_protected_entities.py `
  tests\\test_result_verifier.py tests\\test_tool_execution.py
```

结果：`90 passed, 5 warnings in 3.86s`。5 条警告均来自第三方依赖的弃用提示；无测试失败。

第 12 阶段所有实现必须保持上述虚拟设备白名单、受保护设备规则和既有回归行为。
