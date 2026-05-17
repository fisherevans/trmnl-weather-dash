#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "playwright>=1.44",
#     "jinja2>=3.1",
#     "pillow>=10.0",
# ]
# ///
"""Render the TRMNL X weather dashboard.

Loads data.json, renders template.html with it, screenshots via headless
Chromium at the device's native 1872x1404 resolution, then quantizes the
PNG to the 16-level grayscale palette the panel displays in 4-bit mode.

First-time setup (downloads the bundled chromium):
  uv run render.py --setup

Then:
  uv run render.py                              # data.json -> output.png
  uv run render.py --data foo.json --out foo.png
  uv run render.py --keep-html                  # leave the rendered html beside the png
  uv run render.py --no-quantize                # skip the 16-gray snap (useful when iterating)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

ROOT = Path(__file__).parent
WIDTH, HEIGHT = 1872, 1404
# 4-bit grayscale: 16 levels evenly spaced 0..255.
GRAY_LEVELS = [round(i * 255 / 15) for i in range(16)]


def render_html(data: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(ROOT),
        autoescape=select_autoescape(["html"]),
    )
    hourly = data["hourly"]
    n_hours = len(hourly)
    # Rain scale: anchor at 10mm by default; bump only if reality exceeds it,
    # so light-rain days don't squash the bar heights against the floor. We
    # reserve a 10% gutter at the top, so the tallest bar maxes at 90% of
    # the bar area and never collides with the row's top edge.
    max_precip = max(10.0, *(h["precip_mm"] for h in hourly))
    # Cloud scale: anchored at 110% so a real 100% cloud cover only fills
    # 100/110 ≈ 90.9% of the bar area, leaving a small gutter below the
    # tallest bar.
    cloud_scale_max = 110.0
    rain_lines = [
        {"mm": mm, "bar_pct": mm / max_precip * 90.0, "label": label}
        for mm, label in RAIN_THRESHOLDS
        if mm < max_precip
    ]
    cloud_lines = [
        {"pct": pct, "bar_pct": pct / cloud_scale_max * 100.0, "label": label}
        for pct, label in CLOUD_THRESHOLDS
    ]
    enriched = []
    for h in hourly:
        enriched.append({
            **h,
            "precip_h_pct": (h["precip_mm"] / max_precip) * 90.0,
            "cloud_h_pct": float(h["cloud_pct"]) / cloud_scale_max * 100.0,
            "precip_label": format_precip(h["precip_mm"]),
        })
    regions = compute_regions(hourly, n_hours)
    # `updated_at` is the render-time stamp (not part of the data file) so a
    # stale image is visually obvious on the panel. %-I drops the leading
    # zero on the hour to match the header's "10:03 AM" style.
    updated_at = data.get("updated_at") or datetime.now().strftime("%-I:%M %p")
    ctx = {**data, "hourly": enriched,
           "max_precip": max_precip,
           "n_hours": n_hours,
           "regions": regions,
           "rain_lines": rain_lines,
           "cloud_lines": cloud_lines,
           "updated_at": updated_at,
           "night_regions": [r for r in regions if r["is_night"]]}
    return env.get_template("template.html").render(**ctx)


# Intensity thresholds. Rain values are mm/hour. Cloud values are %.
RAIN_THRESHOLDS  = [(2.5, "MODERATE"), (7.5, "HEAVY")]
CLOUD_THRESHOLDS = [(30,  "PARTLY"),   (70,  "OVERCAST")]


def compute_regions(hourly: list, n_hours: int) -> list:
    """Group contiguous hours by is_night into regions covering the chart width."""
    if not hourly:
        return []
    regions = []
    cur_start = 0
    cur_night = hourly[0]["is_night"]
    for i, h in enumerate(hourly):
        if h["is_night"] != cur_night:
            regions.append({"start": cur_start, "end": i, "is_night": cur_night})
            cur_start = i
            cur_night = h["is_night"]
    regions.append({"start": cur_start, "end": len(hourly), "is_night": cur_night})
    for r in regions:
        r["start_at"] = r["start"] / n_hours
        r["end_at"]   = r["end"]   / n_hours
        r["width_at"] = r["end_at"] - r["start_at"]
    return regions


def format_precip(mm: float) -> str:
    if mm <= 0:
        return "0"
    if mm >= 10:
        return f"{mm:.0f}mm"
    return f"{mm:.1f}m"


def screenshot(html: str, out: Path) -> None:
    from playwright.sync_api import sync_playwright

    with NamedTemporaryFile(suffix=".html", dir=ROOT, delete=False, mode="w") as f:
        f.write(html)
        tmp = Path(f.name)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(
                viewport={"width": WIDTH, "height": HEIGHT},
                device_scale_factor=1,
            )
            page = ctx.new_page()
            page.goto(tmp.as_uri())
            # Wait for webfonts to settle so screenshot isn't a flash-of-fallback-font.
            page.wait_for_load_state("networkidle")
            page.evaluate("document.fonts && document.fonts.ready")
            page.screenshot(path=str(out), full_page=False, omit_background=False)
            browser.close()
    finally:
        tmp.unlink(missing_ok=True)


def quantize_to_4bit_gray(path: Path) -> None:
    img = Image.open(path).convert("L")
    # Snap each 0..255 value to the nearest of the 16 device levels.
    lut = [GRAY_LEVELS[min(15, round(v / 17))] for v in range(256)]
    img.point(lut).save(path)


def setup_browser() -> int:
    return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data.json")
    ap.add_argument("--out", default="output.png")
    ap.add_argument("--keep-html", action="store_true")
    ap.add_argument("--no-quantize", action="store_true")
    ap.add_argument("--setup", action="store_true", help="install bundled chromium then exit")
    args = ap.parse_args()

    if args.setup:
        sys.exit(setup_browser())

    data = json.loads((ROOT / args.data).read_text())
    html = render_html(data)
    out = ROOT / args.out
    if args.keep_html:
        out.with_suffix(".html").write_text(html)
    screenshot(html, out)
    if not args.no_quantize:
        quantize_to_4bit_gray(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
