"""Full-screen weather panel for the TRMNL X 10.3" e-ink panel.

Renders a 1872x1404 landscape weather dashboard - hourly chart with
temperature line + precip probability bars + cloud cover overlay,
plus an OUTSIDE / INSIDE summary stack and TODAY / TONIGHT prose.

This is the panel that was the entire project before the suite refactor;
it's now one panel among the (planned) several this package will host.

Exposes a `PANEL` constant so `engine.panel.lookup("weather_landscape")`
resolves to this panel from the dashboard YAML.
"""
from .aggregate import build_context, compute_regions
from .config import LocationConfig, WeatherLandscapeConfig
from .live import build_live_context
from .render import (
    RENDER_SPEC,
    render_from_json,
    render_html,
    render_to_image,
    render_to_png,
)
from ...engine.panel import Panel


PANEL = Panel(
    name="weather_landscape",
    render_spec=RENDER_SPEC,
    config_schema=WeatherLandscapeConfig,
    build_live_context=build_live_context,
    render_to_image=render_to_image,
)


__all__ = [
    "PANEL",
    "RENDER_SPEC",
    "LocationConfig",
    "WeatherLandscapeConfig",
    "build_context",
    "build_live_context",
    "compute_regions",
    "render_from_json",
    "render_html",
    "render_to_image",
    "render_to_png",
]
