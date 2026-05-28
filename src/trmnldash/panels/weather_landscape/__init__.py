"""Full-screen weather panel for the TRMNL X 10.3" e-ink panel.

Renders a 1872x1404 landscape weather dashboard - hourly chart with
temperature line + precip probability bars + cloud cover overlay,
plus an OUTSIDE / INSIDE summary stack and TODAY / TONIGHT prose.

This is the panel that was the entire project before the suite refactor;
it's now one panel among the (planned) several this package will host.
"""
from .aggregate import build_context, compute_regions
from .render import (
    RENDER_SPEC,
    render_from_json,
    render_html,
    render_to_image,
    render_to_png,
)

__all__ = [
    "RENDER_SPEC",
    "build_context",
    "compute_regions",
    "render_from_json",
    "render_html",
    "render_to_image",
    "render_to_png",
]
