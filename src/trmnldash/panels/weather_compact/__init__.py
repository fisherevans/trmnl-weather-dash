"""Compact weather panel for one vertical half of the TRMNL OG (400x480).

Sibling to weather_landscape: same source pipeline, much smaller render
target, no chart, no indoor stack. Shows current condition icon + temp,
today's high / low, and a short forecast prose chunk. Designed to live
alongside calendar_agenda in an hstack on the OG combined dashboard.
"""
from .config import WeatherCompactConfig
from .live import build_live_context
from .render import RENDER_SPEC, render_html, render_to_image
from ...engine.panel import Panel


PANEL = Panel(
    name="weather_compact",
    render_spec=RENDER_SPEC,
    config_schema=WeatherCompactConfig,
    build_live_context=build_live_context,
    render_to_image=render_to_image,
)


__all__ = [
    "PANEL",
    "RENDER_SPEC",
    "WeatherCompactConfig",
    "build_live_context",
    "render_html",
    "render_to_image",
]
