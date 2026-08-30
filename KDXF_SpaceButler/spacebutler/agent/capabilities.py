"""Device Capability Registry: the only vocabulary the planner is allowed to use."""

from __future__ import annotations

from spacebutler.agent.models import DeviceCapability

# inventory type -> capability template. Ranges mirror the deterministic
# control layer (workbench._call_device_service) and the ESPHome firmware.
_TEMPLATES: dict[str, dict[str, object]] = {
    "light": {
        "actions": ["turn_on", "turn_off", "set_brightness", "read_state"],
        "brightness_range": (1, 100),
    },
    "switch": {
        "actions": ["turn_on", "turn_off", "read_state"],
    },
    "curtain": {
        "actions": ["set_position", "read_state"],
        "position_range": (0, 100),
    },
    "climate": {
        "actions": ["set_hvac_mode", "set_temperature", "turn_off", "read_state"],
        "temperature_range": (16, 30),
        "modes": ["off", "cool", "heat"],
    },
    "presence": {"actions": ["read_state"], "readonly": True},
    "contact": {"actions": ["read_state"], "readonly": True},
    "illuminance": {"actions": ["read_state"], "readonly": True},
    "temperature": {"actions": ["read_state"], "readonly": True},
}


def build_capability_registry(devices: list[dict[str, object]]) -> dict[str, DeviceCapability]:
    """Project the live workbench inventory into planner-facing capabilities."""
    registry: dict[str, DeviceCapability] = {}
    for item in devices:
        if not isinstance(item, dict) or not item.get("online") or not item.get("entity_id"):
            continue
        device_type = str(item.get("type", ""))
        template = _TEMPLATES.get(device_type)
        if template is None:
            continue
        device_id = str(item.get("device_id"))
        ha_state = item.get("ha_state") if isinstance(item.get("ha_state"), dict) else {}
        attributes = ha_state.get("attributes") if isinstance(ha_state.get("attributes"), dict) else {}
        modes = list(template.get("modes", []))
        if device_type == "climate":
            live_modes = attributes.get("hvac_modes")
            if isinstance(live_modes, list):
                modes = [str(mode).lower() for mode in live_modes if str(mode).strip()]
        temperature_range = template.get("temperature_range")
        temperature_step = None
        minimum = attributes.get("min_temp")
        maximum = attributes.get("max_temp")
        if device_type == "climate" and isinstance(minimum, (int, float)) and isinstance(maximum, (int, float)):
            temperature_range = (float(minimum), float(maximum))
        live_step = attributes.get("target_temp_step")
        if device_type == "climate" and isinstance(live_step, (int, float)) and float(live_step) > 0:
            temperature_step = float(live_step)
        registry[device_id] = DeviceCapability(
            device_id=device_id,
            entity_id=str(item.get("entity_id")),
            domain=_domain_for(device_type, str(item.get("entity_id"))),
            name=str(item.get("name", device_id)),
            room=str(item.get("room", "")),
            actions=list(template.get("actions", [])),
            brightness_range=template.get("brightness_range"),
            temperature_range=temperature_range,
            temperature_step=temperature_step,
            position_range=template.get("position_range"),
            modes=modes,
            readonly=bool(template.get("readonly", False)),
        )
    return registry


def _domain_for(device_type: str, entity_id: str) -> str:
    mapping = {"light": "light", "switch": "switch", "curtain": "cover", "climate": "climate",
               "presence": "binary_sensor", "contact": "binary_sensor", "temperature": "sensor",
               "illuminance": "sensor"}
    domain = mapping.get(device_type, entity_id.split(".", 1)[0])
    return domain or device_type
