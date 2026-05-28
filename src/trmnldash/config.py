"""Top-level YAML config: dashboard + render + serve.

A deploy is fully described by one `config.yaml`. The shape:

    dashboard:
      device: {width, height, palette, rotate}
      layout: <PanelSlot | VStack | HStack>     # see engine/layout.py

    render:
      output_path: /data/dashboard.png
      refresh_minutes: 5

    serve:
      host: 0.0.0.0
      port: 8080
      secret_path: ...

Per-panel config blocks live inside the layout tree (PanelSlot.config)
and are validated against the panel's own schema after the layout is
parsed - that's done in `load_config`, since pydantic doesn't know
which schema applies until the panel name is resolved.

Secrets stay in env vars referenced by name (e.g. `token_env: HA_TOKEN`)
so a config file is safe to commit alongside the dashboard.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .engine.layout import DeviceProfile, HStack, Layout, PanelSlot, VStack
from .engine.panel import PanelLookupError, lookup
from .sources.config import (ConfigError, SensorRef, as_sensor_list,
                             require_env)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DashboardConfig(_Strict):
    device: DeviceProfile
    layout: Layout


class RenderConfig(_Strict):
    output_path: Path = Path("/data/dashboard.png")
    refresh_minutes: int = Field(default=1, ge=1, le=180)


class ServeConfig(_Strict):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    secret_path: str | None = Field(
        default=None,
        description=("If set, PNG is served at /<secret_path>/dashboard.png. "
                     "Treat as a shared secret. Leave unset only for trusted local networks."),
    )


class Config(_Strict):
    dashboard: DashboardConfig
    render: RenderConfig = RenderConfig()
    serve: ServeConfig = ServeConfig()


def load_config(path: Path | None = None) -> Config:
    """Load + validate a config file. Raises `ConfigError` with a human-
    readable message on any failure (missing file, bad YAML, schema
    violations, unknown panel, invalid per-panel config)."""
    if path is None:
        env_path = os.environ.get("TRMNLDASH_CONFIG")
        if not env_path:
            raise ConfigError(
                "No config path provided. Pass --config <path> or set TRMNLDASH_CONFIG."
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
        cfg = Config.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(_format_validation_errors(e, path)) from e

    # Walk the layout tree, look up each panel, and validate its config
    # block against the panel's pydantic schema. Mutates the PanelSlot's
    # `config` field from dict to the validated model instance so the
    # pipeline can pass it straight to the panel.
    _validate_panel_configs(cfg.dashboard.layout, path)
    return cfg


def _validate_panel_configs(node: Layout, path: Path) -> None:
    if isinstance(node, PanelSlot):
        try:
            panel = lookup(node.panel)
        except PanelLookupError as e:
            raise ConfigError(f"{path}: dashboard.layout.{node.panel}: {e}") from e
        try:
            validated = panel.config_schema.model_validate(node.config)
        except ValidationError as e:
            raise ConfigError(
                _format_validation_errors(e, path, prefix=f"dashboard.layout[{node.panel}].config")
            ) from e
        # Stash the validated config back on the slot; the pipeline reads
        # this attribute rather than parsing again.
        object.__setattr__(node, "config", validated)
        return
    body = node.vstack if isinstance(node, VStack) else node.hstack
    for child in body.children:
        _validate_panel_configs(child, path)


def _format_validation_errors(exc: ValidationError, path: Path, *, prefix: str = "") -> str:
    lines = [f"Config errors in {path}:"]
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "<root>"
        if prefix:
            loc = f"{prefix}.{loc}"
        lines.append(f"  {loc}: {err['msg']}")
    return "\n".join(lines)


__all__ = [
    "Config",
    "ConfigError",
    "DashboardConfig",
    "DeviceProfile",
    "RenderConfig",
    "SensorRef",
    "ServeConfig",
    "as_sensor_list",
    "load_config",
    "require_env",
]
