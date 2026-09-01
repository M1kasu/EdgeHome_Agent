"""双通道路由：云端优先、本地兜底，两边都不行时必须安全失败而非编造计划。"""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from spacebutler.llm.claude import _strict_schema
from spacebutler.llm.router import DualChannelRouter, build_router


def _channel(result, *, available=True, stage=None, context=None, model="m"):
    client = Mock()
    client.available = Mock(return_value=available)
    client.chat_json = Mock(return_value=result)
    client.context_window = Mock(return_value=context)
    client.last_error_stage = stage
    client.last_retry_count = 0
    client.model = model
    return client


class RoutingTests(unittest.TestCase):
    def test_cloud_serves_when_healthy_and_local_is_never_called(self) -> None:
        cloud = _channel({"ok": True})
        local = _channel({"from": "local"})

        router = DualChannelRouter(cloud=cloud, local=local)
        result = router.chat_json("sys", "user", {"type": "object"})

        self.assertEqual(result, {"ok": True})
        self.assertEqual(router.last_channel, "cloud")
        local.chat_json.assert_not_called()

    def test_cloud_failure_falls_back_to_local_in_the_same_call(self) -> None:
        cloud = _channel(None, stage="timeout")
        local = _channel({"from": "local"})

        router = DualChannelRouter(cloud=cloud, local=local)
        result = router.chat_json("sys", "user")

        self.assertEqual(result, {"from": "local"})
        self.assertEqual(router.last_channel, "local")
        local.chat_json.assert_called_once()

    def test_both_channels_down_returns_none_so_the_planner_exits_safely(self) -> None:
        """绝不能编造计划：两边都失败时返回 None，上层零命令退出。"""
        router = DualChannelRouter(cloud=_channel(None, stage="network_error"),
                                   local=_channel(None, stage="http_error"))

        self.assertIsNone(router.chat_json("sys", "user"))
        self.assertIsNone(router.last_channel)

    def test_cloud_is_muted_after_a_failure_so_the_next_call_skips_the_timeout(self) -> None:
        """连续规划时不能每次都先白等一个云端超时。"""
        cloud = _channel(None, stage="timeout")
        local = _channel({"from": "local"})
        router = DualChannelRouter(cloud=cloud, local=local, cooldown_seconds=300)

        router.chat_json("sys", "first")
        router.chat_json("sys", "second")

        self.assertEqual(cloud.chat_json.call_count, 1)
        self.assertEqual(local.chat_json.call_count, 2)

    def test_auth_errors_mute_the_cloud_far_longer_than_transient_ones(self) -> None:
        """鉴权失败是配置问题，重试没有意义。"""
        transient = DualChannelRouter(cloud=_channel(None, stage="timeout"),
                                      local=_channel({}), cooldown_seconds=60)
        configured = DualChannelRouter(cloud=_channel(None, stage="auth_error"),
                                       local=_channel({}), cooldown_seconds=60)
        transient.chat_json("s", "u")
        configured.chat_json("s", "u")

        transient_mute = next(c for c in transient.channels if c["channel"] == "cloud")["muted_seconds"]
        configured_mute = next(c for c in configured.channels if c["channel"] == "cloud")["muted_seconds"]
        self.assertGreater(configured_mute, transient_mute)

    def test_router_reports_latency_and_both_channels(self) -> None:
        router = DualChannelRouter(cloud=_channel({"ok": 1}, model="claude-opus-5"),
                                   local=_channel({}, context=16384))
        router.chat_json("s", "u")

        self.assertIsInstance(router.last_latency_seconds, float)
        labels = {c["channel"] for c in router.channels}
        self.assertEqual(labels, {"cloud", "local"})

    def test_context_check_is_skipped_while_the_cloud_channel_serves(self) -> None:
        """槽位上下文是 llama.cpp 的概念；云端在跑时不该用它拦住开拍。"""
        router = DualChannelRouter(cloud=_channel({"ok": 1}), local=_channel({}, context=512))

        self.assertIsNone(router.context_window())

    def test_context_check_returns_the_local_limit_once_the_cloud_is_muted(self) -> None:
        router = DualChannelRouter(cloud=_channel(None, stage="timeout"),
                                   local=_channel({}, context=512), cooldown_seconds=300)
        router.chat_json("s", "u")

        self.assertEqual(router.context_window(), 512)


class BuilderTests(unittest.TestCase):
    def test_only_local_configured_returns_the_bare_client(self) -> None:
        """单通道时不包路由器，保持既有调用栈不变。"""
        local = _channel({})

        self.assertIs(build_router(None, local), local)

    def test_unavailable_cloud_is_not_wrapped(self) -> None:
        local = _channel({})

        self.assertIs(build_router(_channel(None, available=False), local), local)

    def test_available_cloud_produces_a_router(self) -> None:
        router = build_router(_channel({}), _channel({}))

        self.assertIsInstance(router, DualChannelRouter)


class StrictSchemaTests(unittest.TestCase):
    def test_additional_properties_is_added_at_every_object_level(self) -> None:
        """Claude 的约束解码要求每层 object 显式禁止额外键。"""
        schema = {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"summary": {"type": "string"}}},
                }
            },
            "required": ["candidates"],
        }

        strict = _strict_schema(schema)

        self.assertFalse(strict["additionalProperties"])
        self.assertFalse(strict["properties"]["candidates"]["items"]["additionalProperties"])

    def test_the_original_schema_is_not_mutated(self) -> None:
        schema = {"type": "object", "properties": {}}

        _strict_schema(schema)

        self.assertNotIn("additionalProperties", schema)

    def test_required_is_left_alone(self) -> None:
        """放宽必填项是业务决策，适配层不能偷偷改。"""
        schema = {"type": "object",
                  "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                  "required": ["a"]}

        self.assertEqual(_strict_schema(schema)["required"], ["a"])


if __name__ == "__main__":
    unittest.main()
