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

from datetime import datetime
from zoneinfo import ZoneInfo

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
    # Peak/trough may fall on the next calendar day in the configured tz
    # (e.g. at 9pm, today's peak already happened; the next 18h reaches
    # tomorrow's peak). `tomorrow` flags that case so the template can
    # show a "TMRW" badge — otherwise an 81°F high at 1pm reads as
    # today's, which is misleading after sunset.
    high_h = max(weather.hourly, key=lambda h: h.temp_f)
    low_h  = min(weather.hourly, key=lambda h: h.temp_f)
    today = now.date()

    # ── precip type drives bg-{rain,snow}.svg selection ───────────────────
    precip_type = "snow" if any(h.weather_code in SNOW_CODES for h in weather.hourly) else "rain"

    # ── chart-footer summary ──────────────────────────────────────────────
    total_precip_mm = sum(h.precip_mm for h in weather.hourly)
    avg_cloud_pct   = round(sum(h.cloud_pct for h in weather.hourly) / n_hours)

    return {
        "date_line": now.strftime("%A, %B %-d, %Y").upper(),
        "time":      now.strftime("%-I:%M %p"),
        "inside": {
            "temp_f":       _round_or_placeholder(in_temp),
            "humidity_pct": _round_or_placeholder(in_hum),
        },
        "outside": {
            "temp_f":       round(out_temp),
            "humidity_pct": round(out_hum),
            "condition":    condition,
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
                "tomorrow": low_h.timestamp.date() != today,
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
        "hourly":                hourly,
    }


# ──────────────────────────────────────────────────────────────────────────


def wmo_to_icon(code: int, is_day: bool) -> str:
    day, night = WMO_ICON_MAP.get(code, DEFAULT_ICON)
    return day if is_day else night


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
