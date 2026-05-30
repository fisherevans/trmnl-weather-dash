"""Google Calendar source: read-only fetch of events across one or more
calendars via the Google Calendar v3 API.

Auth model is OAuth desktop client + refresh token. The user creates an
OAuth client in Google Cloud Console (Desktop application type), runs
`scripts/google_auth.py` once locally to grant calendar.readonly scope
and persist a refresh token. The source uses the token at fetch time;
google-auth handles the access-token refresh dance automatically.

Why not iCal: the iCal secret-URL path is simpler but each calendar is
a separate URL and event metadata is sparser (no event IDs, weaker
recurring-instance handling). The OAuth API path gives us a single auth
context for any number of calendars and clean recurring-event expansion
via `singleEvents=True`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from .config import CalendarRef, ConfigError, GoogleCalendarConfig

logger = logging.getLogger(__name__)


class CalendarError(Exception):
    """Raised when calendar events cannot be fetched (auth, network, parse)."""


@dataclass(frozen=True)
class Event:
    """A single event instance ready for the panel to render.

    `all_day` events have start.time() == 00:00 and end at start-of-next-day
    in the local timezone; the renderer treats them as a top-of-list block
    without start/end clock times.

    `response_status` is the current user's RSVP for this event when
    `self` is in the attendees list:
      'accepted'    - explicit yes (no badge)
      'needsAction' - invited but not responded (renders "INVITED")
      'tentative'   - maybe (renders "MAYBE")
      'declined'    - filtered out at fetch time, never reaches the renderer
    Defaults to 'accepted' for events where the user isn't listed as an
    attendee (own calendar / shared calendars where you're the organizer).
    """
    title: str
    start: datetime              # tz-aware (local)
    end: datetime                # tz-aware (local)
    all_day: bool
    calendar_label: str
    location: str | None = None
    response_status: str = "accepted"


class CalendarSource(Protocol):
    """Generic calendar source. One method: pull events in a tz-aware window."""

    def fetch_events(self, start: datetime, end: datetime) -> list[Event]: ...


class GoogleCalendarProvider:
    """Google Calendar v3 implementation. One provider serves multiple
    calendar IDs - one API call per calendar (sequential, ~tens of ms each
    against Google's edges, fine for a 1-5 minute render cadence).
    """

    def __init__(self, config: GoogleCalendarConfig, *, timezone: str):
        self._config = config
        self._tz = ZoneInfo(timezone)
        self._service = None  # built lazily on first fetch

    def fetch_events(self, start: datetime, end: datetime) -> list[Event]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("fetch_events window must be tz-aware")
        service = self._build_service()
        all_events: list[Event] = []
        for cal in self._config.calendars:
            try:
                items = self._list_events(service, cal.id, start, end)
            except Exception as e:                                  # noqa: BLE001 - log + continue
                logger.warning(
                    "calendar %s (%s) fetch failed: %s",
                    cal.label, cal.id, e,
                )
                continue
            for raw in items:
                ev = self._parse_event(raw, cal)
                if ev is not None:
                    all_events.append(ev)
        # Stable sort by start; all-day events naturally sort first because
        # their start is at midnight local.
        all_events.sort(key=lambda e: e.start)
        return all_events

    # ── internals ──────────────────────────────────────────────────────────

    def _build_service(self):
        if self._service is not None:
            return self._service
        # Import here so callers that never touch this source don't pay the
        # google-api-python-client import cost (and so missing deps don't
        # break the package's offline render path).
        from google.auth.transport.requests import Request           # type: ignore[import-not-found]
        from google.oauth2.credentials import Credentials             # type: ignore[import-not-found]
        from googleapiclient.discovery import build                   # type: ignore[import-not-found]

        token_path = Path(self._config.token_file)
        if not token_path.exists():
            raise ConfigError(
                f"Google Calendar token file not found: {token_path}. "
                f"Run `uv run scripts/google_auth.py` to generate it."
            )
        creds = Credentials.from_authorized_user_file(
            str(token_path),
            scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        )
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                # Persist the refreshed creds so the access-token rotation
                # survives container restarts. .to_json() includes the
                # refresh token, so the file stays usable.
                token_path.write_text(creds.to_json())
            else:
                raise CalendarError(
                    "Google Calendar credentials are invalid and have no refresh "
                    "token. Re-run scripts/google_auth.py."
                )
        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def _list_events(self, service, calendar_id: str,
                     start: datetime, end: datetime) -> list[dict]:
        # singleEvents=True expands recurring rules into per-instance entries.
        # orderBy='startTime' is only valid when singleEvents=True.
        resp = service.events().list(
            calendarId=calendar_id,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=100,
        ).execute()
        return resp.get("items", [])

    def _parse_event(self, raw: dict, cal: CalendarRef) -> Event | None:
        # Google returns either {date: 'YYYY-MM-DD'} for all-day events
        # or {dateTime: ISO8601, timeZone: ...} for timed events. The two
        # cases parse differently.
        start_raw = raw.get("start") or {}
        end_raw = raw.get("end") or {}
        if "date" in start_raw:
            try:
                start_d = date.fromisoformat(start_raw["date"])
                end_d = date.fromisoformat(end_raw.get("date", start_raw["date"]))
            except ValueError:
                return None
            # All-day events render as midnight-to-next-midnight in the local
            # tz so the renderer's "is this before now?" check is consistent.
            start_dt = datetime.combine(start_d, time(0, 0), tzinfo=self._tz)
            end_dt = datetime.combine(end_d, time(0, 0), tzinfo=self._tz)
            all_day = True
        elif "dateTime" in start_raw:
            try:
                start_dt = datetime.fromisoformat(start_raw["dateTime"]).astimezone(self._tz)
                end_dt = datetime.fromisoformat(end_raw["dateTime"]).astimezone(self._tz)
            except ValueError:
                return None
            all_day = False
        else:
            # Malformed entry (no start) — skip without raising.
            return None
        # Pick the current user's RSVP from the attendees list. Google
        # marks the user's own row with `self: true`; events the user
        # created (own calendar, no other attendees) and shared
        # calendars where the user isn't in the attendee list both
        # fall through to "accepted" so they render without a badge.
        response_status = "accepted"
        for att in raw.get("attendees") or []:
            if att.get("self"):
                response_status = att.get("responseStatus", "accepted")
                break
        # Declined events shouldn't appear on the agenda at all -
        # filter at the source so the panel never has to think about
        # them.
        if response_status == "declined":
            return None

        title = (raw.get("summary") or "(no title)").strip()
        location = (raw.get("location") or "").strip() or None
        return Event(
            title=title,
            start=start_dt,
            end=end_dt,
            all_day=all_day,
            calendar_label=cal.label,
            location=location,
            response_status=response_status,
        )


__all__ = ["CalendarError", "CalendarSource", "Event", "GoogleCalendarProvider"]
