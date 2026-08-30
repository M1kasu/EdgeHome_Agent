from __future__ import annotations

import base64
from pathlib import Path
import sys

import pytest
import yaml

from spacebutler.esphome_registry import esphome_nodes, resolve_entity_id


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ha_bootstrap import require_esphome_noise_psk  # noqa: E402


def test_real_node_ports_and_alias_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ESPHOME_HOST", raising=False)
    assert esphome_nodes() == (
        ("light", "192.168.111.134", 6053),
        ("curtain", "192.168.111.134", 6054),
        ("ac", "192.168.111.134", 6055),
        ("presence", "192.168.111.134", 6056),
    )
    states = [
        {
            "entity_id": "light.living_room_light",
            "attributes": {"friendly_name": "Living Room Light"},
        }
    ]
    assert resolve_entity_id(states, "living_room_main_light") == "light.living_room_light"


def test_noise_psk_is_required_and_never_generated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ESPHOME_NOISE_PSK", raising=False)
    monkeypatch.delenv("SPACEBUTLER_ESPHOME_NOISE_PSK", raising=False)
    with pytest.raises(RuntimeError, match="required"):
        require_esphome_noise_psk()

    existing_key = base64.b64encode(bytes(range(32))).decode("ascii")
    monkeypatch.setenv("ESPHOME_NOISE_PSK", existing_key)
    assert require_esphome_noise_psk() == existing_key

    monkeypatch.setenv("ESPHOME_NOISE_PSK", "not-a-key")
    with pytest.raises(RuntimeError, match="Base64"):
        require_esphome_noise_psk()


def test_compose_disables_duplicate_and_legacy_devices_by_default() -> None:
    compose = yaml.safe_load((ROOT / "deployment" / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    for suffix in ("light", "curtain", "ac", "presence"):
        assert services[f"spacebutler-esphome-{suffix}"]["profiles"] == ["local-esphome"]

    assert services["spacebutler-home-assistant"].get("depends_on") is None
    for name, service in services.items():
        if name == "spacebutler-mqtt" or name.startswith("spacebutler-device-"):
            assert service["profiles"] == ["legacy-simulator"]
