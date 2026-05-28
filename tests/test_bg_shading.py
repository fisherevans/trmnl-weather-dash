"""Unit tests for bg_shading.

The interesting properties are (per the math write-up in bg_shading.py):
- Each bucket maps to 3 *distinct* quantize levels (no collapse to 2 colors).
- Every fill is at quantize level <= 12 so it's visible against both the
  day bg (level 14) and the night-shade (level 13).
- Buckets are monotonically lighter from 4 to 0.
"""
from __future__ import annotations

import pytest

from trmnldash.bg_shading import (BUCKET_PALETTES, INTENSITY_BUCKETS,
                                    cloud_bucket, precip_bucket, row_bg_color,
                                    shaded_svg_url)


def _hex_to_level(hex_str: str) -> int:
    """Map a hex like '#AAA' or '#AAAAAA' to its 4-bit quantize level."""
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    val = int(h[:2], 16)
    return round(val / 17)


@pytest.mark.parametrize("bucket_idx", range(1, 5))
def test_non_zero_bucket_has_three_distinct_quantize_levels(bucket_idx):
    """Each bucket >= 1's 3 fills must land at 3 different quantize levels.

    Bucket 0 is the "barely visible" case and may intentionally collapse
    two fills onto the same level — at that intensity the pattern reads
    as a single faint wash regardless, so shape distinction stops
    mattering.
    """
    mapping = INTENSITY_BUCKETS[bucket_idx]
    levels = sorted(_hex_to_level(v) for v in mapping.values())
    assert len(set(levels)) == 3, (
        f"bucket {bucket_idx} fills {mapping} collapse to {len(set(levels))} levels"
    )


@pytest.mark.parametrize("bucket_idx", range(1, 5))
@pytest.mark.parametrize("mode", ["day", "night"])
def test_shape_fills_visible_against_own_bg(bucket_idx, mode):
    """Slots 0 and 1 (the SVG shape colors) must quantize to a level
    *different from* slot 2 (the row's bg). If a shape fill matches the
    bg level, those shapes disappear after the 4-bit quantize step.

    Bucket 0 is intentionally exempt: at "barely there" intensity the
    artist palette collapses slot 1 onto slot 2 so the pattern reads as
    a single faint wash. Shape distinction stops mattering at that
    level."""
    palette = getattr(BUCKET_PALETTES[bucket_idx], mode)
    bg_level = _hex_to_level(palette[2])
    for slot in (0, 1):
        assert _hex_to_level(palette[slot]) != bg_level, (
            f"{mode} bucket {bucket_idx} slot {slot} = {palette[slot]} "
            f"matches bg level {bg_level} — shapes will be invisible"
        )


def test_buckets_progress_monotonically_lighter_to_subtle():
    """As bucket index drops from 4 to 0, slot 0 (the darkest fill) in
    both day AND night palettes must get lighter (no regressions)."""
    for mode in ("day", "night"):
        levels = [_hex_to_level(getattr(p, mode)[0]) for p in BUCKET_PALETTES]
        for i in range(len(levels) - 1):
            assert levels[i] >= levels[i + 1], (
                f"{mode} bucket {i} darkest ({levels[i]}) should be ≥ "
                f"{i+1} ({levels[i+1]})"
            )


def test_row_bg_color_returns_slot_2_per_mode():
    """row_bg_color(bucket, mode) must equal palette[2] of the matching mode."""
    for b in range(5):
        assert row_bg_color(b, "day")   == BUCKET_PALETTES[b].day[2]
        assert row_bg_color(b, "night") == BUCKET_PALETTES[b].night[2]


def test_shaded_svg_url_differs_between_day_and_night():
    """A bucket's day and night palettes are distinct (they at least have
    different bg colors), so the shaded SVG content should differ too."""
    for b in range(5):
        d = shaded_svg_url("bg-cloud.svg", b, "day")
        n = shaded_svg_url("bg-cloud.svg", b, "night")
        assert d != n, f"bucket {b}: day and night URLs should differ"


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


@pytest.mark.parametrize("bucket_idx", range(1, 5))
def test_shaded_cloud_svg_retains_three_distinct_fills(bucket_idx):
    """Regression: an earlier sequential-str.replace implementation
    cascaded — if bucket N mapped #666→#999 and #999→#BBB in the same
    pass, the post-step-1 #999 got caught by step 2 and collapsed.
    bg-cloud.svg uses all three artist fills (#666/#999/#BBB), so a
    non-zero bucket's output must still contain 3 distinct fill values.
    (Bucket 0 intentionally collapses — see INTENSITY_BUCKETS docs.)"""
    import base64
    import re
    url = shaded_svg_url("bg-cloud.svg", bucket_idx)
    payload = url.split(",", 1)[1]
    svg_text = base64.b64decode(payload).decode("utf-8")
    fills = set(re.findall(r'fill="(#[0-9A-Fa-f]+)"', svg_text))
    assert len(fills) == 3, (
        f"bucket {bucket_idx} produced {len(fills)} distinct fills: {fills}"
    )
