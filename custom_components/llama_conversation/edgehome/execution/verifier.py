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
