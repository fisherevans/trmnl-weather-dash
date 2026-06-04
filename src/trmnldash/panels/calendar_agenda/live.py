"""Live-data fetch for the calendar_agenda panel.

Pulls today's events filtered to a rolling window (now - past_hours -> 24:00)
from all configured Google Calendars. If capacity remains after today's
visible events, fills with tomorrow's events up to max_events total.

Per-event derivation (is_past, time_label, etc.) happens in render.py so
the offline JSON path produces identical output for the same `now`.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import CalendarAgendaConfig
from ...sources.google_calendar import CalendarError, GoogleCalendarProvider

logger = logging.getLogger(__name__)


def build_live_context(config: CalendarAgendaConfig) -> dict:
    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz=tz)
    today_start = datetime.combine(now.date(), time(0, 0), tzinfo=tz)
    today_end = today_start + timedelta(days=1)
    tomorrow_start = today_end
    tomorrow_end = tomorrow_start + timedelta(days=1)
    # Timed events that ended before this cutoff are hidden. All-day events
    # are always visible - they span the full day and are not time-bounded.
    cutoff = now - timedelta(hours=config.past_hours)

    provider = GoogleCalendarProvider(config.google, timezone=config.timezone)

    try:
        today_all = provider.fetch_events(today_start, today_end)
    except CalendarError as e:
        logger.error("calendar fetch failed: %s", e)
        today_all = []

    # Filter: keep all-day events always; keep timed events only if they
    # ended within the past `past_hours` hours or haven't ended yet.
    today_visible = [
        e for e in today_all
        if e.all_day or e.end >= cutoff
    ][: config.max_events]

    events = [
        {**_event_to_dict(e), "section": "today"}
        for e in today_visible
    ]

    # Fill remaining capacity with tomorrow's events.
    remaining = config.max_events - len(events)
    if remaining > 0:
        try:
            tomorrow_all = provider.fetch_events(tomorrow_start, tomorrow_end)
            for e in tomorrow_all[:remaining]:
                events.append({**_event_to_dict(e), "section": "tomorrow"})
        except CalendarError as e:
            logger.error("tomorrow calendar fetch failed: %s", e)

    has_tomorrow = any(e["section"] == "tomorrow" for e in events)
    tomorrow_label = (now + timedelta(days=1)).strftime("%a %b %-d").upper()

    return {
        "now":            now.isoformat(),
        "today_label":    now.strftime("%a %b %-d").upper(),
        "tomorrow_label": tomorrow_label,
        "has_tomorrow":   has_tomorrow,
        "events":         events,
    }


def _event_to_dict(e) -> dict:
    return {
        "title":           e.title,
        "start":           e.start.isoformat(),
        "end":             e.end.isoformat(),
        "all_day":         e.all_day,
        "calendar_label":  e.calendar_label,
        "location":        e.location,
        "response_status": e.response_status,
    }
