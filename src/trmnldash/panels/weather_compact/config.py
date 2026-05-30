"""Compact-weather panel config.

Reuses LocationConfig and the source-shaped WeatherConfig that the
weather_landscape panel + sources/config.py already define, so the
deployed YAML has the same shape for the lat/lon/provider triplet.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..weather_landscape.config import LocationConfig
from ...sources.config import ClimateConfig, WeatherConfig


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WeatherCompactConfig(_Strict):
    """Per-deploy config for the compact weather panel."""
    location: LocationConfig
    weather: WeatherConfig = WeatherConfig()
    climate: ClimateConfig = ClimateConfig()


__all__ = ["WeatherCompactConfig"]
