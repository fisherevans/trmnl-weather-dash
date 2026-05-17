"""Unit tests for bg_shading.

The interesting properties are (per the math write-up in bg_shading.py):
- Each bucket maps to 3 *distinct* quantize levels (no collapse to 2 colors).
- Every fill is at quantize level <= 12 so it's visible against both the
  day bg (level 14) and the night-shade (level 13).
- Buckets are monotonically lighter from 4 to 0.
"""
from __future__ import annotations

import pytest

from weatherdash.bg_shading import (INTENSITY_BUCKETS, cloud_bucket,
                                    precip_bucket, shaded_svg_url)


def _hex_to_level(hex_str: str) -> int:
    """Map a hex like '#AAA' or '#AAAAAA' to its 4-bit quantize level."""
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    val = int(h[:2], 16)
    return round(val / 17)


@pytest.mark.parametrize("bucket_idx", range(5))
def test_bucket_has_three_distinct_quantize_levels(bucket_idx):
    """Each bucket's 3 fills must land at 3 different quantize levels."""
    mapping = INTENSITY_BUCKETS[bucket_idx]
    levels = sorted(_hex_to_level(v) for v in mapping.values())
    assert len(set(levels)) == 3, (
        f"bucket {bucket_idx} fills {mapping} collapse to {len(set(levels))} levels"
    )


@pytest.mark.parametrize("bucket_idx", range(5))
def test_bucket_fills_visible_on_both_bg(bucket_idx):
    """All fills must quantize to level <= 12 so they're visible on both
    the day bg (level 14, panel) and the night-shade (level 13)."""
    for fill in INTENSITY_BUCKETS[bucket_idx].values():
        assert _hex_to_level(fill) <= 12, (
            f"bucket {bucket_idx} fill {fill} quantizes too light to render"
            " against the night-shade"
        )


def test_buckets_progress_monotonically_lighter_to_subtle():
    """As bucket index drops from 4 to 0, the DARKEST fill in each bucket
    must get lighter (no regressions)."""
    darkest_levels = []
    for b in INTENSITY_BUCKETS:
        # The original artist palette puts the darkest fill on #666666.
        # That key tells us where the "darkest visible" lands after shift.
        darkest_levels.append(_hex_to_level(b["#666666"]))
    # bucket 4 (last) is the artist original (darkest), bucket 0 the lightest
    for i in range(len(darkest_levels) - 1):
        assert darkest_levels[i] >= darkest_levels[i + 1], (
            f"bucket {i} darkest ({darkest_levels[i]}) should be ≥ "
            f"bucket {i+1} darkest ({darkest_levels[i+1]})"
        )


@pytest.mark.parametrize("avg_cloud,expected", [
    (0, 0), (10, 0), (19, 0),     # < 20 -> bucket 0
    (20, 1), (44, 1),              # 20-45 -> 1
    (45, 2), (69, 2),              # 45-70 -> 2
    (70, 3), (89, 3),              # 70-90 -> 3
    (90, 4), (100, 4),             # >= 90 -> 4
])
def test_cloud_bucket_thresholds(avg_cloud, expected):
    assert cloud_bucket(avg_cloud) == expected


@pytest.mark.parametrize("total_mm,expected", [
    (0.0, 0), (0.9, 0),            # < 1 -> 0
    (1.0, 1), (4.9, 1),            # 1-5 -> 1
    (5.0, 2), (11.9, 2),           # 5-12 -> 2
    (12.0, 3), (24.9, 3),          # 12-25 -> 3
    (25.0, 4), (100.0, 4),         # >= 25 -> 4
])
def test_precip_bucket_thresholds(total_mm, expected):
    assert precip_bucket(total_mm) == expected


def test_shaded_svg_url_returns_data_url():
    url = shaded_svg_url("bg-cloud.svg", 2)
    assert url.startswith("data:image/svg+xml;base64,"), url[:60]


def test_shaded_svg_url_actually_swaps_fills():
    """A lower bucket should produce different SVG bytes than a higher one."""
    a = shaded_svg_url("bg-cloud.svg", 0)
    b = shaded_svg_url("bg-cloud.svg", 4)
    assert a != b, "bucket 0 and bucket 4 should produce different SVG output"


@pytest.mark.parametrize("bucket_idx", range(5))
def test_shaded_cloud_svg_retains_three_distinct_fills(bucket_idx):
    """Regression: an earlier sequential-str.replace implementation
    cascaded — if bucket N mapped #666→#999 and #999→#BBB in the same
    pass, the post-step-1 #999 got caught by step 2 and collapsed.
    bg-cloud.svg uses all three artist fills (#666/#999/#BBB), so the
    output must still contain 3 distinct fill values per bucket."""
    import base64
    import re
    url = shaded_svg_url("bg-cloud.svg", bucket_idx)
    # Decode the data: URL back to SVG text.
    payload = url.split(",", 1)[1]
    svg_text = base64.b64decode(payload).decode("utf-8")
    fills = set(re.findall(r'fill="(#[0-9A-Fa-f]+)"', svg_text))
    assert len(fills) == 3, (
        f"bucket {bucket_idx} produced {len(fills)} distinct fills: {fills}"
    )
