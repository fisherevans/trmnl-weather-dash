"""WeatherSource Protocol + the normalized forecast shape providers return.

All providers translate their wire format into NormalizedForecast. The
aggregation layer (#5) consumes only this shape — providers swap freely.

Units are locked at F / mph / mm / % across all providers; the per-provider
implementation does whatever unit math it needs.

`weather_code` is the WMO code (Open-Meteo's native). Other providers
map their codes into the WMO space so the icon mapper in aggregation has
a single namespace to deal with.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


class ForecastError(Exception):
    """Raised by a provider when the forecast cannot be fetched (network, auth, parse)."""


@dataclass(frozen=True)
class HourlyPoint:
    timestamp: datetime         # tz-aware, in the configured timezone
    temp_f: float
    precip_mm: float            # rain (mm) — quantitative forecast amount
    cloud_pct: int              # 0..100
    weather_code: int           # WMO code
    is_day: bool
    humidity_pct: int | None = None    # not all providers expose hourly humidity
    snow_cm: float = 0.0        # snow accumulation (cm; ~10x rain mm at equal volume)
    precip_prob_pct: int = 0    # 0..100 probability of any precip in this hour
    wind_mph: float = 0.0       # sustained wind speed
    wind_gust_mph: float = 0.0  # gust speed (== wind_mph when provider lacks gust series)
    wind_dir: str = ""          # 16-pt cardinal (e.g. 'NW'), empty when unknown


@dataclass(frozen=True)
class CurrentObservation:
    temp_f: float
    humidity_pct: int           # 0..100
    wind_mph: float
    wind_gust_mph: float
    wind_dir: str               # cardinal: N, NNE, NE, ..., NNW
    weather_code: int
    is_day: bool


@dataclass(frozen=True)
class SunInfo:
    sunrise: datetime           # next sunrise after fetch time
    sunset: datetime            # next sunset after fetch time


@dataclass(frozen=True)
class NormalizedForecast:
    hourly: list[HourlyPoint]
    current: CurrentObservation
    sun: SunInfo


@dataclass(frozen=True)
class ForecastPeriod:
    """A multi-hour forecast period with human-written prose.

    NWS exposes these natively as 12-hour day/night blocks ("Today",
    "Tonight", "Tuesday Night", ...). Open-Meteo has no equivalent;
    aggregate.py derives a stand-in summary from the hourly numerics
    when no ForecastSource is configured.

    Two roles for `label`:
      - Day chunks: the provider's own day name ("Today", "Tuesday").
      - Night chunks: the provider's night name ("Tonight", "Tuesday
        Night"). Aggregate normalizes these to "TODAY" / "TONIGHT" /
        "TOMORROW" in the header chip — provider label is kept here
        as a fallback for callers that want to surface it raw.
    """
    label: str
    start: datetime              # tz-aware, inclusive
    end: datetime                # tz-aware, exclusive
    is_day: bool
    short_forecast: str          # 2-5 words: "Mostly Sunny", "Chance Showers"


@runtime_checkable
class WeatherSource(Protocol):
    """Hourly numerics + current obs + sun events. Drives the chart."""
    def fetch(self, lat: float, lon: float, hours: int) -> NormalizedForecast: ...


@runtime_checkable
class ForecastSource(Protocol):
    """Optional source for human-written period prose (NWS-style
    'shortForecast' strings). When absent, aggregate.py derives an
    equivalent from the hourly time-series. Implementations: NWS via
    /gridpoints/{office}/{x},{y}/forecast."""
    def fetch_periods(self, lat: float, lon: float) -> list[ForecastPeriod]: ...


# Helpers shared across providers.

_CARDINALS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def deg_to_cardinal(deg: float) -> str:
    """Compass degrees (0..360, 0=N) -> 16-point cardinal string."""
    idx = round(deg / 22.5) % 16
    return _CARDINALS[idx]
