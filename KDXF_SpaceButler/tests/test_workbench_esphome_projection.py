from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import unittest
from unittest.mock import Mock

from spacebutler.brands import BrandBindingStore
from spacebutler.workbench import (
    WorkbenchController,
    _power_timeline,
    _state_at,
    _trapezoid_kwh,
)


def _named(value: str, friendly: str, extra: dict[str, object] | None = None) -> dict[str, object]:
    return {"state": value, "attributes": {"friendly_name": friendly, **(extra or {})}}


def _mixed_states() -> dict[str, dict[str, object]]:
    """One entity per deployed ESPHome node; MQTT 设备已全部迁入固件节点。"""
    return {
        # 客厅四节点
        "light.spacebutler_living_room_main_2": _named("on", "Living Room Light", {"brightness": 128}),
        "climate.spacebutler_living_room_ac_2": _named(
            "cool", "Living Room AC", {"temperature": 25.5, "current_temperature": 26.0}
        ),
        "cover.spacebutler_living_room_curtain_2": _named("closed", "Living Room Curtain", {"current_position": 0}),
        "binary_sensor.spacebutler_living_room_presence": _named("on", "Living Room Presence"),
        "sensor.spacebutler_living_room_temperature": _named("25.4", "Living Room Temperature"),
        # 卧室节点
        "light.bedroom_reading_light": _named("on", "Bedroom Reading Light", {"brightness": 128}),
        "binary_sensor.bedroom_presence": _named("on", "Bedroom Presence"),
        "sensor.bedroom_illuminance": _named("8", "Bedroom Illuminance"),
        # 客厅辅助节点
        "switch.living_room_tv": _named("off", "Living Room TV"),
        "sensor.living_room_tv_power": _named("1.5", "Living Room TV Power"),
        "binary_sensor.living_room_window": _named("on", "Living Room Window"),
    }


def _controller(ha_states: dict[str, dict[str, object]]) -> WorkbenchController:
    controller = object.__new__(WorkbenchController)
    controller._lock = threading.RLock()
    controller._ha = Mock()
    # 出厂品牌映射，不落盘；投影要读它填充设备卡的品牌徽标。
    controller._brand_bindings = BrandBindingStore()
    controller._ha.states = Mock(
        return_value=[{"entity_id": key, **value} for key, value in ha_states.items()]
    )
    return controller


class EsphomeProjectionTests(unittest.TestCase):
    def test_every_device_is_projected_from_the_single_esphome_transport(self) -> None:
        devices = WorkbenchController._projected_devices(_mixed_states())

        by_id = {str(item["device_id"]): item for item in devices}
        # 迁移后不再有第二种来源；任何 mqtt 标记都是回归。
        self.assertEqual({str(item["source"]) for item in devices}, {"esphome"})
        self.assertIn("bedroom_reading_light", by_id)
        self.assertIn("living_room_window_sensor", by_id)
        # 两个空间都应出现，仪表盘才能分组展示。
        self.assertEqual({str(item["room"]) for item in devices}, {"living_room", "bedroom"})

    def test_each_device_carries_its_brand_for_cross_vendor_display(self) -> None:
        devices = WorkbenchController._projected_devices(_mixed_states())

        brands = {
            str(item["device_id"]): (item["brand"] or {}).get("brand_id")
            for item in devices
        }
        self.assertEqual(brands["living_room_main_light"], "mijia")
        self.assertEqual(brands["living_room_curtain"], "aqara")
        self.assertEqual(brands["living_room_ac"], "midea")
        self.assertEqual(brands["living_room_tv"], "harmony")
        self.assertEqual(brands["bedroom_reading_light"], "hue")
        # 每台投影出来的设备都必须能说出品牌，否则前端会出现空徽标。
        self.assertTrue(all(value for value in brands.values()))

    def test_window_contact_resolves_to_its_real_open_state(self) -> None:
        """主动规则依赖门窗传感器；投影缺失会让界面显示假的“关闭”。"""
        devices = WorkbenchController._projected_devices(_mixed_states())

        window = next(item for item in devices if item["device_id"] == "living_room_window_sensor")
        self.assertEqual(window["type"], "contact")
        self.assertTrue(window["state"]["open"])
        self.assertTrue(window["readonly"])
        self.assertTrue(window["ui"]["warning"])

    def test_ambiguous_esphome_entities_are_not_projected(self) -> None:
        states = _mixed_states()
        # 同一设备匹配到两个 ESPHome 实体时必须拒绝猜测，避免控制错设备。
        states["light.spacebutler_living_room_main_legacy"] = _named(
            "off", "SpaceButler Living Room Main", {"brightness": 0}
        )

        devices = WorkbenchController._projected_devices(states)

        matching = [item for item in devices if item["device_id"] == "living_room_main_light"]
        self.assertEqual(matching, [])

    def test_switch_folds_in_its_companion_power_meter(self) -> None:
        devices = WorkbenchController._projected_devices(_mixed_states())

        tv = next(item for item in devices if item["device_id"] == "living_room_tv")
        self.assertEqual(tv["state"]["power"], "OFF")
        self.assertEqual(tv["state"]["power_w"], 1.5)

    def test_readonly_sensors_expose_no_control_descriptors(self) -> None:
        devices = WorkbenchController._projected_devices(_mixed_states())

        for device_id in (
            "living_room_window_sensor",
            "bedroom_presence_sensor",
            "bedroom_illuminance_sensor",
            "living_room_temperature_sensor",
        ):
            device = next(item for item in devices if item["device_id"] == device_id)
            real_controls = [
                control
                for control in device["ui"]["controls"]
                if not control.get("test_control")
            ]
            self.assertEqual(real_controls, [], f"{device_id} 不应暴露可控开关")

    def test_source_readout_names_the_real_transport(self) -> None:
        devices = WorkbenchController._projected_devices(_mixed_states())

        for device_id in ("living_room_temperature_sensor", "bedroom_presence_sensor"):
            sensor = next(item for item in devices if item["device_id"] == device_id)
            readout = str(sensor["ui"]["state_secondary"])
            self.assertIn("ESPHome", readout)
            self.assertNotIn("MQTT", readout)


class EsphomeNodeStatusTests(unittest.TestCase):
    def test_nodes_report_resolution_against_projected_entities(self) -> None:
        controller = _controller(_mixed_states())

        report = controller.esphome_nodes()

        self.assertEqual(report["projected"], {"esphome": 10, "online": 10})
        self.assertEqual(
            {node["role"] for node in report["nodes"]},
            {"light", "curtain", "ac", "presence", "bedroom", "living_room_aux"},
        )
        self.assertTrue(all(node["connected"] for node in report["nodes"]))

    def test_missing_node_is_reported_as_unresolved(self) -> None:
        states = _mixed_states()
        del states["cover.spacebutler_living_room_curtain_2"]
        controller = _controller(states)

        report = controller.esphome_nodes()

        curtain = next(node for node in report["nodes"] if node["role"] == "curtain")
        self.assertFalse(curtain["connected"])
        self.assertEqual(report["projected"]["esphome"], 9)


class PowerHistoryTests(unittest.TestCase):
    def test_state_at_returns_the_last_value_not_after_the_moment(self) -> None:
        base = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
        points = [
            {"state": "off", "last_updated": base.isoformat()},
            {"state": "cool", "last_updated": (base + timedelta(minutes=10)).isoformat()},
            {"state": "off", "last_updated": (base + timedelta(minutes=40)).isoformat()},
        ]

        self.assertEqual(_state_at(points, base + timedelta(minutes=5))["state"], "off")
        self.assertEqual(_state_at(points, base + timedelta(minutes=20))["state"], "cool")
        self.assertEqual(_state_at(points, base + timedelta(minutes=50))["state"], "off")
        self.assertIsNone(_state_at(points, base - timedelta(minutes=1)))

    def test_timeline_applies_the_rated_model_to_recorded_states(self) -> None:
        base = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
        devices = [
            {"device_id": "ac", "entity_id": "climate.ac", "type": "climate"},
            {"device_id": "tv", "entity_id": "switch.tv", "type": "switch"},
        ]
        history = {
            "climate.ac": [
                {"state": "off", "last_updated": base.isoformat(), "attributes": {}},
                {
                    "state": "cool",
                    "last_updated": (base + timedelta(hours=1)).isoformat(),
                    "attributes": {"temperature": 25},
                },
            ],
            "switch.tv": [
                {"state": "off", "last_updated": base.isoformat(), "attributes": {"power_w": 1.5}},
            ],
        }

        points = _power_timeline(devices, history, base, base + timedelta(hours=2), 3)

        # 起点空调关闭：仅电视待机 1.5 W；1 小时后制冷启动，加 1050 W 额定功率。
        self.assertEqual([point["power_w"] for point in points], [1.5, 1051.5, 1051.5])

    def test_timeline_skips_unavailable_recorded_states(self) -> None:
        base = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
        devices = [{"device_id": "ac", "entity_id": "climate.ac", "type": "climate"}]
        history = {
            "climate.ac": [
                {"state": "unavailable", "last_updated": base.isoformat(), "attributes": {}},
                {
                    "state": "cool",
                    "last_updated": (base + timedelta(hours=1)).isoformat(),
                    "attributes": {"temperature": 25},
                },
            ]
        }

        points = _power_timeline(devices, history, base, base + timedelta(hours=2), 3)

        self.assertEqual([point["power_w"] for point in points], [0.0, 1050.0, 1050.0])

    def test_trapezoid_integration_converts_a_power_curve_into_kwh(self) -> None:
        base = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
        points = [
            {"at": base.isoformat(), "power_w": 0.0},
            {"at": (base + timedelta(hours=1)).isoformat(), "power_w": 1000.0},
            {"at": (base + timedelta(hours=2)).isoformat(), "power_w": 1000.0},
        ]

        # 梯形积分：第一小时 (0+1000)/2 = 500 Wh，第二小时 1000 Wh，合计 1.5 kWh。
        self.assertEqual(_trapezoid_kwh(points), 1.5)

    def test_trapezoid_integration_of_a_short_curve_is_zero(self) -> None:
        self.assertEqual(_trapezoid_kwh([]), 0.0)
        self.assertEqual(_trapezoid_kwh([{"at": "2026-08-31T12:00:00+00:00", "power_w": 900}]), 0.0)


if __name__ == "__main__":
    unittest.main()
