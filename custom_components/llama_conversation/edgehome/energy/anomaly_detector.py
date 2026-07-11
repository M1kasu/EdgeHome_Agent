"""基于明确阈值和连续区间的确定性能耗异常检测。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta

from .schemas import EnergyIntervalFinding, EnergyRecord


class EnergyAnomalyDetector:
    """检测无人运行、电视待机和夜间长亮，阈值不会由模型改写。"""

    def detect(self, records: Iterable[EnergyRecord]) -> list[EnergyIntervalFinding]:
        ordered = sorted(records, key=lambda item: (item.entity_id, item.timestamp))
        return [*self.unoccupied_ac(ordered), *self.tv_standby(ordered), *self.night_light(ordered)]

    def unoccupied_ac(self, records: Iterable[EnergyRecord]) -> list[EnergyIntervalFinding]:
        candidates = [
            item
            for item in records
            if item.entity_id == "climate.edgehome_living_room_ac" and item.state == "on" and not item.occupancy
        ]
        return _intervals(candidates, "unoccupied_ac", 15, "客厅无人时空调仍在运行")

    def tv_standby(self, records: Iterable[EnergyRecord]) -> list[EnergyIntervalFinding]:
        by_time = {item.timestamp: item for item in records if item.entity_id == "switch.edgehome_living_room_tv"}
        candidates = [
            item
            for item in records
            if item.entity_id == "switch.edgehome_test_socket"
            and item.power_w >= 18.0
            and by_time.get(item.timestamp) is not None
            and by_time[item.timestamp].state == "off"
        ]
        return _intervals(candidates, "tv_standby", 60, "电视已关闭但测试插座待机功率偏高")

    def night_light(self, records: Iterable[EnergyRecord]) -> list[EnergyIntervalFinding]:
        candidates = [
            item
            for item in records
            if item.entity_id == "light.edgehome_living_room_main"
            and item.state == "on"
            and not item.occupancy
            and (item.timestamp.hour >= 23 or item.timestamp.hour < 6)
        ]
        return _intervals(candidates, "night_light", 60, "夜间无人时测试灯持续开启")


def _intervals(
    records: list[EnergyRecord],
    kind: str,
    minimum_minutes: int,
    detail: str,
) -> list[EnergyIntervalFinding]:
    if not records:
        return []
    grouped: list[list[EnergyRecord]] = []
    current = [records[0]]
    for record in records[1:]:
        if record.timestamp - current[-1].timestamp <= timedelta(hours=1, minutes=1):
            current.append(record)
        else:
            grouped.append(current)
            current = [record]
    grouped.append(current)
    results: list[EnergyIntervalFinding] = []
    for group in grouped:
        start, end = group[0].timestamp, group[-1].timestamp + timedelta(hours=1)
        duration = round((end - start).total_seconds() / 60)
        if duration < minimum_minutes:
            continue
        results.append(
            EnergyIntervalFinding(
                kind=kind,
                entity_id=group[0].entity_id,
                start=start,
                end=end,
                duration_minutes=duration,
                energy_kwh=round(sum(item.energy_kwh for item in group), 4),
                detail=detail,
            )
        )
    return results
