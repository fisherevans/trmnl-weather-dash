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

# ── panel geometry constants (pixels) ─────────────────────────────────────
# Used to pre-calculate how many event rows fit so we truncate cleanly
# rather than letting flex compress rows below their natural height.
_PANEL_H       = 480
_BODY_PAD_V    = 20    # body padding: 10px top + 10px bottom
_HEADER_H      = 52    # agenda-header block (conservative estimate)
_FOOTER_H      = 18    # updated-at stamp (conservative)
_SECTION_H     = 34    # tomorrow section-break li (border + text + padding)

# Conservative row-height estimates at each density. "Conservative" means
# rounding up so we never show a row that would be partially clipped.
# Actual heights are a few px shorter; the extra margin absorbs rounding.
_ROW_H: dict[str, int] = {
    "density-xl": 84,  # 28px title + 20px time + line heights + gap + pad
    "density-lg": 68,
    "density-md": 58,
}
_DEFAULT_ROW_H = 58


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
            "rsvp_label": _rsvp_label(e.get("response_status")),
        })

    # Density tier: capped at density-md. For dense days we truncate to
    # what fits at comfortable type rather than compressing further.
    density = _density_tier(len(enriched))

    # Greedy row-fit: walk the list and accumulate pixel height, stopping
    # before a row would overflow. The section-break between today and
    # tomorrow is counted when the first tomorrow event is encountered.
    visible, more_count = _fit_events(enriched, density)

    # Re-evaluate density from the visible count so sparse days (few events
    # actually shown) still get the breathing room they deserve.
    density = _density_tier(len(visible))

    has_tomorrow = any(e.get("section") == "tomorrow" for e in visible)

    # "and N more" label shown when the list was truncated.
    more_label = ""
    if more_count > 0:
        truncated = enriched[len(visible):]
        if all(e.get("section") == "tomorrow" for e in truncated):
            more_label = f"and {more_count} more tomorrow"
        elif all(e.get("section") == "today" for e in truncated):
            more_label = f"and {more_count} more today"
        else:
            more_label = f"and {more_count} more events"

    ctx = {
        "now":            now,
        "today_label":    data.get("today_label", now.strftime("%a %b %-d").upper()),
        "tomorrow_label": data.get("tomorrow_label", ""),
        "has_tomorrow":   has_tomorrow,
        "events":         visible,
        "empty":          not visible,
        "density":        density,
        "more_label":     more_label,
        "updated_label":  f"updated {_format_clock(now)}",
    }
    return env.get_template("template.html").render(**ctx)


def _density_tier(n_events: int) -> str:
    """Map event count -> CSS density class.

    Dense days (6+) are capped at density-md. Rather than compressing
    further to fit more events, the renderer pre-truncates the list
    and shows "and N more events" instead. density-sm/xs/xxs are kept
    in CSS but are not reached by the normal rendering path.
    """
    if n_events <= 3:  return "density-xl"
    if n_events <= 5:  return "density-lg"
    return "density-md"


def _fit_events(enriched: list[dict], density: str) -> tuple[list[dict], int]:
    """Greedily select events that fit in the panel without clipping.

    Accumulates pixel height row-by-row, accounting for the section-break
    that appears before the first tomorrow event. Stops before a row that
    would overflow the available area. Returns (visible, more_count).
    """
    available = _PANEL_H - _BODY_PAD_V - _HEADER_H - _FOOTER_H
    row_h = _ROW_H.get(density, _DEFAULT_ROW_H)
    used = 0
    visible: list[dict] = []
    section_break_counted = False

    for e in enriched:
        extra = 0
        if e.get("section") == "tomorrow" and not section_break_counted:
            extra = _SECTION_H
            section_break_counted = True
        if used + extra + row_h > available:
            break
        used += extra + row_h
        visible.append(e)

    return visible, len(enriched) - len(visible)


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


_RSVP_LABELS = {
    "needsAction": "INVITED",
    "tentative":   "MAYBE",
    # accepted + declined render without a label. Declined never gets
    # here in the live path (filtered at source); the offline path
    # treats declined as accepted-ish - the user can keep it out of
    # the fixture if they don't want it shown.
}


def _rsvp_label(response_status: str | None) -> str:
    return _RSVP_LABELS.get(response_status or "accepted", "")


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
