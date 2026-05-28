"""Panel manifest + name-based lookup.

A panel is a self-contained renderable thing: its module exposes a
`PANEL` constant carrying the panel's render spec, the pydantic schema
for its config block, and the functions the engine drives to build
its render context and turn that context into an image.

Lookup is lazy: `lookup("weather_landscape")` imports
`trmnldash.panels.weather_landscape` and pulls its `PANEL` attribute.
No registry, no decorator dance - the module name IS the panel name.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable

from PIL import Image
from pydantic import BaseModel

from . import RenderSpec


@dataclass(frozen=True)
class Panel:
    """A panel's public contract.

    `name` matches the module name under `trmnldash.panels`.

    `render_spec` is the panel's natural size + the device palette the
    panel was designed for. The compose pass may render the panel at a
    different size when the layout slot doesn't match; whether that
    works depends on whether the panel's template is responsive.

    `config_schema` is a pydantic model that validates the panel's
    `config:` block from the dashboard YAML.

    `build_live_context(panel_config) -> dict` fetches the panel's
    data sources and returns the render context dict.

    `render_to_image(context, *, width, height) -> Image` renders the
    context to a PIL.Image of the requested size.
    """
    name: str
    render_spec: RenderSpec
    config_schema: type[BaseModel]
    build_live_context: Callable[[Any], dict]
    render_to_image: Callable[..., Image.Image]


class PanelLookupError(Exception):
    """Raised when a panel name doesn't resolve to a panels.<name> module
    with a `PANEL` attribute."""


def lookup(name: str) -> Panel:
    """Import `trmnldash.panels.<name>` and return its `PANEL` constant.

    Lazy import keeps cold-start cheap for dashboards that use only a
    subset of installed panels.
    """
    try:
        mod = importlib.import_module(f"trmnldash.panels.{name}")
    except ImportError as e:
        raise PanelLookupError(f"unknown panel {name!r}: {e}") from e
    panel = getattr(mod, "PANEL", None)
    if not isinstance(panel, Panel):
        raise PanelLookupError(
            f"trmnldash.panels.{name} does not expose a `PANEL` constant"
        )
    return panel
