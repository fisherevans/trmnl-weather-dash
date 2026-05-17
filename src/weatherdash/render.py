"""Render pipeline: data dict -> template -> Chromium screenshot -> 4-bit PNG.

The renderer reads template + asset files from a sibling `assets/` directory
that ships with the package. Callers supply a data dict matching the shape
in `data.json` plus optional `updated_at` (defaults to now, formatted as
"H:MM AM/PM").
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

from .bg_shading import cloud_bucket, precip_bucket, row_bg_color, shaded_svg_url

ASSETS = Path(__file__).parent / "assets"
WIDTH, HEIGHT = 1872, 1404
# 4-bit grayscale: 16 levels evenly spaced 0..255.
GRAY_LEVELS = [round(i * 255 / 15) for i in range(16)]

# Intensity thresholds. Rain values are mm/hour. Cloud values are %.
RAIN_THRESHOLDS  = [(2.5, "MODERATE"), (7.5, "HEAVY")]
CLOUD_THRESHOLDS = [(30,  "PARTLY"),   (70,  "OVERCAST")]


def render_html(data: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(ASSETS),
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

    # Pre-computed bg URLs from the aggregation layer take priority. For
    # static `data*.json` renders (which skip the aggregation layer), we
    # compute them here from the same fields the live pipeline uses, so
    # offline fixture renders still produce density-shifted backgrounds
    # plus the matching no-data overlays.
    cloud_bg_url_day      = data.get("cloud_bg_url_day")
    cloud_bg_url_night    = data.get("cloud_bg_url_night")
    cloud_bg_color_day    = data.get("cloud_bg_color_day")
    cloud_bg_color_night  = data.get("cloud_bg_color_night")
    cloud_empty_text      = data.get("cloud_empty_text")
    precip_bg_url_day     = data.get("precip_bg_url_day")
    precip_bg_url_night   = data.get("precip_bg_url_night")
    precip_bg_color_day   = data.get("precip_bg_color_day")
    precip_bg_color_night = data.get("precip_bg_color_night")
    precip_empty_text     = data.get("precip_empty_text")
    precip_type           = data.get("precip_type", "rain")
    if not cloud_bg_url_day:
        avg_cloud = data.get("avg_cloud_pct", 0)
        c_bucket = cloud_bucket(avg_cloud)
        cloud_bg_url_day     = shaded_svg_url("bg-cloud.svg", c_bucket, "day")
        cloud_bg_url_night   = shaded_svg_url("bg-cloud.svg", c_bucket, "night")
        cloud_bg_color_day   = row_bg_color(c_bucket, "day")
        cloud_bg_color_night = row_bg_color(c_bucket, "night")
        if cloud_empty_text is None and c_bucket == 0:
            cloud_empty_text = "CLEAR SKIES"
    if not precip_bg_url_day:
        precip_svg = "bg-snow.svg" if precip_type == "snow" else "bg-rain.svg"
        total_mm = data.get("total_accumulation_mm", 0.0)
        p_bucket = precip_bucket(total_mm)
        precip_bg_url_day     = shaded_svg_url(precip_svg, p_bucket, "day")
        precip_bg_url_night   = shaded_svg_url(precip_svg, p_bucket, "night")
        precip_bg_color_day   = row_bg_color(p_bucket, "day")
        precip_bg_color_night = row_bg_color(p_bucket, "night")
        if precip_empty_text is None and p_bucket == 0:
            precip_empty_text = (
                "NO SNOW FORECASTED" if precip_type == "snow" else "NO RAIN FORECASTED"
            )

    ctx = {**data, "hourly": enriched,
           "max_precip": max_precip,
           "n_hours": n_hours,
           "regions": regions,
           "rain_lines": rain_lines,
           "cloud_lines": cloud_lines,
           "updated_at": updated_at,
           "cloud_bg_url_day": cloud_bg_url_day,
           "cloud_bg_url_night": cloud_bg_url_night,
           "precip_bg_url_day": precip_bg_url_day,
           "precip_bg_url_night": precip_bg_url_night,
           "cloud_bg_color_day": cloud_bg_color_day,
           "cloud_bg_color_night": cloud_bg_color_night,
           "precip_bg_color_day": precip_bg_color_day,
           "precip_bg_color_night": precip_bg_color_night,
           "cloud_empty_text": cloud_empty_text,
           "precip_empty_text": precip_empty_text,
           "night_regions": [r for r in regions if r["is_night"]]}
    return env.get_template("template.html").render(**ctx)


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

    # Inject `<base href>` pointing at the assets dir so relative URLs in the
    # template (bg-*.svg, makin-grey/<stem>.svg) resolve regardless of where
    # the tmp HTML lives. Previously the tmp file sat inside the assets dir
    # for that purpose, but in a Docker container the assets dir is owned by
    # root + read-only for the unprivileged runtime user.
    base_tag = f'<base href="{ASSETS.as_uri()}/">'
    html = html.replace("<head>", f"<head>\n  {base_tag}", 1)

    with NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
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


def render_to_png(data: dict, out: Path, *, quantize: bool = True, keep_html: bool = False) -> None:
    """Render `data` to `out`. If `keep_html`, also write the intermediate HTML beside `out`."""
    html = render_html(data)
    if keep_html:
        out.with_suffix(".html").write_text(html)
    screenshot(html, out)
    if quantize:
        quantize_to_4bit_gray(out)


def render_from_json(data_path: Path, out: Path, *, quantize: bool = True, keep_html: bool = False) -> None:
    data = json.loads(data_path.read_text())
    render_to_png(data, out, quantize=quantize, keep_html=keep_html)
