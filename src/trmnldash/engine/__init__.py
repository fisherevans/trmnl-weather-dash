"""Rendering engine: panel-agnostic HTML -> Chromium -> PIL.Image -> quantize.

Panels supply a template + assets + a build_context function. The engine
turns that into a final PNG. The engine knows nothing about weather, calendars,
or any specific dashboard - it's the generic plumbing every panel runs through.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RenderSpec:
    """A panel's natural render size + the device palette it targets.

    `width`/`height` are the chromium viewport dimensions. `palette` is a
    key into engine.quantize.PALETTES. The dashboard layer (phase 3) may
    feed a panel a different size if the layout calls for it; for now
    each panel renders at its declared size and the result is quantized
    end-to-end at this same palette.
    """
    width: int
    height: int
    palette: str
