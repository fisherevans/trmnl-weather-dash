"""Weather-landscape panel config schema.

This is the pydantic shape that the panel's `config:` block in the
dashboard YAML validates against. The engine's config loader looks up
this schema by panel name and parses the YAML's per-panel config
through it.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...sources.config import HomeAssistantConfig, WeatherConfig


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocationConfig(_Strict):
    lat: float = Field(..., description="Decimal degrees, [-90, 90]")
    lon: float = Field(..., description="Decimal degrees, [-180, 180]")
    timezone: str = Field("UTC", description="IANA timezone (e.g. 'America/New_York')")

    @field_validator("lat")
    @classmethod
    def _lat_in_range(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError(f"must be in [-90, 90], got {v}")
        return v

    @field_validator("lon")
    @classmethod
    def _lon_in_range(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError(f"must be in [-180, 180], got {v}")
        return v


class WeatherLandscapeConfig(_Strict):
    """Full config for the weather_landscape panel.

    Goes in a dashboard YAML under `layout.config:` when this panel is
    used as a leaf slot.
    """
    location: LocationConfig
    weather: WeatherConfig = WeatherConfig()
    home_assistant: HomeAssistantConfig
    summary_side: Literal["left", "right"] = Field(
        default="left",
        description=("Which side of the panel holds the date + OUTSIDE + "
                     "TEMP FORECAST + INSIDE stack. 'right' mirrors the "
                     "layout horizontally."),
    )


__all__ = ["LocationConfig", "WeatherLandscapeConfig"]
