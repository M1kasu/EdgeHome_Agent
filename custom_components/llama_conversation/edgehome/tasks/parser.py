"""Parse a narrow Chinese relative-delay command before routing its inner command."""

from __future__ import annotations

import re

from .delayed import DelayedCommandRequest


class ChineseDelayedCommandParser:
    """Recognize only numeric seconds, minutes, or hours followed by one command."""

    _PATTERN = re.compile(r"^\s*(?P<value>\d{1,4})\s*(?P<unit>秒钟?|分钟?|分|个?小时)后(?P<command>.+?)\s*$")
    _UNIT_SECONDS = {"秒": 1, "秒钟": 1, "分": 60, "分钟": 60, "小时": 3600, "个小时": 3600}

    def parse(self, text: str) -> DelayedCommandRequest | None:
        """Return a bounded delay plus the remaining direct command, or no match."""
        match = self._PATTERN.match(text)
        if not match:
            return None
        seconds = int(match.group("value")) * self._UNIT_SECONDS[match.group("unit")]
        try:
            return DelayedCommandRequest(delay_seconds=seconds, command=match.group("command"))
        except ValueError:
            return None
