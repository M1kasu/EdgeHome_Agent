"""Unit coverage for M1's generic parsing and protocol-feedback configuration."""

from custom_components.llama_conversation.edgehome.understanding.intent_classifier import ChineseIntentClassifier
from custom_components.llama_conversation.edgehome.understanding.models import FastIntent
from custom_components.llama_conversation.edgehome.understanding.slot_extractor import ChineseSlotExtractor
from custom_components.llama_conversation.edgehome.entity_registry import build_demo_entity_registry
from custom_components.llama_conversation.edgehome.light import brightness_verification
from device_simulator.edgehome_simulator.app import LightDevice, _feedback_discovery


def test_brightness_parser_retains_negative_values_for_validation() -> None:
    slots = ChineseSlotExtractor().extract("把客厅主灯调到-10%", FastIntent.SET_BRIGHTNESS)

    assert slots.value == -10
    assert slots.errors == ["百分比必须在 0 到 100 之间"]


def test_brightness_parser_supports_generic_half_and_tenths() -> None:
    extractor = ChineseSlotExtractor()

    assert extractor.extract("把客厅主灯调到一半", FastIntent.SET_BRIGHTNESS).value == 50
    assert extractor.extract("把客厅主灯调成三成", FastIntent.SET_BRIGHTNESS).value == 30
    assert extractor.extract("客厅那盏大灯开到四成", FastIntent.SET_BRIGHTNESS).value == 40
    assert extractor.extract("窗帘拉到三分之一", FastIntent.CONTROL_CURTAIN).value == 33


def test_light_intent_accepts_generic_open_and_brightness_forms() -> None:
    classifier = ChineseIntentClassifier()

    assert classifier.classify("把客厅主照明开一下").intent == FastIntent.TURN_ON
    assert classifier.classify("灯光降到20%").intent == FastIntent.SET_BRIGHTNESS
    assert classifier.classify("客厅那盏大灯给我开到四成").intent == FastIntent.SET_BRIGHTNESS


def test_entity_resolution_combines_room_and_device_tokens_without_a_full_phrase() -> None:
    registry = build_demo_entity_registry()

    assert registry.resolve_query("大厅那盏主灯设为17%", {"light"}) == "light.edgehome_living_room_main"


def test_feedback_sensor_discovery_is_read_only_and_shares_the_device_identity() -> None:
    device = LightDevice(
        device_id="living_room_main_light",
        name="客厅主灯",
        room="living_room",
        initial_state={"power": "ON", "brightness": 204},
        command_delay_ms=200,
        failure_rate=0,
    )

    discovery = _feedback_discovery(device)

    assert discovery["state_topic"] == "edgehome/devices/living_room_main_light/feedback"
    assert discovery["json_attributes_topic"] == discovery["state_topic"]
    assert "command_topic" not in discovery
    assert discovery["device"]["identifiers"] == ["edgehome_device_simulator_living_room"]


def test_zero_percent_uses_a_verified_off_target() -> None:
    verification = brightness_verification("light.edgehome_living_room_main", 0)

    assert verification.state == "off"
    assert verification.attributes == {}
