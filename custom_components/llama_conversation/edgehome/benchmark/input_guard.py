"""拒绝超长输入，避免端侧模型在未知上下文占用下失控。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class InputLengthPolicy(BaseModel):
    """长度阈值可通过依赖注入调整，不与模型上下文长度混淆。"""

    model_config = ConfigDict(extra="forbid")

    max_characters: int = Field(default=2_000, ge=64, le=32_768)


class InputLengthGuard:
    """在路由前执行确定性输入边界检查。"""

    def __init__(self, policy: InputLengthPolicy | None = None) -> None:
        self.policy = policy or InputLengthPolicy()

    def accepts(self, text: str) -> bool:
        """只接受非空且不超过已配置字符数的文本。"""
        return bool(text.strip()) and len(text) <= self.policy.max_characters
