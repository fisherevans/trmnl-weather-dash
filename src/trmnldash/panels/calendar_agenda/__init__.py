"""Daily-agenda panel sourced from one or more Google Calendars.

Renders today's events as a vertical list: time range, title, calendar
label. Past events are greyed; the next upcoming event gets a "Xm until"
badge. Designed for the TRMNL OG's 800x480 panel as the bottom half of
a vstack with weather_compact on top, but the slot dimensions are
honored at render time so other layouts work.
"""
from .config import CalendarAgendaConfig
from .live import build_live_context
from .render import RENDER_SPEC, render_html, render_to_image
from ...engine.panel import Panel


PANEL = Panel(
    name="calendar_agenda",
    render_spec=RENDER_SPEC,
    config_schema=CalendarAgendaConfig,
    build_live_context=build_live_context,
    render_to_image=render_to_image,
)


__all__ = [
    "PANEL",
    "RENDER_SPEC",
    "CalendarAgendaConfig",
    "build_live_context",
    "render_html",
    "render_to_image",
]
