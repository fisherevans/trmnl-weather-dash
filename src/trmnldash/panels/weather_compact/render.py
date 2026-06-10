"""weather_compact renderer.

Takes a raw forecast dict (current obs + 18 hourly entries + sun events
+ prose chunks) and turns it into the augmented context the template
consumes. The chart geometry - temp curve path, precip bar coords,
grid line positions + scale labels, hour ticks, night-shade rect - is
computed here rather than carried in the data dict so the fixtures /
live pipeline only need to ship raw forecast values.

Shared makin-grey icons live with weather_landscape; the base_uri for
the screenshot points there.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

from ...engine import RenderSpec
from ...engine.render import html_to_image


TEMPLATE_DIR = Path(__file__).parent
_LANDSCAPE_ASSETS = TEMPLATE_DIR.parent / "weather_landscape" / "assets"

RENDER_SPEC = RenderSpec(width=400, height=480, palette="2bit-grey")


# ── chart geometry constants ───────────────────────────────────────────────
#
# Coords are in the SVG viewBox (380 x 128). The template scales the
# viewBox to whatever CSS width the chart section ends up with.

_VIEW_W        = 380
_VIEW_H        = 128
_AXIS_Y        = 110        # baseline for precip bars + temp range bottom
_PRECIP_TOP_Y  = 30         # 100% precip line = top of temp range
_LEFT_AXIS_X   = 38         # right edge of left-side scale labels
_RIGHT_AXIS_X  = 342        # left edge of right-side scale labels
_RAIN_LABEL_X  = 10         # rotated 'RAIN' title centerline
_TEMP_LABEL_X  = 370        # rotated 'TEMP' title centerline
_CHART_MID_Y   = 70         # vertical midpoint for both rotated titles
_BAR_W         = 14
_TICK_EVERY    = 2          # hour-tick spacing


# ── public entry points ────────────────────────────────────────────────────


def render_html(data: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    return env.get_template("template.html").render(**_augment(data))


def render_to_image(data: dict, *, width: int | None = None, height: int | None = None) -> Image.Image:
    html = render_html(data)
    return html_to_image(
        html,
        base_uri=_LANDSCAPE_ASSETS.as_uri() + "/",
        width=width or RENDER_SPEC.width,
        height=height or RENDER_SPEC.height,
    )


def render_from_json(data_path: Path, out: Path, *, quantize: bool = True) -> None:
    data = json.loads(data_path.read_text())
    img = render_to_image(data)
    if quantize:
        from ...engine.quantize import quantize as _quantize
        img = _quantize(img, RENDER_SPEC.palette)
    img.save(out)


# ── data augmentation ─────────────────────────────────────────────────────


def _augment(data: dict) -> dict:
    """Turn raw forecast into template-ready context.

    The template expects `high`, `low`, and a `chart` dict carrying all
    the SVG coords; this function fills them in from `hourly` + `sun`.
    Anything already present in `data` (e.g. a hand-built `chart` dict
    for mockups) is left in place.
    """
    hourly = data["hourly"]
    out = dict(data)
    if "high" not in out or "low" not in out:
        hi, lo = _compute_high_low(hourly)
        out.setdefault("high", hi)
        out.setdefault("low", lo)
    if "chart" not in out:
        out["chart"] = _build_chart(hourly, data.get("sun"))
    return out


def _compute_high_low(hourly: list[dict]) -> tuple[dict, dict]:
    """Find peak high and trough low across the hourly slice. Returns
    template-ready dicts with rounded temp, formatted time, and the
    humidity reading at that hour (0 if the provider didn't supply it)."""
    temps = [h["temp_f"] for h in hourly]
    hi_idx = temps.index(max(temps))
    lo_idx = temps.index(min(temps))

    def _summary(idx: int) -> dict:
        h = hourly[idx]
        return {
            "temp_f":       int(round(h["temp_f"])),
            "time":         _format_clock(datetime.fromisoformat(h["datetime"])),
            "humidity_pct": h.get("humidity_pct") or 0,
        }

    return _summary(hi_idx), _summary(lo_idx)


def _build_chart(hourly: list[dict], sun: dict | None) -> dict:
    n = len(hourly)
    step = (_RIGHT_AXIS_X - _LEFT_AXIS_X) / n
    precip_h = _AXIS_Y - _PRECIP_TOP_Y

    # Temperature scale: padded so the high doesn't ride the top edge
    # and the low doesn't ride the bottom. Padded range is always a
    # multiple of 4 so the 25/50/75% grid lines land on whole degrees.
    temps = [h["temp_f"] for h in hourly]
    t_min_raw = min(temps)
    t_max_raw = max(temps)
    t_range_raw = t_max_raw - t_min_raw
    # Target ~20% padding, snapped up to a multiple of 4. Minimum range
    # of 20°F so a stable day still reads as a curve, not a flatline.
    t_range = max(20, math.ceil(t_range_raw * 1.2 / 4) * 4)
    center = (t_max_raw + t_min_raw) / 2
    half = t_range // 2
    t_min = int(round(center - half))
    t_max = t_min + t_range

    def _bar_x(i: int) -> float:
        return _LEFT_AXIS_X + i * step + (step - _BAR_W) / 2

    def _hour_center_x(i: int) -> float:
        return _LEFT_AXIS_X + i * step + step / 2

    def _temp_to_y(t: float) -> float:
        return _PRECIP_TOP_Y + (t_max - t) * precip_h / (t_max - t_min)

    # SVG temp path
    parts: list[str] = []
    for i, h in enumerate(hourly):
        cmd = "M" if i == 0 else "L"
        parts.append(f"{cmd} {_hour_center_x(i):.1f},{_temp_to_y(h['temp_f']):.1f}")
    temp_path = " ".join(parts)

    # Precip bars + hour ticks
    precip_bars = [
        {
            "x": round(_bar_x(i), 2),
            "w": _BAR_W,
            "h": round((h.get("precip_prob_pct", 0) / 100.0) * precip_h, 2),
        }
        for i, h in enumerate(hourly)
    ]
    hour_ticks = [
        {"x": round(_hour_center_x(i), 2), "label": hourly[i].get("hour_label", "")}
        for i in range(0, n, _TICK_EVERY)
    ]

    # Grid lines: 25/50/75/100% rain on the left, correlating temp on
    # the right. Skip 0% since the axis line marks it.
    grid_lines: list[dict] = []
    for pct in (100, 75, 50, 25):
        y = _AXIS_Y - (pct / 100.0) * precip_h
        temp_at = t_min + (pct / 100.0) * (t_max - t_min)
        grid_lines.append({
            "y":    round(y, 2),
            "rain": f"{pct}%",
            "temp": f"{int(round(temp_at))}°",
        })

    return {
        "view_w":        _VIEW_W,
        "view_h":        _VIEW_H,
        "axis_y":        _AXIS_Y,
        "precip_top_y":  _PRECIP_TOP_Y,
        "left_axis_x":   _LEFT_AXIS_X,
        "right_axis_x":  _RIGHT_AXIS_X,
        "rain_label_x":  _RAIN_LABEL_X,
        "temp_label_x":  _TEMP_LABEL_X,
        "chart_mid_y":   _CHART_MID_Y,
        "rain_label":    "RAIN",
        "temp_label":    "TEMP",
        "grid_lines":    grid_lines,
        "temp_path":     temp_path,
        "precip_bars":   precip_bars,
        "hour_ticks":    hour_ticks,
        "nights":        _compute_nights(hourly, sun, step),
    }


def _compute_nights(hourly: list[dict], sun: dict | None, step: float) -> list[dict]:
    """Return one {x, w} shade rect per contiguous run of night hours.

    Night membership comes from each hour's `is_night` flag, so any number
    of blocks render: an evening block, an overnight block pinned to the
    left edge when the chart opens after dark, or both at once. The earlier
    version mapped only the single next_sunset/next_sunrise pair, so a chart
    that began at night (e.g. a 9 PM render, where the next sunrise precedes
    the next sunset) shaded just the later evening block - or nothing, when
    the next sunset fell past the window.

    Each block spans its run's hour-column boundaries. Where an exact
    sunset / sunrise time is known and lands on a block edge, it refines
    that edge to sub-hour precision so the common single-evening-block
    render stays visually stable.
    """
    n = len(hourly)
    if n == 0:
        return []
    # Older fixtures / hand-built mockups may omit per-hour is_night; fall
    # back to the single sunset/sunrise rect so they still shade.
    if not any("is_night" in h for h in hourly):
        legacy = _night_from_sun(hourly, sun, step)
        return [legacy] if legacy else []

    chart_start = datetime.fromisoformat(hourly[0]["datetime"])
    hour_dts = [datetime.fromisoformat(h["datetime"]) for h in hourly]
    sunset_dt = _parse_sun(sun, "next_sunset")
    sunrise_dt = _parse_sun(sun, "next_sunrise")

    def _time_to_x(t: datetime) -> float:
        hours_offset = (t - chart_start).total_seconds() / 3600.0
        return _LEFT_AXIS_X + hours_offset * step + step / 2

    rects: list[dict] = []
    i = 0
    while i < n:
        if not hourly[i].get("is_night"):
            i += 1
            continue
        start = i
        while i + 1 < n and hourly[i + 1].get("is_night"):
            i += 1
        end = i
        i += 1

        # Left edge: chart edge if night is already underway at hour 0,
        # else the day->night column boundary, refined to the exact sunset
        # when one lands in that gap.
        if start == 0:
            x1 = float(_LEFT_AXIS_X)
        else:
            x1 = _LEFT_AXIS_X + start * step
            if sunset_dt and hour_dts[start - 1] <= sunset_dt <= hour_dts[start]:
                x1 = _time_to_x(sunset_dt)
        # Right edge: chart edge if night runs to the last hour, else the
        # night->day boundary, refined to the exact sunrise.
        if end == n - 1:
            x2 = float(_RIGHT_AXIS_X)
        else:
            x2 = _LEFT_AXIS_X + (end + 1) * step
            if sunrise_dt and hour_dts[end] <= sunrise_dt <= hour_dts[end + 1]:
                x2 = _time_to_x(sunrise_dt)

        x1 = max(_LEFT_AXIS_X, x1)
        x2 = min(_RIGHT_AXIS_X, x2)
        if x2 - x1 > 0:
            rects.append({"x": round(x1, 2), "w": round(x2 - x1, 2)})
    return rects


def _parse_sun(sun: dict | None, key: str) -> datetime | None:
    if not sun:
        return None
    val = sun.get(key)
    return datetime.fromisoformat(val) if val else None


def _night_from_sun(hourly: list[dict], sun: dict | None, step: float) -> dict | None:
    """Legacy single-rect fallback for inputs without per-hour is_night.

    Maps the upcoming sunset / sunrise pair to one x-range. Returns None
    when no sunset falls inside the chart window. Cannot represent a chart
    that opens at night - that's why the is_night path above is preferred.
    """
    sunset_dt = _parse_sun(sun, "next_sunset")
    if not sunset_dt:
        return None
    sunrise_dt = _parse_sun(sun, "next_sunrise")
    chart_start = datetime.fromisoformat(hourly[0]["datetime"])
    chart_end = datetime.fromisoformat(hourly[-1]["datetime"]) + timedelta(hours=1)
    if sunset_dt >= chart_end:
        return None

    def _time_to_x(t: datetime) -> float:
        hours_offset = (t - chart_start).total_seconds() / 3600.0
        return _LEFT_AXIS_X + hours_offset * step + step / 2

    night_x = max(_LEFT_AXIS_X, _time_to_x(sunset_dt))
    night_end_x = _RIGHT_AXIS_X
    if sunrise_dt and sunset_dt < sunrise_dt < chart_end:
        night_end_x = min(_RIGHT_AXIS_X, _time_to_x(sunrise_dt))
    width = night_end_x - night_x
    if width <= 0:
        return None
    return {"x": round(night_x, 2), "w": round(width, 2)}


def _format_clock(dt: datetime) -> str:
    """Format a datetime as '3 PM' or '3:30 AM' - drops minutes when zero."""
    if dt.minute == 0:
        return dt.strftime("%-I %p")
    return dt.strftime("%-I:%M %p")
