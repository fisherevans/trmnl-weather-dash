"""Live-data fetch for weather_compact.

Reuses the existing weather source pipeline (sources.factory) + the
prose chunk helpers from weather_landscape, then projects the
NormalizedForecast into the slim shape this panel's renderer expects:
current obs, an 18-hour array, the upcoming sunrise/sunset pair, and
the two prose chunks (TODAY / TONIGHT or TONIGHT / TOMORROW depending
on render time).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import WeatherCompactConfig
from ..weather_landscape.aggregate import _forecast_chunks
from ...sources.base import HourlyPoint, NormalizedForecast
from ...sources.factory import make_weather_source

logger = logging.getLogger(__name__)


def build_live_context(config: WeatherCompactConfig) -> dict:
    tz = ZoneInfo(config.location.timezone)
    now = datetime.now(tz=tz)

    weather_src = make_weather_source(config.weather, timezone=config.location.timezone)
    weather = weather_src.fetch(
        config.location.lat, config.location.lon, config.weather.hours,
    )

    hourly = _hourly_to_dicts(weather.hourly, tz)
    today_prose, tonight_prose = _prose_chunks(weather.hourly, climate=config.climate)

    return {
        "now":           now.isoformat(),
        "current": {
            "temp_f":       int(round(weather.current.temp_f)),
            "humidity_pct": weather.current.humidity_pct,
        },
        "hourly":        hourly,
        "sun":           _sun_events(weather, now),
        "today_prose":   today_prose,
        "tonight_prose": tonight_prose,
    }


def _hourly_to_dicts(hourly: list[HourlyPoint], tz) -> list[dict]:
    """Project the typed forecast into render-ready dicts. Hour label uses
    the local-tz hour with no leading zero (`11A` / `3P` / `12A`)."""
    out: list[dict] = []
    for h in hourly:
        local = h.timestamp.astimezone(tz)
        out.append({
            "datetime":        local.isoformat(),
            "hour_label":      _format_hour(local),
            "temp_f":          float(h.temp_f),
            "humidity_pct":    h.humidity_pct,
            "precip_prob_pct": int(h.precip_prob_pct or 0),
            "is_night":        not h.is_day,
        })
    return out


def _format_hour(dt: datetime) -> str:
    """'11A' / '3P' / '12A' (midnight) / '12P' (noon)."""
    suffix = "A" if dt.hour < 12 else "P"
    h12 = dt.hour % 12 or 12
    return f"{h12}{suffix}"


def _prose_chunks(hourly: list[HourlyPoint], climate=None) -> tuple[str, str]:
    """Run the landscape aggregate's chunker and return the first two
    chunks' text. The chunker labels them TODAY/TONIGHT/TOMORROW based
    on the day/night transition - we discard the label here, the
    template hard-codes 'TODAY' / 'TONIGHT' for this panel."""
    # precip_type='rain' is a presentational hint for the helper; the
    # compact panel doesn't distinguish snow vs rain in its prose.
    chunks = _forecast_chunks(hourly, precip_type="rain", climate=climate)
    today = chunks[0]["text"] if len(chunks) >= 1 else ""
    tonight = chunks[1]["text"] if len(chunks) >= 2 else ""
    return today, tonight


def _sun_events(weather: NormalizedForecast, now: datetime) -> dict:
    """Pull the NEXT sunrise + sunset pair relative to `now`.

    The chart's night-shade computation in render.py wants the closest
    upcoming events; NormalizedForecast.sun carries the next pair after
    fetch time, which is what we want.
    """
    sun = weather.sun
    if sun is None:
        return {}
    return {
        "next_sunrise": sun.sunrise.isoformat() if sun.sunrise else None,
        "next_sunset":  sun.sunset.isoformat()  if sun.sunset  else None,
    }
