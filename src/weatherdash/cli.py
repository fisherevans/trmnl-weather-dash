"""Console entrypoint. Subcommands: render (offline json), render-live
(fetch + aggregate + render), setup (chromium install), validate (config
check). Scheduler + HTTP server arrive in #7 (`weatherdash serve`)."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import render as render_mod
from .config import ConfigError, load_config
from .pipeline import run_once
from .sources.base import ForecastError


def cmd_render(args: argparse.Namespace) -> int:
    render_mod.render_from_json(
        Path(args.data),
        Path(args.out),
        quantize=not args.no_quantize,
        keep_html=args.keep_html,
    )
    print(f"wrote {args.out}")
    return 0


def cmd_setup(_args: argparse.Namespace) -> int:
    return render_mod.setup_browser()


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else None
    try:
        cfg = load_config(path)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    # Show a one-line OK summary; full dump is too noisy for a routine check.
    print(
        f"config OK: provider={cfg.weather.provider.value} "
        f"location=({cfg.location.lat},{cfg.location.lon}) tz={cfg.location.timezone} "
        f"refresh={cfg.render.refresh_minutes}m port={cfg.serve.port}"
    )
    return 0


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
    from .server import run_server
    run_server(cfg)
    return 0


def cmd_render_live(args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else None
    try:
        cfg = load_config(path)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else cfg.render.output_path
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        stats = run_once(cfg, out, quantize=not args.no_quantize)
    except ForecastError as e:
        print(f"forecast fetch failed: {e}", file=sys.stderr)
        return 1
    except ConfigError as e:
        # require_env() can raise this if a referenced env var isn't set
        print(str(e), file=sys.stderr)
        return 1
    _print_summary(cfg, stats)
    return 0


def _print_summary(cfg, stats) -> None:
    print(f"wrote {stats.output_path}")
    print(f"  weather ({cfg.weather.provider.value}): {stats.weather_ms:.0f}ms")
    if stats.ha_sensors_requested == 0:
        print("  ha: not configured")
    elif stats.ha_failed:
        print(f"  ha: FAILED, fell back to weather API ({stats.ha_ms:.0f}ms)")
    else:
        missing = stats.ha_sensors_requested - stats.ha_sensors_got
        miss_tag = "" if missing == 0 else f" [{missing} missing]"
        print(
            f"  ha: {stats.ha_sensors_got}/{stats.ha_sensors_requested} sensors "
            f"({stats.ha_ms:.0f}ms){miss_tag}"
        )
    print(f"  aggregate: {stats.aggregate_ms:.0f}ms")
    print(f"  render: {stats.render_ms:.0f}ms")
    print(f"  total: {stats.total_ms:.0f}ms")


def main(argv: list[str] | None = None) -> int:
    # WARNINGs from sources/{openmeteo,homeassistant} should hit stderr by
    # default — per-sensor skips and retry chatter are useful signals.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    ap = argparse.ArgumentParser(prog="weatherdash")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_render = sub.add_parser("render", help="render a data.json file to a PNG (offline)")
    p_render.add_argument("--data", default="data.json")
    p_render.add_argument("--out", default="output.png")
    p_render.add_argument("--keep-html", action="store_true",
                          help="leave the rendered HTML beside the PNG")
    p_render.add_argument("--no-quantize", action="store_true",
                          help="skip the 16-gray snap (faster iteration)")
    p_render.set_defaults(func=cmd_render)

    p_live = sub.add_parser("render-live", help="fetch live data + render once")
    p_live.add_argument("--config", default=None,
                        help="path to config.yaml (or set WEATHERDASH_CONFIG)")
    p_live.add_argument("--out", default=None,
                        help="output PNG path (default: config's render.output_path)")
    p_live.add_argument("--no-quantize", action="store_true",
                        help="skip the 16-gray snap (faster iteration)")
    p_live.set_defaults(func=cmd_render_live)

    p_serve = sub.add_parser("serve", help="long-running scheduler + HTTP server")
    p_serve.add_argument("--config", default=None,
                         help="path to config.yaml (or set WEATHERDASH_CONFIG)")
    p_serve.set_defaults(func=cmd_serve)

    p_setup = sub.add_parser("setup", help="install bundled chromium then exit")
    p_setup.set_defaults(func=cmd_setup)

    p_validate = sub.add_parser("validate", help="load and validate a config.yaml")
    p_validate.add_argument("--config", default=None,
                            help="path to config.yaml (or set WEATHERDASH_CONFIG)")
    p_validate.set_defaults(func=cmd_validate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
