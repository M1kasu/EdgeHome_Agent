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
        ("bedroom", "192.168.111.134", 6057),
        ("living_room_aux", "192.168.111.134", 6058),
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


class _EsphomeLoader(yaml.SafeLoader):
    """ESPHome 配置含 !secret 等自定义标签，测试只关心结构，忽略标签取值。"""


_EsphomeLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _load_node(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_EsphomeLoader)


def test_demo_services_referenced_by_the_console_exist_in_node_firmware() -> None:
    """演示控制台调用的 esphome.* 服务必须真的在节点固件里声明。

    服务名写错不会报错，只会静默不生效——录制现场才发现就太晚了。
    """
    esphome_dir = ROOT / "deployment" / "esphome"
    declared: set[str] = set()
    for config_name in ("ac.yaml", "bedroom.yaml", "living_room_aux.yaml"):
        config = _load_node(esphome_dir / config_name)
        node = str(config["esphome"]["name"]).replace("-", "_")
        for action in config.get("api", {}).get("actions", []) or []:
            declared.add(f"{node}_{action['action']}")

    assert {
        "living_room_ac_demo_heat_injection",
        "bedroom_demo_set_presence",
        "bedroom_demo_set_illuminance",
        "living_room_aux_demo_set_window",
    } <= declared


def test_migrated_devices_are_declared_by_the_new_nodes() -> None:
    """原 MQTT 的五台设备必须由卧室/客厅辅助节点提供，且实体名与注册表别名一致。"""
    esphome_dir = ROOT / "deployment" / "esphome"
    bedroom = _load_node(esphome_dir / "bedroom.yaml")
    aux = _load_node(esphome_dir / "living_room_aux.yaml")

    assert bedroom["light"][0]["name"] == "Reading Light"
    assert bedroom["binary_sensor"][0]["name"] == "Presence"
    assert bedroom["sensor"][0]["name"] == "Illuminance"
    assert aux["switch"][0]["name"] == "TV"
    assert aux["binary_sensor"][0]["name"] == "Window"
    # 功耗是独立实体，节能统计依赖它。
    assert aux["sensor"][0]["unit_of_measurement"] == "W"


def test_compose_disables_duplicate_and_legacy_devices_by_default() -> None:
    compose = yaml.safe_load((ROOT / "deployment" / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    for suffix in ("light", "curtain", "ac", "presence", "bedroom", "living-room-aux"):
        assert services[f"spacebutler-esphome-{suffix}"]["profiles"] == ["local-esphome"]

    assert services["spacebutler-home-assistant"].get("depends_on") is None
    for name, service in services.items():
        if name == "spacebutler-mqtt" or name.startswith("spacebutler-device-"):
            assert service["profiles"] == ["legacy-simulator"]
