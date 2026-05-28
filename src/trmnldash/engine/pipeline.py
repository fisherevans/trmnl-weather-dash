"""Dashboard pipeline: walk the layout, build each panel's context,
render, compose, quantize, save.

Engine-level orchestrator. Knows nothing weather-specific - each panel
owns its own fetch + aggregate via `Panel.build_live_context`. The
shape that flows through this function is:

    Config -> [for each PanelSlot: panel.build_live_context() -> ctx,
               panel.render_to_image(ctx, w, h) -> Image]
            -> render_layout(...) -> composed canvas
            -> rotate (if device.rotate)
            -> quantize(palette)
            -> save
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .compose import render_layout
from .layout import PanelSlot
from .panel import lookup
from .quantize import quantize as quantize_image
from ..config import Config

logger = logging.getLogger(__name__)


@dataclass
class PanelTiming:
    name: str
    fetch_ms: float
    render_ms: float


@dataclass
class RenderStats:
    output_path: Path | None = None
    total_ms: float = 0.0
    compose_ms: float = 0.0
    quantize_ms: float = 0.0
    panels: list[PanelTiming] = field(default_factory=list)


def run_once(config: Config, out_path: Path, *, quantize: bool = True) -> RenderStats:
    """Render the dashboard once and save. Returns per-stage timings.

    Failure semantics:
    - A panel raising during fetch propagates - if the panel can't render,
      the dashboard can't render. (Panels are expected to handle their own
      soft failures internally; only hard failures escape.)
    - Quantize is optional so callers iterating on layout can skip the
      level snap for speed.
    """
    stats = RenderStats(output_path=out_path)
    t_total = time.monotonic()

    device = config.dashboard.device

    def build_panel(slot: PanelSlot, w: int, h: int) -> Image.Image:
        panel = lookup(slot.panel)
        t0 = time.monotonic()
        ctx = panel.build_live_context(slot.config)
        fetch_ms = (time.monotonic() - t0) * 1000
        t0 = time.monotonic()
        img = panel.render_to_image(ctx, width=w, height=h)
        render_ms = (time.monotonic() - t0) * 1000
        stats.panels.append(PanelTiming(slot.panel, fetch_ms, render_ms))
        return img

    t0 = time.monotonic()
    canvas = render_layout(
        config.dashboard.layout,
        canvas_size=(device.width, device.height),
        build_panel=build_panel,
    )
    if device.rotate:
        # Pillow rotates counter-clockwise; the device profile's `rotate`
        # is clockwise degrees, so negate.
        canvas = canvas.rotate(-device.rotate, expand=True)
    stats.compose_ms = (time.monotonic() - t0) * 1000

    if quantize:
        t0 = time.monotonic()
        canvas = quantize_image(canvas, device.palette)
        stats.quantize_ms = (time.monotonic() - t0) * 1000

    canvas.save(out_path)
    stats.total_ms = (time.monotonic() - t_total) * 1000
    return stats
