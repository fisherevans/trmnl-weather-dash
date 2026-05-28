"""Palette-driven quantization to e-ink panel levels.

Each TRMNL device has a fixed palette of greys it can actually display.
The renderer produces an RGB screenshot; this module snaps each pixel to
the nearest level for the configured palette so what the renderer sees
matches what the panel will show.

Adding a new palette: register the level list in PALETTES. Levels are
0..255 grey values; the quantizer builds a 256-entry LUT that snaps each
input value to the nearest registered level. For e-ink devices that
benefit from dithering (1-bit), set `dither=True` on the entry.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class Palette:
    levels: tuple[int, ...]    # grey values 0..255 the device can display
    dither: bool = False       # apply Floyd-Steinberg before snapping


# 4-bit greyscale: 16 levels evenly spaced 0..255. Drives the TRMNL X 10.3".
_FOUR_BIT_GREY = Palette(
    levels=tuple(round(i * 255 / 15) for i in range(16)),
    dither=False,
)


PALETTES: dict[str, Palette] = {
    "4bit-grey": _FOUR_BIT_GREY,
}


def quantize(img: Image.Image, palette: str) -> Image.Image:
    """Snap each pixel of `img` to the nearest level in the named palette.

    Returns a new mode-"L" image (single-channel grey). Raises KeyError
    on an unknown palette name - palettes are device-specific and adding
    a new one is a deliberate act (see PALETTES).
    """
    spec = PALETTES[palette]
    grey = img.convert("L")
    if spec.dither:
        # Floyd-Steinberg via the 'P' mode path. Build a palette image with
        # the device's grey levels and let Pillow handle the diffusion.
        # Reserved for future 1-bit support; 4-bit + 2-bit don't dither.
        raise NotImplementedError("dithered palettes not implemented yet")
    lut = _nearest_lut(spec.levels)
    return grey.point(lut)


def _nearest_lut(levels: tuple[int, ...]) -> list[int]:
    """Build a 256-entry LUT mapping each 0..255 input to the nearest level."""
    sorted_levels = sorted(levels)
    out: list[int] = []
    for v in range(256):
        # Linear scan is fine - palettes have <= 16 entries.
        best = sorted_levels[0]
        best_d = abs(v - best)
        for lv in sorted_levels[1:]:
            d = abs(v - lv)
            if d < best_d:
                best, best_d = lv, d
        out.append(best)
    return out
