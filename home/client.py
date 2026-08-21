from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from home.entity_registry import EntityRegistry
from home.state_cache import StateCache


class SimulatedHomeClient:
    """Deterministic Home Assistant-shaped adapter used for local demonstrations/tests."""

    def __init__(self, registry: EntityRegistry, cache: StateCache | None = None) -> None:
        self.registry = registry
        self.cache = cache or StateCache()
        self._states: dict[str, dict[str, Any]] = {
            "light.living_room_main": {"state": "on", "attributes": {"brightness": 70}, "online": True},
            "light.living_room_ambient": {"state": "off", "attributes": {"brightness": 10}, "online": True},
            "climate.living_room_ac": {"state": "on", "attributes": {"temperature": 27, "mode": "cool"}, "online": True},
            "cover.living_room_curtain": {"state": "open", "attributes": {"position": 100}, "online": True},
            "media_player.living_room_tv": {"state": "off", "attributes": {}, "online": True},
            "switch.living_room_socket": {"state": "on", "attributes": {}, "online": True},
            "light.bedroom_main": {"state": "off", "attributes": {"brightness": 50}, "online": True},
            "climate.bedroom_ac": {"state": "off", "attributes": {"temperature": 27, "mode": "cool"}, "online": True},
            "media_player.bedroom_tv": {"state": "off", "attributes": {}, "online": True},
            "switch.refrigerator": {"state": "on", "attributes": {}, "online": True},
            "switch.router": {"state": "on", "attributes": {}, "online": True},
            "vacuum.robot": {"state": "docked", "attributes": {}, "online": True},
        }
        self._environment = {
            "living_room": {"occupancy": False, "temperature": 29.0, "humidity": 68.0, "illuminance": 120.0, "window_open": False},
            "bedroom": {"occupancy": False, "temperature": 27.0, "humidity": 60.0, "illuminance": 15.0, "window_open": False},
            "entryway": {"occupancy": False, "temperature": 28.0, "humidity": 62.0, "illuminance": 80.0, "window_open": False},
        }

    def set_online(self, entity_id: str, online: bool) -> None:
        self._states[entity_id]["online"] = online

    def get_state(self, entity_id: str) -> dict[str, Any] | None:
        item = self._states.get(entity_id)
        if item is None:
            return None
        entity = self.registry.get(entity_id)
        return {
            "entity_id": entity_id,
            "name": entity.name if entity else entity_id,
            "room": entity.room if entity else None,
            "state": item["state"],
            "attributes": deepcopy(item["attributes"]),
            "online": item["online"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_room_state(self, room: str) -> dict[str, Any]:
        cached = self.cache.get(f"room:{room}")
        if cached:
            return cached
        environment = self._environment.get(room)
        if environment is None:
            raise ValueError(f"未知房间: {room}")
        value = {
            "room": room,
            **deepcopy(environment),
            "devices": [self.get_state(entity.entity_id) for entity in self.registry.in_room(room)],
        }
        self.cache.set(f"room:{room}", value)
        return value

    def get_home_state(self) -> dict[str, Any]:
        return {
            "current_time": datetime.now().strftime("%H:%M"),
            "rooms": {room: self.get_room_state(room) for room in self._environment},
        }

    def execute(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        entity_id = arguments.get("entity_id")
        if tool in {"get_home_state", "get_room_state", "get_device_state"}:
            if tool == "get_home_state":
                return {"ok": True, "data": self.get_home_state()}
            if tool == "get_room_state":
                return {"ok": True, "data": self.get_room_state(arguments["room"])}
            state = self.get_state(entity_id)
            return {"ok": state is not None, "data": state, "error": None if state else "entity_not_found"}
        item = self._states.get(entity_id)
        if item is None:
            return {"ok": False, "error": "entity_not_found"}
        if not item["online"]:
            return {"ok": False, "error": "device_offline"}
        if tool == "turn_on_device":
            item["state"] = "on"
        elif tool == "turn_off_device":
            item["state"] = "off"
        elif tool == "set_light_brightness":
            item["state"] = "on"
            item["attributes"]["brightness"] = arguments["brightness"]
        elif tool == "set_temperature":
            item["state"] = "on"
            item["attributes"]["temperature"] = arguments["temperature"]
        elif tool == "control_curtain":
            position = arguments["position"]
            item["attributes"]["position"] = position
            item["state"] = "open" if position > 0 else "closed"
        else:
            return {"ok": False, "error": f"unsupported_tool:{tool}"}
        entity = self.registry.get(entity_id)
        if entity:
            self.cache.invalidate(f"room:{entity.room}")
        return {"ok": True, "data": self.get_state(entity_id)}


class HomeAssistantClient(SimulatedHomeClient):
    """Minimal Home Assistant REST adapter using the same safe internal contract.

    It intentionally accepts only entity IDs selected from ``EntityRegistry`` by the
    upper layers. The token is held in a private field and is never serialized.
    """

    def __init__(self, registry: EntityRegistry, url: str, token: str, cache: StateCache | None = None) -> None:
        super().__init__(registry, cache)
        self.url = url.rstrip("/")
        self._token = token

    def _request(self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> tuple[bool, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.url}{path}",
            data=body,
            method=method,
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=5) as response:  # nosec B310: URL is administrator configuration
                raw = response.read().decode("utf-8")
                return True, json.loads(raw) if raw else {}
        except HTTPError as error:
            return False, f"home_assistant_http_{error.code}"
        except (URLError, TimeoutError, OSError):
            return False, "home_assistant_unavailable"

    def get_state(self, entity_id: str) -> dict[str, Any] | None:
        if self.registry.get(entity_id) is None:
            return None
        ok, payload = self._request(f"/api/states/{entity_id}")
        if not ok:
            return {"entity_id": entity_id, "state": "unknown", "attributes": {}, "online": False, "error": payload}
        entity = self.registry.get(entity_id)
        attributes = payload.get("attributes", {})
        # Internally brightness is always a percentage, even though HA returns 0-255.
        if entity and entity.kind == "light" and "brightness" in attributes:
            attributes = {**attributes, "brightness": round(attributes["brightness"] / 255 * 100)}
        return {
            "entity_id": entity_id,
            "name": entity.name if entity else entity_id,
            "room": entity.room if entity else None,
            "state": payload.get("state", "unknown"),
            "attributes": attributes,
            "online": payload.get("state") not in {"unavailable", "unknown"},
            "updated_at": payload.get("last_updated", datetime.now(timezone.utc).isoformat()),
        }

    def execute(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool in {"get_home_state", "get_room_state", "get_device_state"}:
            return super().execute(tool, arguments)
        entity_id = arguments.get("entity_id")
        entity = self.registry.get(entity_id) if entity_id else None
        if entity is None:
            return {"ok": False, "error": "entity_not_found"}
        current = self.get_state(entity_id)
        if not current or not current.get("online"):
            return {"ok": False, "error": (current or {}).get("error", "device_offline")}
        if tool in {"turn_on_device", "turn_off_device"}:
            domain, service = entity_id.split(".", 1)[0], "turn_on" if tool == "turn_on_device" else "turn_off"
            payload = {"entity_id": entity_id}
        elif tool == "set_light_brightness":
            domain, service = "light", "turn_on"
            payload = {"entity_id": entity_id, "brightness_pct": arguments["brightness"]}
        elif tool == "set_temperature":
            domain, service = "climate", "set_temperature"
            payload = {"entity_id": entity_id, "temperature": arguments["temperature"]}
        elif tool == "control_curtain":
            domain, service = "cover", "set_cover_position"
            payload = {"entity_id": entity_id, "position": arguments["position"]}
        else:
            return {"ok": False, "error": f"unsupported_tool:{tool}"}
        ok, response = self._request(f"/api/services/{domain}/{service}", method="POST", payload=payload)
        if not ok:
            return {"ok": False, "error": response}
        self.cache.invalidate(f"room:{entity.room}")
        return {"ok": True, "data": self.get_state(entity_id), "service_response": response}
