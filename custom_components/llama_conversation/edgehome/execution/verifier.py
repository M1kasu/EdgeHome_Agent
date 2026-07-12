"""Read Home Assistant state again and compare it with a declared target."""

from __future__ import annotations

import asyncio
from numbers import Real
from typing import Any

from ..entity_resolver import ExposedEntityResolver
from ..models import StateCondition, VerificationResult, VerificationRule


class StateVerifier:
    """Verify state and attributes with an optional numeric tolerance."""

    def __init__(self, resolver: ExposedEntityResolver) -> None:
        self._resolver = resolver

    async def async_verify(self, rule: VerificationRule | StateCondition) -> VerificationResult:
        """Wait for state propagation when requested, then obtain a fresh snapshot."""
        if isinstance(rule, VerificationRule) and rule.settle_seconds:
            await asyncio.sleep(rule.settle_seconds)

        snapshot = self._resolver.snapshot(rule.entity_id)
        if rule.state is not None and snapshot.state != rule.state:
            return VerificationResult(
                passed=False,
                reason=f"状态不匹配，期望 {rule.state}，实际 {snapshot.state}",
                snapshot=snapshot,
            )

        for name, expected in rule.attributes.items():
            if name not in snapshot.attributes:
                return VerificationResult(
                    passed=False,
                    reason=f"状态属性缺失: {name}",
                    snapshot=snapshot,
                )
            actual = snapshot.attributes[name]
            if self._matches(actual, expected, rule.tolerance):
                continue
            return VerificationResult(
                passed=False,
                reason=f"属性 {name} 不匹配，期望 {expected!r}，实际 {actual!r}",
                snapshot=snapshot,
            )
        return VerificationResult(passed=True, snapshot=snapshot)

    async def async_verify_within(
        self,
        rule: VerificationRule,
        timeout_seconds: float,
        poll_interval_seconds: float = 0.2,
    ) -> VerificationResult:
        """Poll a device-owned state topic until it verifies or its feedback window expires."""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        if rule.settle_seconds:
            await asyncio.sleep(min(rule.settle_seconds, timeout_seconds))
        immediate_rule = rule.model_copy(update={"settle_seconds": 0.0})
        result = await self.async_verify(immediate_rule)
        while not result.passed and loop.time() < deadline:
            await asyncio.sleep(min(poll_interval_seconds, max(0.0, deadline - loop.time())))
            result = await self.async_verify(immediate_rule)
        if result.passed:
            return result
        reason = result.reason or "设备状态未达到预期"
        return result.model_copy(
            update={
                "timed_out": True,
                "reason": f"设备反馈在 {timeout_seconds:g} 秒内未达到目标：{reason}",
            }
        )

    @staticmethod
    def _matches(actual: Any, expected: Any, tolerance: float) -> bool:
        if (
            isinstance(actual, Real)
            and not isinstance(actual, bool)
            and isinstance(expected, Real)
            and not isinstance(expected, bool)
        ):
            return abs(float(actual) - float(expected)) <= tolerance
        return bool(actual == expected)
