"""Source-shaped config blocks + small utilities shared across sources.

These are the pydantic models that describe HOW to call a data source -
provider name, API key env var, HA base URL + sensor mapping, etc. They
live with the sources so a panel's config can compose them without the
panel having to know which providers exist.

The companion `LocationConfig` lives in the weather panel since
"where the dashboard is" is a panel-level concept, not a source-level
one - a provider takes lat/lon as fetch arguments, not configuration.
"""
from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Union

from pydantic import BaseModel, ConfigDict, Field


class ConfigError(Exception):
    """Raised for any user-fixable config problem (missing env var, etc.).
    The same exception type lives in trmnldash.config and is re-exported
    here for sources that need to raise without depending on the top level.
    """


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClimateBand(_Strict):
    """One ordered threshold → feel-word band used by the forecast
    prose generator. The band applies when the temperature is below
    `below_f`. A band with `below_f=None` is the open-top bucket
    used for temperatures above every prior threshold; exactly one
    open-top band must appear last in each season's list."""
    feel: str
    below_f: float | None = None


class ClimateConfig(_Strict):
    """Per-season temperature → feel-word mappings used by the weather
    panels' forecast prose ("warm with afternoon thunderstorms",
    "frigid and clear", etc.).

    The defaults are calibrated against Burlington, VT - a temperate
    New England climate where a February day at 30°F reads as
    "chilly", not "freezing". A Phoenix deploy probably wants warmer
    summer ceilings; a Miami deploy probably wants the winter band
    bottoming out around 50°F instead of 10°F.

    Each season's list walks ascending by `below_f`: the first band
    whose threshold is greater than the temperature wins; the last
    band (open-top) applies if none match. The lists must be ordered
    correctly - we don't sort at load time.
    """
    winter: list[ClimateBand] = Field(default_factory=lambda: [
        ClimateBand(below_f=10,   feel="frigid"),
        ClimateBand(below_f=25,   feel="cold"),
        ClimateBand(below_f=38,   feel="chilly"),
        ClimateBand(below_f=50,   feel="mild"),
        ClimateBand(below_f=65,   feel="warm"),
        ClimateBand(below_f=None, feel="hot"),
    ])
    summer: list[ClimateBand] = Field(default_factory=lambda: [
        ClimateBand(below_f=55,   feel="cool"),
        ClimateBand(below_f=70,   feel="comfortable"),
        ClimateBand(below_f=80,   feel="warm"),
        ClimateBand(below_f=92,   feel="hot"),
        ClimateBand(below_f=None, feel="very hot"),
    ])
    shoulder: list[ClimateBand] = Field(default_factory=lambda: [
        ClimateBand(below_f=25,   feel="cold"),
        ClimateBand(below_f=40,   feel="chilly"),
        ClimateBand(below_f=58,   feel="cool"),
        ClimateBand(below_f=72,   feel="comfortable"),
        ClimateBand(below_f=82,   feel="warm"),
        ClimateBand(below_f=None, feel="hot"),
    ])

    def bands_for_month(self, month: int) -> list[ClimateBand]:
        if month in (12, 1, 2):
            return self.winter
        if month in (6, 7, 8):
            return self.summer
        return self.shoulder


# Either a single entity_id, or a list of them. A single value is used
# as-is; a list is averaged at aggregation time.
SensorRef = Union[str, list[str], None]


class WeatherProvider(str, Enum):
    OPEN_METEO = "open-meteo"
    NWS = "nws"
    PIRATE = "pirate"
    OPENWEATHERMAP = "openweathermap"


class ForecastProvider(str, Enum):
    """Source for the human-written TODAY/TONIGHT prose chunks.

    NWS exposes a `shortForecast` string per 12-hour period. DERIVE falls
    back to aggregate._summarize, which composes a feel-word summary
    from hourly temp/humidity/precip/cloud."""
    DERIVE = "derive"
    NWS = "nws"


class WeatherConfig(_Strict):
    provider: WeatherProvider = WeatherProvider.OPEN_METEO
    api_key_env: str | None = Field(
        default=None,
        description="Env var holding the API key, if the provider needs one",
    )
    hours: int = Field(default=24, ge=1, le=168, description="Forecast horizon in hours")
    forecast_provider: ForecastProvider = Field(
        default=ForecastProvider.DERIVE,
        description=("Source for TODAY/TONIGHT prose. 'derive' composes from "
                     "hourly numerics; 'nws' uses api.weather.gov shortForecast."),
    )


class SensorsConfig(_Strict):
    """Maps weather-panel fields to HA entity IDs.

    Each field is either a string (single sensor, used as-is) or a list
    of strings (averaged at aggregation time). Any field can be omitted.
    """
    outdoor_temp_f: SensorRef = None
    outdoor_humidity: SensorRef = None
    indoor_temp_f: SensorRef = None
    indoor_humidity: SensorRef = None


class HomeAssistantConfig(_Strict):
    base_url: str = Field(..., description="e.g. http://homeassistant.local:8123")
    token_env: str = Field("HA_TOKEN", description="Env var name for the long-lived token")
    sensors: SensorsConfig = SensorsConfig()


class CalendarRef(_Strict):
    """One Google Calendar to pull events from."""
    id: str = Field(..., description=(
        "Calendar ID. Use 'primary' for the account's main calendar, or the "
        "calendar's `<hash>@group.calendar.google.com` address for shared ones."
    ))
    label: str = Field(..., description=(
        "Short display name shown next to each event ('Work', 'Family', ...). "
        "Keeps the rendered agenda readable without spelling out the calendar address."
    ))


class GoogleCalendarConfig(_Strict):
    """OAuth-based read access to one or more Google Calendars.

    Credentials are an OAuth desktop client (created in Google Cloud
    Console -> APIs & Services -> Credentials). The refresh token is
    generated by running `scripts/google_auth.py` against the
    credentials once and stored in `token_file`; the deploy mounts both
    files into the container so the source can mint short-lived access
    tokens on demand.
    """
    credentials_file: Path = Field(..., description=(
        "Path to the OAuth client JSON (the file downloaded from Google "
        "Cloud Console)."
    ))
    token_file: Path = Field(..., description=(
        "Path to the refresh-token JSON written by scripts/google_auth.py. "
        "Generated once, then mounted into the container at deploy time."
    ))
    calendars: list[CalendarRef] = Field(..., min_length=1)


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


__all__ = [
    "CalendarRef",
    "ClimateBand",
    "ClimateConfig",
    "ConfigError",
    "ForecastProvider",
    "GoogleCalendarConfig",
    "HomeAssistantConfig",
    "SensorRef",
    "SensorsConfig",
    "WeatherConfig",
    "WeatherProvider",
    "as_sensor_list",
    "require_env",
]
