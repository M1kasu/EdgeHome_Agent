from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import unittest
from unittest.mock import Mock

from spacebutler.workbench import (
    WorkbenchController,
    _power_timeline,
    _state_at,
    _trapezoid_kwh,
)


def _named(value: str, friendly: str, extra: dict[str, object] | None = None) -> dict[str, object]:
    return {"state": value, "attributes": {"friendly_name": friendly, **(extra or {})}}


def _mixed_states() -> dict[str, dict[str, object]]:
    """One entity per deployed node, covering both transports."""
    return {
        # ESPHome 节点
        "light.spacebutler_living_room_main_2": _named("on", "Living Room Light", {"brightness": 128}),
        "climate.spacebutler_living_room_ac_2": _named(
            "cool", "Living Room AC", {"temperature": 25.5, "current_temperature": 26.0}
        ),
        "cover.spacebutler_living_room_curtain_2": _named("closed", "Living Room Curtain", {"current_position": 0}),
        "binary_sensor.spacebutler_living_room_presence": _named("on", "Living Room Presence"),
        "sensor.spacebutler_living_room_temperature": _named("25.4", "Living Room Temperature"),
        # MQTT 容器
        "light.spacebutler_bedroom_reading_light": _named("on", "卧室阅读灯", {"brightness": 128}),
        "switch.spacebutler_living_room_tv": _named("off", "客厅电视"),
        "sensor.spacebutler_living_room_tv_power": _named("1.5", "客厅电视 功耗"),
        "binary_sensor.spacebutler_living_room_window_sensor": _named("on", "客厅窗户传感器"),
        "binary_sensor.spacebutler_bedroom_presence_sensor": _named("on", "卧室起身传感器"),
        "sensor.spacebutler_bedroom_illuminance_sensor": _named("8", "卧室照度传感器"),
    }


def _controller(ha_states: dict[str, dict[str, object]]) -> WorkbenchController:
    controller = object.__new__(WorkbenchController)
    controller._lock = threading.RLock()
    controller._ha = Mock()
    controller._ha.states = Mock(
        return_value=[{"entity_id": key, **value} for key, value in ha_states.items()]
    )
    return controller


class MqttProjectionTests(unittest.TestCase):
    def test_both_transports_are_projected_with_their_real_source(self) -> None:
        devices = WorkbenchController._projected_devices(_mixed_states())

        by_id = {str(item["device_id"]): item for item in devices}
        self.assertEqual(by_id["living_room_main_light"]["source"], "esphome")
        self.assertEqual(by_id["bedroom_reading_light"]["source"], "mqtt")
        self.assertEqual(by_id["living_room_window_sensor"]["source"], "mqtt")
        # 两个空间都应出现，仪表盘才能分组展示。
        self.assertEqual({str(item["room"]) for item in devices}, {"living_room", "bedroom"})

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

        esphome_sensor = next(
            item for item in devices if item["device_id"] == "living_room_temperature_sensor"
        )
        mqtt_sensor = next(
            item for item in devices if item["device_id"] == "bedroom_presence_sensor"
        )
        self.assertIn("ESPHome", str(esphome_sensor["ui"]["state_secondary"]))
        self.assertIn("MQTT", str(mqtt_sensor["ui"]["state_secondary"]))


class EsphomeNodeStatusTests(unittest.TestCase):
    def test_nodes_report_resolution_against_projected_entities(self) -> None:
        controller = _controller(_mixed_states())

        report = controller.esphome_nodes()

        self.assertEqual(report["projected"], {"esphome": 5, "mqtt": 5})
        self.assertEqual({node["role"] for node in report["nodes"]}, {"light", "curtain", "ac", "presence"})
        self.assertTrue(all(node["connected"] for node in report["nodes"]))

    def test_missing_node_is_reported_as_unresolved(self) -> None:
        states = _mixed_states()
        del states["cover.spacebutler_living_room_curtain_2"]
        controller = _controller(states)

        report = controller.esphome_nodes()

        curtain = next(node for node in report["nodes"] if node["role"] == "curtain")
        self.assertFalse(curtain["connected"])
        self.assertEqual(report["projected"]["esphome"], 4)


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
