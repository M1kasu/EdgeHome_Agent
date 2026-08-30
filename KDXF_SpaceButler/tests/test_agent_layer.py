"""Autonomous agent layer tests: goal compiler, counterfactual planner,
scorer, policy guard, verifier, override attribution, preference learning,
replanning guards and LLM fallback behaviour."""

from __future__ import annotations

from copy import deepcopy
import unittest

from spacebutler.agent.counterfactual import CounterfactualPredictor
from spacebutler.agent.goal_compiler import GoalCompiler, is_cancel_text
from spacebutler.agent.goal_manager import GoalManager
from spacebutler.agent.models import CompiledGoal, GoalConstraint, GoalStatus, RoomState
from spacebutler.agent.override_detector import OverrideDetector
from spacebutler.agent.planner import CounterfactualPlanner
from spacebutler.agent.policy_guard import PolicyGuard
from spacebutler.agent.preference_memory import PreferenceMemory
from spacebutler.agent.capabilities import build_capability_registry
from spacebutler.agent.verifier import Verifier, action_matches
from spacebutler.agent.world_model import HomeWorldModel


def demo_devices() -> list[dict[str, object]]:
    return [
        {
            "device_id": "living_room_main_light",
            "entity_id": "light.spacebutler_living_room_main_2",
            "type": "light",
            "name": "客厅主灯",
            "room": "living_room",
            "online": True,
            "state": {"power": "ON", "brightness": 204},
        },
        {
            "device_id": "living_room_curtain",
            "entity_id": "cover.spacebutler_living_room_curtain_2",
            "type": "curtain",
            "name": "客厅窗帘",
            "room": "living_room",
            "online": True,
            "state": {"position": 100},
        },
        {
            "device_id": "living_room_ac",
            "entity_id": "climate.spacebutler_living_room_ac_2",
            "type": "climate",
            "name": "客厅空调",
            "room": "living_room",
            "online": True,
            "state": {"mode": "off", "temperature": 26, "current_temperature": 30.2},
        },
        {
            "device_id": "living_room_presence_sensor",
            "entity_id": "binary_sensor.spacebutler_living_room_presence",
            "type": "presence",
            "name": "客厅人体存在传感器",
            "room": "living_room",
            "online": True,
            "state": {"occupied": True},
        },
        {
            "device_id": "living_room_temperature_sensor",
            "entity_id": "sensor.spacebutler_living_room_temperature",
            "type": "temperature",
            "name": "客厅温度传感器",
            "room": "living_room",
            "online": True,
            "state": {"temperature": 30.2},
        },
    ]


def demo_state(**overrides: object) -> RoomState:
    state = RoomState(
        temperature=30.2,
        presence=True,
        light_on=True,
        brightness_pct=80.0,
        curtain_position=100.0,
        ac_mode="off",
        ac_target=None,
    )
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


class FakeLlm:
    """Scriptable OpenAI-compatible client double."""

    def __init__(self, response: object = None, fail: bool = False) -> None:
        self.response = response
        self.fail = fail
        self.calls = 0

    def available(self, force_refresh: bool = False) -> bool:
        return not self.fail

    def chat_json(self, system: str, user: str, max_attempts: int = 2, schema: dict | None = None) -> dict | None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("llm down")
        if isinstance(self.response, dict):
            return deepcopy(self.response)
        return None


class GoalCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = GoalCompiler()

    def test_movie_text_compiles_multi_constraints_via_fallback(self) -> None:
        goal, mode, _semantic = self.compiler.compile("我准备在客厅看会电影，有点热，灯别太亮。")
        self.assertEqual(mode, "fallback")
        self.assertIsNotNone(goal)
        assert goal is not None
        self.assertEqual(goal.activity, "watch_content")
        metrics = {item.metric for item in goal.constraints}
        self.assertIn("brightness", metrics)
        self.assertIn("temperature", metrics)
        self.assertIn("curtain_position", metrics)

    def test_llm_semantic_goal_drives_compilation(self) -> None:
        llm = FakeLlm(
            {
                "is_home_goal": True,
                "location": "living_room",
                "intent_summary": "想窝在沙发看点东西，别太黑",
                "activity": "watch_content",
                "constraints": [
                    {"dimension": "thermal", "direction": "cooler", "strength": "moderate", "target_range": [24, 27], "reason": "有点闷"},
                    {"dimension": "lighting", "direction": "dimmer", "strength": "moderate", "target_range": [30, 55], "reason": "别太黑"},
                ],
            }
        )
        goal, mode, semantic = self.compiler.compile("我想窝在沙发上看点东西，屋里有点闷，但是别搞得太黑。", llm)
        self.assertEqual(mode, "llm")
        assert goal is not None
        self.assertEqual(goal.activity, "watch_content")
        metrics = {item.metric for item in goal.constraints}
        self.assertEqual(metrics, {"temperature", "brightness"})
        self.assertTrue(semantic and semantic.get("intent_summary"))

    def test_llm_non_goal_text_is_rejected(self) -> None:
        llm = FakeLlm({"is_home_goal": False, "constraints": []})
        goal, mode, _semantic = self.compiler.compile("今天天气怎么样", llm)
        self.assertIsNone(goal)
        self.assertEqual(mode, "llm")

    def test_not_too_bright_is_interval_not_fixed_value(self) -> None:
        goal, _mode, _semantic = self.compiler.compile("我要看电影，灯别太亮")
        assert goal is not None
        brightness = next(item for item in goal.constraints if item.metric == "brightness")
        self.assertIsNotNone(brightness.lower)
        self.assertIsNotNone(brightness.upper)
        self.assertGreater(brightness.upper, brightness.lower)

    def test_avoid_complete_darkness_sets_lower_bound(self) -> None:
        goal, _mode, _semantic = self.compiler.compile("看电影，但是别弄得完全黑")
        assert goal is not None
        brightness = next(item for item in goal.constraints if item.metric == "brightness")
        self.assertIsNotNone(brightness.lower)
        self.assertGreaterEqual(brightness.lower, 10)

    def test_llm_malformed_output_falls_back(self) -> None:
        llm = FakeLlm({"unexpected": "shape"})
        goal, mode, _semantic = self.compiler.compile("我想看电影", llm)
        self.assertEqual(mode, "fallback")
        assert goal is not None
        self.assertTrue(goal.constraints)

    def test_llm_out_of_range_values_clamped(self) -> None:
        llm = FakeLlm(
            {
                "is_home_goal": True,
                "activity": "rest",
                "constraints": [{"dimension": "thermal", "target_range": [99, 120], "direction": "cooler"}],
            }
        )
        goal, _mode, _semantic = self.compiler.compile("热", llm)
        assert goal is not None
        temperature = next(item for item in goal.constraints if item.metric == "temperature")
        self.assertLessEqual(temperature.upper, 30)
        self.assertGreaterEqual(temperature.lower, 16)

    def test_text_classifiers(self) -> None:
        self.assertTrue(is_cancel_text("不看了"))
        self.assertFalse(is_cancel_text("你好"))


class PlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = CounterfactualPlanner()
        self.registry = build_capability_registry(demo_devices())

    def movie_goal(self) -> CompiledGoal:
        return CompiledGoal(
            goal_id="g1",
            activity="watch_movie",
            room="living_room",
            title="观影",
            constraints=[
                GoalConstraint(metric="brightness", lower=20, upper=45),
                GoalConstraint(metric="temperature", lower=24, upper=27.5, target=26),
                GoalConstraint(metric="curtain_position", upper=30),
            ],
        )

    def test_initial_state_produces_multiple_candidates_and_selects_one(self) -> None:
        outcome = self.planner.plan(self.movie_goal(), demo_state(), self.registry)
        self.assertFalse(outcome.no_action)
        self.assertGreaterEqual(len(outcome.candidates), 2)
        self.assertIsNotNone(outcome.selected)
        selected = outcome.selected
        self.assertTrue(all(item.scores.total <= selected.scores.total + 1e-9 for item in outcome.candidates if item.actions))
        capabilities = {action.capability for action in selected.actions}
        self.assertIn("set_position", capabilities)

    def test_satisfied_state_produces_no_action(self) -> None:
        state = demo_state(brightness_pct=30.0, curtain_position=0.0, ac_mode="cool", ac_target=26.0, temperature=26.0)
        outcome = self.planner.plan(self.movie_goal(), state, self.registry)
        self.assertTrue(outcome.no_action)
        self.assertIn("satisfied", outcome.reason)

    def test_different_states_yield_different_selected_plans(self) -> None:
        outcome_hot = self.planner.plan(self.movie_goal(), demo_state(), self.registry)
        outcome_cool = self.planner.plan(
            self.movie_goal(),
            demo_state(temperature=25.0, ac_mode="cool", ac_target=26.0),
            self.registry,
        )
        hot_actions = {(a.device_id, a.capability) for a in outcome_hot.selected.actions}
        cool_actions = {(a.device_id, a.capability) for a in outcome_cool.selected.actions}
        self.assertNotEqual(hot_actions, cool_actions)

    def test_living_room_device_preferred_over_other_rooms(self) -> None:
        devices = demo_devices()
        devices.append(
            {
                "device_id": "bedroom_reading_light",
                "entity_id": "light.spacebutler_bedroom_reading_light",
                "type": "light",
                "name": "卧室阅读灯",
                "room": "bedroom",
                "online": True,
                "state": {"power": "ON", "brightness": 100},
            }
        )
        registry = build_capability_registry(devices)
        outcome = self.planner.plan(self.movie_goal(), demo_state(), registry)
        light_actions = [
            action for action in outcome.selected.actions if action.capability == "set_brightness"
        ]
        self.assertTrue(light_actions)
        self.assertEqual(light_actions[0].device_id, "living_room_main_light")

    def test_llm_candidates_drive_planning_when_valid(self) -> None:
        llm = FakeLlm(
            {
                "candidates": [
                    {
                        "summary": "关窗帘并调暗灯光",
                        "actions": [
                            {"device_id": "living_room_curtain", "capability": "set_position", "value": 20},
                            {"device_id": "living_room_main_light", "capability": "set_brightness", "value": 25},
                        ],
                    },
                    {
                        "summary": "只开空调降温和保留灯光",
                        "actions": [
                            {"device_id": "living_room_ac", "capability": "set_temperature", "value": 26, "mode": "cool"}
                        ],
                    },
                    {
                        "summary": "组合方案",
                        "actions": [
                            {"device_id": "living_room_curtain", "capability": "set_position", "value": 40},
                            {"device_id": "living_room_main_light", "capability": "set_brightness", "value": 35},
                            {"device_id": "living_room_ac", "capability": "set_temperature", "value": 25, "mode": "cool"},
                        ],
                    },
                ]
            }
        )
        outcome = self.planner.plan(self.movie_goal(), demo_state(), self.registry, llm_client=llm)
        self.assertEqual(outcome.planner_mode, "llm")
        self.assertTrue(all(item.source == "llm" for item in outcome.candidates if item.source == "llm"))
        self.assertTrue(any(item.source == "llm" for item in outcome.candidates))
        self.assertEqual(outcome.llm_metrics.llm_candidates_generated, 3)
        self.assertEqual(outcome.llm_metrics.llm_candidates_valid, 3)
        self.assertIsNotNone(outcome.selected)

    def test_llm_illegal_actions_rejected_by_policy_guard(self) -> None:
        llm = FakeLlm(
            {
                "candidates": [
                    {
                        "summary": "非法设备",
                        "actions": [
                            {"device_id": "ghost_device", "capability": "set_brightness", "value": 30},
                            {"device_id": "living_room_main_light", "capability": "set_brightness", "value": 900},
                        ],
                    }
                ]
            }
        )
        outcome = self.planner.plan(self.movie_goal(), demo_state(), self.registry, llm_client=llm)
        self.assertTrue(any("拒绝" in item.label for item in outcome.candidates))
        self.assertEqual(outcome.llm_metrics.llm_candidates_rejected, 1)
        self.assertEqual(outcome.llm_metrics.llm_candidates_valid, 0)
        self.assertFalse(any("ghost_device" == a.device_id for item in outcome.candidates for a in item.actions))


class ScorerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scorer = __import__("spacebutler.agent.scorer", fromlist=["PlanScorer"]).PlanScorer()
        self.predictor = CounterfactualPredictor()

    def test_high_energy_plan_loses_when_goal_equal(self) -> None:
        goal = CompiledGoal("g", "watch_movie", "living_room", "观影", [GoalConstraint(metric="temperature", lower=24, upper=27.5, target=26)])
        cool_plan = self.predictor.predict(
            [{"capability": "set_temperature", "value": 26, "mode": "cool", "device_id": "ac"}],
            demo_state(),
        )
        cold_plan = self.predictor.predict(
            [{"capability": "set_temperature", "value": 18, "mode": "cool", "device_id": "ac"}],
            demo_state(),
        )
        score_cool = self.scorer.score("a", "cool", [{"capability": "set_temperature"}], cool_plan, goal, demo_state(), None)
        score_cold = self.scorer.score("b", "cold", [{"capability": "set_temperature"}], cold_plan, goal, demo_state(), None)
        self.assertGreater(score_cool.scores.total, score_cold.scores.total)

    def test_preference_changes_ranking(self) -> None:
        from spacebutler.agent.models import PreferenceEstimate

        goal = CompiledGoal("g", "watch_movie", "living_room", "观影", [GoalConstraint(metric="brightness", lower=20, upper=45)])
        plan_30 = self.predictor.predict([{"capability": "set_brightness", "value": 30, "device_id": "light"}], demo_state())
        plan_45 = self.predictor.predict([{"capability": "set_brightness", "value": 45, "device_id": "light"}], demo_state())
        without = (
            self.scorer.score("a", "30", [], plan_30, goal, demo_state(), None).scores.preference_match,
            self.scorer.score("b", "45", [], plan_45, goal, demo_state(), None).scores.preference_match,
        )
        preference = PreferenceEstimate(metric="brightness", mean=45, lower=40, upper=50, count=5, confidence=0.9)
        with_pref = (
            self.scorer.score("a", "30", [], plan_30, goal, demo_state(), preference).scores.preference_match,
            self.scorer.score("b", "45", [], plan_45, goal, demo_state(), preference).scores.preference_match,
        )
        self.assertEqual(without[0], without[1])
        self.assertGreater(with_pref[1], with_pref[0])


class PolicyGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = PolicyGuard(build_capability_registry(demo_devices()))

    def test_unknown_device_rejected(self) -> None:
        issues = self.guard.issues([{"device_id": "ghost", "capability": "set_brightness", "value": 30}])
        self.assertTrue(any("不可用设备" in item for item in issues))

    def test_illegal_brightness_rejected(self) -> None:
        issues = self.guard.issues([{"device_id": "living_room_main_light", "capability": "set_brightness", "value": 900}])
        self.assertTrue(any("超出" in item for item in issues))

    def test_illegal_temperature_rejected(self) -> None:
        issues = self.guard.issues([{"device_id": "living_room_ac", "capability": "set_temperature", "value": 10, "mode": "cool"}])
        self.assertTrue(any("超出" in item for item in issues))

    def test_unsupported_service_rejected(self) -> None:
        issues = self.guard.issues([{"device_id": "living_room_curtain", "capability": "set_brightness", "value": 30}])
        self.assertTrue(any("不支持动作" in item for item in issues))

    def test_valid_actions_pass(self) -> None:
        issues = self.guard.issues(
            [
                {"device_id": "living_room_main_light", "capability": "set_brightness", "value": 30},
                {"device_id": "living_room_ac", "capability": "set_temperature", "value": 26, "mode": "cool"},
            ]
        )
        self.assertEqual(issues, [])


class VerifierTests(unittest.TestCase):
    def test_expected_matches_observed(self) -> None:
        observed = {"state": "on", "attributes": {"brightness": 51}}
        self.assertTrue(action_matches(observed, {"state": "on", "brightness": 20}))

    def test_mismatch_detected(self) -> None:
        observed = {"state": "on", "attributes": {"brightness": 200}}
        self.assertFalse(action_matches(observed, {"state": "on", "brightness": 20}))

    def test_prediction_error_computed(self) -> None:
        predictor = CounterfactualPredictor()
        predicted = predictor.predict([], demo_state())
        error = Verifier().prediction_error(predicted, demo_state(temperature=29.0))
        self.assertIsNotNone(error)
        self.assertGreaterEqual(error, 0.0)


class OverrideDetectorTests(unittest.TestCase):
    def _detector(self) -> tuple[OverrideDetector, HomeWorldModel, PreferenceMemory]:
        world = HomeWorldModel()
        preferences = PreferenceMemory(None)
        return OverrideDetector(world, preferences), world, preferences

    def test_agent_own_change_is_not_override(self) -> None:
        detector, world, _ = self._detector()

        class _W:
            def record_action(self, record):
                world.record_action(record)

        from spacebutler.agent.models import ActionRecord, now as _now

        world.record_action(
            ActionRecord("a1", "living_room_main_light", "light.x", "set_brightness", 30, _now(), {"brightness": 30})
        )
        attribution = detector.classify("living_room_main_light", "light", "set_brightness", 80, 30, _now() + 1)
        self.assertEqual(attribution.value, "AGENT_ACTION")

    def test_user_change_is_override_and_recorded(self) -> None:
        detector, world, preferences = self._detector()
        from spacebutler.agent.models import CompiledGoal, GoalConstraint, now as _now

        # 仅在目标活跃期间学习偏好（上下文绑定）。
        world.set_active_goal(
            CompiledGoal(
                "g",
                "watch_movie",
                "living_room",
                "观影",
                [GoalConstraint(metric="brightness", lower=20, upper=45)],
            ),
            GoalStatus.ACTIVE,
        )
        attribution = detector.classify("living_room_main_light", "light", "set_brightness", 30, 45, _now())
        self.assertEqual(attribution.value, "HUMAN_OVERRIDE")
        estimate = preferences.estimate("watch_movie", "living_room", "brightness")
        self.assertIsNotNone(estimate)
        self.assertEqual(estimate.mean, 45.0)
        goal, _ = world.active_goal()
        self.assertEqual(goal.constraints[0].target, 45.0)
        self.assertEqual(world.active_suppressions("g")[0].device_id, "living_room_main_light")

    def test_override_without_active_goal_is_detected_but_not_learned(self) -> None:
        detector, _, preferences = self._detector()
        from spacebutler.agent.models import now as _now

        attribution = detector.classify("living_room_main_light", "light", "set_brightness", 30, 45, _now())
        self.assertEqual(attribution.value, "HUMAN_OVERRIDE")
        self.assertIsNone(preferences.estimate("watch_movie", "living_room", "brightness"))

    def test_sensor_drift_is_environment_change(self) -> None:
        detector, _, _ = self._detector()
        from spacebutler.agent.models import now as _now

        attribution = detector.classify("living_room_temperature_sensor", "temperature", "set_temperature", 30.2, 30.4, _now())
        self.assertEqual(attribution.value, "ENVIRONMENT_CHANGE")


class PreferenceMemoryTests(unittest.TestCase):
    def test_single_observation_has_low_confidence(self) -> None:
        memory = PreferenceMemory(None)
        memory.observe("watch_movie", "living_room", "brightness", 45)
        estimate = memory.estimate("watch_movie", "living_room", "brightness")
        self.assertEqual(estimate.count, 1)
        self.assertLess(estimate.confidence, 0.3)

    def test_repeated_observations_build_confidence(self) -> None:
        memory = PreferenceMemory(None)
        for value in (44, 45, 46, 45):
            memory.observe("watch_movie", "living_room", "brightness", value)
        estimate = memory.estimate("watch_movie", "living_room", "brightness")
        self.assertEqual(estimate.count, 4)
        self.assertGreaterEqual(estimate.confidence, 0.25)
        self.assertLess(abs(estimate.mean - 45.0), 2.0)

    def test_in_memory_mode_works_without_database(self) -> None:
        memory = PreferenceMemory(None)
        memory.observe("watch_movie", "living_room", "brightness", 40)
        self.assertIsNotNone(memory.estimate("watch_movie", "living_room", "brightness"))


class GoalManagerTests(unittest.TestCase):
    def goal(self) -> CompiledGoal:
        return CompiledGoal(
            "g",
            "watch_movie",
            "living_room",
            "观影",
            [GoalConstraint(metric="brightness", lower=20, upper=45)],
        )

    def test_small_change_does_not_replan(self) -> None:
        manager = GoalManager(HomeWorldModel())
        goal = self.goal()
        state = demo_state(brightness_pct=20.0)
        manager.evaluate(goal, state)
        status, _ = manager.evaluate(goal, demo_state(brightness_pct=21.5))
        self.assertNotEqual(status, GoalStatus.REPLANNING)

    def test_significant_deviation_triggers_replan_after_debounce(self) -> None:
        manager = GoalManager(HomeWorldModel())
        goal = self.goal()
        manager.evaluate(goal, demo_state(brightness_pct=20.0))
        status, _ = manager.evaluate(goal, demo_state(brightness_pct=80.0))
        self.assertEqual(status, GoalStatus.DEGRADED)
        status, reason = manager.evaluate(goal, demo_state(brightness_pct=80.0))
        self.assertEqual(status, GoalStatus.REPLANNING)

    def test_cooldown_blocks_immediate_second_replan(self) -> None:
        manager = GoalManager(HomeWorldModel())
        goal = self.goal()
        manager.evaluate(goal, demo_state(brightness_pct=20.0))
        manager.evaluate(goal, demo_state(brightness_pct=80.0))
        manager.evaluate(goal, demo_state(brightness_pct=80.0))
        manager.evaluate(goal, demo_state(brightness_pct=20.0))
        status, _ = manager.evaluate(goal, demo_state(brightness_pct=80.0))
        status, _ = manager.evaluate(goal, demo_state(brightness_pct=80.0))
        self.assertNotEqual(status, GoalStatus.REPLANNING)


class RuntimeFallbackTests(unittest.TestCase):
    def _runtime(self, llm):
        from spacebutler.agent.runtime import AgentRuntime

        devices = demo_devices()

        def provider():
            return {"devices": deepcopy(devices)}

        def control(device_id, payload):
            for device in devices:
                if device["device_id"] == device_id:
                    state = device["state"]
                    if "power" in payload and device["type"] == "light":
                        state["power"] = str(payload["power"]).upper()
                        if "brightness" in payload:
                            state["brightness"] = round(payload["brightness"] * 255 / 100)
                    if "position" in payload:
                        state["position"] = payload["position"]
                    if "mode" in payload:
                        state["mode"] = payload["mode"]
                    if "temperature" in payload:
                        state["temperature"] = payload["temperature"]
                    return {"devices": deepcopy(devices)}
            raise ValueError("unknown device")

        runtime = AgentRuntime(provider, control, llm, None, poll_interval_seconds=3600)
        runtime._world.update_state(provider()["devices"])
        return runtime

    def test_llm_unavailable_does_not_crash_and_reports_fallback(self) -> None:
        runtime = self._runtime(FakeLlm(fail=True))
        history = runtime.submit_text("我准备在客厅看会电影，有点热，灯别太亮。")
        self.assertEqual(runtime._world.planner_mode(), "fallback")
        self.assertTrue(history["messages"][-1]["content"])

    def test_malformed_llm_json_falls_back(self) -> None:
        runtime = self._runtime(FakeLlm({"bogus": 1}))
        history = runtime.submit_text("我准备在客厅看会电影，有点热，灯别太亮。")
        self.assertEqual(runtime._world.planner_mode(), "fallback")
        self.assertTrue(history["messages"])

    def test_cancel_ends_active_goal(self) -> None:
        runtime = self._runtime(None)
        runtime.submit_text("我准备在客厅看会电影")
        history = runtime.submit_text("不看了")
        self.assertIn("已结束", history["messages"][-1]["content"])
        _, status = runtime._world.active_goal()
        self.assertEqual(status, GoalStatus.CANCELLED)


if __name__ == "__main__":
    unittest.main()
