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

ASSETS = Path(__file__).parent / "assets"

# Original SVG palette: dark, mid, light. Day SVGs (bg-cloud, bg-rain,
# bg-snow) all draw from this set. Each bucket below maps these original
# fills onto a progressively lighter set so SVG content fades toward
# the panel color when intensity is low.
# 4-bit grayscale constraints (see bg-palette math in PR notes):
#  - 16 panel levels at integer multiples of 17; quantize snaps to nearest.
#  - Day bg (--panel=#ECECEC) post-quantize sits at level 14 (#EEE).
#  - Night bg (--night=#D8D8D8) post-quantize sits at level 13 (#DDD).
#  - Single palette shared across day+night and across rain+cloud rows;
#    intensity bucket drives the darkness, the day/night region's bg-color
#    tint provides the time-of-day cue.
#  - Buckets must be monotonically lighter from 4 to 0. Each bucket has
#    three fills but the lightest bucket (0) may collapse two fills onto
#    the same level — at "barely visible" the shape distinction stops
#    mattering and a near-invisible pattern reads as one wash anyway.
#
# This palette was tuned to keep bucket 1 ("Partly Cloudy" / "Drizzle")
# visibly distinct from bucket 0 ("Clear" / "Dry"). User feedback: at
# 0mm rain the row should read as almost-empty, while a partly-cloudy
# 30% cover should clearly show pattern.
INTENSITY_BUCKETS: list[dict[str, str]] = [
    # 0 — barely visible. Levels 12, 13, 13 — only 1-2 steps below the
    # day bg (14). On night bg (13) the #DDD fills are invisible and
    # the row reads as a single faint #CCC wash. Intentional: bucket 0
    # is "no data", the pattern shouldn't compete with the message.
    {"#666666": "#CCCCCC", "#999999": "#DDDDDD", "#BBBBBB": "#DDDDDD"},
    # 1 — light. Levels 10, 11, 12.
    {"#666666": "#AAAAAA", "#999999": "#BBBBBB", "#BBBBBB": "#CCCCCC"},
    # 2 — moderate. Levels 9, 11, 12.
    {"#666666": "#999999", "#999999": "#BBBBBB", "#BBBBBB": "#CCCCCC"},
    # 3 — dense. Levels 8, 10, 11.
    {"#666666": "#888888", "#999999": "#AAAAAA", "#BBBBBB": "#BBBBBB"},
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


_FILL_RE = re.compile(r'fill="(#[0-9A-Fa-f]+)"')


@lru_cache(maxsize=24)
def shaded_svg_url(svg_filename: str, bucket: int) -> str:
    """Return a data: URL of the SVG with fills shifted to the given bucket.

    Implemented as a single-pass regex substitution. Earlier versions did
    sequential `str.replace` calls per (orig, repl) pair — that cascades
    when a target color is also a source key in the same mapping. For the
    cloud SVG (which carries all three artist fills #666/#999/#BBB), the
    bucket-1 mapping {#666→#999, #999→#BBB, #BBB→#CCC} collapsed every
    fill to #CCC because the post-stage-1 #999 got caught by stage 2,
    which then got caught by stage 3. The rain SVG escaped that bug
    because it only has two of the three source fills.

    Cached because the inputs are a tiny finite set (3 base SVGs × 5
    buckets) and each render of the dashboard hits the same combos.
    """
    svg_path = ASSETS / svg_filename
    content = svg_path.read_text()
    mapping = INTENSITY_BUCKETS[bucket]
    # Normalize keys to uppercase for case-insensitive lookup.
    mapping_upper = {k.upper(): v for k, v in mapping.items()}

    def _swap(m: re.Match) -> str:
        color = m.group(1).upper()
        return f'fill="{mapping_upper.get(color, m.group(1))}"'

    content = _FILL_RE.sub(_swap, content)
    encoded = b64encode(content.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
