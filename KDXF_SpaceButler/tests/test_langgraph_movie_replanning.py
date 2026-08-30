from __future__ import annotations

from copy import deepcopy
import unittest

from spacebutler.agent.runtime import AgentRuntime


class MovieSemanticModel:
    """Model double: semantic outputs are injected, not inferred by test code."""

    def __init__(self) -> None:
        self.goal_inputs: list[str] = []

    def available(self, force_refresh: bool = False) -> bool:
        return True

    def chat_json(self, system: str, user: str, schema=None, max_attempts: int = 2):
        if "维度审计器" in system:
            return {"dimensions": ["glare", "lighting", "thermal"]}
        if "目标理解器" in system:
            self.goal_inputs.append(user)
            return {
                "is_home_goal": True,
                "location": "living_room",
                "intent_summary": "营造凉爽、柔和且无自然光干扰的客厅观影环境",
                "activity": "watch_movie",
                "constraints": [
                    {
                        "dimension": "glare",
                        "direction": "reduce",
                        "strength": "strong",
                        "target_range": [0, 0],
                        "reason": "减少屏幕反光",
                    },
                    {
                        "dimension": "lighting",
                        "direction": "dimmer",
                        "strength": "moderate",
                        "target_range": [20, 20],
                        "reason": "灯别太亮",
                    },
                    {
                        "dimension": "thermal",
                        "direction": "cooler",
                        "strength": "moderate",
                        "target_range": [26, 26],
                        "reason": "用户感觉有点热",
                    },
                ],
            }
        if "家庭智能体的规划器" in system:
            actions = [
                {"device_id": "living_room_curtain", "capability": "set_position", "value": 0},
                {"device_id": "living_room_main_light", "capability": "set_brightness", "value": 20},
                {
                    "device_id": "living_room_ac",
                    "capability": "set_temperature",
                    "value": 26,
                    "mode": "cool",
                },
            ]
            return {
                "candidates": [
                    {"summary": "同步消除反光、降低灯光并降温", "actions": actions},
                    {"summary": "一次完成三个尚未满足的目标", "actions": deepcopy(actions)},
                ]
            }
        raise AssertionError(f"unexpected model prompt: {system[:80]}")


class RealStateHome:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.devices = [
            _device(
                "living_room_main_light",
                "light.living_room_light",
                "light",
                "Living Room Light",
                {"power": "ON", "brightness": 204},
                {"state": "on", "attributes": {"brightness": 204}},
            ),
            _device(
                "living_room_curtain",
                "cover.living_room_curtain",
                "curtain",
                "Living Room Curtain",
                {"position": 100},
                {"state": "open", "attributes": {"current_position": 100}},
            ),
            _device(
                "living_room_ac",
                "climate.living_room_ac",
                "climate",
                "Living Room AC",
                {"mode": "off", "temperature": 26.0, "current_temperature": 30.2},
                {
                    "state": "off",
                    "attributes": {
                        "temperature": 26.0,
                        "current_temperature": 30.2,
                        "hvac_modes": ["off", "cool"],
                        "min_temp": 16.0,
                        "max_temp": 30.0,
                        "target_temp_step": 0.5,
                    },
                },
            ),
            _device(
                "living_room_temperature_sensor",
                "sensor.living_room_temperature",
                "temperature",
                "Living Room Temperature",
                {"temperature": 30.2},
                {"state": "30.2", "attributes": {}},
            ),
            _device(
                "living_room_presence_sensor",
                "binary_sensor.living_room_presence",
                "presence",
                "Living Room Presence",
                {"occupied": True},
                {"state": "on", "attributes": {}},
            ),
        ]

    def inventory(self) -> dict[str, object]:
        return {"devices": deepcopy(self.devices)}

    def control(self, device_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((device_id, deepcopy(payload)))
        device = next(item for item in self.devices if item["device_id"] == device_id)
        if device_id == "living_room_curtain":
            position = int(payload["position"])
            device["state"] = {"position": position}
            device["ha_state"] = {
                "state": "closed" if position == 0 else "open",
                "attributes": {"current_position": position},
            }
        elif device_id == "living_room_main_light":
            brightness = int(payload["brightness"])
            raw = round(brightness * 255 / 100)
            device["state"] = {"power": "ON", "brightness": raw}
            device["ha_state"] = {"state": "on", "attributes": {"brightness": raw}}
        elif device_id == "living_room_ac":
            target = float(payload["temperature"])
            device["state"] = {"mode": "cool", "temperature": target, "current_temperature": 30.2}
            device["ha_state"] = {
                "state": "cool",
                "attributes": {"temperature": target, "current_temperature": 30.2},
            }
        return self.inventory()


def _device(device_id, entity_id, device_type, name, state, ha_state):
    return {
        "device_id": device_id,
        "entity_id": entity_id,
        "type": device_type,
        "name": name,
        "room": "living_room",
        "room_name": "客厅",
        "online": True,
        "state": state,
        "ha_state": ha_state,
    }


class LangGraphMovieReplanningTests(unittest.TestCase):
    def test_second_identical_goal_observes_state_and_executes_zero_actions(self) -> None:
        home = RealStateHome()
        model = MovieSemanticModel()
        runtime = AgentRuntime(
            home.inventory,
            home.control,
            model,
            poll_interval_seconds=3600,
        )

        first = runtime.submit_text("我准备在客厅看会电影，有点热，灯别太亮。")

        self.assertEqual([device_id for device_id, _ in home.calls], [
            "living_room_curtain",
            "living_room_main_light",
            "living_room_ac",
        ])
        self.assertEqual(first["orchestrator"], "langgraph")
        self.assertEqual(first["graph_trace"], [
            "observe", "understand", "activate", "plan", "execute", "verify", "respond"
        ])
        self.assertTrue(all(action["verified"] for action in first["active_plan"]["selected"]["actions"]))

        calls_after_first = len(home.calls)
        second = runtime.submit_text("现在适合看电影了吗？")

        self.assertEqual(len(home.calls), calls_after_first)
        self.assertIn('"active_goal"', model.goal_inputs[1])
        self.assertIn('"activity": "watch_movie"', model.goal_inputs[1])
        self.assertTrue(second["active_plan"]["no_action"])
        self.assertEqual(second["graph_trace"], ["observe", "understand", "activate", "plan", "respond"])
        reply = second["messages"][-1]["content"]
        self.assertIn("窗帘已关闭", reply)
        self.assertIn("灯光已为 20%", reply)
        self.assertIn("空调正在向 26℃ 制冷", reply)
        self.assertIn("无需重复调整", reply)


if __name__ == "__main__":
    unittest.main()
