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
        description="Cap on event rows. Past events still count against this.",
    )


__all__ = ["CalendarAgendaConfig"]
