"""Render-time SVG fill substitution for chart row backgrounds.

The chart's two rows (precip + cloud) carry SVG patterns. We want the
pattern's visual density to scale with the actual data — a clear-skies
day should show barely-there cloud shapes, a 100% overcast day should
show strong cloud silhouettes. Same for rain.

CSS opacity can't do this cleanly (would dim the bars too, and the
4-bit quantize step doesn't dither so semi-transparent fills produce
visible banding). Instead we substitute the SVG fill colors at render
time and emit the result as a `data:` URL the template inlines as
`background-image`.

Five buckets — bucket 0 is nearly invisible, bucket 4 is the artist's
original palette. The bucket-to-intensity thresholds are tuned to match
how a non-technical reader would describe a forecast at a glance.

Same shifted-fill SVG covers both the day region (on `--panel` background)
and the night region (on `--night` background, a slightly darker shade).
The pattern continues across the day/night boundary; only the
background tint changes.
"""
from __future__ import annotations

import re
from base64 import b64encode
from functools import lru_cache
from pathlib import Path
from typing import Literal, NamedTuple

ASSETS = Path(__file__).parent / "assets"

# Original SVG palette: dark, mid, light. Day SVGs (bg-cloud, bg-rain,
# bg-snow) all draw from this set. Each bucket below maps these original
# fills onto a progressively lighter set so SVG content fades toward
# the panel color when intensity is low.
# 4-bit grayscale constraints (see bg-palette math in PR notes):
#  - 16 panel levels at integer multiples of 17; quantize snaps to nearest.
#  - Buckets must be monotonically lighter from 4 to 0.
#
# Each bucket carries TWO 3-slot palettes — one for the day region and
# one for the night region. The slots have unified semantics so a picker
# can show the same controls for both rain and cloud:
#
#   slot 0  →  darkest fill   →  cloud's #666666 / rain's #666666 (slashes)
#   slot 1  →  mid fill       →  cloud's #999999 / rain's #BBBBBB (drops)
#   slot 2  →  row bg + cloud's lightest fill (cloud's #BBBBBB)
#
# slot 2 is BOTH the row bg and the cloud's lightest fill so those
# highlights blend into bg. Rain only has two visible SVG fills
# (slashes + drops), so its slot 2 is pure bg. Cloud has three fills,
# with #BBB pinned to bg.
#
# Day vs night palettes let the picker tune contrast independently —
# the day region typically wants dark shapes on a near-white bg; the
# night region wants visible shapes against a slightly darker bg.


class BucketPalette(NamedTuple):
    day: tuple[str, str, str]
    night: tuple[str, str, str]


Mode = Literal["day", "night"]


BUCKET_PALETTES: list[BucketPalette] = [
    # 0 — Clear / Dry
    BucketPalette(
        day  =("#DDDDDD", "#EEEEEE", "#FFFFFF"),
        night=("#BBBBBB", "#CCCCCC", "#DDDDDD"),
    ),
    # 1 — Light
    BucketPalette(
        day  =("#CCCCCC", "#DDDDDD", "#EEEEEE"),
        night=("#AAAAAA", "#BBBBBB", "#CCCCCC"),
    ),
    # 2 — Moderate
    BucketPalette(
        day  =("#BBBBBB", "#DDDDDD", "#EEEEEE"),
        night=("#999999", "#BBBBBB", "#CCCCCC"),
    ),
    # 3 — Dense
    BucketPalette(
        day  =("#BBBBBB", "#CCCCCC", "#DDDDDD"),
        night=("#999999", "#AAAAAA", "#BBBBBB"),
    ),
    # 4 — Full
    BucketPalette(
        day  =("#AAAAAA", "#BBBBBB", "#DDDDDD"),
        night=("#888888", "#999999", "#BBBBBB"),
    ),
]

# Per-SVG: which artist fill maps to which slot.
_CLOUD_SLOTS = ("#666666", "#999999", "#BBBBBB")    # darkest, mid, lightest
_RAIN_SLOTS  = ("#666666", "#BBBBBB", None)         # slashes, drops, (bg only)
_SNOW_SLOTS  = ("#666666", "#999999", None)         # outline, body, (bg only)

_SLOT_KEYS_BY_SVG = {
    "bg-cloud.svg": _CLOUD_SLOTS,
    "bg-rain.svg":  _RAIN_SLOTS,
    "bg-snow.svg":  _SNOW_SLOTS,
}

# Back-compat shim for any callers still importing the old dict form
# (day-side mapping only). Each entry is the bucket's day palette
# expressed as the cloud-shaped fill mapping.
INTENSITY_BUCKETS: list[dict[str, str]] = [
    {_CLOUD_SLOTS[i]: p.day[i] for i in range(3)}
    for p in BUCKET_PALETTES
]


def cloud_bucket(avg_cloud_pct: int) -> int:
    """Map average cloud cover percentage to an intensity bucket.

    Boundaries match the chart's threshold labels: <20 (clear), 20-45
    (mostly sunny / partly), 45-70 (partly / mostly cloudy), 70-90
    (mostly cloudy), >=90 (overcast).
    """
    if avg_cloud_pct < 20:
        return 0
    if avg_cloud_pct < 45:
        return 1
    if avg_cloud_pct < 70:
        return 2
    if avg_cloud_pct < 90:
        return 3
    return 4


def precip_bucket(total_mm: float) -> int:
    """Map total daily precip (mm) to an intensity bucket.

    Thresholds calibrated against the chart's 10mm anchor scale: <1mm =
    effectively dry, 1-5mm = drizzle, 5-12mm = light/moderate rain,
    12-25mm = heavy, 25mm+ = severe event.
    """
    if total_mm < 1.0:
        return 0
    if total_mm < 5.0:
        return 1
    if total_mm < 12.0:
        return 2
    if total_mm < 25.0:
        return 3
    return 4


_FILL_RE = re.compile(r'fill="(#[0-9A-Fa-f]+)"')


@lru_cache(maxsize=48)
def shaded_svg_url(svg_filename: str, bucket: int, mode: Mode = "day") -> str:
    """Return a data: URL of the SVG with fills shifted to the given
    bucket's day or night palette.

    Single-pass regex substitution. Mapping is built from the SVG's slot
    keys (which artist fill maps to which palette slot) — different SVGs
    use different slots (cloud uses all 3, rain/snow use slots 0+1).
    """
    svg_path = ASSETS / svg_filename
    content = svg_path.read_text()
    palette_pair = BUCKET_PALETTES[bucket]
    palette = palette_pair.day if mode == "day" else palette_pair.night
    slot_keys = _SLOT_KEYS_BY_SVG.get(svg_filename, _CLOUD_SLOTS)

    mapping = {
        slot_keys[i].upper(): palette[i]
        for i in range(3) if slot_keys[i] is not None
    }

    def _swap(m: re.Match) -> str:
        color = m.group(1).upper()
        return f'fill="{mapping.get(color, m.group(1))}"'

    content = _FILL_RE.sub(_swap, content)
    encoded = b64encode(content.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def row_bg_color(bucket: int, mode: Mode = "day") -> str:
    """Return the row's background-color (slot 2) for a given bucket + mode."""
    palette_pair = BUCKET_PALETTES[bucket]
    palette = palette_pair.day if mode == "day" else palette_pair.night
    return palette[2]
