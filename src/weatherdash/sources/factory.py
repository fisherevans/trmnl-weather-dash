"""Build a WeatherSource from a WeatherConfig."""
from __future__ import annotations

from ..config import WeatherConfig, WeatherProvider, require_env
from .base import WeatherSource
from .openmeteo import OpenMeteoProvider


def make_weather_source(cfg: WeatherConfig, *, timezone: str) -> WeatherSource:
    api_key = require_env(cfg.api_key_env) if cfg.api_key_env else None
    if cfg.provider == WeatherProvider.OPEN_METEO:
        return OpenMeteoProvider(timezone=timezone, api_key=api_key)
    raise NotImplementedError(
        f"weather provider {cfg.provider.value!r} not implemented yet "
        f"(tracked in trmnl-weather-dash#11/#12)"
    )
