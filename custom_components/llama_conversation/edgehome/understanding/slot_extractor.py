"""Extract bounded numeric slots from Chinese fast-path commands."""

from __future__ import annotations

import re

from .models import FastIntent, FastSlots


_CHINESE_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CHINESE_UNITS = {"十": 10, "百": 100}
_ARABIC_NUMBER = re.compile(r"(?<![\d.])([+-]?\d{1,3}(?:\.\d+)?)(?:\s*(?:%|％|度|℃|摄氏度))?")
_CHINESE_NUMBER = re.compile(r"[零〇一二两三四五六七八九十百点]+")
_BRIGHTNESS_FRACTION = re.compile(r"([零〇一二两三四五六七八九十\d]+)\s*成")


class ChineseSlotExtractor:
    """Extract only values with known ranges; missing or invalid slots stay non-executable."""

    def extract(self, text: str, intent: FastIntent) -> FastSlots:
        """Return entity text and an optional bounded numeric value for one intent."""
        value = self._value_for_intent(text, intent)
        errors: list[str] = []

        if intent in {FastIntent.SET_BRIGHTNESS, FastIntent.SET_TEMPERATURE, FastIntent.CONTROL_CURTAIN} and value is None:
            if intent == FastIntent.CONTROL_CURTAIN:
                value = self._curtain_endpoint(text)
            if value is None:
                errors.append("缺少目标数值")

        if value is not None:
            errors.extend(self._validate_value(intent, value))

        return FastSlots(entity_query=text.strip(), value=value, errors=errors)

    def _value_for_intent(self, text: str, intent: FastIntent) -> float | None:
        if intent not in {FastIntent.SET_BRIGHTNESS, FastIntent.SET_TEMPERATURE, FastIntent.CONTROL_CURTAIN}:
            return None
        if intent == FastIntent.SET_BRIGHTNESS:
            if "一半" in text:
                return 50.0
            fraction = _BRIGHTNESS_FRACTION.search(text)
            if fraction is not None:
                numerator = _parse_brightness_fraction(fraction.group(1))
                if numerator is not None:
                    return numerator * 10
        numbers = self._numbers(text)
        return numbers[-1] if numbers else None

    @staticmethod
    def _curtain_endpoint(text: str) -> float | None:
        if re.search(r"打开|开启|拉开", text):
            return 100.0
        if re.search(r"关闭|关上|拉上", text):
            return 0.0
        return None

    @staticmethod
    def _validate_value(intent: FastIntent, value: float) -> list[str]:
        if intent in {FastIntent.SET_BRIGHTNESS, FastIntent.CONTROL_CURTAIN}:
            if not value.is_integer():
                return ["百分比必须为整数"]
            if not 0 <= value <= 100:
                return ["百分比必须在 0 到 100 之间"]
        if intent == FastIntent.SET_TEMPERATURE and not 16 <= value <= 30:
            return ["温度必须在 16 到 30 摄氏度之间"]
        return []

    @staticmethod
    def _numbers(text: str) -> list[float]:
        values: list[tuple[int, float]] = []
        for match in _ARABIC_NUMBER.finditer(text):
            values.append((match.start(), float(match.group(1))))

        for match in _CHINESE_NUMBER.finditer(text):
            candidate = match.group(0)
            if candidate == "百":
                continue
            values.append((match.start(), _parse_chinese_number(candidate)))

        return [value for _, value in sorted(values, key=lambda item: item[0])]


def _parse_chinese_number(value: str) -> float:
    """Parse the small Chinese-number subset used by percentages and temperatures."""
    if "点" in value:
        integer, decimal = value.split("点", maxsplit=1)
        decimal_digits = "".join(str(_CHINESE_DIGITS[char]) for char in decimal if char in _CHINESE_DIGITS)
        return _parse_chinese_integer(integer) + float(f"0.{decimal_digits or '0'}")
    return float(_parse_chinese_integer(value))


def _parse_chinese_integer(value: str) -> int:
    if all(char in _CHINESE_DIGITS for char in value):
        return int("".join(str(_CHINESE_DIGITS[char]) for char in value))

    total = 0
    current: int | None = None
    for char in value:
        if char in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[char]
            continue
        unit = _CHINESE_UNITS.get(char)
        if unit is not None:
            total += (current if current is not None else 1) * unit
            current = None
    return total + (current or 0)


def _parse_brightness_fraction(value: str) -> float | None:
    """Parse a generic Chinese or Arabic number of tenths without tying it to a phrase."""
    if value.isdigit():
        return float(value)
    try:
        return float(_parse_chinese_integer(value))
    except (KeyError, ValueError):
        return None
