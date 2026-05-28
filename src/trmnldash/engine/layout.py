"""Dashboard layout types.

A dashboard is a device profile (canvas size + palette + rotation) plus
a layout tree. Layout nodes are either a `PanelSlot` (a leaf naming a
panel by module name) or a stack (`VStack`/`HStack`) of child nodes
with optional padding, gap, and a separator between children.

YAML shape - the discriminating key (panel / vstack / hstack) marks
the node type, so a layout reads naturally:

    layout:
      vstack:
        padding: 8
        separator: {thickness: 2, color: '#000'}
        children:
          - panel: weather_compact
            size: 1fr
            config: {...}
          - panel: calendar_agenda
            size: 1fr
            config: {...}
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceProfile(_Strict):
    """Target device: canvas dimensions in panel-orientation pixels, palette
    name (key into engine.quantize.PALETTES), and a rotation applied after
    composition so portrait layouts can render onto landscape-native panels."""
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)
    palette: str = "4bit-grey"
    rotate: Literal[0, 90, 180, 270] = 0


class Separator(_Strict):
    """A line drawn between siblings in a stack. `color` is any value
    Pillow accepts (hex string, integer grey, etc.)."""
    thickness: int = Field(..., gt=0)
    color: str = "#000000"


_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)(px|fr|%)?$")


class Size:
    """Parsed `size:` value: either a pixel count, a fractional unit, or
    a percentage of the parent's main axis. The compose pass resolves
    fr/% against the available space after subtracting padding + gaps."""

    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value: float):
        self.kind = kind   # "px" | "fr" | "%"
        self.value = value

    @classmethod
    def parse(cls, raw: Any) -> "Size":
        if isinstance(raw, (int, float)):
            return cls("px", float(raw))
        if isinstance(raw, str):
            m = _SIZE_RE.match(raw.strip())
            if not m:
                raise ValueError(f"unrecognized size: {raw!r}")
            value = float(m.group(1))
            unit = m.group(2) or "px"
            return cls(unit, value)
        raise TypeError(f"size must be int/float/str, got {type(raw).__name__}")

    def __repr__(self) -> str:
        return f"Size({self.value!r}{self.kind})"


class PanelSlot(_Strict):
    """A leaf in the layout tree. References a panel by its module name
    (`trmnldash.panels.<name>`). The `config` dict is opaque here; the
    config loader validates it against the panel's `config_schema` once
    the panel is looked up.
    """
    panel: str
    size: Any = "1fr"
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _parse_size(self) -> "PanelSlot":
        # Validate size string is parseable; keep the raw form so the
        # config dump round-trips, parse to a Size only when composing.
        Size.parse(self.size)
        return self

    @property
    def parsed_size(self) -> Size:
        return Size.parse(self.size)


class StackBody(_Strict):
    """Shared fields for vstack/hstack body."""
    children: list["Layout"]
    padding: int = 0
    gap: int = 0
    separator: Separator | None = None


class VStack(_Strict):
    vstack: StackBody


class HStack(_Strict):
    hstack: StackBody


Layout = Annotated[
    Union[PanelSlot, VStack, HStack],
    Field(discriminator=None),   # discriminate by which top-level key is set
]

# Forward-ref resolution for the recursive children list.
StackBody.model_rebuild()


__all__ = [
    "DeviceProfile",
    "HStack",
    "Layout",
    "PanelSlot",
    "Separator",
    "Size",
    "StackBody",
    "VStack",
]
