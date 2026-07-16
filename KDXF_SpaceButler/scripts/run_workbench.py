"""Run the local SpaceButler control workbench."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ha_bootstrap import obtain_token, wait_until_ready  # noqa: E402
from spacebutler import EdgeLanguageRouter, EdgeLlmClient, HomeAssistantClient, HouseholdMemory  # noqa: E402
from spacebutler.workbench import WorkbenchController, serve_workbench  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    ha_url = os.getenv("SPACEBUTLER_HA_URL", "http://127.0.0.1:8900")
    simulator_url = os.getenv("SPACEBUTLER_SIMULATOR_URL", "http://127.0.0.1:8091")
    edge_llm_url = os.getenv("EDGE_LLM_URL", "http://127.0.0.1:8081")
    auth_store = ROOT / "deployment" / "homeassistant" / ".storage" / "auth"
    wait_until_ready(ha_url, timeout_seconds=120)
    token = obtain_token(ha_url, auth_store)
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
        simulator_url,
        HouseholdMemory(ROOT / "deployment" / "runtime" / "agent" / "household_memory.db"),
        language_router,
        edge_llm_client,
    )
    server = serve_workbench(controller, ROOT / "workbench", args.host, args.port)
    print(f"SpaceButler workbench: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
