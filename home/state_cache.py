from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class StateCache:
    def __init__(self, ttl_seconds: int = 3) -> None:
        self.ttl = timedelta(seconds=ttl_seconds)
        self._items: dict[str, tuple[datetime, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._items.get(key)
        if not item or datetime.now(timezone.utc) - item[0] > self.ttl:
            return None
        return item[1]

    def set(self, key: str, value: Any) -> None:
        self._items[key] = (datetime.now(timezone.utc), value)

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._items.clear()
        else:
            self._items.pop(key, None)
