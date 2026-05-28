"""Live-data fetching for the weather_landscape panel.

Called by the dashboard pipeline when the panel needs to build its
render context from real data sources. Same flow that used to live in
engine/pipeline.py - moved here so each panel owns its own fetch logic
and the engine stays panel-agnostic.

Failure model:
- Weather fetch failure: raises. Without a forecast the panel can't render.
- HA failure: logs a warning, continues. Outdoor temps fall back to the
  weather API's current observation; indoor sensors show "--".
- Forecast-prose fetch failure: logs a warning, continues. The aggregate
  layer derives equivalent text from the hourly numerics.
"""
from __future__ import annotations

import logging

from .aggregate import build_context
from .config import WeatherLandscapeConfig
from ...sources.base import ForecastError
from ...sources.config import ConfigError, as_sensor_list
from ...sources.factory import make_forecast_source, make_weather_source
from ...sources.homeassistant import HomeAssistantError, make_ha_client

logger = logging.getLogger(__name__)


def build_live_context(config: WeatherLandscapeConfig) -> dict:
    """Fetch live weather + HA, aggregate, return the render context dict."""
    # ── Weather ──────────────────────────────────────────────────────────
    weather_src = make_weather_source(config.weather, timezone=config.location.timezone)
    weather = weather_src.fetch(config.location.lat, config.location.lon, config.weather.hours)
    forecast_periods = None
    forecast_src = make_forecast_source(
        config.weather,
        timezone=config.location.timezone,
        hourly_source=weather_src,
    )
    if forecast_src is not None:
        try:
            forecast_periods = forecast_src.fetch_periods(
                config.location.lat, config.location.lon
            )
        except ForecastError as e:
            logger.warning("forecast prose unavailable: %s - falling back to derived summary", e)

    # ── Home Assistant (optional / best-effort) ──────────────────────────
    entity_ids = _collect_entity_ids(config)
    ha_readings: dict = {}
    if entity_ids:
        try:
            ha_client = make_ha_client(config.home_assistant)
            ha_readings = ha_client.fetch_states(entity_ids)
        except (HomeAssistantError, ConfigError) as e:
            # ConfigError catches a missing HA_TOKEN env var. Don't hard-
            # fail - a missing indoor reading shouldn't take down the
            # whole panel.
            logger.warning("HA disabled: %s - falling back to weather API", e)

    return build_context(config, weather, ha_readings, forecast_periods=forecast_periods)


def _collect_entity_ids(config: WeatherLandscapeConfig) -> list[str]:
    """Flatten the configured sensor refs into a unique entity_id list."""
    seen: dict[str, None] = {}     # dict preserves insertion order
    sensors = config.home_assistant.sensors
    for ref in (
        sensors.outdoor_temp_f,
        sensors.outdoor_humidity,
        sensors.indoor_temp_f,
        sensors.indoor_humidity,
    ):
        for eid in as_sensor_list(ref):
            seen[eid] = None
    return list(seen.keys())
