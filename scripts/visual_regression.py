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
#     "aiohttp>=3.9",
#     "astral>=3.2",
# ]
# ///
"""Visual regression suite.

Renders 18 hand-crafted scenarios covering edge cases across:
- time of day (sun marker placement, which chunks appear)
- weather conditions (clear, rain, snow, thunder, muggy)
- structural edge cases (sun events at chart edges, narrow regions)

Usage:
    uv run scripts/visual_regression.py
    open out/scenarios/index.html

Each scenario has a slug, title, and description. The generated index page
lists them in order so you can scroll through and call out issues by
scenario number.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Make the trmnldash package importable when running this script directly.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

from trmnldash.panels.weather_landscape import build_context, render_to_png  # noqa: E402
from trmnldash.panels.weather_landscape.config import (LocationConfig,  # noqa: E402
                                                       WeatherLandscapeConfig)
from trmnldash.sources.config import HomeAssistantConfig, WeatherConfig  # noqa: E402
from trmnldash.sources.base import (CurrentObservation, HourlyPoint, # noqa: E402
                                      NormalizedForecast, SunInfo,
                                      deg_to_cardinal)


TZ = ZoneInfo("America/New_York")
OUT = ROOT / "out" / "scenarios"


@dataclass
class Scenario:
    slug: str
    title: str
    description: str
    now: datetime
    hourly: list
    current: CurrentObservation
    sunrise: datetime
    sunset: datetime
    ha: dict = field(default_factory=dict)
    # Past-window accumulation (mm/cm) — what's already fallen, sensed
    # from a rain gauge or NWS observations. Surfaces in the
    # PRECIPITATION title as "(2.4mm already)".
    past_precip_mm: float = 0.0
    past_snow_cm: float = 0.0
    # Health-relevant chips that append to the first header chunk when
    # above threshold (UV>=6, AQI>=100, Pollen>=7).
    uv_index_max: int | None = None
    aqi: int | None = None
    pollen_index: int | None = None
    # Override the render-config summary_side for this scenario only.
    # 'right' mirrors the layout horizontally.
    summary_side: str = "left"


# ── small helpers ────────────────────────────────────────────────────────


def _hourly(
    start: datetime,
    hours: int,
    *,
    sunrise: datetime,
    sunset: datetime,
    base_temp: float = 70.0,
    diurnal_swing: float = 12.0,
    base_cloud: int = 30,
    base_precip: float = 0.0,
    weather_code: int = 1,
    humidity: int = 50,
    precip_window: tuple[int, int] | None = None,    # (start_hour_idx, end_hour_idx)
    precip_intensity: float = 0.0,
    precip_kind: str = "rain",       # "rain" -> precip_mm, "snow" -> snow_cm
    precip_prob_during: int = 85,    # probability inside precip_window
    precip_prob_dry: int = 0,        # probability outside precip_window
    code_during_precip: int | None = None,
    wind_mph: float = 5.0,
    wind_gust_mph: float = 8.0,
    wind_dir: str = "S",
) -> list[HourlyPoint]:
    """Generate hourly forecast points with a diurnal temp curve and optional
    precipitation window. Times in `start` should be tz-aware."""
    out = []
    for i in range(hours):
        ts = start + timedelta(hours=i)
        is_day = sunrise <= _wall(ts, sunrise) < sunset
        # Sinusoidal temp around base_temp, peaking ~3-4pm.
        hour_frac = (ts.hour + ts.minute / 60.0) / 24.0
        phase = (hour_frac - (15 / 24.0)) * 2 * 3.14159
        temp = base_temp + diurnal_swing / 2 * _cos(phase)
        rain = base_precip
        snow = 0.0
        code = weather_code
        prob = precip_prob_dry
        if precip_window is not None and precip_window[0] <= i < precip_window[1]:
            if precip_kind == "snow":
                snow = precip_intensity
            else:
                rain += precip_intensity
            if code_during_precip is not None:
                code = code_during_precip
            prob = precip_prob_during
        out.append(HourlyPoint(
            timestamp=ts,
            temp_f=temp,
            precip_mm=rain,
            snow_cm=snow,
            cloud_pct=base_cloud,
            weather_code=code,
            is_day=is_day,
            humidity_pct=humidity,
            precip_prob_pct=prob,
            wind_mph=wind_mph,
            wind_gust_mph=wind_gust_mph,
            wind_dir=wind_dir,
        ))
    return out


def _cos(rad: float) -> float:
    import math
    return math.cos(rad)


def _wall(ts: datetime, ref: datetime) -> datetime:
    """Return ts shifted onto the same date as ref (for sunrise/sunset compare)."""
    return ts.replace(year=ref.year, month=ref.month, day=ref.day)


def _current(temp_f: float, humidity: int = 50, wind_mph: float = 5.0,
             code: int = 1, is_day: bool = True) -> CurrentObservation:
    return CurrentObservation(
        temp_f=temp_f,
        humidity_pct=humidity,
        wind_mph=wind_mph,
        wind_gust_mph=wind_mph * 1.5,
        wind_dir=deg_to_cardinal(180),
        weather_code=code,
        is_day=is_day,
    )


def _sun(day: datetime, sunrise_hour: float = 5.5, sunset_hour: float = 20.1) -> tuple[datetime, datetime]:
    """Return (sunrise, sunset) for the given day's date at typical times."""
    sr = day.replace(hour=int(sunrise_hour), minute=int((sunrise_hour % 1) * 60),
                     second=0, microsecond=0)
    ss = day.replace(hour=int(sunset_hour), minute=int((sunset_hour % 1) * 60),
                     second=0, microsecond=0)
    return sr, ss


# ── scenario definitions ─────────────────────────────────────────────────


def scenarios() -> list[Scenario]:
    out: list[Scenario] = []
    today = datetime(2026, 5, 18, tzinfo=TZ)

    # === Time of day ============================================================

    # 1. Early morning before sunrise — sunrise visible early, day starts ~hour 1
    now = today.replace(hour=4, minute=30)
    sr, ss = _sun(today, 5.5, 20.1)
    out.append(Scenario(
        slug="pre-dawn",
        title="Pre-dawn (4:30 AM)",
        description="Sunrise just ahead. Window covers tonight (ending) + tomorrow (mostly).",
        now=now,
        hourly=_hourly(now.replace(minute=0), 18, sunrise=sr, sunset=ss,
                       base_temp=58, diurnal_swing=18, base_cloud=20),
        current=_current(56, code=0, is_day=False),
        sunrise=sr,
        sunset=ss,
    ))

    # 2. Morning — full day ahead
    now = today.replace(hour=9, minute=0)
    sr, ss = _sun(today, 5.5, 20.1)
    out.append(Scenario(
        slug="morning",
        title="Mid-morning (9:00 AM)",
        description="Full day visible before sunset. Today/tonight chunks.",
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=75, diurnal_swing=14, base_cloud=25),
        current=_current(73, code=1, is_day=True),
        sunrise=sr,
        sunset=ss,
    ))

    # 3. Midday — sunset mid-chart
    now = today.replace(hour=12, minute=0)
    sr, ss = _sun(today, 5.5, 20.1)
    out.append(Scenario(
        slug="midday",
        title="Midday (12:00 PM)",
        description="Sunset around hour 8 of the chart.",
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=78, diurnal_swing=16, base_cloud=40),
        current=_current(77, code=2, is_day=True),
        sunrise=sr,
        sunset=ss,
    ))

    # 4. Afternoon — sunset closer to start, sunrise NOT in window
    now = today.replace(hour=15, minute=30)
    sr, ss = _sun(today, 5.5, 20.1)
    out.append(Scenario(
        slug="afternoon",
        title="Afternoon (3:30 PM)",
        description="Sunset around hour 4-5. Long night region. Sunrise just at the edge.",
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=80, diurnal_swing=20, base_cloud=35),
        current=_current(82, code=1, is_day=True),
        sunrise=sr,
        sunset=ss,
    ))

    # 5. Just before sunset — sunset right at start
    now = today.replace(hour=19, minute=30)
    sr, ss = _sun(today, 5.5, 20.1)
    out.append(Scenario(
        slug="pre-sunset",
        title="Just before sunset (7:30 PM)",
        description="Sunset within hour 1 of the chart — edge case for marker clipping.",
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=72, diurnal_swing=14, base_cloud=20),
        current=_current(70, code=0, is_day=True),
        sunrise=sr,
        sunset=ss,
    ))

    # 6. Evening / early night
    now = today.replace(hour=21, minute=0)
    sr, ss = _sun(today, 5.5, 20.1)
    out.append(Scenario(
        slug="evening",
        title="Evening (9:00 PM)",
        description="Already night. Tonight chunk + tomorrow chunk.",
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=68, diurnal_swing=14, base_cloud=25),
        current=_current(66, code=0, is_day=False),
        sunrise=sr,
        sunset=ss,
    ))

    # 7. Deep night
    now = today.replace(hour=2, minute=0)
    sr, ss = _sun(today, 5.5, 20.1)
    out.append(Scenario(
        slug="deep-night",
        title="Deep night (2:00 AM)",
        description="Sunrise mid-chart. Tonight (short) + tomorrow chunks.",
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=62, diurnal_swing=18, base_cloud=15),
        current=_current(60, code=0, is_day=False),
        sunrise=sr,
        sunset=ss,
    ))

    # 8. Pre-sunrise — sunrise right at start
    now = today.replace(hour=5, minute=20)
    sr, ss = _sun(today, 5.5, 20.1)
    out.append(Scenario(
        slug="pre-sunrise",
        title="Pre-sunrise (5:20 AM)",
        description="Sunrise within hour 1. Edge case for marker clipping on left.",
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=64, diurnal_swing=16, base_cloud=20),
        current=_current(58, code=0, is_day=False),
        sunrise=sr,
        sunset=ss,
    ))

    # === Weather variations =====================================================

    # 9. Hot and sunny
    now = today.replace(hour=10, minute=0)
    sr, ss = _sun(today, 5.5, 20.1)
    out.append(Scenario(
        slug="hot-sunny",
        title="Hot and sunny",
        description="High temperatures, clear skies, no precipitation.",
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=92, diurnal_swing=12, base_cloud=5,
                       weather_code=0, humidity=35),
        current=_current(92, humidity=35, code=0, is_day=True),
        sunrise=sr,
        sunset=ss,
    ))

    # 10. Cold and snowy — set in mid-February to engage winter feel words.
    now = today.replace(month=2, day=15, hour=14, minute=0)
    sr, ss = _sun(now.replace(hour=12), 7.0, 17.0)
    out.append(Scenario(
        slug="cold-light-snow",
        title="Cold with light snow (Feb)",
        description="Winter scene. Light snow throughout, ~25°F. Season-aware feel.",
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=25, diurnal_swing=8, base_cloud=85,
                       weather_code=71, humidity=70,
                       precip_window=(0, 18), precip_intensity=0.3,
                       precip_kind="snow",
                       code_during_precip=71),
        current=_current(23, humidity=72, code=71, is_day=True),
        sunrise=sr,
        sunset=ss,
    ))

    # 11. Heavy rain
    now = today.replace(hour=11, minute=0)
    sr, ss = _sun(today, 5.5, 20.1)
    out.append(Scenario(
        slug="heavy-rain",
        title="Heavy rain all day",
        description="Sustained heavy rain, overcast.",
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=58, diurnal_swing=4, base_cloud=95,
                       weather_code=65, humidity=90,
                       precip_window=(0, 18), precip_intensity=4.0,
                       code_during_precip=65),
        current=_current(56, humidity=92, code=63, is_day=True),
        sunrise=sr,
        sunset=ss,
    ))

    # 12. Thunderstorms in afternoon
    now = today.replace(hour=10, minute=0)
    sr, ss = _sun(today, 5.5, 20.1)
    h = _hourly(now, 18, sunrise=sr, sunset=ss,
                base_temp=82, diurnal_swing=14, base_cloud=40,
                weather_code=2, humidity=60,
                precip_window=(4, 9), precip_intensity=8.0,
                code_during_precip=95)
    # Bump cloud cover during the precip window
    for i in range(len(h)):
        if 4 <= i < 9:
            h[i] = replace(h[i], cloud_pct=90, humidity_pct=78)
    out.append(Scenario(
        slug="afternoon-thunder",
        title="Afternoon thunderstorms",
        description="Morning clear, severe thunderstorms 2-7 PM, evening calms.",
        now=now,
        hourly=h,
        current=_current(80, humidity=58, code=2, is_day=True),
        sunrise=sr,
        sunset=ss,
    ))

    # 13. Muggy
    now = today.replace(hour=13, minute=0)
    sr, ss = _sun(today, 5.5, 20.1)
    out.append(Scenario(
        slug="muggy",
        title="Muggy summer",
        description="Hot + high humidity. Should trigger 'muggy' prose override.",
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=86, diurnal_swing=10, base_cloud=55,
                       weather_code=2, humidity=85),
        current=_current(88, humidity=83, code=2, is_day=True),
        sunrise=sr,
        sunset=ss,
    ))

    # 14. Overcast no precip
    now = today.replace(hour=11, minute=0)
    sr, ss = _sun(today, 5.5, 20.1)
    out.append(Scenario(
        slug="overcast-dry",
        title="Overcast, no rain",
        description="Heavy cloud cover but no precipitation. Tests cloud row at max.",
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=64, diurnal_swing=6, base_cloud=95,
                       weather_code=3, humidity=68),
        current=_current(63, humidity=70, code=3, is_day=True),
        sunrise=sr,
        sunset=ss,
    ))

    # 15. Mixed: rain ending into clear
    now = today.replace(hour=8, minute=0)
    sr, ss = _sun(today, 5.5, 20.1)
    h = _hourly(now, 18, sunrise=sr, sunset=ss,
                base_temp=68, diurnal_swing=10, base_cloud=20,
                weather_code=0, humidity=55,
                precip_window=(0, 5), precip_intensity=2.5,
                code_during_precip=61)
    # Heavy cloud during precip, dropping after
    for i in range(len(h)):
        if i < 5:
            h[i] = replace(h[i], cloud_pct=80, humidity_pct=75)
        elif i < 8:
            h[i] = replace(h[i], cloud_pct=50, humidity_pct=60)
    out.append(Scenario(
        slug="rain-then-clear",
        title="Rain ending into clear",
        description="Showers early, clears mid-morning. Mixed precip pattern.",
        now=now,
        hourly=h,
        current=_current(65, humidity=78, code=61, is_day=True),
        sunrise=sr,
        sunset=ss,
    ))

    # === Structural edge cases ==================================================

    # 16. Late afternoon — sunset early in the chart, then long night,
    # then sunrise just before the end. Tests both transitions present.
    now = today.replace(hour=17, minute=0)
    sr, ss = _sun(today, 5.5, 20.1)
    out.append(Scenario(
        slug="late-afternoon",
        title="Late afternoon (5 PM)",
        description="Window covers sunset, full night, then early morning. Tests both sun markers.",
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=72, diurnal_swing=14, base_cloud=30),
        current=_current(72, code=1, is_day=True),
        sunrise=sr,
        sunset=ss,
    ))

    # 17. Winter long night — start late evening, sun stays down until ~7am
    now = today.replace(month=12, day=15, hour=21, minute=0)
    winter_day = now.replace(hour=12)
    sr, ss = _sun(winter_day, 7.3, 16.4)   # winter short days
    out.append(Scenario(
        slug="long-winter-night",
        title="Long winter night (9 PM, Dec)",
        description="Late evening into winter morning. Sunrise around hour 10. Cold all night.",
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=18, diurnal_swing=12, base_cloud=40),
        current=_current(20, code=2, is_day=False),
        sunrise=sr,
        sunset=ss,
    ))

    # 18. Long heavy snow into clearing — winter
    now = today.replace(month=12, day=15, hour=8, minute=0)
    sr, ss = _sun(now.replace(hour=12), 7.0, 17.0)
    h = _hourly(now, 18, sunrise=sr, sunset=ss,
                base_temp=24, diurnal_swing=10, base_cloud=90,
                weather_code=75, humidity=75,
                precip_window=(0, 12), precip_intensity=2.5,
                precip_kind="snow",
                code_during_precip=75)
    for i in range(len(h)):
        if i >= 12:
            h[i] = replace(h[i], precip_mm=0.0, snow_cm=0.0,
                           cloud_pct=30, weather_code=1,
                           humidity_pct=55, precip_prob_pct=0)
    out.append(Scenario(
        slug="heavy-snow-then-sun",
        title="Heavy snow then clearing",
        description="12 hours heavy snow, then sun. Tests snow palette + transitions.",
        now=now,
        hourly=h,
        current=_current(22, humidity=78, code=75, is_day=True),
        sunrise=sr,
        sunset=ss,
    ))

    # 19. Mid-storm — already-fallen rain plus more in the forecast.
    # Exercises the "(X mm already)" suffix on the precip-row title.
    now = today.replace(month=10, day=14, hour=14, minute=0)
    sr, ss = _sun(now.replace(hour=12), 7.0, 18.3)
    h = _hourly(now, 18, sunrise=sr, sunset=ss,
                base_temp=54, diurnal_swing=8, base_cloud=92,
                weather_code=63, humidity=88,
                precip_window=(0, 8), precip_intensity=1.8,
                precip_prob_during=85,
                code_during_precip=63)
    out.append(Scenario(
        slug="ongoing-rain-with-prior",
        title="Mid-storm: prior accumulation + more coming",
        description=("Rendered mid-afternoon after a morning of rain. The "
                     "precip title shows forecast total plus the already-fallen "
                     "amount in parentheses."),
        now=now,
        hourly=h,
        current=_current(54, humidity=92, code=63, is_day=True),
        sunrise=sr,
        sunset=ss,
        past_precip_mm=11.6,
    ))

    # 20. Health-metric chips — high UV + AQI + pollen all triggering.
    # Exercises the optional metadata chips that append to TODAY's prose
    # when each metric is above its noteworthy threshold.
    now = today.replace(month=7, day=18, hour=10, minute=0)
    sr, ss = _sun(now.replace(hour=12), 5.4, 20.6)
    out.append(Scenario(
        slug="health-chips",
        title="High UV, AQI, and pollen",
        description=("Summer day with all three health chips appended to the "
                     "TODAY prose: UV peaks at 9, AQI at 128, pollen at 10."),
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=88, diurnal_swing=14, base_cloud=15,
                       weather_code=0, humidity=55),
        current=_current(86, humidity=52, code=0, is_day=True),
        sunrise=sr,
        sunset=ss,
        uv_index_max=9,
        aqi=215,
        pollen_index=10,
    ))

    # 21. Mirrored layout — summary stack on the right, chart on the left.
    # Exercises the render.summary_side='right' toggle.
    now = today.replace(hour=10, minute=0)
    sr, ss = _sun(today, 5.5, 20.1)
    out.append(Scenario(
        slug="mirrored-layout",
        title="Mirrored layout (summary on right)",
        description=("Same dashboard, summary stack flipped to the right "
                     "side. The chart card now occupies the left two thirds; "
                     "the last header chunk separator aligns with the chart "
                     "card's right edge."),
        now=now,
        hourly=_hourly(now, 18, sunrise=sr, sunset=ss,
                       base_temp=68, diurnal_swing=12, base_cloud=40,
                       weather_code=2, humidity=55),
        current=_current(70, humidity=58, code=2, is_day=True),
        sunrise=sr,
        sunset=ss,
        summary_side="right",
    ))

    return out


# ── render + page generation ─────────────────────────────────────────────


def make_config() -> WeatherLandscapeConfig:
    return WeatherLandscapeConfig(
        location=LocationConfig(lat=43.21, lon=-71.54, timezone="America/New_York"),
        weather=WeatherConfig(hours=18),
        home_assistant=HomeAssistantConfig(base_url="http://unused"),
    )


INDEX_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>trmnldash · visual regression</title>
<style>
  :root {{
    --ink: #111;
    --soft: #666;
    --bg: #f4f4f4;
    --card: #fff;
    --line: #ddd;
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
  .lede {{ color: var(--soft); font-size: 13px; margin-bottom: 32px; }}
  .scenario {{
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 8px;
    margin-bottom: 24px;
    overflow: hidden;
  }}
  .head {{
    padding: 14px 18px;
    border-bottom: 1px solid var(--line);
    display: flex;
    align-items: baseline;
    gap: 14px;
  }}
  .num {{
    font-family: ui-monospace, Menlo, monospace;
    font-size: 14px;
    font-weight: 700;
    color: var(--soft);
    min-width: 28px;
  }}
  .title {{
    font-size: 16px;
    font-weight: 600;
    flex: 1;
  }}
  .desc {{
    font-size: 13px;
    color: var(--soft);
    font-style: italic;
  }}
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
</style>
</head>
<body>
  <h1>trmnldash · visual regression</h1>
  <div class="lede">{count} scenarios. Regenerate with <code>uv run scripts/visual_regression.py</code>.</div>
  {cards}
</body>
</html>
"""


CARD_HTML = """\
  <div class="scenario" id="scenario-{n}">
    <div class="head">
      <div class="num">#{n:02d}</div>
      <div class="title">{title}</div>
      <div class="desc">{desc}</div>
    </div>
    <div class="img-wrap"><img src="{png}" alt="scenario {n}"></div>
  </div>
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base_cfg = make_config()
    cards = []
    for i, s in enumerate(scenarios(), start=1):
        wx = NormalizedForecast(
            hourly=s.hourly,
            current=s.current,
            sun=SunInfo(sunrise=s.sunrise, sunset=s.sunset),
        )
        # Per-scenario override of render.summary_side so the index page
        # can preview both layouts.
        cfg = base_cfg.model_copy(deep=True)
        cfg.render.summary_side = s.summary_side
        ctx = build_context(
            cfg, wx, ha=s.ha, _now=s.now,
            past_precip_mm=s.past_precip_mm,
            past_snow_cm=s.past_snow_cm,
            uv_index_max=s.uv_index_max,
            aqi=s.aqi,
            pollen_index=s.pollen_index,
        )
        out_path = OUT / f"{i:02d}-{s.slug}.png"
        render_to_png(ctx, out_path, quantize=True)
        # Cache-buster on the image URL so phone browsers don't serve
        # a stale PNG from a previous rerun.
        cb = int(out_path.stat().st_mtime)
        cards.append(CARD_HTML.format(
            n=i,
            title=s.title.replace("<", "&lt;"),
            desc=s.description.replace("<", "&lt;"),
            png=f"{out_path.name}?v={cb}",
        ))
        print(f"  #{i:02d} {s.slug}: {ctx.get('forecast_chunks')[:80] if False else ''}")

    index = OUT / "index.html"
    index.write_text(INDEX_HTML.format(count=len(cards), cards="".join(cards)))
    print(f"\nwrote {index}")
    print(f"open file://{index.resolve()}")


if __name__ == "__main__":
    main()
