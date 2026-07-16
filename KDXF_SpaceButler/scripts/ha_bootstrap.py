"""Bootstrap a local Home Assistant instance without persisting credentials in reports."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def wait_until_ready(base_url: str, timeout_seconds: float = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    stable_since: float | None = None
    while time.monotonic() < deadline:
        try:
            response = request_json("GET", f"{base_url.rstrip('/')}/api/onboarding")
            if isinstance(response, list):
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= 5:
                    return
            else:
                stable_since = None
        except (OSError, RuntimeError):
            stable_since = None
        time.sleep(0.5)
    raise TimeoutError("Home Assistant did not expose the onboarding API in time")


def obtain_token(base_url: str, auth_store: Path) -> str:
    existing = os.environ.get("HA_TOKEN")
    if existing:
        try:
            probe = request_json("GET", f"{base_url.rstrip('/')}/api/", token=existing)
            if isinstance(probe, dict) and probe.get("message") == "API running.":
                return existing
        except RuntimeError:
            pass
    base_url = base_url.rstrip("/")
    steps = request_json("GET", f"{base_url}/api/onboarding")
    if not isinstance(steps, list):
        raise RuntimeError("invalid Home Assistant onboarding response")
    completed = {str(item.get("step")) for item in steps if isinstance(item, dict) and item.get("done")}
    client_id = f"{base_url}/"
    if "user" not in completed:
        username = f"spacebutler_{secrets.token_hex(4)}"
        created = request_json(
            "POST",
            f"{base_url}/api/onboarding/users",
            {
                "name": "SpaceButler Acceptance",
                "username": username,
                "password": secrets.token_urlsafe(24),
                "client_id": client_id,
                "language": "zh-Hans",
            },
        )
        if not isinstance(created, dict) or not isinstance(created.get("auth_code"), str):
            raise RuntimeError(f"Home Assistant user onboarding failed: {created}")
        token = exchange_authorization_code(base_url, client_id, created["auth_code"])
        _finish_onboarding(base_url, client_id, token, completed)
        return token
    return exchange_refresh_token_from_store(base_url, auth_store)


def exchange_authorization_code(base_url: str, client_id: str, auth_code: str) -> str:
    response = request_form(
        f"{base_url.rstrip('/')}/auth/token",
        {"grant_type": "authorization_code", "code": auth_code, "client_id": client_id},
    )
    if not isinstance(response, dict) or not isinstance(response.get("access_token"), str):
        raise RuntimeError("Home Assistant authorization-code exchange failed")
    return response["access_token"]


def exchange_refresh_token_from_store(base_url: str, auth_store: Path) -> str:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not auth_store.exists():
        time.sleep(0.25)
    if not auth_store.exists():
        raise RuntimeError(f"Home Assistant auth store is missing: {auth_store}")
    payload = json.loads(auth_store.read_text(encoding="utf-8"))
    tokens = payload.get("data", {}).get("refresh_tokens", [])
    refresh = next(
        (
            item
            for item in reversed(tokens)
            if item.get("token_type") == "normal"
            and isinstance(item.get("token"), str)
            and isinstance(item.get("client_id"), str)
        ),
        None,
    )
    if refresh is None:
        raise RuntimeError("Home Assistant auth store has no reusable normal refresh token")
    response = request_form(
        f"{base_url.rstrip('/')}/auth/token",
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh["token"],
            "client_id": refresh["client_id"],
        },
    )
    if not isinstance(response, dict) or not isinstance(response.get("access_token"), str):
        raise RuntimeError("Home Assistant refresh-token exchange failed")
    return response["access_token"]


def configure_mqtt(base_url: str, token: str, broker: str = "spacebutler-mqtt", port: int = 1883) -> dict[str, Any]:
    flow = request_json(
        "POST",
        f"{base_url.rstrip('/')}/api/config/config_entries/flow",
        {"handler": "mqtt"},
        token,
    )
    if isinstance(flow, dict) and flow.get("type") == "abort":
        return {"action": "already_configured", "reason": flow.get("reason")}
    if not isinstance(flow, dict) or flow.get("type") != "form" or not isinstance(flow.get("flow_id"), str):
        raise RuntimeError(f"unexpected MQTT flow response: {flow}")
    completed = request_json(
        "POST",
        f"{base_url.rstrip('/')}/api/config/config_entries/flow/{flow['flow_id']}",
        {"broker": broker, "port": port},
        token,
    )
    if isinstance(completed, dict) and completed.get("type") == "create_entry":
        return {"action": "created", "title": completed.get("title")}
    if isinstance(completed, dict) and completed.get("type") == "abort":
        return {"action": "already_configured", "reason": completed.get("reason")}
    raise RuntimeError(f"MQTT config entry was not created: {completed}")


def request_json(
    method: str,
    url: str,
    payload: dict[str, object] | None = None,
    token: str | None = None,
    timeout_seconds: float = 30,
) -> object:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {url}: {detail}") from error
    except (OSError, URLError) as error:
        raise RuntimeError(f"cannot reach {url}: {getattr(error, 'reason', error)}") from error
    return json.loads(raw.decode("utf-8")) if raw else None


def request_form(url: str, payload: dict[str, str]) -> object:
    request = Request(
        url,
        data=urlencode(payload).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Home Assistant token endpoint returned HTTP {error.code}: {detail}") from error
    return json.loads(raw.decode("utf-8"))


def _finish_onboarding(base_url: str, client_id: str, token: str, completed: set[str]) -> None:
    if "core_config" not in completed:
        request_json("POST", f"{base_url}/api/onboarding/core_config", {}, token)
    if "analytics" not in completed:
        request_json("POST", f"{base_url}/api/onboarding/analytics", {}, token)
    if "integration" not in completed:
        request_json(
            "POST",
            f"{base_url}/api/onboarding/integration",
            {"client_id": client_id, "redirect_uri": client_id},
            token,
        )
