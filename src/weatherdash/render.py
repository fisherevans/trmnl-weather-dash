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

from .aggregate import SNOW_CODES as _SNOW_CODES
from .bg_shading import cloud_bucket, precip_bucket, row_bg_color, shaded_svg_url

ASSETS = Path(__file__).parent / "assets"
WIDTH, HEIGHT = 1872, 1404
# 4-bit grayscale: 16 levels evenly spaced 0..255.
GRAY_LEVELS = [round(i * 255 / 15) for i in range(16)]

# Threshold lines on the precip + cloud charts. Precip values are
# probability-of-precip percent (matches the bar height — see below);
# cloud values are cloud-cover percent.
PRECIP_THRESHOLDS = [(30, "CHANCE"), (60, "LIKELY"), (85, "DEFINITE")]
CLOUD_THRESHOLDS  = [(30, "PARTLY"), (70, "OVERCAST")]


def render_html(data: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(ASSETS),
        autoescape=select_autoescape(["html"]),
    )
    hourly = data["hourly"]
    n_hours = len(hourly)
    # Precip chart now plots probability-of-precip (PoP). Scale anchors at
    # 100% so the tallest bar (a "definite" hour) fills ~90% of row height
    # — leaves space at the top for region labels + threshold-line pills.
    # The shift from accumulation-as-height to probability-as-height
    # answers "will I get wet?" at a glance; mm/cm totals move to the
    # row title strip for the "how much?" answer.
    precip_scale_max = 100.0
    # Cloud scale: anchored at 110% so a real 100% cloud cover only fills
    # 100/110 ≈ 90.9% of the bar area, leaving a small gutter below the
    # tallest bar.
    cloud_scale_max = 110.0
    precip_lines = [
        {"pct": pct, "bar_pct": pct / precip_scale_max * 90.0, "label": label}
        for pct, label in PRECIP_THRESHOLDS
    ]
    cloud_lines = [
        {"pct": pct, "bar_pct": pct / cloud_scale_max * 100.0, "label": label}
        for pct, label in CLOUD_THRESHOLDS
    ]
    enriched = []
    for h in hourly:
        rain_mm = h.get("precip_mm", 0.0)
        snow_cm = h.get("snow_cm", 0.0)
        prob_pct = float(h.get("precip_prob_pct", 0))
        # Bar height = probability * 90% (90 leaves head-room at the top).
        precip_h_pct = (prob_pct / precip_scale_max) * 90.0
        # Bar fill type — snow vs rain — is decided per-hour from the
        # weather code so a mixed-precip day shows the right color per
        # bar. SNOW_CODES is the canonical snow set used elsewhere.
        is_snow_hour = h.get("weather_code", 0) in _SNOW_CODES
        enriched.append({
            **h,
            "precip_h_pct": precip_h_pct,
            "precip_is_snow": is_snow_hour,
            "cloud_h_pct": float(h["cloud_pct"]) / cloud_scale_max * 100.0,
            # Per-bar label is the probability percent; only labeled when
            # >= 10 so the chart stays uncluttered on dry hours.
            "precip_label": f"{int(round(prob_pct))}%" if prob_pct >= 10 else "",
        })
    # Honor pre-computed regions (carries the per-region labels) when the
    # aggregation layer supplied them; otherwise recompute for the offline
    # `weatherdash render --data data.json` path.
    regions = data.get("regions") or compute_regions(hourly, n_hours)
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
    # Derive precip_description if not provided (offline fixtures).
    if not data.get("precip_description"):
        from .aggregate import _precip_description
        total_mm = data.get("total_accumulation_mm", 0.0)
        data = {**data, "precip_description": _precip_description(total_mm, precip_type)}

    ctx = {**data, "hourly": enriched,
           "precip_scale_max": precip_scale_max,
           "n_hours": n_hours,
           "regions": regions,
           "precip_lines": precip_lines,
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


# compute_regions moved to aggregate.py — re-exported here for any
# external caller that still imports from this module.
from .aggregate import compute_regions  # noqa: E402,F401


def format_precip(mm: float) -> str:
    if mm <= 0:
        return "0"
    if mm >= 10:
        return f"{mm:.0f}mm"
    return f"{mm:.1f}m"


def format_precip_mixed(rain_mm: float, snow_cm: float) -> str:
    """Single label for a stacked bar. Uses the dominant component's unit."""
    if rain_mm == 0 and snow_cm == 0:
        return "0"
    if snow_cm > rain_mm:
        # Snow dominant: use cm.
        if snow_cm >= 10:
            return f"{snow_cm:.0f}cm"
        return f"{snow_cm:.1f}c"
    return format_precip(rain_mm)


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
