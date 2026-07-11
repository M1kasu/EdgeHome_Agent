"""超长输入在模型和工具前被拒绝。"""

from __future__ import annotations

from custom_components.llama_conversation.edgehome.benchmark.input_guard import InputLengthGuard, InputLengthPolicy


def test_long_input_guard_rejects_without_truncating_into_a_new_command() -> None:
    guard = InputLengthGuard(InputLengthPolicy(max_characters=64))

    assert guard.accepts("打开客厅灯") is True
    assert guard.accepts("关" * 65) is False
    assert guard.accepts("   ") is False
