"""Run the local SpaceButler control workbench."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ha_bootstrap import (  # noqa: E402
    configure_esphome,
    obtain_token,
    require_esphome_noise_psk,
    wait_until_ready,
)
from spacebutler import EdgeLanguageRouter, EdgeLlmClient, HomeAssistantClient, HouseholdMemory  # noqa: E402
from spacebutler.llm import LlmClient  # noqa: E402
from spacebutler.configuration import load_project_environment  # noqa: E402
from spacebutler.workbench import WorkbenchController, serve_workbench  # noqa: E402


def configure_esphome_with_retry(ha_url: str, token: str, noise_psk: str, timeout_seconds: float = 420) -> None:
    """ESPHome Host 节点首次启动要编译固件，配置流在节点就绪前会失败，按轮次重试。"""
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            results = configure_esphome(ha_url, token, noise_psk)
            print(f"ESPHome integrations: {results}", flush=True)
            return
        except RuntimeError as error:
            if time.monotonic() >= deadline:
                raise
            print(f"waiting for ESPHome nodes: {error}", flush=True)
            time.sleep(10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    load_project_environment()
    ha_url = os.getenv("SPACEBUTLER_HA_URL", "http://127.0.0.1:12900")
    edge_llm_url = os.getenv("EDGE_LLM_URL", "http://127.0.0.1:12881")
    auth_store = ROOT / "deployment" / "homeassistant" / ".storage" / "auth"
    noise_psk = require_esphome_noise_psk()
    wait_until_ready(ha_url, timeout_seconds=120)
    token = obtain_token(ha_url, auth_store)
    configure_esphome_with_retry(ha_url, token, noise_psk)
    language_router = None
    edge_llm_client = None
    try:
        with urlopen(f"{edge_llm_url.rstrip('/')}/health", timeout=3) as response:
            if response.status == 200:
                edge_llm_client = EdgeLlmClient(edge_llm_url, timeout_seconds=60)
                language_router = EdgeLanguageRouter(edge_llm_client)
    except OSError:
        pass
    controller = WorkbenchController(
        HomeAssistantClient(
            ha_url,
            token,
            token_refresher=lambda: obtain_token(ha_url, auth_store),
        ),
        HouseholdMemory(ROOT / "deployment" / "runtime" / "agent" / "household_memory.db"),
        language_router,
        LlmClient.from_env(),
    )
    server = serve_workbench(controller, ROOT / "workbench", args.host, args.port)
    controller.start_night_monitor()
    print(f"SpaceButler workbench: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop_night_monitor()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
