"""Weather-landscape panel renderer.

Builds the weather panel's Jinja context (chart math, region splits,
density-shifted bg URLs) and hands the HTML to the engine for screenshot.

The panel ships its own template + assets in this directory; the asset
dir is exposed to chromium as the page's <base href> so relative URLs
like `makin-grey/cloudy.svg` and `bg-rain.svg` resolve regardless of
where the temp HTML lives.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

from ...engine import RenderSpec
from ...engine.quantize import quantize as _quantize
from ...engine.render import html_to_image
from .aggregate import SNOW_CODES as _SNOW_CODES
from .aggregate import compute_regions
from .bg_shading import cloud_bucket, precip_bucket, row_bg_color, shaded_svg_url
from .config import TuningConfig

TEMPLATE_DIR = Path(__file__).parent
ASSETS = TEMPLATE_DIR / "assets"

RENDER_SPEC = RenderSpec(width=1872, height=1404, palette="4bit-grey")

# Threshold lines on the precip + cloud charts. Precip values are
# probability-of-precip percent (matches the bar height - see below);
# cloud values are cloud-cover percent.
PRECIP_THRESHOLDS = [(30, "CHANCE"), (60, "LIKELY"), (85, "DEFINITE")]
CLOUD_THRESHOLDS  = [(30, "PARTLY"), (70, "OVERCAST")]


def render_html(data: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    # `tuning` lives on the data dict so the offline `render --data X.json`
    # path inherits defaults (no key -> TuningConfig()) without each fixture
    # carrying a tuning block. The live pipeline sticks the panel config's
    # tuning into ctx in panels/weather_landscape/live.py.
    tuning_raw = data.get("tuning")
    if isinstance(tuning_raw, TuningConfig):
        tuning = tuning_raw
    elif isinstance(tuning_raw, dict):
        tuning = TuningConfig.model_validate(tuning_raw)
    else:
        tuning = TuningConfig()

    hourly = data["hourly"]
    # Honor chart_hours by truncating before any chart math runs. The
    # `regions` field in `data` (if present) was computed against the
    # full hourly, so drop it on truncation and let compute_regions
    # rebuild from the truncated slice below.
    regions_override = data.get("regions")
    if tuning.chart_hours < len(hourly):
        hourly = hourly[:tuning.chart_hours]
        regions_override = None
    n_hours = len(hourly)
    # Precip chart now plots probability-of-precip (PoP). Scale anchors at
    # 100% so the tallest bar (a "definite" hour) fills ~90% of row height
    # - leaves space at the top for region labels + threshold-line pills.
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
    # Build the temp line first so each enriched bar can check whether
    # its inside label would collide with the line - if so, the template
    # nudges the label lower in the bar so the dark dot / curve doesn't
    # cover it.
    temp_line = compute_temp_line(hourly)
    enriched = []
    for i, h in enumerate(hourly):
        rain_mm = h.get("precip_mm", 0.0)
        snow_cm = h.get("snow_cm", 0.0)
        prob_pct = float(h.get("precip_prob_pct", 0))
        # Bar height = probability * 90% (90 leaves head-room at the top).
        precip_h_pct = (prob_pct / precip_scale_max) * 90.0
        # Bar fill type - snow vs rain - is decided per-hour from the
        # weather code so a mixed-precip day shows the right color per
        # bar. SNOW_CODES is the canonical snow set used elsewhere.
        is_snow_hour = h.get("weather_code", 0) in _SNOW_CODES
        # Bar top y in row-percent (bars grow up from the bottom). If the
        # temp line passes within ~5% of row height of the bar's top edge
        # - i.e. through the inside-label band - flag the bar so the
        # template pushes that label down into the bar's body. 5% picks
        # up the visible band of the line + its halo + the label's own
        # height without false-positives on bars where the line passes
        # well above or below the label.
        bar_x_pct = (i + 0.5) / n_hours * 100.0
        bar_top_y_pct = 100.0 - precip_h_pct
        line_y = _temp_line_y_at(temp_line, bar_x_pct)
        # Trigger nudge only when the line actually crosses the inside
        # label's vertical band. The label sits at top:6px (~1% of row)
        # below the bar top and is ~20px (~4% of row) tall, so the band
        # occupies roughly (bar_top, bar_top+5%). Add halo + high/low
        # dot extents on either side to get a (bar_top - 0.5, bar_top
        # + 6.5) trigger window. Previously this used a symmetric
        # abs(...) < 5 check which fired even when the line was well
        # ABOVE the bar top (i.e. above the entire bar) - that produced
        # false-positive nudges on bars where the line passes overhead.
        nudge = (
            line_y is not None
            and precip_h_pct >= 8.0  # outside-label bars don't have an inside label
            and bar_top_y_pct - 0.5 < line_y < bar_top_y_pct + 6.5
        )
        enriched.append({
            **h,
            "precip_h_pct": precip_h_pct,
            "precip_is_snow": is_snow_hour,
            "cloud_h_pct": float(h["cloud_pct"]) / cloud_scale_max * 100.0,
            # Per-bar label is the probability percent; only labeled when
            # >= 10 so the chart stays uncluttered on dry hours.
            "precip_label": f"{int(round(prob_pct))}%" if prob_pct >= 10 else "",
            "precip_label_nudge": nudge,
        })
    # Honor pre-computed regions (carries the per-region labels) when the
    # aggregation layer supplied them; otherwise recompute for the offline
    # `trmnldash render --data data.json` path. `regions_override` is the
    # pre-computed list, cleared above if chart_hours truncation changed
    # the hourly span.
    regions = regions_override or compute_regions(hourly, n_hours)
    # Render-time stamp (not part of the data file) so a stale image is
    # visually obvious on the panel. %-I drops the leading zero on the hour
    # to match the header's "10:03 AM" style. Date carries the year so a
    # year-stale image is unambiguous.
    now = datetime.now()
    updated_date = data.get("updated_date") or now.strftime("%b %-d, %Y")
    updated_time = data.get("updated_time") or now.strftime("%-I:%M %p")

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

    # Derive forecast_chunks for offline fixtures. The live aggregate
    # builds these from a NormalizedForecast (or NWS shortForecast); for
    # the JSON fixtures we wrap each hourly dict in a SimpleNamespace
    # carrying the attrs `_forecast_chunks` / `_summarize` expect, then
    # call the same helpers so the prose matches the live deploy.
    if not data.get("forecast_chunks"):
        data = {**data, "forecast_chunks": _derive_chunks_offline(hourly, precip_type)}

    ctx = {**data, "hourly": enriched,
           "precip_scale_max": precip_scale_max,
           "n_hours": n_hours,
           "regions": regions,
           "precip_lines": precip_lines,
           "cloud_lines": cloud_lines,
           "temp_line": temp_line,
           "updated_date": updated_date,
           "updated_time": updated_time,
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
           "night_regions": [r for r in regions if r["is_night"]],
           "tuning_css": tuning.css_overrides(),
           "tuning_summary_layout": tuning.summary_layout}
    return env.get_template("template.html").render(**ctx)


def _derive_chunks_offline(hourly_dicts: list[dict], precip_type: str) -> list[dict]:
    """Build the TODAY/TONIGHT forecast prose chunks from offline hourly
    dicts. Mirrors what aggregate.build_context does in the live path.

    Wraps each dict in a SimpleNamespace carrying the attrs the chunk
    helpers expect (temp_f, humidity_pct, cloud_pct, precip_mm, snow_cm,
    is_day, timestamp). `timestamp` is synthesized from `now` since the
    fixtures don't carry one; _summarize only uses `.month` for season-
    aware feel words, so the exact instant doesn't matter much.
    """
    from .aggregate import _forecast_chunks
    base = datetime.now()
    points = [
        SimpleNamespace(
            temp_f=h["temp_f"],
            humidity_pct=h.get("humidity_pct"),
            cloud_pct=h["cloud_pct"],
            precip_mm=h.get("precip_mm", 0.0),
            snow_cm=h.get("snow_cm", 0.0),
            is_day=not h.get("is_night", False),
            timestamp=base + timedelta(hours=i),
        )
        for i, h in enumerate(hourly_dicts)
    ]
    chunks = _forecast_chunks(points, precip_type)
    # Apply the same flex_style + chips defaults aggregate.build_context
    # sets so the rendered chunks match live structurally. Offline data
    # has no UV/AQI/pollen signal so the chips stay empty.
    for c in chunks:
        c.setdefault("chips", "")
        c["flex_style"] = "1 1 auto"
    if len(chunks) == 2:
        lens = [len(c["text"]) for c in chunks]
        short_i = 0 if lens[0] < lens[1] else 1
        long_i = 1 - short_i
        if lens[short_i] <= 18 and lens[long_i] >= lens[short_i] * 2.5:
            chunks[short_i]["flex_style"] = "0 0 auto"
    return chunks


# Vertical band the temperature line occupies inside the precip-bars row,
# in percent from the row's top. The band sits clear of the sun-marker
# pills (which span roughly 5..19% of the row) at the top, and clear of
# the time-axis at the bottom. The Y_TOP value also reserves room for
# the HIGH pill that floats above the line's peak: pill + 10px margin +
# half-dot offset ≈ 9% of row height, so Y_TOP needs to sit roughly
# that far below the marker pill's bottom to guarantee no overlap
# regardless of where the high lands horizontally.
TEMP_LINE_Y_TOP = 32.0
TEMP_LINE_Y_BOT = 88.0
# Minimum y-axis span in °F. On low-variance days (e.g. 87/83 = 4°F
# swing) the line gets centered within this band instead of stretched
# to the row edges, so the chart reads as "stable today" rather than
# "dramatic swing today." Days with actual range >= this value use
# the actual range and fill the full band.
TEMP_LINE_MIN_RANGE_F = 20.0


def _smooth_path(coords):
    """SVG cubic-bezier `d` string through `coords` via uniform
    Catmull-Rom-to-Bezier conversion. The curve passes through every
    input point (so dots placed on data points land exactly on the
    line), with endpoints padded by duplicating the first/last point -
    that flattens the spline's tangent at the edges instead of letting
    it overshoot into the row's top buffer or below the time axis.
    """
    n = len(coords)
    if n == 0:
        return ""
    if n == 1:
        x, y = coords[0]
        return f"M {x:.3f},{y:.3f}"
    padded = [coords[0], *coords, coords[-1]]
    parts = [f"M {coords[0][0]:.3f},{coords[0][1]:.3f}"]
    for i in range(n - 1):
        p0 = padded[i]
        p1 = padded[i + 1]
        p2 = padded[i + 2]
        p3 = padded[i + 3]
        c1x = p1[0] + (p2[0] - p0[0]) / 6
        c1y = p1[1] + (p2[1] - p0[1]) / 6
        c2x = p2[0] - (p3[0] - p1[0]) / 6
        c2y = p2[1] - (p3[1] - p1[1]) / 6
        parts.append(
            f"C {c1x:.3f},{c1y:.3f} {c2x:.3f},{c2y:.3f} "
            f"{p2[0]:.3f},{p2[1]:.3f}"
        )
    return " ".join(parts)


def _smooth_series(values, weights=(1, 2, 3, 2, 1)):
    """Weighted moving-average smoothing across a 5-point window. The
    Catmull-Rom spline alone passes through every raw data point, which
    hitches at every 1-degree hourly wobble - pre-smoothing the temp
    series with this kernel lets the curve ease across past + future
    neighbors instead of chasing each spike. Endpoints clip the window
    to in-range neighbors so the edges don't get pulled toward zero.
    Weights are a mild triangular kernel (center=3, falloff to 1);
    bump them up for heavier smoothing or shrink the window for less.
    """
    n = len(values)
    if n == 0:
        return []
    radius = len(weights) // 2
    out = []
    for i in range(n):
        num = 0.0
        den = 0.0
        for k, w in enumerate(weights):
            j = i + k - radius
            if 0 <= j < n:
                num += w * values[j]
                den += w
        out.append(num / den)
    return out


def compute_temp_line(hourly):
    """Map hourly temps to (x%, y%) coordinates in the precip-bars row.

    Two series are in play. Raw temps drive the high/low call-outs:
    the dot pills show the actual hottest/coldest forecast values, and
    the high/low indices are the raw extremes. A smoothed series drives
    the curve's y positions and the y-axis scale - so the line eases
    across short-range wobbles instead of zig-zagging through every
    1-degree blip. Dot y values come from the smoothed curve too, so
    the dots land exactly on the line.
    """
    n = len(hourly)
    if n == 0:
        return None
    temps = [h["temp_f"] for h in hourly]
    t_min, t_max = min(temps), max(temps)
    if t_min == t_max:
        # Flat-temp day - center the line so it reads as horizontal.
        mid = (TEMP_LINE_Y_TOP + TEMP_LINE_Y_BOT) / 2
        points = [
            {"x": (i + 0.5) / n * 100.0, "y": mid,
             "temp_f": t, "is_high": False, "is_low": False}
            for i, t in enumerate(temps)
        ]
        return {"points": points, "t_min": t_min, "t_max": t_max,
                "path": _smooth_path([(p["x"], p["y"]) for p in points])}

    # High/low indices come from the RAW temps so the call-outs mark
    # the actual data extremes - not smoothing-shifted ghosts.
    hi = temps.index(t_max)
    lo = temps.index(t_min)

    smoothed = _smooth_series(temps)
    # Re-anchor the extremes back to raw so the curve actually touches
    # the day's high/low at those indices. Without this, a sharp 90°F
    # peak between 85° neighbors gets averaged down to ~88°F by the
    # 5-point kernel, so the line's apex wouldn't match the "90°" dot.
    # Surrounding points stay smoothed; Catmull-Rom's tangent at the
    # anchored point is computed from those (symmetric) neighbors, so
    # the curve approaches the peak with a near-horizontal slope
    # rather than spiking into it - no overshoot above the band.
    smoothed[hi] = t_max
    smoothed[lo] = t_min
    # Display range is clamped to TEMP_LINE_MIN_RANGE_F so a flat day
    # (e.g. 87/83 = 4°F swing) reads as subtle. We center the data
    # within the clamped range so the line sits in the middle of the
    # band; the high/low dots end up symmetrically offset from center.
    actual_range = t_max - t_min
    display_range = max(actual_range, TEMP_LINE_MIN_RANGE_F)
    mid = (t_min + t_max) / 2.0
    display_min = mid - display_range / 2.0
    span = TEMP_LINE_Y_BOT - TEMP_LINE_Y_TOP
    def to_y(t):
        return TEMP_LINE_Y_BOT - (t - display_min) / display_range * span

    # If the high/low dot sits within ~one column of the row edge, snap
    # the pill label to the dot's side instead of centering on it - keeps
    # the pill from clipping against the chart card border.
    edge = 100.0 / n
    def align(x):
        if x < edge: return "left"
        if x > 100 - edge: return "right"
        return "center"

    points = []
    for i, t in enumerate(temps):
        x = (i + 0.5) / n * 100.0
        is_hi = i == hi
        is_lo = i == lo
        p = {"x": x, "y": to_y(smoothed[i]), "temp_f": t,
             "is_high": is_hi, "is_low": is_lo}
        if is_hi or is_lo:
            p["align"] = align(x)
            # `hour` is the hour string (e.g. "3p"). The pill renders
            # it as a smaller italic "@3p" tail so the call-out carries
            # *when* as well as *what*.
            p["hour"] = hourly[i].get("hour", "")
        points.append(p)
    return {"points": points, "t_min": t_min, "t_max": t_max,
            "path": _smooth_path([(p["x"], p["y"]) for p in points])}


def _temp_line_y_at(temp_line, x_pct):
    """Linear-interpolate the temp line's y at a given x. Used for
    collision detection between the line and bar labels - the smoothed
    bezier deviates from the linear chord by at most a few percent of
    row height, well under the collision threshold, so this is precise
    enough without re-evaluating the curve."""
    if not temp_line:
        return None
    pts = temp_line["points"]
    if not pts:
        return None
    if x_pct <= pts[0]["x"]:
        return pts[0]["y"]
    if x_pct >= pts[-1]["x"]:
        return pts[-1]["y"]
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if a["x"] <= x_pct <= b["x"]:
            f = (x_pct - a["x"]) / (b["x"] - a["x"])
            return a["y"] + f * (b["y"] - a["y"])
    return None


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


def render_to_image(data: dict, *, width: int | None = None, height: int | None = None) -> Image.Image:
    """Render the panel's data dict to a PIL.Image (no quantize, no save).

    Width/height default to the panel's RENDER_SPEC. The template was
    authored at 1872x1404; rendering at other sizes will visibly
    misfit since the CSS uses absolute pixel measurements throughout.
    The dashboard layer feeds the result through `engine.quantize` and
    composes it onto the device's final canvas.
    """
    html = render_html(data)
    return html_to_image(
        html,
        base_uri=ASSETS.as_uri() + "/",
        width=width or RENDER_SPEC.width,
        height=height or RENDER_SPEC.height,
    )


def render_to_png(data: dict, out: Path, *, quantize: bool = True, keep_html: bool = False) -> None:
    """Convenience wrapper for the offline / single-panel render path.

    Maintains the original render-to-disk signature so the CLI's `render`
    subcommand and the pipeline's `render-live` flow can call straight in
    without dealing with PIL.Image directly.
    """
    if keep_html:
        out.with_suffix(".html").write_text(render_html(data))
    img = render_to_image(data)
    if quantize:
        img = _quantize(img, RENDER_SPEC.palette)
    img.save(out)


def render_from_json(data_path: Path, out: Path, *, quantize: bool = True, keep_html: bool = False) -> None:
    data = json.loads(data_path.read_text())
    render_to_png(data, out, quantize=quantize, keep_html=keep_html)
