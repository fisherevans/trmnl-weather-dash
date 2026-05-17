"""Unit tests for aggregate.build_context — the only place real logic lives.

No network. All inputs are synthetic NormalizedForecast + SensorReading
fixtures built inline.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from weatherdash.aggregate import (DEFAULT_ICON, WMO_ICON_MAP, build_context,
                                   wmo_to_icon)
from weatherdash.config import (Config, HomeAssistantConfig, LocationConfig,
                                SensorsConfig, WeatherConfig)
from weatherdash.sources.base import (CurrentObservation, HourlyPoint,
                                      NormalizedForecast, SunInfo)
from weatherdash.sources.homeassistant import SensorReading


TZ = ZoneInfo("America/New_York")


# ── fixture builders ──────────────────────────────────────────────────────


def _hourly(n: int = 18, start_temp: float = 60.0, ramp: float = 1.0,
            humidity: int = 50, code: int = 0, is_day: bool = True,
            start_at: datetime | None = None) -> list[HourlyPoint]:
    """N consecutive hourly points starting at start_at (or 8pm today)."""
    if start_at is None:
        start_at = datetime(2026, 5, 16, 20, 0, tzinfo=TZ)
    return [
        HourlyPoint(
            timestamp=start_at.replace(hour=(start_at.hour + i) % 24) +
                      __import__("datetime").timedelta(days=(start_at.hour + i) // 24),
            temp_f=start_temp + i * ramp,
            precip_mm=0.0,
            cloud_pct=0,
            weather_code=code,
            is_day=is_day,
            humidity_pct=humidity,
        )
        for i in range(n)
    ]


def _forecast(
    *,
    hourly: list[HourlyPoint] | None = None,
    current_temp_f: float = 70.0,
    current_humidity: int = 50,
    current_code: int = 0,
    current_is_day: bool = True,
    sunrise: datetime | None = None,
    sunset: datetime | None = None,
) -> NormalizedForecast:
    if hourly is None:
        hourly = _hourly()
    return NormalizedForecast(
        hourly=hourly,
        current=CurrentObservation(
            temp_f=current_temp_f,
            humidity_pct=current_humidity,
            wind_mph=5.0,
            wind_gust_mph=10.0,
            wind_dir="N",
            weather_code=current_code,
            is_day=current_is_day,
        ),
        sun=SunInfo(
            sunrise=sunrise or hourly[0].timestamp.replace(hour=5, minute=30),
            sunset =sunset  or hourly[0].timestamp.replace(hour=20, minute=15),
        ),
    )


def _reading(entity_id: str, state: float, unit: str = "°F") -> SensorReading:
    return SensorReading(
        entity_id=entity_id,
        state=state,
        unit=unit,
        last_updated=datetime.now(tz=timezone.utc),
    )


def _config(sensors: SensorsConfig | None = None) -> Config:
    return Config(
        location=LocationConfig(lat=43.21, lon=-71.54, timezone="America/New_York"),
        weather=WeatherConfig(hours=18),
        home_assistant=HomeAssistantConfig(
            base_url="http://ha.test",
            sensors=sensors or SensorsConfig(),
        ),
    )


# ── source precedence (the most important behavior) ─────────────────────


def test_outdoor_temp_prefers_ha_when_present():
    cfg = _config(SensorsConfig(outdoor_temp_f="sensor.out_temp"))
    wx = _forecast(current_temp_f=70.0)
    ha = {"sensor.out_temp": _reading("sensor.out_temp", 64.5)}
    ctx = build_context(cfg, wx, ha)
    # HA reads 64.5 -> rounded to 65, NOT the API's 70.
    assert ctx["outside"]["temp_f"] == 64


def test_outdoor_temp_falls_back_to_api_when_ha_absent():
    cfg = _config(SensorsConfig(outdoor_temp_f="sensor.out_temp"))
    wx = _forecast(current_temp_f=70.0)
    # Sensor is configured but didn't return a usable reading -> empty ha
    ctx = build_context(cfg, wx, ha={})
    assert ctx["outside"]["temp_f"] == 70


def test_outdoor_temp_falls_back_when_no_sensors_configured_at_all():
    cfg = _config(SensorsConfig())   # no entries
    wx = _forecast(current_temp_f=68.0)
    ctx = build_context(cfg, wx, ha={})
    assert ctx["outside"]["temp_f"] == 68


def test_outdoor_humidity_same_rule():
    cfg = _config(SensorsConfig(outdoor_humidity="sensor.out_hum"))
    wx = _forecast(current_humidity=42)
    ha = {"sensor.out_hum": _reading("sensor.out_hum", 88.0, "%")}
    ctx = build_context(cfg, wx, ha)
    assert ctx["outside"]["humidity_pct"] == 88


# ── indoor mean ──────────────────────────────────────────────────────────


def test_indoor_mean_across_three_sensors():
    cfg = _config(SensorsConfig(indoor_temp_f=[
        "sensor.a", "sensor.b", "sensor.c",
    ]))
    wx = _forecast()
    ha = {
        "sensor.a": _reading("sensor.a", 70.0),
        "sensor.b": _reading("sensor.b", 72.0),
        "sensor.c": _reading("sensor.c", 74.0),
    }
    ctx = build_context(cfg, wx, ha)
    assert ctx["inside"]["temp_f"] == 72   # (70+72+74)/3 = 72


def test_indoor_single_sensor_used_as_is():
    cfg = _config(SensorsConfig(indoor_temp_f="sensor.lr"))
    wx = _forecast()
    ha = {"sensor.lr": _reading("sensor.lr", 71.4)}
    ctx = build_context(cfg, wx, ha)
    assert ctx["inside"]["temp_f"] == 71   # rounded


def test_indoor_partial_availability_only_averages_present():
    """If 2 of 3 sensors are unavailable (absent from ha dict),
    the mean is over the 1 that's present."""
    cfg = _config(SensorsConfig(indoor_temp_f=[
        "sensor.a", "sensor.b", "sensor.c",
    ]))
    wx = _forecast()
    ha = {"sensor.b": _reading("sensor.b", 72.0)}
    ctx = build_context(cfg, wx, ha)
    assert ctx["inside"]["temp_f"] == 72


def test_indoor_zero_readings_renders_placeholder():
    cfg = _config(SensorsConfig(indoor_temp_f="sensor.gone"))
    wx = _forecast()
    ctx = build_context(cfg, wx, ha={})
    assert ctx["inside"]["temp_f"] == "--"
    assert ctx["inside"]["humidity_pct"] == "--"


# ── WMO weather code -> icon mapping ─────────────────────────────────────


@pytest.mark.parametrize("code,day_stem,night_stem", [
    (0,  "clear-day",                   "clear-night"),
    (1,  "cloudy-1-day",                "cloudy-1-night"),
    (2,  "partly-cloudy-day",           "partly-cloudy-night"),
    (3,  "cloudy",                      "cloudy"),
    (45, "fog-day",                     "fog-night"),
    (51, "rainy-1-day",                 "rainy-1-night"),
    (63, "rainy-2-day",                 "rainy-2-night"),
    (65, "rainy-3-day",                 "rainy-3-night"),
    (71, "snowy-1-day",                 "snowy-1-night"),
    (73, "snowy-2-day",                 "snowy-2-night"),
    (75, "snowy-3-day",                 "snowy-3-night"),
    (95, "scattered-thunderstorms-day", "scattered-thunderstorms-night"),
    (96, "severe-thunderstorm",         "severe-thunderstorm"),
])
def test_wmo_to_icon_known_codes(code, day_stem, night_stem):
    assert wmo_to_icon(code, is_day=True)  == day_stem
    assert wmo_to_icon(code, is_day=False) == night_stem


def test_wmo_to_icon_unknown_code_falls_back():
    # WMO doesn't define 7 (gap in the spec) — we hit the default.
    assert wmo_to_icon(7, is_day=True)  == DEFAULT_ICON[0]
    assert wmo_to_icon(7, is_day=False) == DEFAULT_ICON[1]


def test_outside_condition_uses_current_is_day():
    cfg = _config()
    wx = _forecast(current_code=2, current_is_day=False)
    ctx = build_context(cfg, wx, ha={})
    assert ctx["outside"]["condition"] == "partly-cloudy-night"


def test_wmo_map_has_no_gaps_in_documented_codes():
    """Sanity check that every code we documented in WMO_ICON_MAP returns
    valid (string, string) tuples — catches typos at import time."""
    for code, (day, night) in WMO_ICON_MAP.items():
        assert isinstance(day, str) and day
        assert isinstance(night, str) and night


# ── forecast high/low extraction ─────────────────────────────────────────


def test_forecast_high_low_picks_extremes_and_their_times():
    # Hand-rolled 6 hours: high=85 at idx 2, low=55 at idx 4.
    start = datetime(2026, 5, 16, 20, 0, tzinfo=TZ)
    hourly = [
        HourlyPoint(start.replace(hour=20+i % 24) if i < 4 else
                    start.replace(hour=(i-4), day=17),
                    temp_f=temp, precip_mm=0.0, cloud_pct=0,
                    weather_code=0, is_day=True, humidity_pct=50)
        for i, temp in enumerate([70.0, 78.0, 85.0, 65.0, 55.0, 60.0])
    ]
    wx = _forecast(hourly=hourly)
    cfg = _config()
    ctx = build_context(cfg, wx, ha={})
    assert ctx["forecast"]["high"]["temp_f"] == 85
    assert "10:00 PM" in ctx["forecast"]["high"]["time"]   # idx 2 = 22:00
    assert ctx["forecast"]["low"]["temp_f"] == 55


# ── tomorrow flag rules ───────────────────────────────────────────────────


def test_high_tomorrow_flag_set_when_peak_on_next_day():
    """At 9pm today, the 18h window's peak is tomorrow afternoon — TMRW."""
    start = datetime(2026, 5, 16, 21, 0, tzinfo=TZ)
    hourly = []
    import datetime as dt
    for i in range(18):
        ts = start + dt.timedelta(hours=i)
        # Rising temps that peak at 81 at 1pm tomorrow (hour idx 16)
        temp = 70.0 if i < 16 else 81.0 if i == 16 else 78.0
        hourly.append(HourlyPoint(ts, temp, 0.0, 0, 0, True, 50))
    wx = _forecast(hourly=hourly, current_temp_f=68.0)
    ctx = build_context(_config(), wx, ha={})
    assert ctx["forecast"]["high"]["tomorrow"] is True


def test_low_tomorrow_flag_suppressed_for_overnight_low_before_noon():
    """A 5am-tomorrow low is just the overnight low — not TMRW-worthy."""
    start = datetime(2026, 5, 16, 21, 0, tzinfo=TZ)
    import datetime as dt
    hourly = [
        HourlyPoint(start + dt.timedelta(hours=i),
                    temp_f=(70.0 if i < 8 else 55.0 if i == 8 else 60.0),
                    precip_mm=0.0, cloud_pct=0, weather_code=0,
                    is_day=True, humidity_pct=50)
        for i in range(18)
    ]
    # idx 8 = 5am next day. Low timestamp is < noon tomorrow -> no TMRW.
    wx = _forecast(hourly=hourly)
    ctx = build_context(_config(), wx, ha={})
    assert ctx["forecast"]["low"]["tomorrow"] is False


def test_low_tomorrow_flag_set_when_low_is_past_noon_tomorrow():
    """Rare day-cooling event: low falls at 3pm tomorrow — TMRW."""
    start = datetime(2026, 5, 16, 21, 0, tzinfo=TZ)
    import datetime as dt
    hourly = [
        HourlyPoint(start + dt.timedelta(hours=i),
                    temp_f=(70.0 if i != 17 else 50.0),
                    precip_mm=0.0, cloud_pct=0, weather_code=0,
                    is_day=True, humidity_pct=50)
        for i in range(18)
    ]
    # idx 17 = 2pm next day, but we need past noon AND the lowest. Set
    # idx 17 = 50 (lowest) and timestamp = 2pm tomorrow.
    wx = _forecast(hourly=hourly)
    ctx = build_context(_config(), wx, ha={})
    assert ctx["forecast"]["low"]["tomorrow"] is True


# ── outdoor trends ───────────────────────────────────────────────────────


def test_temp_trend_up_when_forecast_rises_more_than_2f():
    # current 70, forecast hour-3 at 73 -> delta +3 -> "up"
    start = datetime(2026, 5, 16, 20, 0, tzinfo=TZ)
    import datetime as dt
    hourly = [
        HourlyPoint(start + dt.timedelta(hours=i),
                    temp_f=70.0 + i, precip_mm=0.0, cloud_pct=0,
                    weather_code=0, is_day=True, humidity_pct=50)
        for i in range(18)
    ]
    wx = _forecast(hourly=hourly, current_temp_f=70.0)
    ctx = build_context(_config(), wx, ha={})
    assert ctx["outside"]["temp_trend"] == "up"


def test_temp_trend_flat_within_deadband():
    start = datetime(2026, 5, 16, 20, 0, tzinfo=TZ)
    import datetime as dt
    # +1 degree over 3 hours -> flat (deadband is +/-2)
    hourly = [
        HourlyPoint(start + dt.timedelta(hours=i),
                    temp_f=70.0 + (i / 3.0), precip_mm=0.0, cloud_pct=0,
                    weather_code=0, is_day=True, humidity_pct=50)
        for i in range(18)
    ]
    wx = _forecast(hourly=hourly, current_temp_f=70.0)
    ctx = build_context(_config(), wx, ha={})
    assert ctx["outside"]["temp_trend"] == "flat"


def test_humidity_trend_down_when_forecast_drops_more_than_8pct():
    start = datetime(2026, 5, 16, 20, 0, tzinfo=TZ)
    import datetime as dt
    hourly = [
        HourlyPoint(start + dt.timedelta(hours=i),
                    temp_f=70.0, precip_mm=0.0, cloud_pct=0,
                    weather_code=0, is_day=True,
                    humidity_pct=80 - i * 5)
        for i in range(18)
    ]
    wx = _forecast(hourly=hourly, current_humidity=80)
    ctx = build_context(_config(), wx, ha={})
    assert ctx["outside"]["humidity_trend"] == "down"


def test_humidity_trend_flat_when_provider_lacks_hourly_humidity():
    """If the provider doesn't expose humidity_pct on hourly[],
    aggregation must default to 'flat' rather than crash."""
    start = datetime(2026, 5, 16, 20, 0, tzinfo=TZ)
    import datetime as dt
    hourly = [
        HourlyPoint(start + dt.timedelta(hours=i),
                    temp_f=70.0, precip_mm=0.0, cloud_pct=0,
                    weather_code=0, is_day=True, humidity_pct=None)
        for i in range(18)
    ]
    wx = _forecast(hourly=hourly, current_humidity=50)
    ctx = build_context(_config(), wx, ha={})
    assert ctx["outside"]["humidity_trend"] == "flat"


# ── precip_type selection ────────────────────────────────────────────────


def test_precip_type_snow_when_any_hour_has_a_snow_code():
    start = datetime(2026, 5, 16, 20, 0, tzinfo=TZ)
    import datetime as dt
    hourly = []
    for i in range(18):
        # All clear except hour 10 (snow code 71)
        code = 71 if i == 10 else 0
        hourly.append(HourlyPoint(start + dt.timedelta(hours=i),
                                  70.0, 0.0, 0, code, True, 50))
    wx = _forecast(hourly=hourly)
    ctx = build_context(_config(), wx, ha={})
    assert ctx["precip_type"] == "snow"


def test_precip_type_rain_when_no_snow_codes_present():
    wx = _forecast()
    ctx = build_context(_config(), wx, ha={})
    assert ctx["precip_type"] == "rain"


# ── cloud_description tiers ──────────────────────────────────────────────


@pytest.mark.parametrize("avg_pct,label", [
    (5, "Clear"),
    (20, "Mostly Sunny"),
    (45, "Partly Cloudy"),
    (75, "Mostly Cloudy"),
    (95, "Overcast"),
])
def test_cloud_description_tiers(avg_pct, label):
    start = datetime(2026, 5, 16, 20, 0, tzinfo=TZ)
    import datetime as dt
    hourly = [
        HourlyPoint(start + dt.timedelta(hours=i),
                    70.0, 0.0, avg_pct, 0, True, 50)
        for i in range(18)
    ]
    wx = _forecast(hourly=hourly)
    ctx = build_context(_config(), wx, ha={})
    assert ctx["cloud_description"] == label
