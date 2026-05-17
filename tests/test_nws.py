"""Unit tests for the NWS provider's pure helpers.

The HTTP layer isn't covered here — that needs a mocked client and
would mostly be testing httpx. The interesting NWS-specific logic is
the shortForecast-text-to-WMO mapper and the wind-speed parser.
"""
from __future__ import annotations

import pytest

from weatherdash.sources.nws import _parse_wind_speed, _short_forecast_to_wmo


# ── shortForecast text -> (WMO code, synthetic cloud_pct) ────────────────


@pytest.mark.parametrize("text,expected_code", [
    # Clear / cloud-only
    ("Sunny", 0),
    ("Clear", 0),
    ("Mostly Sunny", 1),
    ("Mostly Clear", 1),
    ("Fair", 1),
    ("Partly Cloudy", 2),
    ("Partly Sunny", 2),
    ("Mostly Cloudy", 3),
    ("Overcast", 3),

    # Rain
    ("Light Rain", 61),
    ("Rain", 63),
    ("Heavy Rain", 65),
    ("Showers", 81),
    ("Light Showers", 80),
    ("Drizzle", 51),

    # Snow / mix
    ("Light Snow", 71),
    ("Snow", 73),
    ("Heavy Snow", 75),
    ("Snow Showers", 85),
    ("Freezing Rain", 66),
    ("Sleet", 66),

    # Thunderstorms (must beat rain since "Thunderstorms with Rain" should
    # map to thunder, not rain)
    ("Thunderstorms", 95),
    ("Severe Thunderstorm", 96),
    ("Chance of Thunder", 95),

    # Visibility
    ("Fog", 45),
    ("Patchy Fog", 45),
])
def test_short_forecast_maps_to_expected_wmo(text, expected_code):
    code, _ = _short_forecast_to_wmo(text)
    assert code == expected_code, f"{text!r} -> {code}, expected {expected_code}"


def test_short_forecast_case_insensitive():
    # NWS sometimes emits Title Case, sometimes UPPER CASE, sometimes lower.
    assert _short_forecast_to_wmo("SUNNY") == _short_forecast_to_wmo("sunny")
    assert _short_forecast_to_wmo("SUNNY") == _short_forecast_to_wmo("Sunny")


def test_thunderstorm_beats_rain_when_both_present():
    """A 'Thunderstorms with Light Rain' string should map to thunder, not rain."""
    code, _ = _short_forecast_to_wmo("Thunderstorms with Light Rain")
    assert code == 95


def test_severe_thunderstorm_more_specific_than_thunderstorm():
    code, _ = _short_forecast_to_wmo("Severe Thunderstorm Warning")
    assert code == 96


def test_unknown_text_falls_through_to_default():
    """Unknown NWS phrasing should land on a neutral 'overcast' rather
    than blowing up — the dashboard should still render."""
    code, cloud = _short_forecast_to_wmo("Inferno of frogs")
    assert code == 3
    assert 0 <= cloud <= 100


def test_short_forecast_returns_cloud_pct_in_valid_range():
    """The synthetic cloud_pct must always be 0..100 — feeds into chart math."""
    for text in ["Sunny", "Cloudy", "Heavy Rain", "Snow", "Inferno of frogs"]:
        _, cloud = _short_forecast_to_wmo(text)
        assert 0 <= cloud <= 100


# ── wind speed parser ────────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("5 mph",      5.0),
    ("10 mph",     10.0),
    ("5 to 10 mph", 10.0),    # range -> take the higher bound
    ("15 to 25 mph", 25.0),
    ("0 mph",      0.0),
    ("",           0.0),
    ("5 MPH",      5.0),       # case-insensitive unit
    ("Variable",   0.0),       # no numeric content
])
def test_parse_wind_speed(text, expected):
    assert _parse_wind_speed(text) == expected
