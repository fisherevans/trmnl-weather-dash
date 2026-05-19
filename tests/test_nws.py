"""Unit tests for the NWS provider's pure helpers.

The HTTP layer isn't exercised here — that's mostly httpx. The
NWS-specific logic worth covering:
- ISO 8601 duration parsing (the limited subset NWS uses)
- Expanding the gridpoint validTime/value format into per-hour values
- Picking a WMO code from a mixed-precip weather list
- Unit conversion across the wmoUnit:* namespace
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from weatherdash.sources.nws import (_convert_amount, _expand_amount_to_hours,
                                     _expand_to_hours, _expand_weather_to_hours,
                                     _parse_iso_duration, _parse_valid_time,
                                     _pick_weather_code)


# ── ISO 8601 duration ────────────────────────────────────────────────────


@pytest.mark.parametrize("s,expected", [
    ("PT1H",   timedelta(hours=1)),
    ("PT3H",   timedelta(hours=3)),
    ("PT6H",   timedelta(hours=6)),
    ("PT12H",  timedelta(hours=12)),
    ("PT24H",  timedelta(hours=24)),
    ("P1DT6H", timedelta(days=1, hours=6)),
    ("PT1H30M", timedelta(hours=1, minutes=30)),
])
def test_parse_iso_duration_accepts_nws_forms(s, expected):
    assert _parse_iso_duration(s) == expected


@pytest.mark.parametrize("bad", ["", "P", "1H", "PT", "PT1Y", "P1Y", "garbage"])
def test_parse_iso_duration_rejects_garbage(bad):
    with pytest.raises(ValueError):
        _parse_iso_duration(bad)


def test_parse_valid_time_splits_and_parses_both_sides():
    start, dur = _parse_valid_time("2024-01-15T08:00:00+00:00/PT3H")
    assert start == datetime(2024, 1, 15, 8, 0, tzinfo=timezone.utc)
    assert dur == timedelta(hours=3)


# ── _expand_to_hours: instantaneous-value series ─────────────────────────


def test_expand_to_hours_repeats_value_across_period():
    prop = {"values": [
        {"validTime": "2024-01-15T00:00:00+00:00/PT3H", "value": 5.0},
        {"validTime": "2024-01-15T03:00:00+00:00/PT2H", "value": 7.0},
    ]}
    out = _expand_to_hours(prop)
    assert len(out) == 5
    for i in range(3):
        assert out[datetime(2024, 1, 15, i, tzinfo=timezone.utc)] == 5.0
    for i in range(3, 5):
        assert out[datetime(2024, 1, 15, i, tzinfo=timezone.utc)] == 7.0


def test_expand_to_hours_empty_property_returns_empty():
    assert _expand_to_hours(None) == {}
    assert _expand_to_hours({"values": []}) == {}


# ── _expand_amount_to_hours: TOTAL-over-period, split evenly ─────────────


def test_expand_amount_splits_total_evenly_across_hours():
    """6mm over PT6H -> 1mm/hour, not 6mm/hour repeated."""
    prop = {"values": [{"validTime": "2024-01-15T00:00:00+00:00/PT6H", "value": 6.0}]}
    out = _expand_amount_to_hours(prop, uom="wmoUnit:mm", to="mm")
    assert len(out) == 6
    for v in out.values():
        assert v == pytest.approx(1.0)


def test_expand_amount_converts_meters_to_cm_for_snowfall():
    """NWS snowfallAmount typically comes in meters."""
    prop = {"values": [{"validTime": "2024-01-15T00:00:00+00:00/PT2H", "value": 0.05}]}
    out = _expand_amount_to_hours(prop, uom="wmoUnit:m", to="cm")
    # 0.05m -> 5cm total, /2 hours -> 2.5 cm/hour
    for v in out.values():
        assert v == pytest.approx(2.5)


def test_expand_amount_skips_null_values():
    """NWS sometimes emits null values for forecast gaps."""
    prop = {"values": [
        {"validTime": "2024-01-15T00:00:00+00:00/PT1H", "value": None},
        {"validTime": "2024-01-15T01:00:00+00:00/PT1H", "value": 2.0},
    ]}
    out = _expand_amount_to_hours(prop, uom="wmoUnit:mm", to="mm")
    assert len(out) == 1
    assert out[datetime(2024, 1, 15, 1, tzinfo=timezone.utc)] == pytest.approx(2.0)


# ── unit conversion ──────────────────────────────────────────────────────


@pytest.mark.parametrize("v,uom,to,expected", [
    (5.0,   "wmoUnit:mm", "mm",  5.0),
    (0.005, "wmoUnit:m",  "mm",  5.0),
    (0.5,   "wmoUnit:cm", "mm",  5.0),
    (5.0,   "wmoUnit:cm", "cm",  5.0),
    (0.05,  "wmoUnit:m",  "cm",  5.0),
    (50.0,  "wmoUnit:mm", "cm",  5.0),
])
def test_convert_amount(v, uom, to, expected):
    assert _convert_amount(v, uom, to) == pytest.approx(expected)


# ── weather code picker ──────────────────────────────────────────────────


def test_pick_weather_code_empty_list_returns_zero():
    assert _pick_weather_code([]) == 0


def test_pick_weather_code_picks_strongest_intensity():
    """Heavier hazard wins."""
    code = _pick_weather_code([
        {"weather": "rain", "intensity": "light"},
        {"weather": "rain", "intensity": "heavy"},
    ])
    assert code == 65   # heavy rain WMO


def test_pick_weather_code_snow_beats_rain_at_equal_rank():
    """A mixed `[rain light, snow light]` should surface snow on the dashboard."""
    code = _pick_weather_code([
        {"weather": "rain", "intensity": "light"},
        {"weather": "snow", "intensity": "light"},
    ])
    assert code == 71   # light snow WMO


def test_pick_weather_code_unmapped_token_returns_zero():
    """Unknown token + intensity falls through to 0 (clear), not an error."""
    assert _pick_weather_code([{"weather": "frogs", "intensity": "biblical"}]) == 0


def test_pick_weather_code_thunderstorms_classified_as_95():
    assert _pick_weather_code([{"weather": "thunderstorms", "intensity": "moderate"}]) == 95


# ── _expand_weather_to_hours: full pipeline ──────────────────────────────


def test_expand_weather_emits_one_code_per_hour():
    prop = {"values": [{
        "validTime": "2024-01-15T00:00:00+00:00/PT3H",
        "value": [{"weather": "rain", "intensity": "moderate"}],
    }]}
    out = _expand_weather_to_hours(prop)
    assert len(out) == 3
    for v in out.values():
        assert v == 63   # moderate rain WMO
