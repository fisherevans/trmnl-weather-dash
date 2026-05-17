"""Merge a NormalizedForecast + HA SensorReadings into the dict the
Jinja template consumes.

The output shape is the same one `data.json` documents — see that file
for the canonical reference. Anything new the template needs starts
here so the renderer stays purely mechanical.

Source-precedence rules (set in trmnl-weather-dash#5 design):

- **outdoor temp / humidity:** prefer HA when present; fall back to the
  weather API's current observation. The HA sensor is the actual reading
  at the user's location; averaging it with a 1-30km-away API estimate
  dilutes ground truth.
- **indoor temp / humidity:** mean across all configured HA sensors.
  No HA fallback (the API doesn't measure indoors). Zero readings -> "--".
- **forecast (hourly, sun, wind, condition):** always from the weather API.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .bg_shading import cloud_bucket, precip_bucket, shaded_svg_url
from .config import Config, SensorRef, as_sensor_list
from .sources.base import NormalizedForecast
from .sources.homeassistant import SensorReading


# Open-Meteo's WMO weather codes -> (day stem, night stem) from makin-grey/.
# Codes that don't have a meaningful day/night split (e.g. fog, sleet,
# severe thunder) use the same stem for both.
WMO_ICON_MAP: dict[int, tuple[str, str]] = {
    0:  ("clear-day",                  "clear-night"),
    1:  ("cloudy-1-day",               "cloudy-1-night"),
    2:  ("partly-cloudy-day",          "partly-cloudy-night"),
    3:  ("cloudy",                     "cloudy"),
    45: ("fog-day",                    "fog-night"),
    48: ("fog-day",                    "fog-night"),
    51: ("rainy-1-day",                "rainy-1-night"),
    53: ("rainy-1-day",                "rainy-1-night"),
    55: ("rainy-2-day",                "rainy-2-night"),
    56: ("rain-and-sleet-mix",         "rain-and-sleet-mix"),
    57: ("rain-and-sleet-mix",         "rain-and-sleet-mix"),
    61: ("rainy-1-day",                "rainy-1-night"),
    63: ("rainy-2-day",                "rainy-2-night"),
    65: ("rainy-3-day",                "rainy-3-night"),
    66: ("rain-and-sleet-mix",         "rain-and-sleet-mix"),
    67: ("rain-and-sleet-mix",         "rain-and-sleet-mix"),
    71: ("snowy-1-day",                "snowy-1-night"),
    73: ("snowy-2-day",                "snowy-2-night"),
    75: ("snowy-3-day",                "snowy-3-night"),
    77: ("snowy-1-day",                "snowy-1-night"),
    80: ("rainy-1-day",                "rainy-1-night"),
    81: ("rainy-2-day",                "rainy-2-night"),
    82: ("rainy-3-day",                "rainy-3-night"),
    85: ("snowy-1-day",                "snowy-1-night"),
    86: ("snowy-2-day",                "snowy-2-night"),
    95: ("scattered-thunderstorms-day", "scattered-thunderstorms-night"),
    96: ("severe-thunderstorm",        "severe-thunderstorm"),
    99: ("severe-thunderstorm",        "severe-thunderstorm"),
}
DEFAULT_ICON = ("cloudy", "cloudy")             # neutral fallback for unknown codes
SNOW_CODES = frozenset({71, 73, 75, 77, 85, 86})

# Look-ahead and deadbands for the OUTSIDE trend arrows. Compares the
# current observation against the forecast `TREND_HOURS` hours out.
TREND_HOURS = 3
TREND_TEMP_THRESHOLD_F = 2.0
# 8% chosen as the humidity deadband: typical hourly RH drift from the
# diurnal cycle is 3-6%, so 8% suppresses that noise while still flagging
# a real shift (front moving in, etc).
TREND_HUMIDITY_THRESHOLD_PCT = 8


def build_context(
    config: Config,
    weather: NormalizedForecast,
    ha: dict[str, SensorReading],
) -> dict:
    if not weather.hourly:
        raise ValueError("weather.hourly is empty — provider returned no data")

    tz = ZoneInfo(config.location.timezone)
    now = datetime.now(tz=tz)
    chart_start = weather.hourly[0].timestamp
    n_hours = len(weather.hourly)

    # ── outdoor: HA-as-truth, API fallback ────────────────────────────────
    sensors = config.home_assistant.sensors
    out_temp = _ha_mean(sensors.outdoor_temp_f, ha)
    if out_temp is None:
        out_temp = weather.current.temp_f
    out_hum = _ha_mean(sensors.outdoor_humidity, ha)
    if out_hum is None:
        out_hum = weather.current.humidity_pct

    # ── indoor: mean across HA, no fallback ───────────────────────────────
    in_temp = _ha_mean(sensors.indoor_temp_f, ha)
    in_hum  = _ha_mean(sensors.indoor_humidity, ha)

    # ── current condition (icon) ──────────────────────────────────────────
    condition = wmo_to_icon(weather.current.weather_code, weather.current.is_day)

    # ── outdoor trends (next ~3h vs now) ──────────────────────────────────
    # Compares current observation to the forecast 3 hours out. Within
    # the deadband -> "flat". Renders as ↗ / → / ↘ on the OUTSIDE card.
    look_ahead = min(TREND_HOURS, len(weather.hourly) - 1)
    future = weather.hourly[look_ahead]
    temp_trend = _trend(future.temp_f - weather.current.temp_f, TREND_TEMP_THRESHOLD_F)
    humidity_trend = "flat"
    if future.humidity_pct is not None:
        humidity_trend = _trend(
            future.humidity_pct - weather.current.humidity_pct,
            TREND_HUMIDITY_THRESHOLD_PCT,
        )

    # ── hourly slice ──────────────────────────────────────────────────────
    hourly = [
        {
            "hour":      _format_chart_hour(h.timestamp),
            "precip_mm": round(h.precip_mm, 1),
            "cloud_pct": h.cloud_pct,
            "is_night":  not h.is_day,
            "temp_f":    round(h.temp_f),
        }
        for h in weather.hourly
    ]

    # ── sun markers (hour_index relative to chart start) ──────────────────
    sunrise_idx = (weather.sun.sunrise - chart_start).total_seconds() / 3600
    sunset_idx  = (weather.sun.sunset  - chart_start).total_seconds() / 3600

    # ── forecast high/low across the visible window ───────────────────────
    # `tomorrow` flags add a "TMRW" badge in the template. Rules differ for
    # high vs low because of how the diurnal cycle reads:
    #   - High: peaks mid-afternoon. After today's peak, the window's
    #     next high is tomorrow's. Flag any high timestamp on a later
    #     date than `now`.
    #   - Low: troughs in the pre-dawn hours. A 5am low *technically* on
    #     tomorrow's date is still "tonight's overnight low" colloquially
    #     — flagging it as TMRW is noisy. Only flag if the low is past
    #     noon tomorrow (a rare day-cooling event).
    high_h = max(weather.hourly, key=lambda h: h.temp_f)
    low_h  = min(weather.hourly, key=lambda h: h.temp_f)
    today = now.date()
    noon_tomorrow = (now + timedelta(days=1)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )

    # ── precip type drives bg-{rain,snow}.svg selection ───────────────────
    precip_type = "snow" if any(h.weather_code in SNOW_CODES for h in weather.hourly) else "rain"

    # ── chart-footer summary ──────────────────────────────────────────────
    total_precip_mm = sum(h.precip_mm for h in weather.hourly)
    avg_cloud_pct   = round(sum(h.cloud_pct for h in weather.hourly) / n_hours)

    # ── density-shifted background SVGs ───────────────────────────────────
    # Cloud-row darkness scales with avg cloud %, precip-row scales with
    # total mm. SVG fills get swapped at render time and inlined as
    # data: URLs (see bg_shading.py).
    cloud_bg_url  = shaded_svg_url("bg-cloud.svg", cloud_bucket(avg_cloud_pct))
    precip_svg    = "bg-snow.svg" if precip_type == "snow" else "bg-rain.svg"
    precip_bg_url = shaded_svg_url(precip_svg, precip_bucket(total_precip_mm))

    return {
        "date_line": now.strftime("%A, %B %-d, %Y").upper(),
        "time":      now.strftime("%-I:%M %p"),
        "inside": {
            "temp_f":       _round_or_placeholder(in_temp),
            "humidity_pct": _round_or_placeholder(in_hum),
        },
        "outside": {
            "temp_f":         round(out_temp),
            "temp_trend":     temp_trend,
            "humidity_pct":   round(out_hum),
            "humidity_trend": humidity_trend,
            "condition":      condition,
            "wind": {
                "speed_mph": round(weather.current.wind_mph),
                "gust_mph":  round(weather.current.wind_gust_mph),
                "direction": weather.current.wind_dir,
            },
        },
        "forecast": {
            "high": {
                "temp_f":   round(high_h.temp_f),
                "time":     _format_clock(high_h.timestamp),
                "tomorrow": high_h.timestamp.date() != today,
            },
            "low": {
                "temp_f":   round(low_h.temp_f),
                "time":     _format_clock(low_h.timestamp),
                "tomorrow": low_h.timestamp >= noon_tomorrow,
            },
        },
        "sun": {
            "sunrise": {"time": _format_clock(weather.sun.sunrise), "hour_index": sunrise_idx},
            "sunset":  {"time": _format_clock(weather.sun.sunset),  "hour_index": sunset_idx},
        },
        "precip_type":           precip_type,
        "total_accumulation_mm": round(total_precip_mm, 1),
        "avg_cloud_pct":         avg_cloud_pct,
        "cloud_description":     _cloud_description(avg_cloud_pct),
        "cloud_bg_url":          cloud_bg_url,
        "precip_bg_url":         precip_bg_url,
        "hourly":                hourly,
    }


# ──────────────────────────────────────────────────────────────────────────


def wmo_to_icon(code: int, is_day: bool) -> str:
    day, night = WMO_ICON_MAP.get(code, DEFAULT_ICON)
    return day if is_day else night


def _trend(delta: float, threshold: float) -> str:
    """Return 'up' / 'flat' / 'down' for a deadband around 0."""
    if delta > threshold:
        return "up"
    if delta < -threshold:
        return "down"
    return "flat"


def _ha_mean(ref: SensorRef, ha: dict[str, SensorReading]) -> float | None:
    """Mean over all available readings from the configured entity IDs.
    Returns None if no entity has a usable reading."""
    eids = as_sensor_list(ref)
    vals = [ha[e].state for e in eids if e in ha]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _round_or_placeholder(v: float | None) -> int | str:
    return round(v) if v is not None else "--"


def _format_chart_hour(dt: datetime) -> str:
    """Format hourly[].hour as the chart's compact label, e.g. '11a', '3p'."""
    h12 = dt.strftime("%-I")
    suffix = "a" if dt.hour < 12 else "p"
    return f"{h12}{suffix}"


def _format_clock(dt: datetime) -> str:
    """Format wall-clock time, e.g. '3:00 PM', '5:32 AM'."""
    return dt.strftime("%-I:%M %p")


def _cloud_description(pct: int) -> str:
    if pct < 13:
        return "Clear"
    if pct < 31:
        return "Mostly Sunny"
    if pct < 61:
        return "Partly Cloudy"
    if pct < 88:
        return "Mostly Cloudy"
    return "Overcast"
