"""Calendar-agenda panel renderer.

Enriches the event list with derived display fields (formatted times,
is_past, is_next, minutes_until_text) and feeds the result to a Jinja
template. The "next event" is the earliest upcoming event whose start
is within the configured badge horizon.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

from ...engine import RenderSpec
from ...engine.render import html_to_image


TEMPLATE_DIR = Path(__file__).parent
ASSETS = TEMPLATE_DIR / "assets"

# Natural size for the OG bottom-half. Dashboard layout overrides at compose.
# 300 picks the rough split of 480 - weather_compact (~170) - separator
# (~10) = 300 for the agenda. Tunable from the dashboard YAML.
RENDER_SPEC = RenderSpec(width=800, height=300, palette="2bit-grey")


def render_html(data: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )

    now = _parse_iso(data["now"])
    badge_horizon_min = data.get("next_event_badge_minutes", 60)
    raw_events = data.get("events") or []

    enriched: list[dict] = []
    next_idx: int | None = None

    # First pass: enrich each event with derived display fields. Track the
    # earliest event whose start is in the future as the candidate for the
    # "Xm until" badge.
    for i, e in enumerate(raw_events):
        start = _parse_iso(e["start"])
        end = _parse_iso(e["end"])
        is_past = end <= now
        is_future = start > now
        is_now = (not is_past) and (not is_future)
        if next_idx is None and is_future:
            next_idx = i
        enriched.append({
            **e,
            "start_dt": start,
            "end_dt":   end,
            "is_past":  is_past,
            "is_now":   is_now,
            "time_label": _format_time_range(start, end, e.get("all_day", False)),
        })

    # Second pass: stamp is_next + minutes_until on the elected candidate
    # iff its start falls within the configured badge horizon.
    if next_idx is not None:
        ne = enriched[next_idx]
        delta = ne["start_dt"] - now
        minutes = int(delta.total_seconds() // 60)
        if 0 <= minutes <= badge_horizon_min:
            ne["is_next"] = True
            ne["minutes_until_text"] = _format_until(minutes)
        else:
            ne["is_next"] = False
            ne["minutes_until_text"] = ""
    for e in enriched:
        e.setdefault("is_next", False)
        e.setdefault("minutes_until_text", "")

    ctx = {
        "now":         now,
        "today_label": data.get("today_label", now.strftime("%a %b %-d").upper()),
        "events":      enriched,
        "empty":       not enriched,
    }
    return env.get_template("template.html").render(**ctx)


def render_to_image(data: dict, *, width: int | None = None, height: int | None = None) -> Image.Image:
    html = render_html(data)
    base_uri = ASSETS.as_uri() + "/" if ASSETS.exists() else TEMPLATE_DIR.as_uri() + "/"
    return html_to_image(
        html,
        base_uri=base_uri,
        width=width or RENDER_SPEC.width,
        height=height or RENDER_SPEC.height,
    )


def render_from_json(data_path: Path, out: Path, *, quantize: bool = True) -> None:
    """Offline convenience: JSON in -> PNG out. Mirrors weather_landscape."""
    data = json.loads(data_path.read_text())
    img = render_to_image(data)
    if quantize:
        from ...engine.quantize import quantize as _quantize
        img = _quantize(img, RENDER_SPEC.palette)
    img.save(out)


# ── helpers ────────────────────────────────────────────────────────────────


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _format_time_range(start: datetime, end: datetime, all_day: bool) -> str:
    """Render an event's time slot. All-day events read 'ALL DAY' instead
    of '12:00 AM - 12:00 AM'. Times use %-I for no leading zero (1:30
    not 01:30) and drop the minutes when zero (3 PM not 3:00 PM)."""
    if all_day:
        return "ALL DAY"
    return f"{_format_clock(start)} - {_format_clock(end)}"


def _format_clock(dt: datetime) -> str:
    if dt.minute == 0:
        return dt.strftime("%-I %p")
    return dt.strftime("%-I:%M %p")


def _format_until(minutes: int) -> str:
    if minutes <= 0:
        return "now"
    if minutes < 60:
        return f"{minutes}m until"
    hours, rem = divmod(minutes, 60)
    if rem == 0:
        return f"{hours}h until"
    return f"{hours}h {rem}m until"
