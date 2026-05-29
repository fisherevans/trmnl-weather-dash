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

# Natural size: one vertical half of the OG's 800x480 panel, mounted
# landscape with an hstack split. Tall + narrow drives a stacked event
# layout (time on one line, title on the next) rather than a single-row
# grid - 400 px isn't wide enough for time + title + cal label inline.
RENDER_SPEC = RenderSpec(width=400, height=480, palette="2bit-grey")


def render_html(data: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )

    now = _parse_iso(data["now"])
    raw_events = data.get("events") or []

    # Enrich each event with derived display fields. is_past drives the
    # greying; we do NOT compute "next event" / "Xm until" because the
    # TRMNL device polls on a multi-minute cadence and any countdown
    # would be visibly stale by the time the panel shows it.
    enriched: list[dict] = []
    for e in raw_events:
        start = _parse_iso(e["start"])
        end = _parse_iso(e["end"])
        enriched.append({
            **e,
            "start_dt":   start,
            "end_dt":     end,
            "is_past":    end <= now,
            "time_label": _format_time_range(start, end, e.get("all_day", False)),
        })

    # Density tier: pick the smallest CSS class that comfortably fits
    # `len(events)` rows. Sparse days get breathing room; full days
    # collapse to tighter type. Thresholds picked so each tier preserves
    # readable line-height at 480 px panel height.
    density = _density_tier(len(enriched))

    ctx = {
        "now":         now,
        "today_label": data.get("today_label", now.strftime("%a %b %-d").upper()),
        "events":      enriched,
        "empty":       not enriched,
        "density":     density,
    }
    return env.get_template("template.html").render(**ctx)


def _density_tier(n_events: int) -> str:
    """Map event count -> CSS density class.

    xl: 1-3 events  (huge type, lots of breathing room)
    lg: 4-5 events
    md: 6-7 events
    sm: 8-9 events
    xs: 10+ events  (the cap of max_events=12 fits here)
    """
    if n_events <= 3:  return "density-xl"
    if n_events <= 5:  return "density-lg"
    if n_events <= 7:  return "density-md"
    if n_events <= 9:  return "density-sm"
    return "density-xs"


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


