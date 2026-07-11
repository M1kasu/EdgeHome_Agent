"""Scene semantics that guide planning but never issue implicit device commands."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SceneName(StrEnum):
    """Named scene intents recognized before a model is asked to fill a fixed plan."""

    MOVIE = "movie"
    LEAVING = "leaving"
    SLEEPING = "sleeping"
    RETURNING = "returning"
    ENERGY_SAVING = "energy_saving"


class SceneTemplate(BaseModel):
    """A prompt-level scene description with no hard-coded entity or service access."""

    model_config = ConfigDict(extra="forbid")

    name: SceneName
    keywords: tuple[str, ...]
    planning_guidance: str = Field(min_length=1)


class SceneTemplateLibrary:
    """Match one scene name; actual device selection must still pass plan validation."""

    _TEMPLATES = (
        SceneTemplate(
            name=SceneName.MOVIE,
            keywords=("观影", "看电影", "电影模式"),
            planning_guidance="观影场景只可使用快照中明确列出的设备；没有明确设备时不要猜测。",
        ),
        SceneTemplate(
            name=SceneName.LEAVING,
            keywords=("离家", "出门", "我走了"),
            planning_guidance="离家场景必须保守，不得关闭未来标记为受保护的设备。",
        ),
        SceneTemplate(
            name=SceneName.SLEEPING,
            keywords=("睡觉", "晚安", "睡眠模式"),
            planning_guidance="睡眠场景应只计划用户已明确提及或快照中可安全控制的设备。",
        ),
        SceneTemplate(
            name=SceneName.RETURNING,
            keywords=("回家", "到家"),
            planning_guidance="回家场景不能假定设备状态，所有操作都必须声明验证规则。",
        ),
        SceneTemplate(
            name=SceneName.ENERGY_SAVING,
            keywords=("节能", "省电", "省电模式"),
            planning_guidance="节能场景不应根据猜测关闭设备；仅操作快照内明确实体。",
        ),
    )

    def match(self, text: str) -> SceneTemplate | None:
        """Return the first explicit scene keyword match, otherwise no scene guidance."""
        normalized = "".join(character.lower() for character in text if not character.isspace())
        for template in self._TEMPLATES:
            if any(keyword in normalized for keyword in template.keywords):
                return template
        return None

    def all(self) -> tuple[SceneTemplate, ...]:
        """Return immutable template metadata for a future debug panel."""
        return self._TEMPLATES
