"""Console entrypoint. Subcommands: render (offline json), render-live
(fetch + aggregate + render), setup (chromium install), validate (config
check), serve (long-running scheduler + HTTP server)."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import Config, ConfigError, DashboardEntry, load_config
from .engine.pipeline import run_once
from .engine.render import setup_browser
from .panels.weather_landscape import render_from_json
from .sources.base import ForecastError


def cmd_render(args: argparse.Namespace) -> int:
    render_from_json(
        Path(args.data),
        Path(args.out),
        quantize=not args.no_quantize,
        keep_html=args.keep_html,
    )
    print(f"wrote {args.out}")
    return 0


def cmd_setup(_args: argparse.Namespace) -> int:
    return setup_browser()


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else None
    try:
        cfg = load_config(path)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(
        f"config OK: {len(cfg.dashboards)} dashboard(s) on "
        f"http://{cfg.serve.host}:{cfg.serve.port}/"
    )
    for entry in cfg.dashboards:
        d = entry.dashboard
        panel_names = _layout_panel_names(d.layout)
        print(
            f"  {entry.name}: device={d.device.width}x{d.device.height} "
            f"palette={d.device.palette} rotate={d.device.rotate} "
            f"panels=[{', '.join(panel_names)}] "
            f"refresh={entry.render.refresh_minutes}m "
            f"-> /{entry.serve.secret_path}/dashboard.png "
            f"-> {entry.render.output_path}"
        )
    return 0


def _layout_panel_names(node) -> list[str]:
    from .engine.layout import HStack, PanelSlot, VStack
    if isinstance(node, PanelSlot):
        return [node.panel]
    body = node.vstack if isinstance(node, VStack) else node.hstack
    names: list[str] = []
    for child in body.children:
        names.extend(_layout_panel_names(child))
    return names


def cmd_serve(args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else None
    try:
        cfg = load_config(path)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    # Long-running service — bump default log level to INFO so the per-render
    # timing lines from server.py are visible without --verbose.
    logging.getLogger().setLevel(logging.INFO)
    from .engine.server import run_server
    run_server(cfg)
    return 0


def cmd_render_live(args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else None
    try:
        cfg = load_config(path)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    try:
        entry = _resolve_dashboard(cfg, args.dashboard)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else entry.render.output_path
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        stats = run_once(entry, out, quantize=not args.no_quantize)
    except ForecastError as e:
        print(f"forecast fetch failed: {e}", file=sys.stderr)
        return 1
    except ConfigError as e:
        # require_env() can raise this if a referenced env var isn't set
        print(str(e), file=sys.stderr)
        return 1
    _print_summary(entry, stats)
    return 0


def _resolve_dashboard(cfg: Config, name: str | None) -> DashboardEntry:
    """Pick a dashboard from the loaded config. With multiple dashboards
    declared, the caller must name one - we don't guess. With exactly
    one declared, the name is optional and we fall through to it."""
    if name is None:
        if len(cfg.dashboards) == 1:
            return cfg.dashboards[0]
        names = [e.name for e in cfg.dashboards]
        raise ConfigError(
            f"config has {len(cfg.dashboards)} dashboards: "
            f"pass --dashboard <name> to pick one (available: {', '.join(names)})"
        )
    for entry in cfg.dashboards:
        if entry.name == name:
            return entry
    names = [e.name for e in cfg.dashboards]
    raise ConfigError(
        f"no dashboard named {name!r} in config (available: {', '.join(names)})"
    )


def _print_summary(entry: DashboardEntry, stats) -> None:
    print(f"wrote {stats.output_path}  (dashboard={entry.name})")
    for p in stats.panels:
        print(f"  {p.name}: fetch={p.fetch_ms:.0f}ms render={p.render_ms:.0f}ms")
    print(f"  compose+rotate: {stats.compose_ms:.0f}ms")
    print(f"  quantize: {stats.quantize_ms:.0f}ms")
    print(f"  total: {stats.total_ms:.0f}ms")


def main(argv: list[str] | None = None) -> int:
    # WARNINGs from sources/{openmeteo,homeassistant} should hit stderr by
    # default — per-sensor skips and retry chatter are useful signals.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    ap = argparse.ArgumentParser(prog="trmnldash")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_render = sub.add_parser("render", help="render a data.json file to a PNG (offline)")
    p_render.add_argument("--data", default="data.json")
    p_render.add_argument("--out", default="output.png")
    p_render.add_argument("--keep-html", action="store_true",
                          help="leave the rendered HTML beside the PNG")
    p_render.add_argument("--no-quantize", action="store_true",
                          help="skip the 16-gray snap (faster iteration)")
    p_render.set_defaults(func=cmd_render)

    p_live = sub.add_parser("render-live", help="fetch live data + render one dashboard once")
    p_live.add_argument("--config", default=None,
                        help="path to config.yaml (or set TRMNLDASH_CONFIG)")
    p_live.add_argument("--dashboard", default=None,
                        help="name of the dashboard to render (required with >1 dashboard)")
    p_live.add_argument("--out", default=None,
                        help="output PNG path (default: dashboard's render.output_path)")
    p_live.add_argument("--no-quantize", action="store_true",
                        help="skip the palette snap (faster iteration)")
    p_live.set_defaults(func=cmd_render_live)

    p_serve = sub.add_parser("serve", help="long-running scheduler + HTTP server")
    p_serve.add_argument("--config", default=None,
                         help="path to config.yaml (or set TRMNLDASH_CONFIG)")
    p_serve.set_defaults(func=cmd_serve)

    p_setup = sub.add_parser("setup", help="install bundled chromium then exit")
    p_setup.set_defaults(func=cmd_setup)

    p_validate = sub.add_parser("validate", help="load and validate a config.yaml")
    p_validate.add_argument("--config", default=None,
                            help="path to config.yaml (or set TRMNLDASH_CONFIG)")
    p_validate.set_defaults(func=cmd_validate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
