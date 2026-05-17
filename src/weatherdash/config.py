"""Config schema + YAML loader.

A deployable instance is fully configured by one `config.yaml`. The schema
covers the location, weather provider choice, Home Assistant connection
+ sensor mapping, render cadence, and HTTP serve settings.

Secrets live in env vars referenced by name (e.g. `token_env: HA_TOKEN`),
not inline strings, so a config file is safe to commit to a public repo.

Usage:
    cfg = load_config(Path("config.yaml"))     # or env var WEATHERDASH_CONFIG
    token = require_env(cfg.home_assistant.token_env)
"""
from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ConfigError(Exception):
    """Raised for any user-fixable config problem (missing file, bad schema, missing env var)."""


# Either a single entity_id, or a list of them. A single value is used as-is;
# a list is averaged at aggregation time.
SensorRef = Union[str, list[str], None]


class WeatherProvider(str, Enum):
    OPEN_METEO = "open-meteo"
    NWS = "nws"
    PIRATE = "pirate"
    OPENWEATHERMAP = "openweathermap"


class _Strict(BaseModel):
    # Reject unknown keys so typos surface as errors, not silent drops.
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


class WeatherConfig(_Strict):
    provider: WeatherProvider = WeatherProvider.OPEN_METEO
    api_key_env: str | None = Field(
        default=None,
        description="Name of the env var holding the API key, if the provider requires one",
    )
    hours: int = Field(default=24, ge=1, le=168, description="Forecast horizon in hours")


class SensorsConfig(_Strict):
    """Maps dashboard fields to HA entity IDs.

    Each field is either a string (single sensor, used as-is) or a list of
    strings (averaged at aggregation time). Any field can be omitted; the
    aggregation layer falls back to the weather API where applicable, or
    hides the related card.
    """
    outdoor_temp_f: SensorRef = None
    outdoor_humidity: SensorRef = None
    indoor_temp_f: SensorRef = None
    indoor_humidity: SensorRef = None


class HomeAssistantConfig(_Strict):
    base_url: str = Field(..., description="e.g. http://homeassistant.local:8123")
    token_env: str = Field("HA_TOKEN", description="Env var name for the long-lived token")
    sensors: SensorsConfig = SensorsConfig()


class RenderConfig(_Strict):
    output_path: Path = Path("/data/dashboard.png")
    refresh_minutes: int = Field(default=10, ge=1, le=180)


class ServeConfig(_Strict):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    secret_path: str | None = Field(
        default=None,
        description=("If set, PNG is served at /<secret_path>/dashboard.png. "
                     "Treat as a shared secret. Leave unset only for trusted local networks."),
    )


class Config(_Strict):
    location: LocationConfig
    weather: WeatherConfig = WeatherConfig()
    home_assistant: HomeAssistantConfig
    render: RenderConfig = RenderConfig()
    serve: ServeConfig = ServeConfig()


def load_config(path: Path | None = None) -> Config:
    """Load + validate a config file. Raises `ConfigError` with a human-readable
    message on any failure (missing file, bad YAML, schema violations)."""
    if path is None:
        env_path = os.environ.get("WEATHERDASH_CONFIG")
        if not env_path:
            raise ConfigError(
                "No config path provided. Pass --config <path> or set WEATHERDASH_CONFIG."
            )
        path = Path(env_path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping, got {type(raw).__name__}")
    try:
        return Config.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(_format_validation_errors(e, path)) from e


def _format_validation_errors(exc: ValidationError, path: Path) -> str:
    lines = [f"Config errors in {path}:"]
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "<root>"
        # Pydantic's "msg" is human-readable on its own; skip the type tag.
        lines.append(f"  {loc}: {err['msg']}")
    return "\n".join(lines)


def require_env(name: str) -> str:
    """Resolve an env-var reference from config. Fails loudly if unset."""
    val = os.environ.get(name)
    if val is None or val == "":
        raise ConfigError(f"Environment variable {name!r} is referenced in config but not set")
    return val


def as_sensor_list(ref: SensorRef) -> list[str]:
    """Normalize a SensorRef to a (possibly empty) list of entity IDs."""
    if ref is None:
        return []
    if isinstance(ref, str):
        return [ref]
    return list(ref)
