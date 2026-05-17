"""Console entrypoint. Subcommands grow as later issues land (#6 render-live, #7 serve)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import render as render_mod
from .config import ConfigError, load_config


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="weatherdash")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_render = sub.add_parser("render", help="render a data.json file to a PNG")
    p_render.add_argument("--data", default="data.json")
    p_render.add_argument("--out", default="output.png")
    p_render.add_argument("--keep-html", action="store_true",
                          help="leave the rendered HTML beside the PNG")
    p_render.add_argument("--no-quantize", action="store_true",
                          help="skip the 16-gray snap (faster iteration)")
    p_render.set_defaults(func=cmd_render)

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
