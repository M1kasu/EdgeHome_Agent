from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.service import EdgeHomeAgent


class EdgeHomeAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.agent = EdgeHomeAgent(data_dir=Path(self.temp_dir.name))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_simple_brightness_command_is_executed_and_verified(self) -> None:
        response = self.agent.chat("simple", "把客厅灯调到一半")

        self.assertFalse(response["need_confirmation"])
        self.assertTrue(response["execution_results"][0]["verified"])
        self.assertEqual(50, self.agent.home.get_state("light.living_room_main")["attributes"]["brightness"])

    def test_multiturn_reference_inherits_last_brightness(self) -> None:
        self.agent.chat("dialogue", "把客厅灯调到一半")
        response = self.agent.chat("dialogue", "卧室的也一样")

        self.assertTrue(response["execution_results"][0]["success"])
        self.assertEqual(50, self.agent.home.get_state("light.bedroom_main")["attributes"]["brightness"])

    def test_movie_respects_temperature_lower_bound(self) -> None:
        response = self.agent.chat("movie", "我要在客厅看电影，把环境调舒服一点，空调不要低于25度")

        temperatures = [step["arguments"].get("temperature") for step in response["plan"]["steps"]]
        self.assertIn(25, temperatures)
        self.assertTrue(all(item["success"] for item in response["execution_results"]))
        self.assertEqual(25, self.agent.home.get_state("climate.living_room_ac")["attributes"]["temperature"])

    def test_away_never_turns_off_protected_entities(self) -> None:
        response = self.agent.chat("away", "我要出门一天，把不用的设备关掉，冰箱、路由器和扫地机器人不要动")
        planned_entities = {step["arguments"].get("entity_id") for step in response["plan"]["steps"]}

        self.assertNotIn("switch.refrigerator", planned_entities)
        self.assertNotIn("switch.router", planned_entities)
        self.assertEqual("on", self.agent.home.get_state("switch.refrigerator")["state"])
        self.assertEqual("on", self.agent.home.get_state("switch.router")["state"])

    def test_protected_direct_action_requires_confirmation(self) -> None:
        first = self.agent.chat("protected", "关闭冰箱")

        self.assertTrue(first["need_confirmation"])
        self.assertEqual("on", self.agent.home.get_state("switch.refrigerator")["state"])
        second = self.agent.chat("protected", "确认")
        self.assertFalse(second["need_confirmation"])
        self.assertEqual("off", self.agent.home.get_state("switch.refrigerator")["state"])

    def test_offline_optional_curtain_does_not_abort_movie(self) -> None:
        self.agent.home.set_online("cover.living_room_curtain", False)
        response = self.agent.chat("offline", "我要看电影")

        curtain = next(item for item in response["execution_results"] if item["step_id"] == "step_2")
        self.assertFalse(curtain["success"])
        self.assertEqual("device_offline", curtain["error"])
        self.assertTrue(any(item["success"] for item in response["execution_results"] if item["step_id"] != "step_2"))

    def test_sleep_followup_creates_delayed_task_and_sets_temperature(self) -> None:
        self.agent.chat("sleep", "我要睡觉了")
        response = self.agent.chat("sleep", "半小时后关，卧室调到26度")

        self.assertIsNotNone(response["task_id"])
        self.assertEqual("scheduled", self.agent.task(response["task_id"])["status"])
        self.assertEqual(26, self.agent.home.get_state("climate.bedroom_ac")["attributes"]["temperature"])

    def test_knowledge_returns_local_excerpt_or_explicit_no_result(self) -> None:
        found = self.agent.chat("knowledge", "空调 E1 是什么")
        missing = self.agent.chat("knowledge", "不存在的 ZX99 故障代码是什么")

        self.assertTrue(found["data"]["hits"])
        self.assertIn("不会猜测", missing["reply"])

    def test_energy_analysis_is_deterministic_and_identifies_growth(self) -> None:
        response = self.agent.chat("energy", "最近一周为什么用电量变高了？")

        data = response["data"]
        self.assertGreater(data["total_kwh"], data["previous_total_kwh"])
        self.assertEqual("climate.living_room_ac", data["top_devices"][0]["device"])
        self.assertTrue(data["abnormal_periods"])

    def test_invalid_brightness_is_rejected_before_execution(self) -> None:
        response = self.agent.chat("invalid", "把客厅灯调到150%")

        self.assertIn("已拦截", response["reply"])
        self.assertEqual(70, self.agent.home.get_state("light.living_room_main")["attributes"]["brightness"])


if __name__ == "__main__":
    unittest.main()
