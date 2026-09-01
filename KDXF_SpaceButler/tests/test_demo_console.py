"""演示控制台：复位走真实设备链路，自检不能把坏栈报成可以开拍。"""

from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock

from spacebutler.brands import BrandBindingStore
from spacebutler.workbench import WorkbenchController


def _controller() -> WorkbenchController:
    controller = object.__new__(WorkbenchController)
    controller._lock = threading.RLock()
    controller._ha = Mock()
    # 出厂品牌映射，不落盘；投影要读它填充设备卡的品牌徽标。
    controller._brand_bindings = BrandBindingStore()
    controller.control_device = Mock(return_value={})
    controller.clear_chat = Mock(return_value={})
    controller.clear_preferences = Mock(return_value={})
    controller.devices = Mock(return_value={"devices": [], "rooms": []})
    return controller


class DemoResetTests(unittest.TestCase):
    def test_reset_drives_every_device_through_the_real_control_path(self) -> None:
        controller = _controller()

        result = controller.demo_reset()

        self.assertTrue(result["ok"])
        controlled = {call.args[0] for call in controller.control_device.call_args_list}
        # 电视和阅读灯是迁移进 ESPHome 的设备，复位必须覆盖它们。
        self.assertEqual(controlled, {
            "living_room_main_light",
            "living_room_curtain",
            "living_room_ac",
            "living_room_tv",
            "bedroom_reading_light",
        })

    def test_reset_restores_the_heat_and_sensor_preconditions(self) -> None:
        controller = _controller()

        controller.demo_reset()

        services = {
            (call.args[0], call.args[1]): call.args[2]
            for call in controller._ha.call_service.call_args_list
        }
        self.assertEqual(
            services[("esphome", "living_room_ac_demo_heat_injection")],
            {"target_temperature": 30.2},
        )
        # 起身传感器必须复位为 off，否则夜间场景没有 off -> on 上升沿可演示。
        self.assertEqual(
            services[("esphome", "bedroom_demo_set_presence")], {"occupied": False})
        self.assertEqual(
            services[("esphome", "bedroom_demo_set_illuminance")], {"lux": 8.0})
        self.assertEqual(
            services[("esphome", "living_room_aux_demo_set_window")], {"opened": True})

    def test_reset_clears_conversation_and_learned_preferences(self) -> None:
        controller = _controller()

        controller.demo_reset()

        controller.clear_chat.assert_called_once()
        controller.clear_preferences.assert_called_once()

    def test_a_failed_step_is_reported_instead_of_claiming_success(self) -> None:
        controller = _controller()
        controller.control_device = Mock(side_effect=RuntimeError("窗帘无响应"))

        result = controller.demo_reset()

        self.assertFalse(result["ok"])
        failed = [step for step in result["reset"] if not step["ok"]]
        self.assertTrue(failed)
        self.assertIn("窗帘无响应", str(failed[0]["error"]))


class DemoReadinessTests(unittest.TestCase):
    def _ready_controller(self) -> WorkbenchController:
        controller = _controller()
        controller.health = Mock(return_value={
            "workbench": "ok", "home_assistant": "ok", "llm": "ok",
            "agent": {"status": "ready", "mode": "llm", "thread_alive": True,
                      "fatal_error": None, "last_tick_age_seconds": 0.8},
        })
        controller.devices = Mock(return_value={
            "devices": [{"device_id": "a", "online": True}, {"device_id": "b", "online": True}],
            "diagnostics": {"missing_devices": []},
        })
        # 默认：上下文充足，个别用例再覆盖成不足。
        controller._language_router = Mock()
        controller._language_router.client.context_window = Mock(return_value=16384)
        return controller

    def test_a_healthy_stack_reports_ready(self) -> None:
        readiness = self._ready_controller().demo_readiness()

        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["device_online"], 2)
        self.assertTrue(readiness["brands"])

    def test_a_slot_context_too_small_for_planning_blocks_recording(self) -> None:
        """健康检查通过但槽位上下文装不下规划 prompt 时，模型会静默退回确定性规划。

        这正是自检必须拦住的情况：录出来的画面里"AI 没参与"。
        """
        controller = self._ready_controller()
        controller._language_router = Mock()
        controller._language_router.client.context_window = Mock(return_value=512)

        readiness = controller.demo_readiness()

        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["planner_context_window"], 512)
        context_check = next(
            item for item in readiness["checks"] if item["name"] == "模型上下文容量")
        self.assertFalse(context_check["ok"])

    def test_a_sufficient_slot_context_passes(self) -> None:
        controller = self._ready_controller()
        controller._language_router = Mock()
        controller._language_router.client.context_window = Mock(return_value=16384)

        self.assertTrue(controller.demo_readiness()["ready"])

    def test_an_unreported_context_does_not_block_recording(self) -> None:
        """非 llama.cpp 后端不上报 n_ctx 时不能因此判定未就绪。"""
        controller = self._ready_controller()
        controller._language_router = Mock()
        controller._language_router.client.context_window = Mock(return_value=None)

        readiness = controller.demo_readiness()

        self.assertTrue(readiness["ready"])
        self.assertIsNone(readiness["planner_context_window"])

    def test_a_fallback_agent_is_not_reported_as_ready(self) -> None:
        controller = self._ready_controller()
        # 模型不可用时 Agent 会安全拒绝执行，这种状态不能开拍。
        controller.health = Mock(return_value={
            "workbench": "ok", "home_assistant": "ok", "llm": "error",
            "agent": {"status": "ready", "mode": "fallback", "thread_alive": True},
        })

        readiness = controller.demo_readiness()

        self.assertFalse(readiness["ready"])

    def test_an_agent_busy_planning_is_still_ready(self) -> None:
        """一次本地规划要 75–145 秒，期间 tick 滞后、runtime_health 转 degraded。

        录完一个场景马上点自检正好撞上这个窗口；把"在忙"报成"不能开拍"会误导用户。
        """
        controller = self._ready_controller()
        controller.health = Mock(return_value={
            "workbench": "ok", "home_assistant": "ok", "llm": "ok",
            "agent": {"status": "degraded", "mode": "llm", "thread_alive": True,
                      "fatal_error": None, "last_tick_age_seconds": 96.0},
        })

        readiness = controller.demo_readiness()

        self.assertTrue(readiness["ready"])
        agent_check = next(c for c in readiness["checks"] if c["name"] == "Agent 运行时")
        self.assertIn("规划中", str(agent_check["detail"]))

    def test_a_dead_agent_thread_blocks_recording(self) -> None:
        controller = self._ready_controller()
        controller.health = Mock(return_value={
            "workbench": "ok", "home_assistant": "ok", "llm": "ok",
            "agent": {"status": "degraded", "mode": "llm", "thread_alive": False},
        })

        self.assertFalse(controller.demo_readiness()["ready"])

    def test_a_fatal_agent_error_blocks_recording(self) -> None:
        controller = self._ready_controller()
        controller.health = Mock(return_value={
            "workbench": "ok", "home_assistant": "ok", "llm": "ok",
            "agent": {"status": "degraded", "mode": "llm", "thread_alive": True,
                      "fatal_error": "graph crashed"},
        })

        self.assertFalse(controller.demo_readiness()["ready"])

    def test_a_tick_stalled_far_beyond_planning_time_blocks_recording(self) -> None:
        controller = self._ready_controller()
        controller.health = Mock(return_value={
            "workbench": "ok", "home_assistant": "ok", "llm": "ok",
            "agent": {"status": "degraded", "mode": "llm", "thread_alive": True,
                      "fatal_error": None, "last_tick_age_seconds": 900.0},
        })

        readiness = controller.demo_readiness()

        self.assertFalse(readiness["ready"])
        agent_check = next(c for c in readiness["checks"] if c["name"] == "Agent 运行时")
        self.assertIn("卡死", str(agent_check["detail"]))

    def test_an_offline_device_blocks_recording(self) -> None:
        controller = self._ready_controller()
        controller.devices = Mock(return_value={
            "devices": [{"device_id": "a", "online": True}, {"device_id": "b", "online": False}],
            "diagnostics": {"missing_devices": []},
        })

        readiness = controller.demo_readiness()

        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["device_online"], 1)

    def test_a_missing_entity_binding_blocks_recording(self) -> None:
        controller = self._ready_controller()
        controller.devices = Mock(return_value={
            "devices": [{"device_id": "a", "online": True}],
            "diagnostics": {"missing_devices": [{"device_id": "b", "reason": "ambiguous"}]},
        })

        self.assertFalse(controller.demo_readiness()["ready"])


class DemoSensorInjectionTests(unittest.TestCase):
    def test_illuminance_outside_the_sensor_range_is_rejected(self) -> None:
        controller = _controller()

        with self.assertRaises(ValueError):
            controller.demo_illuminance(-1)
        with self.assertRaises(ValueError):
            controller.demo_illuminance(5000)
        controller._ha.call_service.assert_not_called()

    def test_presence_edge_is_sent_to_the_bedroom_node(self) -> None:
        controller = _controller()

        controller.demo_presence(True)

        controller._ha.call_service.assert_called_once_with(
            "esphome", "bedroom_demo_set_presence", {"occupied": True})


if __name__ == "__main__":
    unittest.main()
