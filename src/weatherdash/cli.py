"""Console entrypoint. Subcommands grow as later issues land (#6 render-live, #7 serve)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import render as render_mod


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

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
