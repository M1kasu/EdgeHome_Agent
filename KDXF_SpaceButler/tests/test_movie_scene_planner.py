from __future__ import annotations

from copy import deepcopy
import unittest

from spacebutler.conversation import ConversationAgent


class FakeHome:
    """In-memory device fleet mirroring the PDF demo initial state."""

    def __init__(self) -> None:
        self.devices = [
            _device(
                "living_room_main_light",
                "客厅主灯",
                "living_room",
                "客厅",
                "light",
                {"power": "ON", "brightness": 204},
            ),
            _device(
                "living_room_curtain",
                "客厅窗帘",
                "living_room",
                "客厅",
                "curtain",
                {"position": 100},
            ),
            _device(
                "living_room_ac",
                "客厅空调",
                "living_room",
                "客厅",
                "climate",
                {"mode": "off", "temperature": 26, "current_temperature": 30.2},
            ),
        ]
        self.calls: list[tuple[str, dict[str, object]]] = []

    def inventory(self) -> dict[str, object]:
        return {"devices": deepcopy(self.devices), "rooms": []}

    def execute(self, device_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((device_id, deepcopy(payload)))
        device = next(item for item in self.devices if item["device_id"] == device_id)
        state = device["state"]
        if "power" in payload and device["type"] == "light":
            state["power"] = str(payload["power"]).upper()
            if "brightness" in payload:
                state["brightness"] = round(int(payload["brightness"]) * 255 / 100)
        if "position" in payload:
            state["position"] = int(payload["position"])
        if "mode" in payload:
            state["mode"] = payload["mode"]
        if "temperature" in payload:
            state["temperature"] = payload["temperature"]
        return self.inventory()

    @staticmethod
    def proactive() -> dict[str, object]:
        return {"response": {"status": "ok", "message": "无节能机会"}, "status": {}}


class MovieScenePlannerTest(unittest.TestCase):
    TEXT = "我准备在客厅看会电影，有点热，灯别太亮。"

    def test_initial_state_executes_three_steps_without_confirmation(self) -> None:
        home = FakeHome()
        agent = ConversationAgent(home.inventory, home.execute, home.proactive, None, None)
        history = agent.submit(self.TEXT)
        plan = history["active_plan"]
        self.assertEqual(str(plan["intent"]), "device_control")
        self.assertEqual(str(plan["route"]), "adaptive_scene")
        self.assertFalse(bool(plan["requires_confirmation"]))
        self.assertEqual(len(plan["steps"]), 3)
        self.assertEqual([str(step["action"]) for step in plan["steps"]], ["set_position", "set_brightness", "set_temperature"])
        self.assertTrue(all(step["status"] == "success" for step in plan["steps"]))
        self.assertTrue(all(step["verified"] for step in plan["steps"]))
        # PDF 目标值：窗帘全关、灯 20%、空调制冷 26°C。
        self.assertEqual(home.devices[1]["state"]["position"], 0)
        self.assertEqual(home.devices[0]["state"]["brightness"], round(20 * 255 / 100))
        self.assertEqual(home.devices[2]["state"]["mode"], "cool")
        self.assertEqual(home.devices[2]["state"]["temperature"], 26)

    def test_repeated_request_when_satisfied_skips_execution(self) -> None:
        home = FakeHome()
        agent = ConversationAgent(home.inventory, home.execute, home.proactive, None, None)
        agent.submit(self.TEXT)
        calls_before = len(home.calls)
        history = agent.submit("现在适合看电影了吗？")
        plan = history["active_plan"]
        self.assertEqual(len(home.calls), calls_before, "已满足目标时不得再发设备控制命令")
        self.assertEqual(str(plan["intent"]), "query_state")
        self.assertIn("无需重复调整", str(plan["reply"]))
        self.assertIn("适合观影", str(plan["reply"]))

    def test_partial_state_only_plans_missing_steps(self) -> None:
        home = FakeHome()
        # 灯已经是 20%（亮度 51 ≈ 20%），窗帘与空调仍需调整。
        home.devices[0]["state"]["brightness"] = 51
        agent = ConversationAgent(home.inventory, home.execute, home.proactive, None, None)
        history = agent.submit(self.TEXT)
        plan = history["active_plan"]
        self.assertEqual(len(plan["steps"]), 2)
        self.assertEqual(
            [str(step["device_id"]) for step in plan["steps"]],
            ["living_room_curtain", "living_room_ac"],
        )
        self.assertIn("主灯", str(plan["reasoning"]))
        self.assertTrue(all(step["status"] == "success" for step in plan["steps"]))

    def test_query_only_still_reports_when_devices_missing(self) -> None:
        home = FakeHome()
        home.devices = []
        agent = ConversationAgent(home.inventory, home.execute, home.proactive, None, None)
        history = agent.submit(self.TEXT)
        plan = history["active_plan"]
        self.assertEqual(str(plan["intent"]), "clarification")
        self.assertEqual(plan["steps"], [])


def _device(
    device_id: str,
    name: str,
    room: str,
    room_name: str,
    device_type: str,
    state: dict[str, object],
) -> dict[str, object]:
    return {
        "device_id": device_id,
        "name": name,
        "room": room,
        "room_name": room_name,
        "type": device_type,
        "state": state,
        "online": True,
        "discovered": True,
        "fault_mode": "none",
        "entity_id": f"{device_type}.{device_id}",
        "ha_state": {"state": "on", "attributes": {}},
        "feedback": {"state": "ok", "attributes": {}},
    }


if __name__ == "__main__":
    unittest.main()
