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


class TuningConfig(_Strict):
    """Layout calibration for the weather panel.

    Defaults match the template's hand-picked values, so an unconfigured
    deploy renders identically to before this block existed. Use
    scripts/tune_studio.py to find values interactively, then paste the
    resulting block under `dashboard.layout.config.tuning:` in config.yaml.

    Font sizes are CSS pixels at the panel's native 1872x1404 viewport;
    the panel renders the template at exactly those dimensions before
    quantize, so 1 CSS px == 1 panel pixel.
    """
    # How much of the hourly forecast to graph. 24 shows a full day plus
    # the start of tomorrow; 16-18 typically covers "today and tonight"
    # or "tonight and tomorrow morning" depending on render time, with
    # ~30% more horizontal space per bar.
    chart_hours: int = Field(default=24, ge=8, le=48)

    # TEMP FORECAST card layout. 'stacked' keeps HIGH on top of LOW;
    # 'side-by-side' puts them in a single row, freeing vertical space
    # in the summary column for bigger OUTSIDE / INSIDE readings.
    summary_layout: Literal["stacked", "side-by-side"] = "stacked"

    # OUTSIDE card font sizes (CSS px).
    outside_temp_fs: int = Field(default=138, ge=40, le=400)
    outside_tempsup_fs: int = Field(default=60, ge=12, le=200)
    outside_trend_fs: int = Field(default=42, ge=12, le=120)
    outside_hum_fs: int = Field(default=70, ge=20, le=200)

    # INSIDE card font sizes (CSS px).
    inside_temp_fs: int = Field(default=64, ge=20, le=300)
    inside_tempsup_fs: int = Field(default=28, ge=8, le=100)
    inside_sep_fs: int = Field(default=44, ge=12, le=120)
    inside_hum_fs: int = Field(default=52, ge=16, le=200)

    # TEMP FORECAST card font sizes (CSS px).
    forecast_big_fs: int = Field(default=104, ge=40, le=300)
    forecast_arrow_fs: int = Field(default=88, ge=20, le=200)
    forecast_when_fs: int = Field(default=28, ge=10, le=100)
    forecast_rh_fs: int = Field(default=20, ge=8, le=80)

    # Summary column geometry. col_left_width is the fixed pixel width
    # of the left summary column (date / OUTSIDE / TEMP FORECAST /
    # INSIDE); the chart absorbs the remainder. The three *_weight
    # values are CSS-grid fr units sharing the column's height. INSIDE's
    # default is intentionally small so its compact reading docks at
    # the bottom; bump it up to give INSIDE more vertical room.
    col_left_width: int = Field(default=460, ge=240, le=900)
    outside_weight: float = Field(default=1.15, ge=0.1, le=10.0)
    forecast_weight: float = Field(default=1.0, ge=0.1, le=10.0)
    inside_weight: float = Field(default=0.3, ge=0.1, le=10.0)

    def css_overrides(self) -> str:
        """Build a :root { --X: Ypx; } block for font-size overrides.

        Only emits properties that differ from the dataclass defaults so
        a default-valued TuningConfig produces an empty string (no
        runtime override, template defaults rule).
        """
        defaults = TuningConfig()
        mapping = {
            "--outside-temp-fs":         ("outside_temp_fs", "px"),
            "--outside-tempsup-fs":      ("outside_tempsup_fs", "px"),
            "--outside-trend-fs":        ("outside_trend_fs", "px"),
            "--outside-hum-fs":          ("outside_hum_fs", "px"),
            "--inside-temp-fs":          ("inside_temp_fs", "px"),
            "--inside-tempsup-fs":       ("inside_tempsup_fs", "px"),
            "--inside-sep-fs":           ("inside_sep_fs", "px"),
            "--inside-hum-fs":           ("inside_hum_fs", "px"),
            "--forecast-big-fs":         ("forecast_big_fs", "px"),
            "--forecast-arrow-fs":       ("forecast_arrow_fs", "px"),
            "--forecast-when-fs":        ("forecast_when_fs", "px"),
            "--forecast-rh-fs":          ("forecast_rh_fs", "px"),
            "--col-left-width":          ("col_left_width", "px"),
            "--col-left-row-outside":    ("outside_weight", "fr"),
            "--col-left-row-forecast":   ("forecast_weight", "fr"),
            "--col-left-row-inside":     ("inside_weight", "fr"),
        }
        lines = []
        for var, (field_name, unit) in mapping.items():
            cur = getattr(self, field_name)
            if cur != getattr(defaults, field_name):
                lines.append(f"  {var}: {cur}{unit};")
        if not lines:
            return ""
        return ":root {\n" + "\n".join(lines) + "\n}"


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
    tuning: TuningConfig = Field(
        default_factory=TuningConfig,
        description=("Layout calibration: chart hours, summary stacked vs "
                     "side-by-side, and per-element font sizes. Defaults "
                     "match the template; tweak via scripts/tune_studio.py."),
    )


__all__ = ["LocationConfig", "TuningConfig", "WeatherLandscapeConfig"]
