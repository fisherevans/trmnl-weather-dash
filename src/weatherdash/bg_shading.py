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

from base64 import b64encode
from functools import lru_cache
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"

# Original SVG palette: dark, mid, light. Day SVGs (bg-cloud, bg-rain,
# bg-snow) all draw from this set. Each bucket below maps these original
# fills onto a progressively lighter set so SVG content fades toward
# the panel color when intensity is low.
# 4-bit grayscale constraints (see bg-palette math in PR notes):
#  - 16 panel levels at integer multiples of 17; quantize snaps to nearest.
#  - Day bg (--panel=#ECECEC) post-quantize sits at level 14 (#EEE).
#  - Night bg (--night=#D8D8D8) post-quantize sits at level 13 (#DDD).
#  - For a fill to render visibly in BOTH day and night regions it must
#    quantize to level ≤ 12 (#CCC). Anything at level 13 vanishes on the
#    night-shade; anything at level 14 vanishes on the day bg too.
#  - Each bucket must have 3 distinct quantize levels so the SVG's three
#    artist fills don't collapse into 2 colors.
#
# The progression below: darkest fill steps from level 6 (bucket 4) up to
# level 10 (bucket 0); mid + light stagger one level each. Monotonic +
# distinct + visible-everywhere.
INTENSITY_BUCKETS: list[dict[str, str]] = [
    # 0 — barely visible. Levels 10, 11, 12 — all 2-4 steps darker than the
    # day bg (level 14) and 1-3 steps darker than the night bg (level 13).
    {"#666666": "#AAAAAA", "#999999": "#BBBBBB", "#BBBBBB": "#CCCCCC"},
    # 1 — light. Levels 9, 11, 12.
    {"#666666": "#999999", "#999999": "#BBBBBB", "#BBBBBB": "#CCCCCC"},
    # 2 — moderate. Levels 8, 10, 12.
    {"#666666": "#888888", "#999999": "#AAAAAA", "#BBBBBB": "#CCCCCC"},
    # 3 — dense. Levels 7, 9, 11.
    {"#666666": "#777777", "#999999": "#999999", "#BBBBBB": "#BBBBBB"},
    # 4 — full strength (artist original). Levels 6, 9, 11.
    {"#666666": "#666666", "#999999": "#999999", "#BBBBBB": "#BBBBBB"},
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


@lru_cache(maxsize=24)
def shaded_svg_url(svg_filename: str, bucket: int) -> str:
    """Return a data: URL of the SVG with fills shifted to the given bucket.

    Cached because the inputs are a tiny finite set (3 base SVGs × 5
    buckets) and each render of the dashboard hits the same combos.
    """
    svg_path = ASSETS / svg_filename
    content = svg_path.read_text()
    for orig, replacement in INTENSITY_BUCKETS[bucket].items():
        content = content.replace(f'fill="{orig}"', f'fill="{replacement}"')
    encoded = b64encode(content.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
