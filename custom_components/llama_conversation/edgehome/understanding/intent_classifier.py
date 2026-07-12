"""Conservative rule-based classification for high-frequency Chinese commands."""

from __future__ import annotations

import re

from .models import FastIntent, FastIntentMatch


class ChineseIntentClassifier:
    """Recognize only narrow single-device instructions suitable for direct execution."""

    _COMPLEX_REQUEST = re.compile(
        r"然后|再把|并且|同时|以及|全部|所有|定时|分钟后|小时后|天后|明天|今晚|早上|如果|当.+(?:时|后)|、|；|;"
    )
    _QUERY_WORDS = re.compile(r"状态|查询|查看|看看|多少|几度|吗|么|？|\?")
    _BRIGHTNESS_WORDS = re.compile(r"亮度|调亮|调暗|调高|调低|提高|降低|明亮")
    _LIGHT_PERCENT_COMMAND = re.compile(
        r"(?:灯|灯光|照明|光).*(?:调到|调至|设为|设置|改成|调成|降到|升到|提高|降低|调高|调低|亮度).*(?:%|％|百分之|成|一半)"
    )
    _TEMPERATURE_WORDS = re.compile(r"温度|几度|制冷|制热")
    _CURTAIN_WORDS = re.compile(r"窗帘|帘子|遮阳帘")
    _SECURITY_WORDS = re.compile(r"安防|安全模式")
    _OFF_WORDS = re.compile(r"关闭|关掉|关上|熄灭|停止|停掉")
    _ON_WORDS = re.compile(r"打开|开启|开灯|点亮|启动|开(?:一下|吧)?")

    def classify(self, text: str) -> FastIntentMatch:
        """Return an explicit intent only when the request has one safe interpretation."""
        normalized = self._normalize(text)
        if not normalized:
            return FastIntentMatch(intent=FastIntent.UNKNOWN, confidence=0.0, reason="输入为空")
        if self._COMPLEX_REQUEST.search(normalized):
            return FastIntentMatch(intent=FastIntent.UNKNOWN, confidence=0.0, reason="包含多动作、条件或延时语义")
        if self._QUERY_WORDS.search(normalized):
            return FastIntentMatch(intent=FastIntent.QUERY_STATE, confidence=0.98, reason="匹配状态查询词")
        if self._BRIGHTNESS_WORDS.search(normalized) or self._LIGHT_PERCENT_COMMAND.search(normalized):
            return FastIntentMatch(intent=FastIntent.SET_BRIGHTNESS, confidence=0.98, reason="匹配亮度控制词")
        if self._TEMPERATURE_WORDS.search(normalized):
            return FastIntentMatch(intent=FastIntent.SET_TEMPERATURE, confidence=0.98, reason="匹配温度控制词")
        if self._SECURITY_WORDS.search(normalized) and self._ON_WORDS.search(normalized):
            return FastIntentMatch(intent=FastIntent.ACTIVATE_SECURITY, confidence=0.98, reason="匹配安防模式开启词")
        if self._CURTAIN_WORDS.search(normalized) and (
            self._ON_WORDS.search(normalized)
            or self._OFF_WORDS.search(normalized)
            or re.search(r"拉开|拉上", normalized)
            or "到" in normalized
        ):
            return FastIntentMatch(intent=FastIntent.CONTROL_CURTAIN, confidence=0.97, reason="匹配窗帘控制词")
        if self._OFF_WORDS.search(normalized):
            return FastIntentMatch(intent=FastIntent.TURN_OFF, confidence=0.96, reason="匹配关闭控制词")
        if self._ON_WORDS.search(normalized):
            return FastIntentMatch(intent=FastIntent.TURN_ON, confidence=0.96, reason="匹配打开控制词")
        return FastIntentMatch(intent=FastIntent.UNKNOWN, confidence=0.0, reason="不属于受支持的快速指令")

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", "", text).lower()
