"""End-to-end pipeline: fetch weather + HA -> aggregate -> render.

Shared between the one-shot `render-live` CLI subcommand and the
scheduler loop in #7. Both want the same fetch/aggregate flow, the
same partial-failure semantics, and the same timing instrumentation.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .aggregate import build_context
from .config import Config, ConfigError, as_sensor_list
from .render import render_to_png
from .sources.factory import make_weather_source
from .sources.homeassistant import HomeAssistantError, make_ha_client

logger = logging.getLogger(__name__)


@dataclass
class RenderStats:
    """Per-stage timing + outcome counts. Used for the CLI summary and the
    scheduler's /healthz endpoint (#7)."""
    weather_ms: float = 0.0
    ha_ms: float = 0.0
    aggregate_ms: float = 0.0
    render_ms: float = 0.0
    total_ms: float = 0.0
    ha_sensors_requested: int = 0
    ha_sensors_got: int = 0
    ha_failed: bool = False
    output_path: Path | None = None


def run_once(config: Config, out_path: Path, *, quantize: bool = True) -> RenderStats:
    """Fetch live data, aggregate, render, return per-stage stats.

    Failure semantics:
    - Weather fetch failure: raises (no forecast -> no usable dashboard).
    - HA failure: logs a warning, sets stats.ha_failed, continues with
      whatever the weather API gave us. Outdoor temps fall back to the
      API current observation; indoor shows "--".
    """
    stats = RenderStats(output_path=out_path)
    t_total = time.monotonic()

    # ── Weather ──────────────────────────────────────────────────────────
    t0 = time.monotonic()
    weather_src = make_weather_source(config.weather, timezone=config.location.timezone)
    weather = weather_src.fetch(config.location.lat, config.location.lon, config.weather.hours)
    stats.weather_ms = (time.monotonic() - t0) * 1000

    # ── Home Assistant (optional / best-effort) ──────────────────────────
    entity_ids = _collect_entity_ids(config)
    stats.ha_sensors_requested = len(entity_ids)
    ha_readings: dict = {}
    if entity_ids:
        t0 = time.monotonic()
        try:
            ha_client = make_ha_client(config.home_assistant)
            ha_readings = ha_client.fetch_states(entity_ids)
        except (HomeAssistantError, ConfigError) as e:
            # ConfigError catches missing HA_TOKEN env var. We don't hard-
            # fail because a missing indoor reading shouldn't take down the
            # whole dashboard — the rendering falls back to weather API
            # for outdoor + "--" placeholders for indoor.
            logger.warning("HA disabled: %s — falling back to weather API", e)
            stats.ha_failed = True
        stats.ha_ms = (time.monotonic() - t0) * 1000
    stats.ha_sensors_got = len(ha_readings)

    # ── Aggregate ────────────────────────────────────────────────────────
    t0 = time.monotonic()
    ctx = build_context(config, weather, ha_readings)
    stats.aggregate_ms = (time.monotonic() - t0) * 1000

    # ── Render ───────────────────────────────────────────────────────────
    t0 = time.monotonic()
    render_to_png(ctx, out_path, quantize=quantize)
    stats.render_ms = (time.monotonic() - t0) * 1000

    stats.total_ms = (time.monotonic() - t_total) * 1000
    return stats


def _collect_entity_ids(config: Config) -> list[str]:
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
