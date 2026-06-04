"""Calendar-agenda panel config schema."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ...sources.config import GoogleCalendarConfig


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CalendarAgendaConfig(_Strict):
    """Panel-level config for `panels.calendar_agenda`.

    The list of source calendars + their labels lives under `google.calendars`
    (see sources.config.GoogleCalendarConfig). Other knobs control timezone,
    how many events to show, and how the "next event" badge fires.
    """
    timezone: str = Field(
        default="UTC",
        description="IANA timezone name. Drives 'today' boundaries and clock display.",
    )
    google: GoogleCalendarConfig
    max_events: int = Field(
        default=12, ge=1, le=50,
        description=(
            "Total cap on event rows (today + tomorrow combined). "
            "Past events still count against this."
        ),
    )
    past_hours: int = Field(
        default=1, ge=0,
        description=(
            "Hide timed events that ended more than this many hours ago. "
            "All-day events are always shown regardless. "
            "Freed capacity is filled with tomorrow's events. "
            "0 = show only current and future events."
        ),
    )


__all__ = ["CalendarAgendaConfig"]
