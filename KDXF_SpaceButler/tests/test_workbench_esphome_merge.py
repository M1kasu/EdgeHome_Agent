from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock

from spacebutler.workbench import WorkbenchController, _esphome_record


def _ha_state(value: str, attributes: dict[str, object] | None = None) -> dict[str, object]:
    return {"state": value, "attributes": attributes or {}}


def _named(value: str, friendly: str, extra: dict[str, object] | None = None) -> dict[str, object]:
    attributes = {"friendly_name": friendly, **(extra or {})}
    return {"state": value, "attributes": attributes}


def _esphome_ha_states() -> dict[str, dict[str, object]]:
    return {
        "light.spacebutler_living_room_main": _named("on", "SpaceButler Living Room Main", {"brightness": 204}),
        "cover.spacebutler_living_room_curtain": _named("open", "SpaceButler Living Room Curtain", {"current_position": 100}),
        "climate.spacebutler_living_room_ac": _named(
            "off", "SpaceButler Living Room AC", {"temperature": 26, "current_temperature": 30.2}
        ),
        "binary_sensor.spacebutler_living_room_presence": _named("on", "SpaceButler Living Room Presence"),
        "sensor.spacebutler_living_room_temperature": _named("30.2", "SpaceButler Living Room Temperature"),
    }


def _controller(ha_states: dict[str, dict[str, object]]) -> WorkbenchController:
    controller = object.__new__(WorkbenchController)
    controller._lock = threading.RLock()
    fleet_devices = [
        {
            "device_id": "bedroom_reading_light",
            "entity_id": "light.spacebutler_bedroom_reading_light",
            "type": "light",
            "name": "卧室阅读灯",
            "room": "bedroom",
            "room_name": "卧室",
            "online": True,
            "state": {"power": "OFF", "brightness": 0},
        },
        {
            "device_id": "living_room_tv",
            "entity_id": "switch.spacebutler_living_room_tv",
            "type": "switch",
            "name": "客厅电视",
            "room": "living_room",
            "room_name": "客厅",
            "online": True,
            "state": {"power": "OFF", "power_w": 0.0},
        },
    ]
    controller._simulator = Mock(return_value={"devices": fleet_devices})
    controller._ha = Mock()
    controller._ha.states = Mock(return_value=[{"entity_id": key, **value} for key, value in ha_states.items()])
    controller._ha.state = Mock(
        side_effect=lambda entity_id: ha_states.get(entity_id, _ha_state("unavailable"))
    )
    controller._optional_ha_state = lambda entity_id: ha_states.get(entity_id)
    controller._wait_for = Mock()
    return controller


class EsphomeInventoryMergeTests(unittest.TestCase):
    def test_merged_devices_appear_with_fleet_compatible_ids(self) -> None:
        controller = _controller(_esphome_ha_states())
        payload = controller.devices()
        by_id = {str(item["device_id"]): item for item in payload["devices"]}
        for device_id in (
            "living_room_main_light",
            "living_room_curtain",
            "living_room_ac",
            "living_room_presence_sensor",
            "living_room_temperature_sensor",
        ):
            self.assertIn(device_id, by_id)
        light = by_id["living_room_main_light"]
        self.assertEqual(str(light["source"]), "esphome")
        self.assertEqual(str(light["entity_id"]), "light.spacebutler_living_room_main")
        self.assertEqual(str(light["room"]), "living_room")
        self.assertTrue(bool(light["online"]))
        self.assertEqual(str(light["state"]["power"]), "ON")
        self.assertIn("ui", light)
        # 主动规则等既有绑定使用的 device_id 无需迁移；entity_id 由运行时解析。
        self.assertEqual(str(_esphome_record("living_room_curtain")["type"]), "curtain")

    def test_registry_suffix_entities_still_match_by_friendly_name(self) -> None:
        states = _esphome_ha_states()
        # HA 注册表残留旧条目时，ESPHome 实体可能被挤到 _2 后缀 ID。
        states["light.spacebutler_living_room_main_2"] = states.pop("light.spacebutler_living_room_main")
        controller = _controller(states)
        by_id = {str(item["device_id"]): item for item in controller.devices()["devices"]}
        self.assertEqual(
            str(by_id["living_room_main_light"]["entity_id"]),
            "light.spacebutler_living_room_main_2",
        )
        self.assertEqual(str(by_id["living_room_main_light"]["state"]["power"]), "ON")

    def test_helper_input_with_same_friendly_name_is_ignored(self) -> None:
        states = _esphome_ha_states()
        # 遗留 input_boolean 与 presence 实体同名，但域不同，不能误入清单。
        states["input_boolean.spacebutler_living_room_presence"] = _named("on", "SpaceButler Living Room Presence")
        controller = _controller(states)
        by_id = {str(item["device_id"]): item for item in controller.devices()["devices"]}
        self.assertEqual(
            str(by_id["living_room_presence_sensor"]["entity_id"]),
            "binary_sensor.spacebutler_living_room_presence",
        )

    def test_legacy_simulator_records_are_ignored(self) -> None:
        states = _esphome_ha_states()
        states["light.spacebutler_bedroom_reading_light"] = _ha_state("off")
        controller = _controller(states)
        fleet = controller._simulator.return_value["devices"]
        fleet.append(
            {
                "device_id": "living_room_main_light",
                "entity_id": "light.spacebutler_living_room_main",
                "type": "light",
                "name": "客厅主灯(MQTT)",
                "room": "living_room",
                "room_name": "客厅",
                "online": True,
                "state": {"power": "OFF", "brightness": 0},
            }
        )
        by_id = {str(item["device_id"]): item for item in controller.devices()["devices"]}
        self.assertEqual(str(by_id["living_room_main_light"]["name"]), "客厅主灯")
        self.assertEqual(str(by_id["living_room_main_light"]["source"]), "esphome")

    def test_readonly_sensor_control_is_rejected(self) -> None:
        controller = _controller(_esphome_ha_states())
        with self.assertRaises(ValueError):
            controller.control_device("living_room_presence_sensor", {"occupied": False})
        with self.assertRaises(ValueError):
            controller.control_device("living_room_temperature_sensor", {"temperature": 20})
        controller._ha.call_service.assert_not_called()

    def test_light_control_routes_through_ha_service(self) -> None:
        controller = _controller(_esphome_ha_states())
        controller.control_device("living_room_main_light", {"power": "on", "brightness": 20})
        domain, service, data = controller._ha.call_service.call_args[0]
        self.assertEqual((domain, service), ("light", "turn_on"))
        self.assertEqual(data["brightness_pct"], 20)

    def test_remove_and_fault_are_guarded(self) -> None:
        controller = _controller(_esphome_ha_states())
        with self.assertRaises(ValueError):
            controller.remove_device("living_room_curtain")
        with self.assertRaises(ValueError):
            controller.set_device_fault("living_room_ac", "offline")

    def test_prefixed_ha_entities_populate_inventory_and_controls_use_the_same_id(self) -> None:
        states = {
            "light.living_room_light_living_room_light": _named("on", "Living-Room-Light Living Room Light"),
            "cover.living_room_curtain_living_room_curtain": _named("open", "Living-Room-Curtain Living Room Curtain"),
            "climate.living_room_ac_living_room_ac": _named("cool", "Living-Room-Ac Living Room AC"),
            "sensor.living_room_ac_living_room_temperature": _named("28.1", "Living-Room-Ac Living Room Temperature"),
            "binary_sensor.living_room_presence_living_room_presence": _named("on", "Living-Room-Presence Living Room Presence"),
        }
        controller = _controller(states)
        payload = controller.devices()
        self.assertEqual(len(payload["devices"]), 5)
        self.assertEqual(payload["diagnostics"]["missing_devices"], [])
        controller.control_device("living_room_main_light", {"power": "on", "brightness": 30})
        self.assertEqual(controller._ha.call_service.call_args[0][2]["entity_id"],
                         "light.living_room_light_living_room_light")

    def test_ambiguous_light_is_reported_and_never_controlled(self) -> None:
        states = {
            "light.living_room_light": _named("on", "Living Room Light"),
            "light.living_room_light_2": _named("on", "Living Room Light"),
        }
        controller = _controller(states)
        self.assertEqual(controller.devices()["devices"], [])
        self.assertEqual(controller.device_diagnostics()["missing_devices"][0]["reason"], "ambiguous")
        with self.assertRaises(ValueError):
            controller.control_device("living_room_main_light", {"power": "off"})
        controller._ha.call_service.assert_not_called()

    def test_health_reports_the_five_resolved_entity_roles(self) -> None:
        controller = _controller(_esphome_ha_states())
        controller._agent_runtime = Mock()
        controller._agent_runtime.status.return_value = {"llm_available": True}
        controller._agent_runtime.runtime_health.return_value = {"status": "ready"}
        health = controller.health()
        self.assertEqual(set(health["esphome_devices"].values()), {"ok"})
        self.assertEqual(health["agent"]["status"], "ready")


if __name__ == "__main__":
    unittest.main()
