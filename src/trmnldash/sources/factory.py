"""Build a WeatherSource (and optional ForecastSource) from a WeatherConfig.

The two roles are independent: hourly numerics can come from one
provider while period prose comes from another (or none, in which case
aggregate.py derives prose from the hourly data). A single NWSProvider
instance can fill both roles and share its /points cache between them.
"""
from __future__ import annotations

from .config import (ForecastProvider, WeatherConfig, WeatherProvider,
                     require_env)
from .base import ForecastSource, WeatherSource
from .nws import NWSProvider
from .openmeteo import OpenMeteoProvider


def make_weather_source(cfg: WeatherConfig, *, timezone: str) -> WeatherSource:
    api_key = require_env(cfg.api_key_env) if cfg.api_key_env else None
    if cfg.provider == WeatherProvider.OPEN_METEO:
        return OpenMeteoProvider(timezone=timezone, api_key=api_key)
    if cfg.provider == WeatherProvider.NWS:
        return NWSProvider(timezone=timezone)
    raise NotImplementedError(
        f"weather provider {cfg.provider.value!r} is not implemented "
        f"(currently shipped: open-meteo, nws)"
    )


def make_forecast_source(
    cfg: WeatherConfig,
    *,
    timezone: str,
    hourly_source: WeatherSource | None = None,
) -> ForecastSource | None:
    """Return a ForecastSource if one is configured, else None.

    If the hourly source is already an NWSProvider AND the forecast
    provider is also nws, reuse the same instance so the /points
    response is shared rather than re-fetched.
    """
    if cfg.forecast_provider == ForecastProvider.DERIVE:
        return None
    if cfg.forecast_provider == ForecastProvider.NWS:
        if isinstance(hourly_source, NWSProvider):
            return hourly_source
        return NWSProvider(timezone=timezone)
    raise NotImplementedError(f"forecast provider {cfg.forecast_provider!r} not implemented")
