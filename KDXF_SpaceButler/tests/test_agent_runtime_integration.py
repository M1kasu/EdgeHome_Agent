from __future__ import annotations

from copy import deepcopy
import time
import unittest

from spacebutler.agent.capabilities import build_capability_registry
from spacebutler.agent.models import (
    ActionRecord,
    CandidatePlan,
    CompiledGoal,
    GoalConstraint,
    GoalStatus,
    PlanScores,
    PlannedAction,
    PredictedState,
    PreferenceEstimate,
    now,
)
from spacebutler.agent.planner import PlanningOutcome
from spacebutler.agent.policy_guard import PolicyGuard
from spacebutler.agent.preference_memory import PreferenceMemory
from spacebutler.agent.runtime import AgentRuntime, _device_signatures
from spacebutler.agent.scorer import PlanScorer
from spacebutler.workbench import WorkbenchController


def _devices(temperature: float = 30.2, brightness: int = 204) -> list[dict[str, object]]:
    return [
        {
            "device_id": "living_room_main_light",
            "entity_id": "light.living_room_main",
            "type": "light",
            "name": "Main light",
            "room": "living_room",
            "online": True,
            "state": {"power": "ON", "brightness": brightness},
            "ha_state": {"state": "on", "attributes": {"brightness": brightness}},
        },
        {
            "device_id": "living_room_curtain",
            "entity_id": "cover.living_room_curtain",
            "type": "curtain",
            "name": "Curtain",
            "room": "living_room",
            "online": True,
            "state": {"position": 100},
            "ha_state": {"state": "open", "attributes": {"current_position": 100}},
        },
        {
            "device_id": "living_room_ac",
            "entity_id": "climate.living_room_ac",
            "type": "climate",
            "name": "AC",
            "room": "living_room",
            "online": True,
            "state": {"mode": "off", "temperature": 27.0, "current_temperature": temperature},
            "ha_state": {
                "state": "off",
                "attributes": {
                    "temperature": 27.0,
                    "current_temperature": temperature,
                    "hvac_modes": ["off", "cool"],
                    "min_temp": 16.0,
                    "max_temp": 30.0,
                    "target_temp_step": 0.5,
                },
            },
        },
        {
            "device_id": "living_room_temperature_sensor",
            "entity_id": "sensor.living_room_temperature",
            "type": "temperature",
            "name": "Temperature",
            "room": "living_room",
            "online": True,
            "state": {"temperature": temperature},
            "ha_state": {"state": str(temperature), "attributes": {}},
        },
        {
            "device_id": "living_room_presence_sensor",
            "entity_id": "binary_sensor.living_room_presence",
            "type": "presence",
            "name": "Presence",
            "room": "living_room",
            "online": True,
            "state": {"occupied": True},
            "ha_state": {"state": "on", "attributes": {}},
        },
    ]


class FakeHome:
    def __init__(self, *, apply_control: bool = True, post_temperature: float = 29.4) -> None:
        self.devices = _devices()
        self.apply_control = apply_control
        self.post_temperature = post_temperature
        self.calls: list[tuple[str, dict[str, object]]] = []

    def inventory(self) -> dict[str, object]:
        return {"devices": deepcopy(self.devices)}

    def control(self, device_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((device_id, deepcopy(payload)))
        if self.apply_control and device_id == "living_room_ac":
            climate = self.devices[2]
            climate["state"] = {
                "mode": str(payload.get("mode", "cool")),
                "temperature": float(payload["temperature"]),
                "current_temperature": self.post_temperature,
            }
            climate["ha_state"] = {
                "state": str(payload.get("mode", "cool")),
                "attributes": {
                    "temperature": float(payload["temperature"]),
                    "current_temperature": self.post_temperature,
                },
            }
            sensor = self.devices[3]
            sensor["state"] = {"temperature": self.post_temperature}
            sensor["ha_state"] = {"state": str(self.post_temperature), "attributes": {}}
        return self.inventory()


def _goal(metric: str = "temperature") -> CompiledGoal:
    constraint = (
        GoalConstraint(metric="temperature", lower=24, upper=27, target=26)
        if metric == "temperature"
        else GoalConstraint(metric="brightness", lower=20, upper=35, target=30)
    )
    return CompiledGoal("goal-runtime", "watch_movie", "living_room", "Runtime goal", [constraint])


def _temperature_outcome() -> PlanningOutcome:
    candidate = CandidatePlan(
        candidate_id="fresh",
        label="Fresh feedback",
        actions=[
            PlannedAction(
                "fresh-a1",
                "living_room_ac",
                "AC",
                "climate.living_room_ac",
                "set_temperature",
                26.0,
                "cool",
            )
        ],
        predicted=PredictedState(temperature=29.0, temperature_trend="decreasing", ac_mode="cool"),
        scores=PlanScores(total=1.0),
        selected=True,
    )
    return PlanningOutcome(
        planner_mode="fallback",
        no_action=False,
        reason="integration candidate",
        candidates=[candidate],
        selected=candidate,
    )


class RuntimeFeedbackIntegrationTests(unittest.TestCase):
    def test_agent_action_uses_real_device_identity(self) -> None:
        home = FakeHome()
        runtime = AgentRuntime(home.inventory, home.control, poll_interval_seconds=3600)
        runtime._world.update_state(home.devices)
        runtime._last_signature = _device_signatures(home.devices)
        runtime._world.record_action(
            ActionRecord(
                "agent-light",
                "living_room_main_light",
                "light.living_room_main",
                "set_brightness",
                30.0,
                now(),
                {"brightness": 30},
            )
        )
        home.devices[0]["state"]["brightness"] = round(30 * 255 / 100)
        home.devices[0]["ha_state"]["attributes"]["brightness"] = round(30 * 255 / 100)

        runtime._detect_changes(home.devices)

        attributions = [event for event in runtime.status()["timeline"] if event["kind"] == "attribution"]
        self.assertEqual(attributions[-1]["data"]["attribution"], "AGENT_ACTION")
        self.assertFalse(any(event["kind"] == "human_override" for event in runtime.status()["timeline"]))

    def test_numeric_sensor_jitter_uses_tolerance_before_attribution(self) -> None:
        home = FakeHome()
        runtime = AgentRuntime(home.inventory, home.control, poll_interval_seconds=3600)
        runtime._last_signature = _device_signatures(home.devices)
        sensor = home.devices[3]
        sensor["state"]["temperature"] = 30.19

        runtime._detect_changes(home.devices)

        self.assertFalse(any(event["kind"] == "attribution" for event in runtime.status()["timeline"]))
        sensor["state"]["temperature"] = 29.9
        runtime._detect_changes(home.devices)
        attributions = [event for event in runtime.status()["timeline"] if event["kind"] == "attribution"]
        self.assertEqual(attributions[-1]["data"]["attribution"], "ENVIRONMENT_CHANGE")

    def test_human_override_is_suppressed_and_not_reverted(self) -> None:
        home = FakeHome()
        light = home.devices[0]
        light["state"]["brightness"] = round(30 * 255 / 100)
        light["ha_state"]["attributes"]["brightness"] = round(30 * 255 / 100)
        runtime = AgentRuntime(home.inventory, home.control, poll_interval_seconds=3600)
        runtime._world.update_state(home.devices)
        runtime._last_signature = _device_signatures(home.devices)
        goal = _goal("brightness")
        runtime._world.set_active_goal(goal, GoalStatus.ACTIVE)
        runtime._world.record_action(
            ActionRecord(
                "old-agent-light",
                "living_room_main_light",
                "light.living_room_main",
                "set_brightness",
                30.0,
                now() - 91,
                {"brightness": 30},
            )
        )
        light["state"]["brightness"] = round(45 * 255 / 100)
        light["ha_state"]["attributes"]["brightness"] = round(45 * 255 / 100)

        for _ in range(3):
            runtime._tick()

        self.assertEqual(home.calls, [])
        self.assertAlmostEqual(runtime._world.current_state().brightness_pct, 45.1, delta=0.2)
        self.assertAlmostEqual(goal.constraints[0].target, 45.1, delta=0.2)
        self.assertEqual(runtime._world.active_suppressions(goal.goal_id)[0].device_id, "living_room_main_light")
        self.assertIsNotNone(runtime._preferences.estimate("watch_movie", "living_room", "brightness"))

    def test_prediction_error_uses_fresh_ha_observation(self) -> None:
        home = FakeHome(post_temperature=29.4)
        runtime = AgentRuntime(home.inventory, home.control, poll_interval_seconds=3600)
        before = runtime._world.update_state(home.devices)
        outcome = _temperature_outcome()
        runtime._planner.plan = lambda *args, **kwargs: outcome

        runtime._plan_and_execute(_goal(), before, build_capability_registry(home.devices))

        prediction = runtime.status()["last_prediction"]
        self.assertEqual(before.temperature, 30.2)
        self.assertEqual(runtime._world.current_state().temperature, 29.4)
        self.assertEqual(prediction["observed_temperature"], 29.4)
        self.assertAlmostEqual(prediction["prediction_error"], 0.4)
        self.assertTrue(prediction["observed"]["fresh"])
        self.assertEqual(prediction["observed"]["source"], "HOME_ASSISTANT")
        action = runtime.status()["recent_actions"][-1]
        self.assertLess(prediction["predicted"]["predicted_at"], action["sent_at"])
        self.assertLessEqual(action["sent_at"], prediction["observed"]["observed_at"])
        self.assertEqual(runtime._planner._predictor.calibration_samples, 1)

    def test_verification_failure_never_calibrates(self) -> None:
        home = FakeHome(apply_control=False)
        runtime = AgentRuntime(home.inventory, home.control, poll_interval_seconds=3600)
        before = runtime._world.update_state(home.devices)
        outcome = _temperature_outcome()
        runtime._planner.plan = lambda *args, **kwargs: outcome

        runtime._plan_and_execute(_goal(), before, build_capability_registry(home.devices))

        self.assertEqual(runtime._planner._predictor.calibration_samples, 0)
        self.assertFalse(runtime.status()["recent_actions"][-1]["verified"])
        self.assertTrue(any(event["kind"] == "calibration_skipped" for event in runtime.status()["timeline"]))


class PolicyAndPreferenceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = PolicyGuard(build_capability_registry(_devices()))

    def test_policy_guard_distinguishes_duplicates_conflicts_and_devices(self) -> None:
        light = "living_room_main_light"
        duplicate = [
            {"device_id": light, "capability": "set_brightness", "value": 20},
            {"device_id": light, "capability": "set_brightness", "value": 20},
        ]
        conflict = [
            {"device_id": light, "capability": "set_brightness", "value": 20},
            {"device_id": light, "capability": "set_brightness", "value": 80},
        ]
        off_conflict = [
            {"device_id": light, "capability": "turn_off", "value": None},
            {"device_id": light, "capability": "set_brightness", "value": 80},
        ]
        different_devices = [
            {"device_id": light, "capability": "set_brightness", "value": 20},
            {"device_id": "living_room_curtain", "capability": "set_position", "value": 80},
        ]
        self.assertTrue(any("重复" in issue for issue in self.guard.issues(duplicate)))
        self.assertTrue(any("冲突" in issue for issue in self.guard.issues(conflict)))
        self.assertTrue(any("冲突" in issue for issue in self.guard.issues(off_conflict)))
        self.assertEqual(self.guard.issues(different_devices), [])

    def test_policy_guard_uses_live_hvac_modes(self) -> None:
        issues = self.guard.issues(
            [{"device_id": "living_room_ac", "capability": "set_hvac_mode", "value": "heat"}]
        )
        self.assertTrue(any("不受支持" in issue for issue in issues))

    def test_temperature_value_is_aligned_to_live_device_step(self) -> None:
        from spacebutler.agent.planner import CounterfactualPlanner

        planner = CounterfactualPlanner()
        registry = build_capability_registry(_devices())
        action = planner._enrich(
            {"device_id": "living_room_ac", "capability": "set_temperature", "value": 27.9},
            registry,
        )
        self.assertEqual(action["value"], 28.0)

    def test_preference_requires_consistent_evidence(self) -> None:
        memory = PreferenceMemory(None)
        scorer = PlanScorer()
        memory.observe("watch_movie", "living_room", "brightness", 45)
        single = memory.estimate("watch_movie", "living_room", "brightness")
        self.assertEqual(scorer._preference_match(PredictedState(26, "stable", 45), single), 0.5)

        for value in (44, 45):
            memory.observe("watch_movie", "living_room", "brightness", value)
        consistent = memory.estimate("watch_movie", "living_room", "brightness")
        self.assertGreater(scorer._preference_match(PredictedState(26, "stable", 45), consistent), 0.5)

        contradictory_memory = PreferenceMemory(None)
        for value in (20, 80, 20):
            contradictory_memory.observe("watch_movie", "living_room", "brightness", value)
        contradictory = contradictory_memory.estimate("watch_movie", "living_room", "brightness")
        self.assertLess(contradictory.confidence, consistent.confidence)
        self.assertEqual(scorer._preference_match(PredictedState(26, "stable", 20), contradictory), 0.5)


class RuntimeHealthIntegrationTests(unittest.TestCase):
    class FakeHa:
        @staticmethod
        def states() -> list[dict[str, object]]:
            return []

    @staticmethod
    def _controller(runtime: AgentRuntime, devices: list[dict[str, object]]) -> WorkbenchController:
        controller = WorkbenchController.__new__(WorkbenchController)
        controller._agent_runtime = runtime
        controller._ha = RuntimeHealthIntegrationTests.FakeHa()
        controller.devices = lambda: {"devices": deepcopy(devices)}
        return controller

    def test_llm_offline_is_unavailable_not_full_ready(self) -> None:
        home = FakeHome()
        runtime = AgentRuntime(home.inventory, home.control, None, poll_interval_seconds=0.01)
        runtime.start()
        try:
            time.sleep(0.04)
            health = self._controller(runtime, home.devices).health()
            self.assertEqual(health["llm"], "unavailable")
            self.assertEqual(health["agent"]["status"], "unavailable")
            self.assertTrue(health["agent"]["thread_alive"])
            self.assertTrue(health["agent"]["recent_tick"])
        finally:
            runtime.stop()

    def test_dead_runtime_thread_is_never_ready(self) -> None:
        home = FakeHome()
        runtime = AgentRuntime(home.inventory, home.control, None, poll_interval_seconds=0.01)
        runtime.start()
        time.sleep(0.03)
        runtime.stop()

        health = self._controller(runtime, home.devices).health()

        self.assertEqual(health["agent"]["status"], "degraded")
        self.assertFalse(health["agent"]["thread_alive"])


if __name__ == "__main__":
    unittest.main()
