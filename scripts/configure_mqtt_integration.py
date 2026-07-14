"""Create the Home Assistant MQTT config entry through its authenticated REST flow.

Recent Home Assistant releases intentionally reject broker connection settings in
configuration.yaml. This script uses the supported config-entry flow instead of
editing `.storage` directly. It is idempotent when an MQTT entry already exists.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def configure(home_assistant_url: str, token: str, broker: str, port: int) -> dict[str, Any]:
    """Authenticate and submit the MQTT broker form via Home Assistant's config flow."""
    base_url = home_assistant_url.rstrip("/")
    flow = _post_json(f"{base_url}/api/config/config_entries/flow", token, {"handler": "mqtt"})
    if flow.get("type") == "abort":
        return {"action": "already_configured", "reason": flow.get("reason")}
    if flow.get("type") != "form" or flow.get("step_id") != "broker" or not isinstance(flow.get("flow_id"), str):
        raise RuntimeError(f"unexpected MQTT config flow response: {flow}")

    completed = _post_json(
        f"{base_url}/api/config/config_entries/flow/{flow['flow_id']}",
        token,
        {"broker": broker, "port": port},
    )
    if completed.get("type") == "create_entry":
        return {"action": "created", "title": completed.get("title")}
    if completed.get("type") == "abort":
        return {"action": "already_configured", "reason": completed.get("reason")}
    raise RuntimeError(f"MQTT config entry was not created: {completed}")


def _post_json(url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - Home Assistant URL is an explicit local argument.
            decoded = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Home Assistant config flow returned HTTP {error.code}: {detail}") from error
    if not isinstance(decoded, dict):
        raise RuntimeError(f"unexpected Home Assistant config flow response: {decoded}")
    return decoded


def _validate_home_assistant_url(home_assistant_url: str) -> None:
    parsed = urlparse(home_assistant_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Home Assistant URL must start with http:// or https://")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("HA_URL", "http://127.0.0.1:8123"))
    parser.add_argument("--broker", default=os.environ.get("EDGEHOME_MQTT_BROKER", "mqtt"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("EDGEHOME_MQTT_PORT", "1883")))
    args = parser.parse_args()
    token = os.environ.get("HA_TOKEN")
    if not token:
        raise SystemExit("Set HA_TOKEN before configuring the MQTT integration.")
    _validate_home_assistant_url(args.url)
    print(json.dumps(configure(args.url, token, args.broker, args.port), ensure_ascii=False))


if __name__ == "__main__":
    main()
