"""Read HA entity identities and explain workbench mapping; never control devices."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spacebutler.configuration import load_project_environment  # noqa: E402
from spacebutler.esphome_registry import discover_entities  # noqa: E402
from spacebutler.home_assistant import HomeAssistantClient  # noqa: E402


def main() -> int:
    load_project_environment()
    base_url = os.environ.get("SPACEBUTLER_HA_URL", "http://127.0.0.1:12900")
    token = os.environ.get("HA_TOKEN", "").strip()
    if not token:
        print("请在项目 .env 配置 HA_TOKEN；诊断不会自动创建账户或令牌。")
        return 1
    try:
        states = HomeAssistantClient(base_url, token).states()
    except (RuntimeError, OSError, ValueError) as error:
        print(json.dumps({"error": "读取 HA 失败，请检查 HA 地址、令牌、网络和代理。",
                          "error_type": type(error).__name__,
                          "http_status": getattr(error, "status_code", None)}, ensure_ascii=False))
        return 1
    _, diagnostics = discover_entities(states)
    diagnostics["ha_host"] = urlparse(base_url).hostname
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    return 0 if not diagnostics["missing_devices"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
