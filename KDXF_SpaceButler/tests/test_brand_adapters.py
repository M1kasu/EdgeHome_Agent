"""品牌适配器层：同一个规范化指令必须翻译成各生态的原生报文。"""

from __future__ import annotations

import pytest

from spacebutler.brands import (
    BRAND_RECORDS,
    DEFAULT_DEVICE_BRANDS,
    brand_for_device,
    brand_summary,
    translate_command,
)
from spacebutler.esphome_registry import ESPHOME_DEVICE_RECORDS


def test_every_registered_device_has_a_brand() -> None:
    """设备注册表和品牌映射必须同步，否则前端会出现空徽标。"""
    registry_ids = {str(record["device_id"]) for record in ESPHOME_DEVICE_RECORDS}
    assert registry_ids == set(DEFAULT_DEVICE_BRANDS)


def test_every_mapped_brand_is_defined() -> None:
    defined = {str(record["brand_id"]) for record in BRAND_RECORDS}
    assert set(DEFAULT_DEVICE_BRANDS.values()) <= defined


def test_mijia_light_uses_miot_service_and_property_ids() -> None:
    result = translate_command("living_room_main_light", {"power": "on", "brightness": 45})

    assert result is not None
    assert result["brand_id"] == "mijia"
    assert result["protocol"] == "MIoT-Spec"
    # MIoT 用 siid/piid 定位属性，而不是一个通用的 brightness 字段。
    assert result["native_command"]["siid"] == 2
    assert result["native_command"]["properties"] == [{"siid": 2, "piid": 3, "value": 45}]


def test_hue_rescales_percentage_into_its_own_0_254_range() -> None:
    result = translate_command("bedroom_reading_light", {"power": "on", "brightness": 45})

    assert result is not None
    assert result["brand_id"] == "hue"
    # 45% 在 Hue 上是 114/254，直接传 45 会导致亮度差一大截。
    assert result["native_command"] == {"on": True, "bri": 114}


def test_hue_brightness_never_rounds_down_to_zero() -> None:
    result = translate_command("bedroom_reading_light", {"power": "on", "brightness": 1})

    assert result is not None
    assert result["native_command"]["bri"] >= 1


def test_aqara_curtain_uses_a_zigbee_cluster_command() -> None:
    result = translate_command("living_room_curtain", {"position": 30})

    assert result is not None
    assert result["native_command"] == {
        "cluster": "0x0102",
        "command": "go_to_lift_percentage",
        "payload": {"percentage": 30},
    }


def test_midea_climate_encodes_mode_as_an_enum_with_the_setpoint() -> None:
    result = translate_command("living_room_ac", {"mode": "cool", "temperature": 25.0})

    assert result is not None
    assert result["native_command"] == {
        "power": True,
        "mode": 2,
        "prompt_tone": False,
        "target_temperature": 25.0,
    }


def test_midea_off_mode_clears_power_without_a_setpoint() -> None:
    result = translate_command("living_room_ac", {"mode": "off"})

    assert result is not None
    assert result["native_command"]["power"] is False
    assert "target_temperature" not in result["native_command"]


def test_harmony_switch_uses_service_id_and_properties() -> None:
    result = translate_command("living_room_tv", {"power": "off"})

    assert result is not None
    assert result["native_command"] == {"serviceId": "switch", "properties": {"on": False}}


def test_unknown_device_translates_to_none_instead_of_guessing() -> None:
    assert translate_command("not_a_device", {"power": "on"}) is None
    assert brand_for_device("not_a_device") is None


@pytest.mark.parametrize("device_id", sorted(DEFAULT_DEVICE_BRANDS))
def test_every_device_reports_a_named_brand(device_id: str) -> None:
    brand = brand_for_device(device_id)
    assert brand is not None
    assert brand["name"]
    assert brand["protocol"]


def test_brand_summary_counts_only_brands_in_use() -> None:
    summary = brand_summary()

    counts = {str(item["brand_id"]): item["device_count"] for item in summary}
    assert sum(counts.values()) == len(DEFAULT_DEVICE_BRANDS)
    assert all(count > 0 for count in counts.values())
    # Aqara 覆盖窗帘、两个传感器和窗户，是当前设备最多的生态。
    assert counts["aqara"] == 4
