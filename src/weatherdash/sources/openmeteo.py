"""Open-Meteo forecast provider.

Endpoint: https://api.open-meteo.com/v1/forecast
No API key required for the free non-commercial tier. The schema's
`api_key_env`, if set, attaches `apikey=` for the commercial tier.

Open-Meteo accepts unit preferences as query params, so we ask for F /
mph / mm directly and skip the conversion math.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .base import (CurrentObservation, ForecastError, HourlyPoint,
                   NormalizedForecast, SunInfo, deg_to_cardinal)

ENDPOINT = "https://api.open-meteo.com/v1/forecast"


class OpenMeteoProvider:
    def __init__(
        self,
        *,
        timezone: str = "UTC",
        api_key: str | None = None,
        timeout_s: float = 10.0,
        retries: int = 3,
    ) -> None:
        self._tz_name = timezone
        self._tz = ZoneInfo(timezone)
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._retries = retries

    def fetch(self, lat: float, lon: float, hours: int) -> NormalizedForecast:
        params: dict[str, Any] = {
            "latitude": lat,
            "longitude": lon,
            "timezone": self._tz_name,
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "mm",
            "hourly": ("temperature_2m,relative_humidity_2m,precipitation,"
                       "cloud_cover,weather_code,is_day"),
            "current": ("temperature_2m,relative_humidity_2m,wind_speed_10m,"
                        "wind_gusts_10m,wind_direction_10m,weather_code,is_day"),
            "daily": "sunrise,sunset",
            # forecast_hours covers up to 168 hours; round up to days for daily.
            "forecast_hours": hours,
            "forecast_days": max(2, (hours + 23) // 24 + 1),
        }
        if self._api_key:
            params["apikey"] = self._api_key

        raw = self._get_with_retry(params)
        try:
            return self._parse(raw)
        except (KeyError, ValueError, TypeError) as e:
            raise ForecastError(f"Open-Meteo response did not match expected shape: {e}") from e

    # ──────────────────────────────────────────────────────────────────────

    def _get_with_retry(self, params: dict[str, Any]) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                with httpx.Client(timeout=self._timeout_s) as client:
                    r = client.get(ENDPOINT, params=params)
                if r.status_code < 500:
                    if r.status_code >= 400:
                        raise ForecastError(
                            f"Open-Meteo returned {r.status_code}: {r.text[:200]}"
                        )
                    return r.json()
                # 5xx: retriable
                last_exc = ForecastError(f"Open-Meteo {r.status_code}: {r.text[:200]}")
            except httpx.RequestError as e:
                last_exc = ForecastError(f"Open-Meteo request failed: {e}")
            # Backoff before the next attempt — skip after the final one.
            if attempt < self._retries - 1:
                time.sleep(2 ** attempt)
        assert last_exc is not None
        raise last_exc

    def _parse(self, raw: dict[str, Any]) -> NormalizedForecast:
        # ── hourly ────────────────────────────────────────────────────────
        h = raw["hourly"]
        times = [self._parse_local(t) for t in h["time"]]
        hourly = [
            HourlyPoint(
                timestamp=times[i],
                temp_f=float(h["temperature_2m"][i]),
                precip_mm=float(h["precipitation"][i]),
                cloud_pct=int(h["cloud_cover"][i]),
                weather_code=int(h["weather_code"][i]),
                is_day=bool(h["is_day"][i]),
                humidity_pct=int(h["relative_humidity_2m"][i]),
            )
            for i in range(len(times))
        ]

        # ── current ───────────────────────────────────────────────────────
        c = raw["current"]
        current = CurrentObservation(
            temp_f=float(c["temperature_2m"]),
            humidity_pct=int(c["relative_humidity_2m"]),
            wind_mph=float(c["wind_speed_10m"]),
            wind_gust_mph=float(c["wind_gusts_10m"]),
            wind_dir=deg_to_cardinal(float(c["wind_direction_10m"])),
            weather_code=int(c["weather_code"]),
            is_day=bool(c["is_day"]),
        )

        # ── sun: pick the next sunrise / sunset after "now" in our tz ─────
        d = raw["daily"]
        sunrises = [self._parse_local(t) for t in d["sunrise"]]
        sunsets  = [self._parse_local(t) for t in d["sunset"]]
        now = datetime.now(tz=self._tz)
        sun = SunInfo(
            sunrise=_first_future(sunrises, now),
            sunset=_first_future(sunsets, now),
        )

        return NormalizedForecast(hourly=hourly, current=current, sun=sun)

    def _parse_local(self, s: str) -> datetime:
        """Open-Meteo returns naive ISO strings already in the requested tz."""
        return datetime.fromisoformat(s).replace(tzinfo=self._tz)


def _first_future(events: list[datetime], now: datetime) -> datetime:
    for e in events:
        if e >= now:
            return e
    # All known events are in the past — fall back to the latest one rather
    # than raising. Aggregation can choose to hide markers if both events
    # fall outside the chart window.
    return events[-1]
