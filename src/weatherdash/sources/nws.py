"""US National Weather Service (api.weather.gov) provider.

Two-step fetch:
  1. GET /points/{lat},{lon}                -> metadata, including forecastHourly URL
  2. GET <forecastHourly>                   -> 156-hour hourly forecast

NWS gives us temperature, humidity, wind, isDaytime, and a textual
`shortForecast` ("Mostly Cloudy", "Light Rain", etc.). We parse the text
into a WMO weather code (the namespace the icon mapper uses) and a
synthetic cloud_pct.

Known limitations vs Open-Meteo:
- **No hourly precip amount.** NWS gives a probability and a category;
  quantitative precipitation lives on the lower-level `/gridpoints` raw
  endpoint with its own time-interval format. We set precip_mm=0 across
  the board. The precip-bar chart will be blank, but the cloud-cover
  chart still works.
- **Current observation is the first hourly period, not a live station
  read.** A separate /stations/.../observations/latest call would give
  live data, but it adds another network hop and a station-id lookup.
- US only.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from astral import LocationInfo
from astral.sun import sun

from .base import (CurrentObservation, ForecastError, HourlyPoint,
                   NormalizedForecast, SunInfo, deg_to_cardinal)

POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"
USER_AGENT = ("trmnl-weather-dash/0.1 "
              "(https://github.com/fisherevans/trmnl-weather-dash)")


class NWSProvider:
    def __init__(
        self,
        *,
        timezone: str = "UTC",
        api_key: str | None = None,        # accepted for protocol parity, unused
        timeout_s: float = 10.0,
        retries: int = 3,
    ) -> None:
        self._tz_name = timezone
        self._tz = ZoneInfo(timezone)
        self._timeout_s = timeout_s
        self._retries = retries
        self._headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}

    def fetch(self, lat: float, lon: float, hours: int) -> NormalizedForecast:
        try:
            points = self._get_with_retry(POINTS_URL.format(lat=lat, lon=lon))
            hourly_url = points["properties"]["forecastHourly"]
            hourly_raw = self._get_with_retry(hourly_url)
            return self._parse(hourly_raw, hours, lat, lon)
        except (KeyError, TypeError, ValueError) as e:
            raise ForecastError(f"NWS response did not match expected shape: {e}") from e

    # ──────────────────────────────────────────────────────────────────────

    def _get_with_retry(self, url: str) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                with httpx.Client(timeout=self._timeout_s, headers=self._headers) as client:
                    r = client.get(url)
                if r.status_code < 500:
                    if r.status_code >= 400:
                        raise ForecastError(f"NWS {r.status_code}: {r.text[:200]}")
                    return r.json()
                last_exc = ForecastError(f"NWS {r.status_code}: {r.text[:200]}")
            except httpx.RequestError as e:
                last_exc = ForecastError(f"NWS request failed: {e}")
            if attempt < self._retries - 1:
                time.sleep(2 ** attempt)
        assert last_exc is not None
        raise last_exc

    def _parse(self, raw: dict[str, Any], hours: int, lat: float, lon: float) -> NormalizedForecast:
        periods = raw["properties"]["periods"][:hours]
        hourly = [self._period_to_hourly(p) for p in periods]

        first = periods[0]
        wind_mph = _parse_wind_speed(first.get("windSpeed", ""))
        current_code, _ = _short_forecast_to_wmo(first["shortForecast"])
        current = CurrentObservation(
            temp_f=float(first["temperature"]),
            humidity_pct=int((first.get("relativeHumidity") or {}).get("value") or 0),
            wind_mph=wind_mph,
            wind_gust_mph=wind_mph,             # NWS doesn't expose gust on hourly
            wind_dir=first.get("windDirection") or "N",
            weather_code=current_code,
            is_day=bool(first["isDaytime"]),
        )

        # NWS doesn't surface sunrise/sunset on /forecast/hourly. Compute
        # locally — the only thing we need from the response is "now"
        # to find the next event.
        sun_info = _compute_sun(lat, lon, self._tz, datetime.now(tz=self._tz))
        return NormalizedForecast(hourly=hourly, current=current, sun=sun_info)

    def _period_to_hourly(self, p: dict[str, Any]) -> HourlyPoint:
        ts = datetime.fromisoformat(p["startTime"]).astimezone(self._tz)
        code, cloud_pct = _short_forecast_to_wmo(p["shortForecast"])
        humidity = (p.get("relativeHumidity") or {}).get("value")
        return HourlyPoint(
            timestamp=ts,
            temp_f=float(p["temperature"]),
            precip_mm=0.0,                  # see module docstring re: limitations
            cloud_pct=cloud_pct,
            weather_code=code,
            is_day=bool(p["isDaytime"]),
            humidity_pct=int(humidity) if humidity is not None else None,
        )


# ── shortForecast text -> (WMO code, cloud_pct) ───────────────────────────


# Order matters: we scan in this order and take the first match. More
# specific phrases must come before more general ones (e.g. "thunder"
# before "rain", since "thunderstorm" implies both).
_TEXT_MAP: list[tuple[str, int, int]] = [
    # ── thunderstorms first (more specific than rain) ─────────────────
    ("severe thunderstorm", 96, 95),
    ("thunderstorm",        95, 90),
    ("thunder",             95, 80),

    # ── frozen precip ─────────────────────────────────────────────────
    ("heavy snow",          75, 95),
    ("snow shower",         85, 90),
    ("light snow",          71, 90),
    ("snow",                73, 95),
    ("freezing rain",       66, 95),
    ("sleet",               66, 95),
    ("ice pellets",         66, 95),

    # ── rain / showers ────────────────────────────────────────────────
    ("heavy rain",          65, 95),
    ("light rain",          61, 85),
    ("rain",                63, 90),
    ("light shower",        80, 80),
    ("shower",              81, 85),
    ("drizzle",             51, 85),

    # ── visibility ────────────────────────────────────────────────────
    ("fog",                 45, 95),
    ("haze",                45, 60),
    ("smoke",               45, 60),

    # ── cloud cover only (no precip) ──────────────────────────────────
    ("overcast",            3,  100),
    ("mostly cloudy",       3,  85),
    ("partly cloudy",       2,  50),
    ("partly sunny",        2,  50),
    ("mostly sunny",        1,  20),
    ("mostly clear",        1,  20),
    ("sunny",               0,  0),
    ("clear",               0,  0),
    ("fair",                1,  10),
]


def _short_forecast_to_wmo(text: str) -> tuple[int, int]:
    """Map NWS shortForecast text to (WMO code, synthetic cloud_pct).

    Falls back to (3, 70) — "overcast, no specific precip" — for any
    string we don't recognize. That keeps the dashboard rendering even
    if NWS coins a new phrase.
    """
    needle = text.lower()
    for keyword, code, cloud in _TEXT_MAP:
        if keyword in needle:
            return code, cloud
    return 3, 70


def _parse_wind_speed(s: str) -> float:
    """NWS gives wind as 'N mph' or 'N to M mph'. Take the (higher) bound."""
    if not s:
        return 0.0
    # Strip "mph", split on " to ", take the max numeric token.
    parts = s.replace("mph", "").replace("MPH", "").strip().split("to")
    nums: list[float] = []
    for chunk in parts:
        try:
            nums.append(float(chunk.strip()))
        except ValueError:
            continue
    return max(nums) if nums else 0.0


def _compute_sun(lat: float, lon: float, tz: ZoneInfo, now: datetime) -> SunInfo:
    """Compute the next sunrise + next sunset >= `now` using astral."""
    loc = LocationInfo(name="", region="", timezone=str(tz), latitude=lat, longitude=lon)
    sunrises: list[datetime] = []
    sunsets:  list[datetime] = []
    # Look across a 3-day window to ensure we cover any forecast horizon.
    for delta in range(0, 3):
        d = (now + timedelta(days=delta)).date()
        s = sun(loc.observer, date=d, tzinfo=tz)
        sunrises.append(s["sunrise"])
        sunsets.append(s["sunset"])
    return SunInfo(
        sunrise=_first_future(sunrises, now),
        sunset =_first_future(sunsets,  now),
    )


def _first_future(events: list[datetime], now: datetime) -> datetime:
    for e in events:
        if e >= now:
            return e
    return events[-1]
