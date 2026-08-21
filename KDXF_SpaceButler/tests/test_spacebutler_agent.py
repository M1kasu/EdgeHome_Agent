from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from spacebutler import (
    DeviceState,
    EdgeLanguageRouter,
    EnvironmentState,
    ExecutionStatus,
    HouseholdMember,
    HouseholdMemory,
    InMemoryHomeRuntime,
    MemberRole,
    PlanAction,
    PlanPriority,
    RoomState,
    ServicePlan,
    SpaceButlerSession,
    SpaceButlerAgent,
    SpatialSnapshot,
    execute_and_verify,
)


class SpaceButlerAgentTest(unittest.TestCase):
    def test_return_home_uses_explicit_temperature_preference(self) -> None:
        agent = SpaceButlerAgent()
        agent.memory.learn_preference("member_01", "return_home", "target_temperature", 25)
        snapshot = SpatialSnapshot(
            scene="return_home",
            time_of_day="evening",
            members=(HouseholdMember("member_01", "妈妈", MemberRole.ADULT, "living_room", "returning_home"),),
            environment=EnvironmentState(29, 34, 60, 80, 12),
            devices=(
                DeviceState("climate.living_room_ac", "climate", "living_room", "cool"),
                DeviceState("light.living_room_main", "light", "living_room", "off"),
            ),
        )

        plans = agent.observe_and_plan(snapshot)

        plan = self._plan(plans, "return_home_comfort")
        self.assertTrue(plan.proactive)
        self.assertEqual(plan.actions[0].value, 25.0)
        self.assertEqual(plan.actions[1].capability, "set_brightness")

    def test_night_elder_safety_plan_has_safety_priority(self) -> None:
        agent = SpaceButlerAgent()
        snapshot = SpatialSnapshot(
            scene="daily",
            time_of_day="night",
            members=(HouseholdMember("grandpa", "爷爷", MemberRole.ELDER, "bedroom", "night_walk"),),
            environment=EnvironmentState(24, 20, 55, 20, 9),
            devices=(DeviceState("light.bedroom_path", "light", "bedroom", "off"),),
        )

        plan = self._plan(agent.observe_and_plan(snapshot), "night_elder_safety")

        self.assertEqual(plan.priority.value, "safety")
        self.assertEqual(plan.actions[0].value, 18)
        self.assertFalse(plan.requires_confirmation)

    def test_night_safety_orders_path_and_preserves_manual_lights(self) -> None:
        agent = SpaceButlerAgent()
        snapshot = SpatialSnapshot(
            scene="night_safety",
            time_of_day="night",
            members=(HouseholdMember("grandpa", "爷爷", MemberRole.ELDER, "bedroom", "night_walk"),),
            environment=EnvironmentState(23, 18, 52, 8, 7),
            devices=(
                DeviceState(
                    "light.hallway_path",
                    "light",
                    "hallway",
                    "on",
                    {
                        "brightness_pct": 55,
                        "night_path": ["grandpa"],
                        "night_path_order": 2,
                        "manual_control": True,
                    },
                ),
                DeviceState(
                    "light.bathroom_path",
                    "light",
                    "bathroom",
                    "off",
                    {"night_path": ["grandpa"], "night_path_order": 3},
                ),
                DeviceState(
                    "light.bedroom_path",
                    "light",
                    "bedroom",
                    "off",
                    {"night_path_order": 1},
                ),
            ),
        )

        plan = self._plan(agent.observe_and_plan(snapshot), "night_elder_safety")
        report = execute_and_verify(InMemoryHomeRuntime(snapshot.devices), plan)

        self.assertEqual(
            [action.entity_id for action in plan.actions],
            ["light.bedroom_path", "light.bathroom_path"],
        )
        self.assertIn("1 台", plan.explanation)
        self.assertTrue(report.verified)

    def test_night_safety_does_not_trigger_when_ambient_light_is_sufficient(self) -> None:
        agent = SpaceButlerAgent()
        snapshot = SpatialSnapshot(
            scene="night_safety",
            time_of_day="night",
            members=(HouseholdMember("grandpa", "爷爷", MemberRole.ELDER, "bedroom", "night_walk"),),
            environment=EnvironmentState(23, 18, 52, 90, 7),
            devices=(DeviceState("light.bedroom_path", "light", "bedroom", "off"),),
        )

        plans = agent.observe_and_plan(snapshot)

        self.assertFalse(any(plan.plan_id == "night_elder_safety" for plan in plans))

    def test_night_safety_uses_explicit_member_brightness_preference(self) -> None:
        agent = SpaceButlerAgent()
        agent.memory.learn_preference("grandpa", "night_walk", "path_brightness", 12)
        snapshot = SpatialSnapshot(
            scene="night_safety",
            time_of_day="night",
            members=(HouseholdMember("grandpa", "爷爷", MemberRole.ELDER, "bedroom", "night_walk"),),
            environment=EnvironmentState(23, 18, 52, 5, 7),
            devices=(DeviceState("light.bedroom_path", "light", "bedroom", "off"),),
        )

        plan = self._plan(agent.observe_and_plan(snapshot), "night_elder_safety")

        self.assertEqual(plan.actions[0].value, 12)

    def test_multi_member_conflict_generates_confirmable_compromise(self) -> None:
        agent = SpaceButlerAgent()
        agent.memory.learn_preference("dad", "movie", "target_temperature", 24)
        agent.memory.learn_preference("child", "movie", "target_temperature", 28)
        snapshot = SpatialSnapshot(
            scene="movie",
            time_of_day="evening",
            members=(
                HouseholdMember("dad", "爸爸", MemberRole.ADULT, "living_room", "watching_movie"),
                HouseholdMember("child", "孩子", MemberRole.CHILD, "living_room", "watching_movie"),
            ),
            environment=EnvironmentState(27, 32, 58, 90, 16),
            devices=(DeviceState("climate.living_room_ac", "climate", "living_room", "cool"),),
        )

        plan = self._plan(agent.observe_and_plan(snapshot), "multi_member_temperature_balance")

        self.assertTrue(plan.requires_confirmation)
        self.assertEqual(plan.actions[0].value, 26.0)

    def test_empty_living_room_open_window_turns_off_running_ac(self) -> None:
        agent = SpaceButlerAgent()
        devices = (
            DeviceState("climate.living_room_ac", "climate", "living_room", "cool", {"temperature": 24, "power_w": 1100}),
            DeviceState("window.living_room_window", "window", "living_room", "open"),
        )
        snapshot = SpatialSnapshot(
            scene="daily",
            time_of_day="afternoon",
            members=(HouseholdMember("member_01", "妈妈", MemberRole.ADULT, "bedroom", "resting"),),
            environment=EnvironmentState(27, 34, 62, 500, 18),
            devices=devices,
            rooms=(RoomState("living_room", occupied=False, unoccupied_minutes=23, window_state="open", power_w=1100),),
        )

        plan = self._plan(agent.observe_and_plan(snapshot), "empty_room_open_window_energy_guard")
        report = execute_and_verify(InMemoryHomeRuntime(devices), plan)

        self.assertTrue(plan.requires_confirmation)
        self.assertEqual(plan.actions[0].entity_id, "climate.living_room_ac")
        self.assertTrue(report.verified)

    def test_open_window_energy_guard_does_not_trigger_when_room_occupied(self) -> None:
        agent = SpaceButlerAgent()
        snapshot = SpatialSnapshot(
            scene="daily",
            time_of_day="afternoon",
            members=(HouseholdMember("member_01", "妈妈", MemberRole.ADULT, "living_room", "reading"),),
            environment=EnvironmentState(27, 34, 62, 500, 18),
            devices=(
                DeviceState("climate.living_room_ac", "climate", "living_room", "cool", {"temperature": 24, "power_w": 1100}),
                DeviceState("window.living_room_window", "window", "living_room", "open"),
            ),
            rooms=(RoomState("living_room", occupied=True, unoccupied_minutes=0, window_state="open", power_w=1100),),
        )

        plans = agent.observe_and_plan(snapshot)

        self.assertFalse(any(plan.plan_id == "empty_room_open_window_energy_guard" for plan in plans))

    def test_energy_feedback_changes_next_trigger_to_auto_execute(self) -> None:
        agent = SpaceButlerAgent()
        devices = (
            DeviceState("climate.living_room_ac", "climate", "living_room", "cool", {"temperature": 24, "power_w": 1000}),
            DeviceState("window.living_room_window", "window", "living_room", "open"),
        )
        session = SpaceButlerSession(agent, InMemoryHomeRuntime(devices))
        snapshot = SpatialSnapshot(
            scene="daily",
            time_of_day="afternoon",
            members=(HouseholdMember("member_01", "妈妈", MemberRole.ADULT, "bedroom", "resting"),),
            environment=EnvironmentState(27, 34, 62, 500, 18),
            devices=devices,
            rooms=(RoomState("living_room", occupied=False, unoccupied_minutes=25, window_state="open", power_w=1000),),
        )

        first = session.observe(snapshot)
        learned = session.user_message("household", "以后这种情况直接执行")
        second = SpaceButlerSession(agent, InMemoryHomeRuntime(devices)).observe(snapshot)

        self.assertEqual(first.status, "needs_confirmation")
        self.assertEqual(learned.learned, "learned_auto_execute")
        self.assertEqual(second.status, "executed")
        self.assertTrue(second.report.verified if second.report else False)

    def test_energy_guard_respects_learned_30_minute_threshold(self) -> None:
        agent = SpaceButlerAgent()
        agent.memory.apply_energy_feedback("household", "改成30分钟")
        snapshot = SpatialSnapshot(
            scene="daily",
            time_of_day="afternoon",
            members=(),
            environment=EnvironmentState(27, 34, 62, 500, 18),
            devices=(
                DeviceState("climate.living_room_ac", "climate", "living_room", "cool", {"power_w": 1000}),
                DeviceState("window.living_room_window", "window", "living_room", "open"),
            ),
            rooms=(RoomState("living_room", occupied=False, unoccupied_minutes=25, window_state="open", power_w=1000),),
        )

        plans = agent.observe_and_plan(snapshot)

        self.assertFalse(any(plan.plan_id == "empty_room_open_window_energy_guard" for plan in plans))

    def test_energy_feedback_survives_agent_reconstruction_from_sqlite(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "household_memory.db"
            first_memory = HouseholdMemory(database_path)
            first_memory.apply_energy_feedback("household", "以后这种情况直接执行")

            reconstructed_agent = SpaceButlerAgent(HouseholdMemory(database_path))
            devices = (
                DeviceState("climate.living_room_ac", "climate", "living_room", "cool", {"power_w": 1000}),
                DeviceState("window.living_room_window", "window", "living_room", "open"),
            )
            snapshot = SpatialSnapshot(
                scene="daily",
                time_of_day="afternoon",
                members=(),
                environment=EnvironmentState(27, 34, 62, 500, 18),
                devices=devices,
                rooms=(
                    RoomState(
                        "living_room",
                        occupied=False,
                        unoccupied_minutes=25,
                        window_state="open",
                        power_w=1000,
                    ),
                ),
            )

            response = SpaceButlerSession(
                reconstructed_agent,
                InMemoryHomeRuntime(devices),
            ).observe(snapshot)

            self.assertEqual(response.status, "executed")
            self.assertTrue(response.report.verified if response.report else False)
            preferences = reconstructed_agent.memory.export_preferences()
            self.assertEqual(len(preferences), 1)
            self.assertEqual(preferences[0].sample_count, 1)

    def test_validated_edge_llm_feedback_updates_only_allowed_preference(self) -> None:
        class FakeEdgeClient:
            def classify_energy_feedback(self, _text: str) -> dict[str, object]:
                return {"intent": "set_unoccupied_minutes", "minutes": 40, "action": "turn_off"}

        memory = HouseholdMemory()
        routed = EdgeLanguageRouter(FakeEdgeClient()).apply_energy_feedback(
            memory,
            "household",
            "等客厅没人待够四十分钟再帮我处理空调。",
        )

        self.assertEqual(routed.route.value, "edge_llm")
        self.assertEqual(routed.learned, "learned_unoccupied_40")
        self.assertEqual(memory.recall_number("household", "empty_room_open_window_energy_guard", "unoccupied_minutes", 20), 40)
        self.assertEqual(memory.export_preferences()[0].source, "edge_llm_validated")

    def test_edge_llm_out_of_range_minutes_is_rejected(self) -> None:
        class InvalidEdgeClient:
            def classify_energy_feedback(self, _text: str) -> dict[str, object]:
                return {"intent": "set_unoccupied_minutes", "minutes": 999}

        memory = HouseholdMemory()
        routed = EdgeLanguageRouter(InvalidEdgeClient()).apply_energy_feedback(memory, "household", "模型候选")

        self.assertEqual(routed.route.value, "fallback")
        self.assertEqual(memory.export_preferences(), [])

    def test_malformed_edge_llm_output_falls_back_without_learning(self) -> None:
        class MalformedEdgeClient:
            def classify_energy_feedback(self, _text: str) -> dict[str, object]:
                raise ValueError("invalid JSON")

        memory = HouseholdMemory()
        routed = EdgeLanguageRouter(MalformedEdgeClient()).apply_energy_feedback(
            memory,
            "household",
            "随便处理一下",
        )

        self.assertEqual(routed.route.value, "fallback")
        self.assertEqual(routed.learned, "no_structured_energy_feedback")
        self.assertEqual(memory.export_preferences(), [])

    def test_command_ack_without_state_change_is_validation_failed(self) -> None:
        agent = SpaceButlerAgent()
        devices = (
            DeviceState(
                "climate.living_room_ac",
                "climate",
                "living_room",
                "cool",
                {"power_w": 1000, "ack_without_state_change": True},
            ),
            DeviceState("window.living_room_window", "window", "living_room", "open"),
        )
        snapshot = SpatialSnapshot(
            scene="daily",
            time_of_day="afternoon",
            members=(),
            environment=EnvironmentState(27, 34, 62, 500, 18),
            devices=devices,
            rooms=(RoomState("living_room", occupied=False, unoccupied_minutes=25, window_state="open", power_w=1000),),
        )

        plan = self._plan(agent.observe_and_plan(snapshot), "empty_room_open_window_energy_guard")
        report = execute_and_verify(InMemoryHomeRuntime(devices), plan)

        self.assertTrue(report.executed)
        self.assertFalse(report.verified)
        self.assertEqual(report.status, ExecutionStatus.VALIDATION_FAILED)

    def test_device_unavailable_is_not_reported_as_success(self) -> None:
        agent = SpaceButlerAgent()
        devices = (
            DeviceState("climate.living_room_ac", "climate", "living_room", "cool", {"power_w": 1000, "available": False}),
            DeviceState("window.living_room_window", "window", "living_room", "open"),
        )
        snapshot = SpatialSnapshot(
            scene="daily",
            time_of_day="afternoon",
            members=(),
            environment=EnvironmentState(27, 34, 62, 500, 18),
            devices=devices,
            rooms=(RoomState("living_room", occupied=False, unoccupied_minutes=25, window_state="open", power_w=1000),),
        )

        plan = self._plan(agent.observe_and_plan(snapshot), "empty_room_open_window_energy_guard")
        report = execute_and_verify(InMemoryHomeRuntime(devices), plan)

        self.assertFalse(report.executed)
        self.assertFalse(report.verified)
        self.assertEqual(report.status, ExecutionStatus.DEVICE_UNAVAILABLE)

    def test_response_delay_beyond_budget_is_timeout(self) -> None:
        devices = (
            DeviceState(
                "climate.living_room_ac",
                "climate",
                "living_room",
                "cool",
                {"response_delay_ms": 2_500},
            ),
        )
        plan = ServicePlan(
            plan_id="timeout_plan",
            title="timeout",
            priority=PlanPriority.ENERGY,
            proactive=True,
            target_members=("household",),
            actions=(PlanAction("climate.living_room_ac", "turn_off", True, "energy"),),
            explanation="timeout acceptance",
        )

        report = execute_and_verify(InMemoryHomeRuntime(devices, command_timeout_ms=500), plan)

        self.assertFalse(report.executed)
        self.assertFalse(report.verified)
        self.assertEqual(report.status, ExecutionStatus.TIMEOUT)

    def test_multi_action_plan_reports_partial_success(self) -> None:
        devices = (
            DeviceState("climate.living_room_ac", "climate", "living_room", "cool"),
            DeviceState("light.living_room_main", "light", "living_room", "on", {"available": False}),
        )
        plan = ServicePlan(
            plan_id="partial_plan",
            title="partial",
            priority=PlanPriority.ENERGY,
            proactive=True,
            target_members=("household",),
            actions=(
                PlanAction("climate.living_room_ac", "turn_off", True, "energy"),
                PlanAction("light.living_room_main", "turn_off", True, "energy"),
            ),
            explanation="partial acceptance",
        )

        report = execute_and_verify(InMemoryHomeRuntime(devices), plan)

        self.assertFalse(report.executed)
        self.assertFalse(report.verified)
        self.assertEqual(report.status, ExecutionStatus.PARTIAL_SUCCESS)
        self.assertEqual(report.verification_summary, "1/2 action targets verified")

    @staticmethod
    def _plan(plans, plan_id):
        for plan in plans:
            if plan.plan_id == plan_id:
                return plan
        raise AssertionError(f"missing plan {plan_id}: {plans}")


if __name__ == "__main__":
    unittest.main()
