"""Top-level YAML config: one or more dashboards + a shared HTTP listener.

A deploy is fully described by one `config.yaml`. The v1 shape:

    dashboards:
      - name: <slug>
        dashboard:
          device: {width, height, palette, rotate}
          layout: <PanelSlot | VStack | HStack>     # engine/layout.py
        render:
          output_path: /data/<name>.png
          refresh_minutes: 5
        serve:
          secret_path: <random hex>                  # mounts the PNG at
                                                     # /<secret_path>/dashboard.png
      - name: ...
        ...

    serve:                                           # shared listener
      host: 0.0.0.0
      port: 8080

Each dashboard runs on its own scheduler at its own interval and is
mounted at its own secret_path on the shared port. /healthz reports
green only when every dashboard's most recent render succeeded.

Per-panel config blocks live inside the layout tree (PanelSlot.config)
and are validated against the panel's own schema after the layout is
parsed - that's done in `load_config`, since pydantic doesn't know
which schema applies until the panel name is resolved.

No backwards compatibility with the v0.x `dashboard:` (singular) shape -
deploys migrate to `dashboards:` cleanly at v1.0.0.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import (BaseModel, ConfigDict, Field, ValidationError,
                      field_validator, model_validator)

from .engine.layout import DeviceProfile, HStack, Layout, PanelSlot, VStack
from .engine.panel import PanelLookupError, lookup
from .sources.config import (ConfigError, SensorRef, as_sensor_list,
                             require_env)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DashboardConfig(_Strict):
    """The actual renderable: device profile + a layout tree."""
    device: DeviceProfile
    layout: Layout


class RenderConfig(_Strict):
    """Per-dashboard output + cadence. Each dashboard gets its own
    output_path and refresh_minutes; the scheduler runs one loop per
    dashboard, so different dashboards can refresh at independent
    intervals (e.g. 1 min weather, 10 min calendar)."""
    output_path: Path
    refresh_minutes: int = Field(default=5, ge=1, le=180)


class DashboardServeConfig(_Strict):
    """Per-dashboard HTTP exposure. `secret_path` discriminates the URL:
    `GET /<secret_path>/dashboard.png` serves THIS dashboard. Different
    dashboards must use different secret_paths so the routes don't
    collide. Required - the no-auth case isn't supported at v1."""
    secret_path: str = Field(
        ..., min_length=4,
        description=("URL path prefix that gates this dashboard's PNG. "
                     "Treat as a password - anyone with the URL reads the dashboard."),
    )

    @field_validator("secret_path")
    @classmethod
    def _no_slashes(cls, v: str) -> str:
        if "/" in v or v.startswith("."):
            raise ValueError("secret_path must be a single URL segment (no slashes, no leading dot)")
        return v


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


class DashboardEntry(_Strict):
    """One dashboard's complete description: a human-friendly name (used
    in logs and as the healthz key), the render target (device + layout),
    when/where to write its PNG, and how it's exposed over HTTP."""
    name: str = Field(...)
    dashboard: DashboardConfig
    render: RenderConfig
    serve: DashboardServeConfig

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                "name must be a short slug: [a-z0-9-]+, starting with [a-z0-9], max 31 chars"
            )
        return v


class GlobalServeConfig(_Strict):
    """The HTTP listener that hosts every dashboard's PNG. host/port
    apply to the whole process; per-dashboard auth lives in each
    dashboard's own `serve.secret_path`."""
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)


class Config(_Strict):
    dashboards: list[DashboardEntry] = Field(..., min_length=1)
    serve: GlobalServeConfig = GlobalServeConfig()

    @model_validator(mode="after")
    def _check_uniqueness(self) -> "Config":
        names: dict[str, int] = {}
        secrets: dict[str, str] = {}
        outputs: dict[str, str] = {}
        for entry in self.dashboards:
            if entry.name in names:
                raise ValueError(f"duplicate dashboard name: {entry.name!r}")
            names[entry.name] = 1
            if entry.serve.secret_path in secrets:
                other = secrets[entry.serve.secret_path]
                raise ValueError(
                    f"dashboards {other!r} and {entry.name!r} share "
                    f"serve.secret_path={entry.serve.secret_path!r}"
                )
            secrets[entry.serve.secret_path] = entry.name
            out = str(entry.render.output_path)
            if out in outputs:
                other = outputs[out]
                raise ValueError(
                    f"dashboards {other!r} and {entry.name!r} share "
                    f"render.output_path={out!r}"
                )
            outputs[out] = entry.name
        return self


def load_config(path: Path | None = None) -> Config:
    """Load + validate a config file. Raises `ConfigError` with a human-
    readable message on any failure (missing file, bad YAML, schema
    violations, unknown panel, invalid per-panel config, name/secret
    collisions)."""
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
    if "dashboard" in raw and "dashboards" not in raw:
        # The v0.x shape used a singular `dashboard:`. v1 broke that
        # cleanly; surface a pointed migration message rather than a
        # generic "missing field" error.
        raise ConfigError(
            f"{path}: top-level `dashboard:` is no longer supported. "
            f"v1 requires a `dashboards:` list; even a single-dashboard "
            f"deploy is now `dashboards: [{{name: ..., dashboard: ..., "
            f"render: ..., serve: ...}}]`."
        )
    try:
        cfg = Config.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(_format_validation_errors(e, path)) from e

    # Walk each dashboard's layout, look up each panel, and validate the
    # per-panel config block against the panel's pydantic schema.
    for entry in cfg.dashboards:
        _validate_panel_configs(entry.dashboard.layout, path, entry.name)
    return cfg


def _validate_panel_configs(node: Layout, path: Path, dashboard_name: str) -> None:
    if isinstance(node, PanelSlot):
        try:
            panel = lookup(node.panel)
        except PanelLookupError as e:
            raise ConfigError(
                f"{path}: dashboards[{dashboard_name}].layout.{node.panel}: {e}"
            ) from e
        try:
            validated = panel.config_schema.model_validate(node.config)
        except ValidationError as e:
            raise ConfigError(
                _format_validation_errors(
                    e, path,
                    prefix=f"dashboards[{dashboard_name}].layout[{node.panel}].config",
                )
            ) from e
        # Stash the validated config back on the slot; the pipeline reads
        # this attribute rather than parsing again.
        object.__setattr__(node, "config", validated)
        return
    body = node.vstack if isinstance(node, VStack) else node.hstack
    for child in body.children:
        _validate_panel_configs(child, path, dashboard_name)


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
    "DashboardEntry",
    "DashboardServeConfig",
    "DeviceProfile",
    "GlobalServeConfig",
    "RenderConfig",
    "SensorRef",
    "as_sensor_list",
    "load_config",
    "require_env",
]
