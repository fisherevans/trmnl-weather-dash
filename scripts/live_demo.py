#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "playwright>=1.44",
#     "jinja2>=3.1",
#     "pillow>=10.0",
#     "pydantic>=2.0",
#     "pyyaml>=6.0",
#     "httpx>=0.27",
#     "astral>=3.2",
# ]
# ///
"""Live demo: real NWS data from real US locations, rendered to PNG.

Unlike visual_regression.py (hand-crafted scenarios), this hits
api.weather.gov for actual forecast data. Locations span US time zones
(HI -> AK -> PT -> MT -> CT -> ET) so the renders naturally cover a
range of local times — early morning in Hawaii, evening on the East
Coast — and a range of climates (desert, tropical, subarctic, etc).

Usage:
    uv run scripts/live_demo.py
    open out/live/index.html

NWS rate-limiting: requests are sequential with a small gap. Each
location is one /points + one /gridpoints raw + one /gridpoints/.../forecast
= three calls. Total runtime ~30s for 9 locations.
"""
from __future__ import annotations

import sys
import time as _time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

from trmnldash.config import (Config, ForecastProvider,             # noqa: E402
                                HomeAssistantConfig, LocationConfig,
                                WeatherConfig, WeatherProvider)
from trmnldash.panels.weather_landscape import build_context, render_to_png  # noqa: E402
from trmnldash.sources.base import ForecastError                    # noqa: E402
from trmnldash.sources.factory import (make_forecast_source,        # noqa: E402
                                         make_weather_source)


OUT = ROOT / "out" / "live"


@dataclass(frozen=True)
class Location:
    name: str
    lat: float
    lon: float
    timezone: str
    note: str   # short blurb for the index card


# Spread across timezones + latitudes + climates. NWS covers all of these.
LOCATIONS: list[Location] = [
    Location("Honolulu, HI",     21.31, -157.86, "Pacific/Honolulu",
             "tropical, low latitude, consistent ~6a-6p daylight"),
    Location("Anchorage, AK",    61.22, -149.90, "America/Anchorage",
             "subarctic, very long summer days / short winter days"),
    Location("Seattle, WA",      47.61, -122.33, "America/Los_Angeles",
             "temperate marine, frequent clouds"),
    Location("San Francisco, CA", 37.77, -122.42, "America/Los_Angeles",
             "Mediterranean, marine layer mornings"),
    Location("Phoenix, AZ",      33.45, -112.07, "America/Phoenix",
             "desert, hot/dry, no DST"),
    Location("Denver, CO",       39.74, -104.99, "America/Denver",
             "high-elevation continental"),
    Location("New Orleans, LA",  29.95, -90.07,  "America/Chicago",
             "humid subtropical, frequent thunderstorms"),
    Location("Chicago, IL",      41.88, -87.63,  "America/Chicago",
             "continental, big diurnal swings"),
    Location("Miami, FL",        25.76, -80.19,  "America/New_York",
             "tropical, high humidity"),
    Location("Burlington, VT",   44.49, -73.11,  "America/New_York",
             "cold continental, the project's home location"),
]


def make_config(loc: Location) -> Config:
    return Config(
        location=LocationConfig(lat=loc.lat, lon=loc.lon, timezone=loc.timezone),
        weather=WeatherConfig(
            provider=WeatherProvider.NWS,
            forecast_provider=ForecastProvider.NWS,
            hours=18,
        ),
        # No HA configured -> _collect_entity_ids returns [], skipped cleanly.
        home_assistant=HomeAssistantConfig(base_url="http://unused"),
    )


def render_location(loc: Location, out_path: Path) -> dict:
    """Fetch live NWS data for `loc`, render to PNG, return a small dict
    of metadata for the index page (local time, header prose, etc)."""
    cfg = make_config(loc)
    tz = ZoneInfo(loc.timezone)
    now_local = datetime.now(tz=tz)

    wx_src = make_weather_source(cfg.weather, timezone=loc.timezone)
    forecast_src = make_forecast_source(
        cfg.weather, timezone=loc.timezone, hourly_source=wx_src
    )
    weather = wx_src.fetch(loc.lat, loc.lon, cfg.weather.hours)
    periods = []
    if forecast_src is not None:
        try:
            periods = forecast_src.fetch_periods(loc.lat, loc.lon)
        except ForecastError as e:
            print(f"  ! period prose failed for {loc.name}: {e}")

    ctx = build_context(cfg, weather, ha={}, forecast_periods=periods)
    render_to_png(ctx, out_path, quantize=True)

    chunks = ctx.get("forecast_chunks") or []
    return {
        "local_time":  now_local.strftime("%-I:%M %p"),
        "local_date":  now_local.strftime("%a %b %-d"),
        "first_chunk": chunks[0]["text"] if chunks else "",
        "second_chunk": chunks[1]["text"] if len(chunks) > 1 else "",
        "temp_f":      ctx.get("outside", {}).get("temp_f"),
        "high":        ctx.get("forecast", {}).get("high", {}).get("temp_f"),
        "low":         ctx.get("forecast", {}).get("low", {}).get("temp_f"),
    }


# ── index page ──────────────────────────────────────────────────────────


INDEX_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>trmnldash · live demo</title>
<style>
  :root {{
    --bg:   #f7f7f5;
    --card: #ffffff;
    --ink:  #1a1a1a;
    --soft: #6f6f6f;
    --line: #e1e1de;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 32px 24px 64px;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    background: var(--bg);
    color: var(--ink);
  }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .lede {{ color: var(--soft); font-size: 13px; margin-bottom: 32px; max-width: 760px; }}
  .lede code {{ background: #ececea; padding: 1px 5px; border-radius: 3px; }}
  .card {{
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 8px;
    margin-bottom: 24px;
    overflow: hidden;
  }}
  .head {{
    padding: 14px 18px;
    border-bottom: 1px solid var(--line);
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 8px 18px;
    align-items: baseline;
  }}
  .title {{ font-size: 17px; font-weight: 700; }}
  .meta {{ font-size: 13px; color: var(--soft); font-variant-numeric: tabular-nums; }}
  .note {{ font-size: 13px; color: var(--soft); font-style: italic; grid-column: 1 / -1; margin-top: 2px; }}
  .prose {{
    font-size: 13px;
    color: var(--soft);
    grid-column: 1 / -1;
    margin-top: 4px;
    line-height: 1.4;
  }}
  .prose b {{ color: var(--ink); font-weight: 600; }}
  .img-wrap {{
    background: #fafafa;
    padding: 8px;
    text-align: center;
  }}
  img {{
    max-width: 100%;
    height: auto;
    display: inline-block;
    border: 1px solid var(--line);
  }}
  .toc {{
    margin: 0 0 28px;
    padding: 12px 16px;
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 6px;
    font-size: 13px;
  }}
  .toc a {{ color: var(--ink); text-decoration: none; margin-right: 14px; white-space: nowrap; }}
  .toc a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
  <h1>trmnldash · live demo</h1>
  <div class="lede">
    Real NWS data, rendered at each location's actual local time.
    Generated {generated_at}. {count} locations spanning US time zones
    (HI -> AK -> PT -> MT -> CT -> ET).
    Rerun with <code>uv run scripts/live_demo.py</code>.
  </div>
  <div class="toc">{toc}</div>
  {cards}
</body>
</html>
"""


CARD_HTML = """\
  <div class="card" id="loc-{slug}">
    <div class="head">
      <div class="title">{name}</div>
      <div class="meta">local time: {local_date} · {local_time} · {temp}°F</div>
      <div class="note">{note}</div>
      <div class="prose">{prose}</div>
    </div>
    <div class="img-wrap"><img src="{png}" alt="{name}"></div>
  </div>
"""


def _format_prose(meta: dict) -> str:
    parts = []
    if meta.get("first_chunk"):
        parts.append(f"<b>first chunk:</b> {meta['first_chunk']}")
    if meta.get("second_chunk"):
        parts.append(f"<b>next:</b> {meta['second_chunk']}")
    hi, lo = meta.get("high"), meta.get("low")
    if hi is not None and lo is not None:
        parts.append(f"<b>range:</b> {lo}-{hi}°F")
    return "  ·  ".join(parts)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cards: list[str] = []
    toc_items: list[str] = []
    print(f"rendering {len(LOCATIONS)} live locations...")
    for i, loc in enumerate(LOCATIONS, start=1):
        slug = loc.name.lower().replace(",", "").replace(" ", "-")
        png = OUT / f"{i:02d}-{slug}.png"
        print(f"  #{i:02d} {loc.name} ({loc.timezone})")
        t0 = _time.monotonic()
        try:
            meta = render_location(loc, png)
        except ForecastError as e:
            print(f"     ! NWS error: {e}")
            continue
        elapsed = _time.monotonic() - t0
        print(f"     fetched+rendered in {elapsed:.1f}s · "
              f"local {meta['local_time']} · {meta['temp_f']}°F · "
              f"\"{meta['first_chunk']}\"")
        # Cache-buster on the image URL so phone browsers don't serve a
        # stale PNG from a previous rerun.
        cb = int(png.stat().st_mtime)
        cards.append(CARD_HTML.format(
            slug=slug,
            name=loc.name,
            note=loc.note,
            local_date=meta["local_date"],
            local_time=meta["local_time"],
            temp=meta["temp_f"],
            prose=_format_prose(meta),
            png=f"{png.name}?v={cb}",
        ))
        toc_items.append(f'<a href="#loc-{slug}">{loc.name}</a>')
        # Small gap between NWS fetches — politeness, not a hard limit.
        _time.sleep(0.5)

    index = OUT / "index.html"
    index.write_text(INDEX_HTML.format(
        count=len(cards),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M %Z").strip(),
        toc="".join(toc_items),
        cards="".join(cards),
    ))
    print(f"\nwrote {index}")
    print(f"open file://{index.resolve()}")


if __name__ == "__main__":
    main()
