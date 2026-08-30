"""Bootstrap a local Home Assistant instance without persisting credentials in reports."""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from spacebutler.esphome_registry import esphome_nodes


def wait_until_ready(base_url: str, timeout_seconds: float = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    stable_since: float | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url.rstrip('/')}/", timeout=2) as response:
                ready = response.status == 200
            if ready:
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
    try:
        return exchange_refresh_token_from_store(base_url, auth_store)
    except RuntimeError:
        pass
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


# Host 平台无 mDNS，必须手工配置 Ubuntu VM 地址及各自映射端口。
ESPHOME_NODES: tuple[tuple[str, str, int], ...] = esphome_nodes()


def require_esphome_noise_psk() -> str:
    """Read and validate the existing Ubuntu ESPHome Noise PSK.

    A missing key is a hard startup error. Generating a replacement here would
    make Home Assistant use a key that does not match the already-running nodes.
    """
    key = (
        os.environ.get("ESPHOME_NOISE_PSK")
        or os.environ.get("SPACEBUTLER_ESPHOME_NOISE_PSK")
        or ""
    ).strip()
    if not key:
        raise RuntimeError(
            "ESPHOME_NOISE_PSK is required; copy the existing value from the Ubuntu ESPHome secrets.yaml"
        )
    try:
        decoded = base64.b64decode(key, validate=True)
    except (binascii.Error, ValueError) as error:
        raise RuntimeError("ESPHOME_NOISE_PSK must be valid Base64") from error
    if len(decoded) != 32:
        raise RuntimeError("ESPHOME_NOISE_PSK must decode to exactly 32 bytes")
    return key


def configure_esphome(base_url: str, token: str, noise_psk: str) -> list[dict[str, Any]]:
    """Create the four ESPHome config entries through the official config flow."""
    results: list[dict[str, Any]] = []
    for role, host, port in esphome_nodes():
        flow = request_json(
            "POST",
            f"{base_url.rstrip('/')}/api/config/config_entries/flow",
            {"handler": "esphome"},
            token,
        )
        if isinstance(flow, dict) and flow.get("type") == "abort":
            results.append(
                {"role": role, "host": host, "port": port, "action": "already_configured", "reason": flow.get("reason")}
            )
            continue
        if not isinstance(flow, dict) or flow.get("type") != "form" or not isinstance(flow.get("flow_id"), str):
            raise RuntimeError(f"unexpected ESPHome flow response for {host}: {flow}")
        connection = request_json(
            "POST",
            f"{base_url.rstrip('/')}/api/config/config_entries/flow/{flow['flow_id']}",
            {"host": host, "port": port},
            token,
        )
        if (
            isinstance(connection, dict)
            and connection.get("type") == "form"
            and connection.get("step_id") == "encryption_key"
        ):
            connection = request_json(
                "POST",
                f"{base_url.rstrip('/')}/api/config/config_entries/flow/{flow['flow_id']}",
                {"noise_psk": noise_psk},
                token,
            )
        if isinstance(connection, dict) and connection.get("type") == "create_entry":
            results.append({"role": role, "host": host, "port": port, "action": "created", "title": connection.get("title")})
        elif isinstance(connection, dict) and connection.get("type") == "abort":
            results.append(
                {"role": role, "host": host, "port": port, "action": "already_configured", "reason": connection.get("reason")}
            )
        else:
            raise RuntimeError(f"ESPHome config entry was not created for {host}: {connection}")
    return results


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
