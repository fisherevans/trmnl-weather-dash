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
INTENSITY_BUCKETS: list[dict[str, str]] = [
    # 0 — barely visible. Just a hint that the row carries a pattern.
    {"#666666": "#DDDDDD", "#999999": "#EEEEEE", "#BBBBBB": "#EEEEEE"},
    # 1 — light, "subtle but present".
    {"#666666": "#BBBBBB", "#999999": "#DDDDDD", "#BBBBBB": "#EEEEEE"},
    # 2 — moderate.
    {"#666666": "#999999", "#999999": "#BBBBBB", "#BBBBBB": "#DDDDDD"},
    # 3 — dense.
    {"#666666": "#777777", "#999999": "#AAAAAA", "#BBBBBB": "#CCCCCC"},
    # 4 — full strength (original artist palette).
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
