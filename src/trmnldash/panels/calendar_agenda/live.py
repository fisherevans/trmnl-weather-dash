"""Live-data fetch for the calendar_agenda panel.

Pulls today's events (00:00 -> 24:00 in the configured timezone) from all
configured Google Calendars, returns the render context. Per-event
derivation (is_past, is_next, minutes_until) happens in render.py so the
offline JSON path produces identical output for the same `now`.
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
    start = datetime.combine(now.date(), time(0, 0), tzinfo=tz)
    end = start + timedelta(days=1)
    provider = GoogleCalendarProvider(config.google, timezone=config.timezone)
    try:
        events = provider.fetch_events(start, end)
    except CalendarError as e:
        # No graceful per-event recovery here - a hard auth/network failure
        # means we render no events. Surface clearly in logs; the panel will
        # show "Nothing today" which is at least correctly typed.
        logger.error("calendar fetch failed: %s", e)
        events = []
    # Truncate at max_events first; render.py does the past/next derivation.
    events = events[: config.max_events]
    return {
        "now":   now.isoformat(),
        "today_label": now.strftime("%a %b %-d").upper(),
        "events": [_event_to_dict(e) for e in events],
        "next_event_badge_minutes": config.next_event_badge_minutes,
    }


def _event_to_dict(e) -> dict:
    return {
        "title":          e.title,
        "start":          e.start.isoformat(),
        "end":            e.end.isoformat(),
        "all_day":        e.all_day,
        "calendar_label": e.calendar_label,
        "location":       e.location,
    }
