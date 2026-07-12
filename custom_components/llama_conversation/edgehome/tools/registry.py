"""A closed registry of Home Assistant operations allowed in fixed plans."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from homeassistant.const import ATTR_ENTITY_ID
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..errors import ToolValidationError


class ToolRisk(StrEnum):
    """Risk labels are consumed by the safety stage added later."""

    LOW = "low"
    MEDIUM = "medium"


class EntityArguments(BaseModel):
    """Arguments shared by tools that target one registered entity."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(pattern=r"^[a-z_]+\.[a-z0-9_]+$")


class EmptyArguments(BaseModel):
    """Arguments for a read of the complete registered virtual home."""

    model_config = ConfigDict(extra="forbid")


class RoomArguments(BaseModel):
    """A named demo room, deliberately not an arbitrary Home Assistant area id."""

    model_config = ConfigDict(extra="forbid")

    room: Literal["living_room", "bedroom", "home"]


class SecurityModeArguments(EntityArguments):
    """Security mode can only be activated for the dedicated virtual helper."""


class DelayedTaskArguments(BaseModel):
    """Arguments accepted by the runtime scheduling facade, never by a raw plan."""

    model_config = ConfigDict(extra="forbid")

    delay_seconds: int = Field(ge=1, le=7 * 24 * 60 * 60)
    command: str = Field(min_length=1, max_length=300)


class BrightnessArguments(EntityArguments):
    """Brightness is always expressed as a user-facing percentage."""

    brightness: int = Field(ge=0, le=100)


class TemperatureArguments(EntityArguments):
    """First-version climate controls are deliberately limited to 16–30°C."""

    temperature: float = Field(ge=16.0, le=30.0)


class HvacModeArguments(EntityArguments):
    """Allow only the MQTT climate modes declared by the simulator."""

    hvac_mode: Literal["off", "cool", "heat"]


class CurtainArguments(EntityArguments):
    """Curtain position follows Home Assistant's 0–100 percent convention."""

    position: int = Field(ge=0, le=100)


ServiceDataBuilder = Callable[[BaseModel], dict[str, Any]]


def _entity_service_data(arguments: BaseModel) -> dict[str, Any]:
    values = arguments.model_dump()
    return {ATTR_ENTITY_ID: values["entity_id"]}


def _brightness_service_data(arguments: BaseModel) -> dict[str, Any]:
    values = arguments.model_dump()
    return {ATTR_ENTITY_ID: values["entity_id"], "brightness_pct": values["brightness"]}


def _temperature_service_data(arguments: BaseModel) -> dict[str, Any]:
    values = arguments.model_dump()
    return {ATTR_ENTITY_ID: values["entity_id"], "temperature": values["temperature"]}


def _hvac_mode_service_data(arguments: BaseModel) -> dict[str, Any]:
    values = arguments.model_dump()
    return {ATTR_ENTITY_ID: values["entity_id"], "hvac_mode": values["hvac_mode"]}


def _curtain_service_data(arguments: BaseModel) -> dict[str, Any]:
    values = arguments.model_dump()
    return {ATTR_ENTITY_ID: values["entity_id"], "position": values["position"]}


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Metadata and argument schema for one allowed EdgeHome tool."""

    name: str
    description: str
    argument_model: type[BaseModel]
    allowed_domains: frozenset[str]
    risk: ToolRisk
    timeout_seconds: float = 5.0
    max_retries: int = 1
    rollback: str | None = None
    service_domain: str | None = None
    service: str | None = None
    service_data_builder: ServiceDataBuilder | None = None
    requires_verification: bool = False
    requires_entity: bool = True
    plan_enabled: bool = True

    @property
    def invokes_service(self) -> bool:
        return self.service is not None

    def validate_arguments(self, arguments: dict[str, Any]) -> BaseModel:
        try:
            return self.argument_model.model_validate(arguments)
        except ValidationError as error:
            raise ToolValidationError("invalid_tool_arguments", str(error)) from error

    def build_service_data(self, arguments: BaseModel) -> dict[str, Any]:
        if not self.service_data_builder:
            raise ToolValidationError("tool_has_no_service", f"工具 {self.name} 不调用服务")
        return self.service_data_builder(arguments)


@dataclass(slots=True)
class ToolRegistry:
    """Lookup and validate only tools registered by EdgeHome code."""

    _tools: dict[str, ToolDefinition] = field(default_factory=dict)

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"重复注册工具: {definition.name}")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def definitions(self) -> Iterable[ToolDefinition]:
        return self._tools.values()


def build_default_tool_registry() -> ToolRegistry:
    """Create the small, explicit tool set used before model planning is enabled."""
    registry = ToolRegistry()
    controllable_domains = frozenset({"light", "switch", "fan", "media_player", "vacuum", "climate"})

    registry.register(
        ToolDefinition(
            name="get_home_state",
            description="读取所有已注册 EdgeHome 虚拟设备的当前状态",
            argument_model=EmptyArguments,
            allowed_domains=frozenset(),
            risk=ToolRisk.LOW,
            requires_entity=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="set_hvac_mode",
            description="设置 MQTT 空调模式",
            argument_model=HvacModeArguments,
            allowed_domains=frozenset({"climate"}),
            risk=ToolRisk.MEDIUM,
            service_domain="climate",
            service="set_hvac_mode",
            service_data_builder=_hvac_mode_service_data,
            requires_verification=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="get_room_state",
            description="读取一个已注册演示房间的设备状态",
            argument_model=RoomArguments,
            allowed_domains=frozenset(),
            risk=ToolRisk.LOW,
            requires_entity=False,
        )
    )

    registry.register(
        ToolDefinition(
            name="get_device_state",
            description="读取一个已暴露设备的当前状态",
            argument_model=EntityArguments,
            allowed_domains=frozenset(),
            risk=ToolRisk.LOW,
        )
    )
    registry.register(
        ToolDefinition(
            name="turn_on_device",
            description="打开允许控制的设备",
            argument_model=EntityArguments,
            allowed_domains=controllable_domains,
            risk=ToolRisk.MEDIUM,
            service_domain=None,
            service="turn_on",
            service_data_builder=_entity_service_data,
            requires_verification=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="turn_off_device",
            description="关闭允许控制的设备",
            argument_model=EntityArguments,
            allowed_domains=controllable_domains,
            risk=ToolRisk.MEDIUM,
            service_domain=None,
            service="turn_off",
            service_data_builder=_entity_service_data,
            requires_verification=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="set_light_brightness",
            description="设置灯光亮度百分比",
            argument_model=BrightnessArguments,
            allowed_domains=frozenset({"light"}),
            risk=ToolRisk.LOW,
            service_domain="light",
            service="turn_on",
            service_data_builder=_brightness_service_data,
            requires_verification=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="set_temperature",
            description="设置空调目标温度",
            argument_model=TemperatureArguments,
            allowed_domains=frozenset({"climate"}),
            risk=ToolRisk.MEDIUM,
            service_domain="climate",
            service="set_temperature",
            service_data_builder=_temperature_service_data,
            requires_verification=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="control_curtain",
            description="设置窗帘开合位置",
            argument_model=CurtainArguments,
            allowed_domains=frozenset({"cover"}),
            risk=ToolRisk.LOW,
            service_domain="cover",
            service="set_cover_position",
            service_data_builder=_curtain_service_data,
            requires_verification=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="activate_security_mode",
            description="开启专用的虚拟安防模式",
            argument_model=SecurityModeArguments,
            allowed_domains=frozenset({"input_boolean"}),
            risk=ToolRisk.MEDIUM,
            service_domain="input_boolean",
            service="turn_on",
            service_data_builder=_entity_service_data,
            requires_verification=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="create_delayed_task",
            description="创建已验证固定计划的延迟执行任务",
            argument_model=DelayedTaskArguments,
            allowed_domains=frozenset(),
            risk=ToolRisk.LOW,
            requires_entity=False,
            plan_enabled=False,
        )
    )
    return registry
