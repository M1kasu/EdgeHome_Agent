"""生成固定种子的第 12 阶段小时级能耗测试数据。"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


_SEED = 20260711
_FIELDS = ["timestamp", "entity_id", "device_name", "room", "power_w", "energy_kwh", "state", "occupancy", "temperature", "scene"]


def generate(path: Path, abnormal: bool) -> int:
    """输出一周 7 个受控虚拟设备记录；相同参数必得相同 CSV。"""
    randomizer = random.Random(_SEED)
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows: list[dict[str, str | float | bool]] = []
    for index in range(7 * 24):
        timestamp = start + timedelta(hours=index)
        hour = timestamp.hour
        day = index // 24
        occupied = 7 <= hour < 23
        temperature = round(24 + (3 if 12 <= hour < 19 else 0) + randomizer.uniform(-0.4, 0.4), 1)
        ac_on = occupied and (hour >= 11 or hour < 7)
        living_ac_power = 680.0 if ac_on else 0.0
        if abnormal and day == 5 and 12 <= hour < 20:
            temperature = round(31 + randomizer.uniform(-0.3, 0.3), 1)
            living_ac_power = 1250.0
            ac_on = True
        if abnormal and day == 6 and 13 <= hour < 17:
            occupied = False
            ac_on = True
            living_ac_power = 900.0
        tv_on = occupied and 19 <= hour < 23
        socket_power = 4.0 if not tv_on else 95.0
        if abnormal and day == 4 and 0 <= hour < 5:
            tv_on = False
            socket_power = 28.0
        light_on = occupied and 18 <= hour < 23
        if abnormal and ((day == 5 and hour >= 23) or (day == 6 and hour < 5)):
            light_on = True
            occupied = False
        rows.extend(
            [
                _row(timestamp, "climate.edgehome_living_room_ac", "客厅测试空调", "living_room", living_ac_power, "on" if ac_on else "off", occupied, temperature, "cooling"),
                _row(timestamp, "climate.edgehome_bedroom_ac", "卧室测试空调", "bedroom", 420.0 if 22 <= hour or hour < 6 else 0.0, "on" if 22 <= hour or hour < 6 else "off", occupied, temperature - 1, "sleep"),
                _row(timestamp, "switch.edgehome_living_room_tv", "客厅测试电视", "living_room", 120.0 if tv_on else 0.0, "on" if tv_on else "off", occupied, temperature, "entertainment"),
                _row(timestamp, "light.edgehome_living_room_main", "客厅测试灯", "living_room", 24.0 if light_on else 0.0, "on" if light_on else "off", occupied, temperature, "lighting"),
                _row(timestamp, "switch.edgehome_test_socket", "测试智能插座", "living_room", socket_power, "on", occupied, temperature, "standby"),
                _row(timestamp, "switch.edgehome_refrigerator", "冰箱", "home", 85.0 + randomizer.uniform(-3, 3), "on", occupied, temperature, "protected"),
                _row(timestamp, "switch.edgehome_router", "路由器", "home", 11.0 + randomizer.uniform(-0.5, 0.5), "on", occupied, temperature, "protected"),
            ]
        )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _row(
    timestamp: datetime,
    entity_id: str,
    device_name: str,
    room: str,
    power_w: float,
    state: str,
    occupancy: bool,
    temperature: float,
    scene: str,
) -> dict[str, str | float | bool]:
    return {
        "timestamp": timestamp.isoformat(),
        "entity_id": entity_id,
        "device_name": device_name,
        "room": room,
        "power_w": round(power_w, 2),
        "energy_kwh": round(power_w / 1000, 4),
        "state": state,
        "occupancy": occupancy,
        "temperature": temperature,
        "scene": scene,
    }


if __name__ == "__main__":
    root = Path(__file__).parent
    print(f"normal_week.csv: {generate(root / 'normal_week.csv', abnormal=False)} records")
    print(f"abnormal_week.csv: {generate(root / 'abnormal_week.csv', abnormal=True)} records")
