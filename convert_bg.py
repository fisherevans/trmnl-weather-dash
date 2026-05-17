#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10", "numpy>=1.26"]
# ///
"""Convert the artist-supplied weather background images to 2-tone greys on
transparent backgrounds.

- bg-cloud-raw.png is a blue 2-tone cloud illustration with a baked-in
  transparency checkerboard. We strip the checkerboard to actual alpha=0,
  map the light blue (cloud highlight) to a light grey, and the medium
  blue (cloud body) to a darker grey.

- bg-rain-raw.png is a teal-on-teal seamless rain tile. The dark teal
  background drops out to transparent, the mid-teal slashes pick up the
  same darker grey as the cloud body, and the cream drops pick up the
  same light grey as the cloud highlight. Result: a tile that sits next
  to the cloud illustration with a consistent two-grey palette.

Output: bg-cloud.png, bg-rain.png in the script directory. Tune the
*_BUCKETS tables below if the source images change.
"""
from __future__ import annotations
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).parent

LIGHT_GREY = 0xC8   # 200 - cloud highlights, rain drops
MID_GREY   = 0x80   # 128 - cloud body, rain slashes

# Each entry: (R, G, B, target_grey_or_None). Source pixels are mapped to
# the nearest centre by squared RGB distance; the bucket's target becomes
# either a fully opaque grey or a fully transparent pixel.
CLOUD_BUCKETS: list[tuple[int, int, int, int | None]] = [
    (208, 208, 208, None),        # checkerboard dark square
    (224, 224, 224, None),        # checkerboard mid-tone (anti-aliased edges)
    (240, 240, 240, None),        # checkerboard light square
    (160, 208, 240, LIGHT_GREY),  # cloud highlight
    (64,  144, 224, MID_GREY),    # cloud body
]

RAIN_BUCKETS: list[tuple[int, int, int, int | None]] = [
    (80,  176, 160, None),        # dark teal background
    (112, 192, 176, MID_GREY),    # mid-teal slashes
    (208, 224, 224, LIGHT_GREY),  # cream drops
]


def remap(img: Image.Image, buckets: list[tuple[int, int, int, int | None]]) -> Image.Image:
    arr = np.asarray(img.convert("RGB"), dtype=np.int32)
    flat = arr.reshape(-1, 3)
    centres = np.array([(r, g, b) for r, g, b, _ in buckets], dtype=np.int32)
    targets = [t for *_, t in buckets]
    diffs = flat[:, None, :] - centres[None, :, :]
    best = (diffs * diffs).sum(axis=2).argmin(axis=1)
    out = np.zeros((flat.shape[0], 4), dtype=np.uint8)
    for i, t in enumerate(targets):
        mask = best == i
        if t is None:
            out[mask] = (0, 0, 0, 0)
        else:
            out[mask] = (t, t, t, 255)
    return Image.fromarray(out.reshape(arr.shape[0], arr.shape[1], 4), mode="RGBA")


def main() -> None:
    remap(Image.open(ROOT / "bg-cloud-raw.png"), CLOUD_BUCKETS).save(ROOT / "bg-cloud.png")
    remap(Image.open(ROOT / "bg-rain-raw.png"),  RAIN_BUCKETS).save(ROOT / "bg-rain.png")
    print("wrote bg-cloud.png, bg-rain.png")


if __name__ == "__main__":
    main()
