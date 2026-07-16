from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from spacebutler.conversation import ConversationAgent


class FakePlanner:
    def __init__(self, candidate: dict[str, object]) -> None:
        self.candidate = candidate
        self.calls = 0

    def plan_home_task(
        self,
        text: str,
        devices: list[dict[str, object]],
        context: list[dict[str, object]],
    ) -> dict[str, object]:
        self.calls += 1
        return deepcopy(self.candidate)


class FakeHome:
    def __init__(self) -> None:
        self.devices = [
            _device(
                "bedroom_reading_light",
                "卧室阅读灯",
                "bedroom",
                "卧室",
                "light",
                {"power": "OFF", "brightness": 0},
            ),
            _device(
                "living_room_tv",
                "客厅电视",
                "living_room",
                "客厅",
                "switch",
                {"power": "ON", "power_w": 95},
            ),
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
                {"position": 100, "target_position": 100, "status": "open"},
            ),
            _device(
                "living_room_ac",
                "客厅空调",
                "living_room",
                "客厅",
                "climate",
                {"mode": "cool", "temperature": 24, "current_temperature": 27},
            ),
        ]
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.fail_device_id: str | None = None

    def inventory(self) -> dict[str, object]:
        return {"devices": deepcopy(self.devices), "rooms": []}

    def execute(self, device_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((device_id, deepcopy(payload)))
        if device_id == self.fail_device_id:
            raise RuntimeError("device rejected command")
        device = next(item for item in self.devices if item["device_id"] == device_id)
        state = device["state"]
        if "power" in payload:
            state["power"] = str(payload["power"]).upper()
            if "brightness" in payload:
                state["brightness"] = round(int(payload["brightness"]) * 255 / 100)
        if "position" in payload:
            state["position"] = int(payload["position"])
            state["target_position"] = int(payload["position"])
        if "mode" in payload:
            state["mode"] = payload["mode"]
        if "temperature" in payload:
            state["temperature"] = payload["temperature"]
        return self.inventory()

    @staticmethod
    def proactive() -> dict[str, object]:
        return {
            "response": {"status": "needs_confirmation", "message": "发现无人开窗空调运行的节能机会"},
            "status": {"proactive": {"trigger_ready": True, "data_source": "devices"}},
        }


class ConversationAgentTest(unittest.TestCase):
    def test_model_multi_device_plan_waits_for_confirmation_then_executes(self) -> None:
        home = FakeHome()
        planner = FakePlanner(
            {
                "intent": "device_control",
                "summary": "准备卧室并降低客厅待机能耗",
                "reasoning": "识别到两个设备动作",
                "steps": [
                    {
                        "device_id": "bedroom_reading_light",
                        "action": "set_brightness",
                        "value": 55,
                    },
                    {"device_id": "living_room_tv", "action": "turn_off", "value": None},
                ],
            }
        )
        agent = ConversationAgent(home.inventory, home.execute, home.proactive, planner)

        planned = agent.submit("把卧室阅读灯打开到55%，然后关闭客厅电视")

        self.assertEqual(planned["active_plan"]["status"], "awaiting_confirmation")
        self.assertEqual(planned["active_plan"]["route"], "edge_llm")
        self.assertEqual(home.calls, [])

        completed = agent.confirm()

        self.assertEqual(completed["active_plan"]["status"], "completed")
        self.assertEqual([step["status"] for step in completed["active_plan"]["steps"]], ["success", "success"])
        self.assertEqual([call[0] for call in home.calls], ["bedroom_reading_light", "living_room_tv"])

    def test_model_cannot_execute_a_fictional_device(self) -> None:
        home = FakeHome()
        planner = FakePlanner(
            {
                "intent": "device_control",
                "summary": "控制虚构设备",
                "steps": [{"device_id": "ghost_light", "action": "turn_on", "value": None}],
            }
        )
        agent = ConversationAgent(home.inventory, home.execute, home.proactive, planner)

        result = agent.submit("打开幽灵灯")

        self.assertEqual(result["active_plan"]["status"], "clarification")
        self.assertEqual(result["active_plan"]["steps"], [])
        self.assertEqual(home.calls, [])

    def test_model_general_chat_miss_is_repaired_for_an_explicit_device_command(self) -> None:
        home = FakeHome()
        planner = FakePlanner(
            {
                "intent": "general_chat",
                "summary": "",
                "reply": "",
                "steps": [],
            }
        )
        agent = ConversationAgent(home.inventory, home.execute, home.proactive, planner)

        result = agent.submit("关闭客厅电视")

        self.assertEqual(result["active_plan"]["intent"], "device_control")
        self.assertEqual(result["active_plan"]["route"], "deterministic_repair")
        self.assertEqual(result["active_plan"]["status"], "completed")
        self.assertEqual(home.calls[0][0], "living_room_tv")

    def test_model_plan_keeps_intent_but_grounds_an_ambiguous_climate_reference(self) -> None:
        home = FakeHome()
        home.devices.append(
            _device(
                "bedroom_ac",
                "卧室空调",
                "bedroom",
                "卧室",
                "climate",
                {"mode": "off", "temperature": 24, "current_temperature": 26},
            )
        )
        planner = FakePlanner(
            {
                "intent": "device_control",
                "summary": "调整客厅窗帘和空调",
                "steps": [
                    {"device_id": "living_room_curtain", "action": "set_position", "value": 30},
                    {
                        "device_id": "bedroom_ac",
                        "action": "set_temperature",
                        "value": 25,
                        "mode": "cool",
                    },
                ],
            }
        )
        agent = ConversationAgent(home.inventory, home.execute, home.proactive, planner)

        result = agent.submit("把客厅窗帘调到30%，空调设为25度制冷")

        self.assertEqual(result["active_plan"]["route"], "edge_llm_grounded")
        self.assertEqual(result["active_plan"]["steps"][1]["device_id"], "living_room_ac")

    def test_state_query_is_read_only_and_returns_live_state(self) -> None:
        home = FakeHome()
        planner = FakePlanner(
            {
                "intent": "query_state",
                "summary": "查询客厅空调",
                "steps": [{"device_id": "living_room_ac", "action": "read_state", "value": None}],
            }
        )
        agent = ConversationAgent(home.inventory, home.execute, home.proactive, planner)

        result = agent.submit("客厅空调现在是什么状态？")

        self.assertEqual(result["active_plan"]["status"], "completed")
        self.assertTrue(result["active_plan"]["steps"][0]["verified"])
        self.assertEqual(home.calls, [])
        self.assertIn("设定 24", result["messages"][-1]["content"])

    def test_failure_stops_later_steps_without_false_success(self) -> None:
        home = FakeHome()
        home.fail_device_id = "living_room_tv"
        planner = FakePlanner(
            {
                "intent": "device_control",
                "summary": "关闭电视和主灯",
                "steps": [
                    {"device_id": "living_room_tv", "action": "turn_off", "value": None},
                    {"device_id": "living_room_main_light", "action": "turn_off", "value": None},
                ],
            }
        )
        agent = ConversationAgent(home.inventory, home.execute, home.proactive, planner)
        agent.submit("关闭客厅电视，然后关闭客厅主灯")

        result = agent.confirm()

        self.assertEqual(result["active_plan"]["status"], "failed")
        self.assertEqual([step["status"] for step in result["active_plan"]["steps"]], ["failed", "skipped"])
        self.assertEqual([call[0] for call in home.calls], ["living_room_tv"])
        self.assertIn("未全部完成", result["messages"][-1]["content"])

    def test_pending_plan_and_messages_survive_restart(self) -> None:
        home = FakeHome()
        planner = FakePlanner(
            {
                "intent": "device_control",
                "summary": "关闭两个设备",
                "steps": [
                    {"device_id": "living_room_tv", "action": "turn_off", "value": None},
                    {"device_id": "living_room_main_light", "action": "turn_off", "value": None},
                ],
            }
        )
        with TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "household.db"
            first = ConversationAgent(home.inventory, home.execute, home.proactive, planner, database)
            first.submit("关闭客厅电视和客厅主灯")

            restored = ConversationAgent(home.inventory, home.execute, home.proactive, planner, database)
            history = restored.history()

            self.assertEqual(history["active_plan"]["status"], "awaiting_confirmation")
            self.assertGreaterEqual(len(history["messages"]), 3)


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
