"""Vendor adapter layer: one normalized intent, per-brand command translation.

家庭里的设备来自不同厂商，同一个"调亮到 45%"在各生态里是完全不同的报文：
小米走 MIoT-Spec 的 siid/piid，Aqara 走 Zigbee 簇命令，Philips Hue 的亮度是
0-254 而不是百分比，美的按模式枚举下发，鸿蒙智联走 serviceId/properties。
本模块把 Agent 产出的规范化指令翻译成各品牌原生报文，让上层只需表达意图。

边界声明：本项目当前的执行底座是 ESPHome 虚拟节点经 Home Assistant 下发。
本模块给出的 `native_command` 是**适配器翻译结果**，用于展示跨品牌能力映射与
取值换算；它不代表已经调用了厂商云或真实网关。接入真实小米/Aqara/美的设备时，
替换各适配器的 `dispatch` 实现即可，上层规划、校验和回读逻辑不需要改动。

品牌归属是**运行时绑定**，不是编译期常量：`BrandBindingStore` 持久化 device -> brand
映射，支持发现、纳管和释放。把同一台设备换绑到另一个生态，下发同一个规范化指令会
得到不同的原生报文——这正是适配器层存在的意义。
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Final


BRAND_RECORDS: Final[tuple[dict[str, object], ...]] = (
    {
        "brand_id": "mijia",
        "name": "小米米家",
        "protocol": "MIoT-Spec",
        "transport": "Wi-Fi 本地直连",
        "accent": "#ff6900",
        "pairing_steps": (
            "广播 mDNS _miio._udp 探测局域网节点",
            "校验 did / token 配对凭据",
            "拉取 MIoT-Spec 实例描述 siid/piid",
        ),
        "command_shape": '{"siid":2,"piid":1,"value":true}',
    },
    {
        "brand_id": "aqara",
        "name": "Aqara 绿米",
        "protocol": "Zigbee 3.0 Cluster",
        "transport": "Aqara 网关",
        "accent": "#00a3a1",
        "pairing_steps": (
            "网关进入 Zigbee 允许入网窗口",
            "设备上报 Node Descriptor 与端点列表",
            "绑定 OnOff / WindowCovering 簇",
        ),
        "command_shape": '{"cluster":"0x0006","command":"on"}',
    },
    {
        "brand_id": "midea",
        "name": "美的",
        "protocol": "美居 IoT",
        "transport": "美居云",
        "accent": "#1a63b8",
        "pairing_steps": (
            "美居账号 OAuth 授权",
            "拉取家庭设备列表与 applianceCode",
            "协商 0xAC 空调模式枚举表",
        ),
        "command_shape": '{"mode":2,"target_temperature":25.0}',
    },
    {
        "brand_id": "harmony",
        "name": "鸿蒙智联",
        "protocol": "HarmonyOS Connect",
        "transport": "华为中枢",
        "accent": "#3d5afe",
        "pairing_steps": (
            "碰一碰 / 中枢发现设备 deviceId",
            "读取 profile 的 serviceId 能力集",
            "订阅 properties 变更通知",
        ),
        "command_shape": '{"serviceId":"switch","properties":{"on":true}}',
    },
    {
        "brand_id": "hue",
        "name": "Philips Hue",
        "protocol": "Zigbee Light Link",
        "transport": "Hue Bridge",
        "accent": "#8e44ad",
        "pairing_steps": (
            "Bridge 按钮授权生成 username",
            "touchlink 配对灯具并分配 light id",
            "读取 0-254 亮度量程与色域",
        ),
        "command_shape": '{"on":true,"bri":254}',
    },
)


# 出厂默认归属。运行时以 BrandBindingStore 中的绑定为准；释放后设备变为「待接入」，
# 可以被任意生态重新发现和纳管。
DEFAULT_DEVICE_BRANDS: Final[dict[str, str]] = {
    "living_room_main_light": "mijia",
    "living_room_curtain": "aqara",
    "living_room_ac": "midea",
    "living_room_temperature_sensor": "midea",
    "living_room_presence_sensor": "aqara",
    "living_room_window_sensor": "aqara",
    "living_room_tv": "harmony",
    "bedroom_reading_light": "hue",
    "bedroom_presence_sensor": "mijia",
    "bedroom_illuminance_sensor": "aqara",
}


_BRAND_BY_ID: Final[dict[str, dict[str, object]]] = {
    str(record["brand_id"]): record for record in BRAND_RECORDS
}


def brand_record(brand_id: str) -> dict[str, object] | None:
    """Look up one brand definition."""
    record = _BRAND_BY_ID.get(brand_id)
    return dict(record) if record is not None else None


def brand_ecosystems() -> list[dict[str, object]]:
    """Every ecosystem the adapter layer can translate for, for the onboarding UI."""
    return [
        {**record, "pairing_steps": list(record.get("pairing_steps", ()))}
        for record in BRAND_RECORDS
    ]


def brand_for_device(
    device_id: str,
    bindings: dict[str, str] | None = None,
) -> dict[str, object] | None:
    """Return the brand ecosystem that owns one device.

    ``bindings`` is the runtime binding table; omit it to read factory defaults.
    An explicitly released device resolves to ``None`` instead of silently
    falling back to its default brand.
    """
    table = DEFAULT_DEVICE_BRANDS if bindings is None else bindings
    brand_id = table.get(device_id)
    return brand_record(brand_id) if brand_id else None


def _power_on(payload: dict[str, object]) -> bool:
    return str(payload.get("power", "")).lower() == "on"


def _mijia(payload: dict[str, object]) -> dict[str, object]:
    """MIoT-Spec addresses properties by service id and property id."""
    if "position" in payload:
        return {"siid": 2, "piid": 2, "value": int(payload["position"])}
    command: dict[str, object] = {"siid": 2, "piid": 1, "value": _power_on(payload)}
    if "brightness" in payload:
        command["properties"] = [{"siid": 2, "piid": 3, "value": int(payload["brightness"])}]
    return command


def _aqara(payload: dict[str, object]) -> dict[str, object]:
    """Zigbee devices take cluster commands, not percentages on a generic field."""
    if "position" in payload:
        return {
            "cluster": "0x0102",
            "command": "go_to_lift_percentage",
            "payload": {"percentage": int(payload["position"])},
        }
    return {
        "cluster": "0x0006",
        "command": "on" if _power_on(payload) else "off",
        "payload": {},
    }


_MIDEA_MODES: Final[dict[str, int]] = {"off": 0, "cool": 2, "heat": 4}


def _midea(payload: dict[str, object]) -> dict[str, object]:
    """美居 encodes mode as an enum and carries the setpoint in the same frame."""
    mode = str(payload.get("mode", "off")).lower()
    command: dict[str, object] = {
        "power": mode != "off",
        "mode": _MIDEA_MODES.get(mode, 0),
        "prompt_tone": False,
    }
    if "temperature" in payload:
        command["target_temperature"] = float(payload["temperature"])
    return command


def _harmony(payload: dict[str, object]) -> dict[str, object]:
    """HarmonyOS Connect groups capabilities under serviceId + properties."""
    return {"serviceId": "switch", "properties": {"on": _power_on(payload)}}


def _hue(payload: dict[str, object]) -> dict[str, object]:
    """Hue brightness is 0-254, so the percentage must be rescaled."""
    command: dict[str, object] = {"on": _power_on(payload)}
    if "brightness" in payload:
        command["bri"] = max(1, round(int(payload["brightness"]) * 254 / 100))
    return command


_TRANSLATORS: Final[dict[str, object]] = {
    "mijia": _mijia,
    "aqara": _aqara,
    "midea": _midea,
    "harmony": _harmony,
    "hue": _hue,
}


def translate_command(
    device_id: str,
    payload: dict[str, object],
    bindings: dict[str, str] | None = None,
) -> dict[str, object] | None:
    """Translate a normalized workbench command into its brand-native form.

    Returns ``None`` for devices without a brand binding so callers keep working
    on an un-branded deployment. The result is descriptive: execution still goes
    through Home Assistant, and the caller verifies real state read-back.
    """
    brand = brand_for_device(device_id, bindings)
    if brand is None:
        return None
    translator = _TRANSLATORS.get(str(brand["brand_id"]))
    if translator is None:
        return None
    return {
        "brand_id": brand["brand_id"],
        "brand_name": brand["name"],
        "protocol": brand["protocol"],
        "transport": brand["transport"],
        "normalized_command": dict(payload),
        "native_command": translator(dict(payload)),  # type: ignore[operator]
    }


def brand_summary(bindings: dict[str, str] | None = None) -> list[dict[str, object]]:
    """Count devices per brand for the workbench inventory header."""
    table = DEFAULT_DEVICE_BRANDS if bindings is None else bindings
    counts: dict[str, int] = {}
    for brand_id in table.values():
        counts[brand_id] = counts.get(brand_id, 0) + 1
    return [
        {**record, "pairing_steps": list(record.get("pairing_steps", ())),
         "device_count": counts.get(str(record["brand_id"]), 0)}
        for record in BRAND_RECORDS
        if counts.get(str(record["brand_id"]), 0) > 0
    ]


class BrandBindingStore:
    """Persist which ecosystem owns each device, so onboarding is demonstrable.

    Bindings survive restarts. Releasing a device removes its binding without
    touching the device itself: the ESPHome entity stays online and controllable,
    it just no longer reports a vendor ecosystem until something adopts it again.
    """

    def __init__(self, database_path: str | Path | None = None) -> None:
        self._database_path = Path(database_path) if database_path is not None else None
        self._memory: dict[str, str] = dict(DEFAULT_DEVICE_BRANDS)
        if self._database_path is not None:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_database()
            self._load()

    def bindings(self) -> dict[str, str]:
        return dict(self._memory)

    def adopt(self, device_id: str, brand_id: str) -> None:
        """Bind one device to an ecosystem, replacing any previous owner."""
        if brand_id not in _BRAND_BY_ID:
            raise ValueError(f"未知品牌生态：{brand_id}")
        if not device_id.strip():
            raise ValueError("device_id is required")
        self._memory[device_id] = brand_id
        if self._database_path is not None:
            with closing(sqlite3.connect(self._database_path)) as connection:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO brand_binding (device_id, brand_id) VALUES (?, ?)
                        ON CONFLICT(device_id) DO UPDATE SET brand_id = excluded.brand_id
                        """,
                        (device_id, brand_id),
                    )

    def release(self, device_id: str) -> bool:
        """Unbind one device. Returns False when it was not bound."""
        existed = self._memory.pop(device_id, None) is not None
        if self._database_path is not None:
            with closing(sqlite3.connect(self._database_path)) as connection:
                with connection:
                    connection.execute(
                        "DELETE FROM brand_binding WHERE device_id = ?", (device_id,)
                    )
                    connection.execute(
                        "INSERT OR REPLACE INTO brand_released (device_id) VALUES (?)",
                        (device_id,),
                    )
        return existed

    def reset(self) -> None:
        """Restore factory defaults, for a repeatable demo."""
        self._memory = dict(DEFAULT_DEVICE_BRANDS)
        if self._database_path is not None:
            with closing(sqlite3.connect(self._database_path)) as connection:
                with connection:
                    connection.execute("DELETE FROM brand_binding")
                    connection.execute("DELETE FROM brand_released")

    def _initialize_database(self) -> None:
        with closing(sqlite3.connect(self._database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS brand_binding (
                        device_id TEXT PRIMARY KEY,
                        brand_id TEXT NOT NULL
                    )
                    """
                )
                # 显式释放必须持久化，否则重启后默认映射会把设备"复活"成已接入。
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS brand_released (
                        device_id TEXT PRIMARY KEY
                    )
                    """
                )

    def _load(self) -> None:
        with closing(sqlite3.connect(self._database_path)) as connection:
            overrides = connection.execute(
                "SELECT device_id, brand_id FROM brand_binding"
            ).fetchall()
            released = connection.execute("SELECT device_id FROM brand_released").fetchall()
        for device_id, in released:
            self._memory.pop(str(device_id), None)
        for device_id, brand_id in overrides:
            if str(brand_id) in _BRAND_BY_ID:
                self._memory[str(device_id)] = str(brand_id)
