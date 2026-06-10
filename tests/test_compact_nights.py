"""Unit tests for weather_compact night-block shading (_compute_nights).

The regression these guard: the original single-rect implementation mapped
only the next_sunset/next_sunrise pair, so a chart that opened after dark
(e.g. a 9 PM render, where the next sunrise precedes the next sunset) lost
its leading overnight block - it shaded just the later evening block, or
nothing when the next sunset fell past the window. The is_night-driven
version emits one rect per contiguous night run, so any number of blocks
render.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from trmnldash.panels.weather_compact.render import (_LEFT_AXIS_X, _RIGHT_AXIS_X,
                                                     _compute_nights)


def _hourly(start: str, hours: int, sunrise: datetime, sunset: datetime) -> list[dict]:
    """Build `hours` hourly dicts from `start`, flagging each is_night by
    whether it falls before the morning sunrise or at/after the evening
    sunset."""
    base = datetime.fromisoformat(start)
    out = []
    for k in range(hours):
        t = base + timedelta(hours=k)
        out.append({
            "datetime": t.isoformat(),
            "temp_f":   60,
            "is_night": t < sunrise or t >= sunset,
        })
    return out


def _step(n: int) -> float:
    return (_RIGHT_AXIS_X - _LEFT_AXIS_X) / n


def test_single_evening_block_pins_to_right_edge():
    """Daytime start, sunset inside the window, sunrise beyond it: one
    block from the sunset to the right axis."""
    sunrise = datetime.fromisoformat("2026-05-30T05:28:00-04:00")  # next day, out of window
    sunset = datetime.fromisoformat("2026-05-29T20:15:00-04:00")
    hourly = _hourly("2026-05-29T11:00:00-04:00", 17, sunrise, sunset)
    nights = _compute_nights(hourly, {
        "next_sunrise": sunrise.isoformat(),
        "next_sunset":  sunset.isoformat(),
    }, _step(len(hourly)))
    assert len(nights) == 1
    assert abs(nights[0]["x"] + nights[0]["w"] - _RIGHT_AXIS_X) < 0.01


def test_chart_opens_at_night_pins_leading_block_to_left_edge():
    """9 PM start: the leading overnight block must start at the left axis,
    not vanish. This is the core bug - the old code had no way to express a
    night that was already underway at hour 0."""
    sunrise = datetime.fromisoformat("2026-05-30T05:28:00-04:00")
    sunset = datetime.fromisoformat("2026-05-30T20:15:00-04:00")
    hourly = _hourly("2026-05-29T21:00:00-04:00", 18, sunrise, sunset)
    nights = _compute_nights(hourly, {
        "next_sunrise": sunrise.isoformat(),
        "next_sunset":  sunset.isoformat(),
    }, _step(len(hourly)))
    assert len(nights) == 1
    assert abs(nights[0]["x"] - _LEFT_AXIS_X) < 0.01


def test_two_night_blocks_render_when_window_spans_both():
    """9 PM start over a 28 h window: leading overnight block AND the next
    evening's block both render - the 'sliver of the next nighttime block'
    case the og/closet dashboard hit."""
    sunrise = datetime.fromisoformat("2026-05-30T05:28:00-04:00")
    sunset = datetime.fromisoformat("2026-05-30T20:15:00-04:00")
    hourly = _hourly("2026-05-29T21:00:00-04:00", 28, sunrise, sunset)
    nights = _compute_nights(hourly, {
        "next_sunrise": sunrise.isoformat(),
        "next_sunset":  sunset.isoformat(),
    }, _step(len(hourly)))
    assert len(nights) == 2
    assert abs(nights[0]["x"] - _LEFT_AXIS_X) < 0.01                       # leading pinned left
    assert abs(nights[1]["x"] + nights[1]["w"] - _RIGHT_AXIS_X) < 0.01     # trailing pinned right
    # blocks are disjoint and left-to-right
    assert nights[0]["x"] + nights[0]["w"] < nights[1]["x"]


def test_all_day_window_has_no_blocks():
    sunrise = datetime.fromisoformat("2026-05-29T05:28:00-04:00")
    sunset = datetime.fromisoformat("2026-05-29T20:15:00-04:00")
    hourly = _hourly("2026-05-29T08:00:00-04:00", 8, sunrise, sunset)  # 8A-3P
    assert _compute_nights(hourly, {
        "next_sunrise": sunrise.isoformat(),
        "next_sunset":  sunset.isoformat(),
    }, _step(len(hourly))) == []


def test_rects_stay_within_axes():
    """No block may extend outside [left_axis_x, right_axis_x]."""
    sunrise = datetime.fromisoformat("2026-05-30T05:28:00-04:00")
    sunset = datetime.fromisoformat("2026-05-30T20:15:00-04:00")
    hourly = _hourly("2026-05-29T21:00:00-04:00", 28, sunrise, sunset)
    for nb in _compute_nights(hourly, {
        "next_sunrise": sunrise.isoformat(),
        "next_sunset":  sunset.isoformat(),
    }, _step(len(hourly))):
        assert nb["x"] >= _LEFT_AXIS_X - 0.01
        assert nb["x"] + nb["w"] <= _RIGHT_AXIS_X + 0.01


def test_legacy_fallback_without_is_night_still_shades():
    """Inputs lacking per-hour is_night fall back to the single sunset rect."""
    sunset = datetime.fromisoformat("2026-05-29T20:15:00-04:00")
    base = datetime.fromisoformat("2026-05-29T11:00:00-04:00")
    hourly = [{"datetime": (base + timedelta(hours=k)).isoformat(), "temp_f": 60}
              for k in range(17)]
    nights = _compute_nights(hourly, {"next_sunset": sunset.isoformat()}, _step(len(hourly)))
    assert len(nights) == 1
    assert abs(nights[0]["x"] + nights[0]["w"] - _RIGHT_AXIS_X) < 0.01
