"""Merge a NormalizedForecast + HA SensorReadings into the dict the
Jinja template consumes.

The output shape is the same one `data.json` documents — see that file
for the canonical reference. Anything new the template needs starts
here so the renderer stays purely mechanical.

Source-precedence rules (set in trmnl-weather-dash#5 design):

- **outdoor temp / humidity:** prefer HA when present; fall back to the
  weather API's current observation. The HA sensor is the actual reading
  at the user's location; averaging it with a 1-30km-away API estimate
  dilutes ground truth.
- **indoor temp / humidity:** mean across all configured HA sensors.
  No HA fallback (the API doesn't measure indoors). Zero readings -> "--".
- **forecast (hourly, sun, wind, condition):** always from the weather API.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .bg_shading import cloud_bucket, precip_bucket, row_bg_color, shaded_svg_url
from .config import Config, SensorRef, as_sensor_list
from .sources.base import ForecastPeriod, NormalizedForecast
from .sources.homeassistant import SensorReading


# Open-Meteo's WMO weather codes -> (day stem, night stem) from makin-grey/.
# Codes that don't have a meaningful day/night split (e.g. fog, sleet,
# severe thunder) use the same stem for both.
WMO_ICON_MAP: dict[int, tuple[str, str]] = {
    0:  ("clear-day",                  "clear-night"),
    1:  ("cloudy-1-day",               "cloudy-1-night"),
    2:  ("partly-cloudy-day",          "partly-cloudy-night"),
    3:  ("cloudy",                     "cloudy"),
    45: ("fog-day",                    "fog-night"),
    48: ("fog-day",                    "fog-night"),
    51: ("rainy-1-day",                "rainy-1-night"),
    53: ("rainy-1-day",                "rainy-1-night"),
    55: ("rainy-2-day",                "rainy-2-night"),
    56: ("rain-and-sleet-mix",         "rain-and-sleet-mix"),
    57: ("rain-and-sleet-mix",         "rain-and-sleet-mix"),
    61: ("rainy-1-day",                "rainy-1-night"),
    63: ("rainy-2-day",                "rainy-2-night"),
    65: ("rainy-3-day",                "rainy-3-night"),
    66: ("rain-and-sleet-mix",         "rain-and-sleet-mix"),
    67: ("rain-and-sleet-mix",         "rain-and-sleet-mix"),
    71: ("snowy-1-day",                "snowy-1-night"),
    73: ("snowy-2-day",                "snowy-2-night"),
    75: ("snowy-3-day",                "snowy-3-night"),
    77: ("snowy-1-day",                "snowy-1-night"),
    80: ("rainy-1-day",                "rainy-1-night"),
    81: ("rainy-2-day",                "rainy-2-night"),
    82: ("rainy-3-day",                "rainy-3-night"),
    85: ("snowy-1-day",                "snowy-1-night"),
    86: ("snowy-2-day",                "snowy-2-night"),
    95: ("scattered-thunderstorms-day", "scattered-thunderstorms-night"),
    96: ("severe-thunderstorm",        "severe-thunderstorm"),
    99: ("severe-thunderstorm",        "severe-thunderstorm"),
}
DEFAULT_ICON = ("cloudy", "cloudy")             # neutral fallback for unknown codes
SNOW_CODES = frozenset({71, 73, 75, 77, 85, 86})

# Look-ahead and deadbands for the OUTSIDE trend arrows. Compares the
# current observation against the forecast `TREND_HOURS` hours out.
TREND_HOURS = 3
TREND_TEMP_THRESHOLD_F = 2.0
# 8% chosen as the humidity deadband: typical hourly RH drift from the
# diurnal cycle is 3-6%, so 8% suppresses that noise while still flagging
# a real shift (front moving in, etc).
TREND_HUMIDITY_THRESHOLD_PCT = 8


def build_context(
    config: Config,
    weather: NormalizedForecast,
    ha: dict[str, SensorReading],
    _now: datetime | None = None,
    forecast_periods: list[ForecastPeriod] | None = None,
) -> dict:
    if not weather.hourly:
        raise ValueError("weather.hourly is empty — provider returned no data")

    tz = ZoneInfo(config.location.timezone)
    # `_now` lets tests/scenario generators pin a specific clock time so
    # the same input renders deterministically. Production passes None
    # and falls through to datetime.now().
    now = _now if _now is not None else datetime.now(tz=tz)
    chart_start = weather.hourly[0].timestamp
    n_hours = len(weather.hourly)

    # ── outdoor: HA-as-truth, API fallback ────────────────────────────────
    sensors = config.home_assistant.sensors
    out_temp = _ha_mean(sensors.outdoor_temp_f, ha)
    if out_temp is None:
        out_temp = weather.current.temp_f
    out_hum = _ha_mean(sensors.outdoor_humidity, ha)
    if out_hum is None:
        out_hum = weather.current.humidity_pct

    # ── indoor: mean across HA, no fallback ───────────────────────────────
    in_temp = _ha_mean(sensors.indoor_temp_f, ha)
    in_hum  = _ha_mean(sensors.indoor_humidity, ha)

    # ── current condition (icon) ──────────────────────────────────────────
    condition = wmo_to_icon(weather.current.weather_code, weather.current.is_day)

    # ── outdoor trends (next ~3h vs now) ──────────────────────────────────
    # Compares current observation to the forecast 3 hours out. Within
    # the deadband -> "flat". Renders as ↗ / → / ↘ on the OUTSIDE card.
    look_ahead = min(TREND_HOURS, len(weather.hourly) - 1)
    future = weather.hourly[look_ahead]
    temp_trend = _trend(future.temp_f - weather.current.temp_f, TREND_TEMP_THRESHOLD_F)
    humidity_trend = "flat"
    if future.humidity_pct is not None:
        humidity_trend = _trend(
            future.humidity_pct - weather.current.humidity_pct,
            TREND_HUMIDITY_THRESHOLD_PCT,
        )

    # ── hourly slice ──────────────────────────────────────────────────────
    # snow_cm shares the y-axis with precip_mm in the chart (1 cm snow
    # ≈ 1 mm liquid rain at the loose-pack ratio TRMNL displays at).
    # Stacked bars in the template render snow under rain.
    hourly = [
        {
            "hour":      _format_chart_hour(h.timestamp),
            "precip_mm": round(h.precip_mm, 1),
            "snow_cm":   round(h.snow_cm, 1),
            "cloud_pct": h.cloud_pct,
            "is_night":  not h.is_day,
            "temp_f":    round(h.temp_f),
        }
        for h in weather.hourly
    ]

    # ── sun markers (hour_index relative to chart start) ──────────────────
    sunrise_idx = (weather.sun.sunrise - chart_start).total_seconds() / 3600
    sunset_idx  = (weather.sun.sunset  - chart_start).total_seconds() / 3600

    # ── forecast high/low across the visible window ───────────────────────
    # `tomorrow` flags add a "TMRW" badge in the template. Rules differ for
    # high vs low because of how the diurnal cycle reads:
    #   - High: peaks mid-afternoon. After today's peak, the window's
    #     next high is tomorrow's. Flag any high timestamp on a later
    #     date than `now`.
    #   - Low: troughs in the pre-dawn hours. A 5am low *technically* on
    #     tomorrow's date is still "tonight's overnight low" colloquially
    #     — flagging it as TMRW is noisy. Only flag if the low is past
    #     noon tomorrow (a rare day-cooling event).
    high_h = max(weather.hourly, key=lambda h: h.temp_f)
    low_h  = min(weather.hourly, key=lambda h: h.temp_f)
    today = now.date()
    noon_tomorrow = (now + timedelta(days=1)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )

    # ── precip type drives bg-{rain,snow}.svg selection ───────────────────
    # The bg chooses one or the other; bars themselves stack snow under
    # rain regardless, so a mixed forecast still shows both quantities.
    # Prefer the larger magnitude; if neither has volumetric data,
    # fall back to weather-code presence (covers NWS-like providers
    # that don't expose mm/cm at all).
    total_rain_mm = sum(h.precip_mm for h in weather.hourly)
    total_snow_cm = sum(h.snow_cm for h in weather.hourly)
    if total_snow_cm > total_rain_mm:
        precip_type = "snow"
    elif total_rain_mm > 0:
        precip_type = "rain"
    elif any(h.weather_code in SNOW_CODES for h in weather.hourly):
        precip_type = "snow"
    else:
        precip_type = "rain"

    # ── chart-footer summary ──────────────────────────────────────────────
    # `total_precip_mm` is now total chart UNITS (mm rain + cm snow, with
    # 1 cm snow ≈ 1 mm rain in liquid equivalent, so they share a y-axis
    # in the chart even though the units are reported separately).
    total_precip_mm = total_rain_mm + total_snow_cm
    avg_cloud_pct   = round(sum(h.cloud_pct for h in weather.hourly) / n_hours)

    # ── group hourly into day/night regions for the chart ─────────────────
    enriched_for_regions = [
        {"is_night": not h.is_day} for h in weather.hourly
    ]
    regions = compute_regions(enriched_for_regions, n_hours)

    # ── per-region labels (centered inside each visible day/night block) ──
    # Each region is a contiguous run of same-is_day hours. Width < 4 hrs
    # gets a None label — too cramped to render legibly without clipping
    # at the card edge or wrapping a two-word label like "Partly Cloudy".
    for r in regions:
        pts = weather.hourly[r["start"]:r["end"]]
        if (r["end"] - r["start"]) < 4:
            r["precip_label"] = None
            r["cloud_label"] = None
        else:
            r["precip_label"] = _region_precip_label(pts, precip_type)
            r["cloud_label"]  = _region_cloud_label(pts)

    # ── sun-event markers at the day/night background transition rather
    # than the precise hour-axis position. Markers land on the obvious
    # tonal break in the chart instead of slightly to one side of it,
    # which reads as askew. Clamped 4-96% so a transition right at the
    # chart edge doesn't get clipped. The wall-clock time in the marker
    # pill stays the real sunrise/sunset time.
    sun_markers = []
    for i in range(len(regions) - 1):
        a, b = regions[i], regions[i + 1]
        at = max(0.04, min(0.96, a["end_at"]))
        if a["is_night"] and not b["is_night"]:
            sun_markers.append({
                "label": "SUNRISE",
                "at":    at,
                "time":  _format_clock(weather.sun.sunrise),
            })
        elif (not a["is_night"]) and b["is_night"]:
            sun_markers.append({
                "label": "SUNSET",
                "at":    at,
                "time":  _format_clock(weather.sun.sunset),
            })

    # ── forecast prose chunks (TODAY/TONIGHT/TOMORROW summaries) ──────────
    # Prefer provider-supplied period prose (NWS shortForecast) when
    # available — it's human-written and reflects forecaster judgment.
    # Fall back to _summarize-derived strings from hourly numerics.
    if forecast_periods:
        forecast_chunks = _forecast_chunks_from_periods(forecast_periods, now)
    else:
        forecast_chunks = _forecast_chunks(weather.hourly, precip_type)

    # ── density-shifted background SVGs ───────────────────────────────────
    # Cloud-row darkness scales with avg cloud %, precip-row scales with
    # total mm. SVG fills get swapped at render time and inlined as
    # data: URLs (see bg_shading.py).
    cloud_b = cloud_bucket(avg_cloud_pct)
    precip_b = precip_bucket(total_precip_mm)
    precip_svg = "bg-snow.svg" if precip_type == "snow" else "bg-rain.svg"
    cloud_bg_url_day    = shaded_svg_url("bg-cloud.svg", cloud_b, "day")
    cloud_bg_url_night  = shaded_svg_url("bg-cloud.svg", cloud_b, "night")
    precip_bg_url_day   = shaded_svg_url(precip_svg,    precip_b, "day")
    precip_bg_url_night = shaded_svg_url(precip_svg,    precip_b, "night")
    cloud_bg_color_day    = row_bg_color(cloud_b,  "day")
    cloud_bg_color_night  = row_bg_color(cloud_b,  "night")
    precip_bg_color_day   = row_bg_color(precip_b, "day")
    precip_bg_color_night = row_bg_color(precip_b, "night")

    # ── no-data overlay text (only when bucket 0 = clear / dry) ──────────
    # Fills the row with a short message instead of leaving the eye to
    # parse a near-empty chart. Cloud overlay also triggers when cloud
    # cover is genuinely low even if bucket > 0 in edge cases, but
    # tying to the bucket keeps the rule simple.
    cloud_empty_text  = "CLEAR SKIES"           if cloud_b == 0 else None
    precip_empty_text = (
        "NO SNOW FORECASTED" if (precip_b == 0 and precip_type == "snow") else
        "NO RAIN FORECASTED" if precip_b == 0 else None
    )

    # Header time is rounded to the nearest 5 minutes — the device's
    # Image Display poll floor on TRMNL+, so a precise minute can be up
    # to ~5 min stale before the next poll. The "Updated" stamp in the
    # bottom-right stays precise; it's the data-freshness indicator.
    # Both come from the same tz-aware `now` so they're consistent even
    # when the container's host TZ defaults to UTC.
    return {
        "updated_at": now.strftime("%-I:%M %p"),
        "date_line": now.strftime("%A, %B %-d, %Y").upper(),
        "time":      _round_to_minutes(now, 5).strftime("%-I:%M %p"),
        "inside": {
            "temp_f":       _round_or_placeholder(in_temp),
            "humidity_pct": _round_or_placeholder(in_hum),
        },
        "outside": {
            "temp_f":         round(out_temp),
            "temp_trend":     temp_trend,
            "humidity_pct":   round(out_hum),
            "humidity_trend": humidity_trend,
            "condition":      condition,
            "wind": {
                "speed_mph": round(weather.current.wind_mph),
                "gust_mph":  round(weather.current.wind_gust_mph),
                "direction": weather.current.wind_dir,
            },
        },
        "forecast": {
            "high": {
                "temp_f":   round(high_h.temp_f),
                "time":     _format_clock(high_h.timestamp),
                "tomorrow": high_h.timestamp.date() != today,
            },
            "low": {
                "temp_f":   round(low_h.temp_f),
                "time":     _format_clock(low_h.timestamp),
                "tomorrow": low_h.timestamp >= noon_tomorrow,
            },
        },
        "sun": {
            "sunrise": {"time": _format_clock(weather.sun.sunrise), "hour_index": sunrise_idx},
            "sunset":  {"time": _format_clock(weather.sun.sunset),  "hour_index": sunset_idx},
        },
        "precip_type":           precip_type,
        "total_accumulation_mm": round(total_precip_mm, 1),
        "avg_cloud_pct":         avg_cloud_pct,
        "cloud_description":     _cloud_description(avg_cloud_pct),
        "precip_description":    _precip_description(total_precip_mm, precip_type),
        "cloud_bg_url_day":      cloud_bg_url_day,
        "cloud_bg_url_night":    cloud_bg_url_night,
        "precip_bg_url_day":     precip_bg_url_day,
        "precip_bg_url_night":   precip_bg_url_night,
        "cloud_bg_color_day":    cloud_bg_color_day,
        "cloud_bg_color_night":  cloud_bg_color_night,
        "precip_bg_color_day":   precip_bg_color_day,
        "precip_bg_color_night": precip_bg_color_night,
        "cloud_empty_text":      cloud_empty_text,
        "precip_empty_text":     precip_empty_text,
        "forecast_chunks":       forecast_chunks,
        "sun_markers":           sun_markers,
        "regions":               regions,
        "night_regions":         [r for r in regions if r["is_night"]],
        "hourly":                hourly,
    }


# ──────────────────────────────────────────────────────────────────────────


def wmo_to_icon(code: int, is_day: bool) -> str:
    day, night = WMO_ICON_MAP.get(code, DEFAULT_ICON)
    return day if is_day else night


def _trend(delta: float, threshold: float) -> str:
    """Return 'up' / 'flat' / 'down' for a deadband around 0."""
    if delta > threshold:
        return "up"
    if delta < -threshold:
        return "down"
    return "flat"


def _ha_mean(ref: SensorRef, ha: dict[str, SensorReading]) -> float | None:
    """Mean over all available readings from the configured entity IDs.
    Returns None if no entity has a usable reading."""
    eids = as_sensor_list(ref)
    vals = [ha[e].state for e in eids if e in ha]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _round_or_placeholder(v: float | None) -> int | str:
    return round(v) if v is not None else "--"


def _format_chart_hour(dt: datetime) -> str:
    """Format hourly[].hour as the chart's compact label, e.g. '11a', '3p'."""
    h12 = dt.strftime("%-I")
    suffix = "a" if dt.hour < 12 else "p"
    return f"{h12}{suffix}"


def _format_clock(dt: datetime) -> str:
    """Format wall-clock time, e.g. '3:00 PM', '5:32 AM'."""
    return dt.strftime("%-I:%M %p")


def compute_regions(hourly: list, n_hours: int) -> list:
    """Group contiguous hours by is_night into regions covering the chart width.
    Operates on the enriched-hourly dicts produced by `build_context` /
    render-time enrichment (each item has a `is_night` bool).
    """
    if not hourly:
        return []
    regions = []
    cur_start = 0
    cur_night = hourly[0]["is_night"]
    for i, h in enumerate(hourly):
        if h["is_night"] != cur_night:
            regions.append({"start": cur_start, "end": i, "is_night": cur_night})
            cur_start = i
            cur_night = h["is_night"]
    regions.append({"start": cur_start, "end": len(hourly), "is_night": cur_night})
    for r in regions:
        r["start_at"] = r["start"] / n_hours
        r["end_at"]   = r["end"]   / n_hours
        r["width_at"] = r["end_at"] - r["start_at"]
    return regions


def _round_to_minutes(dt: datetime, minutes: int) -> datetime:
    """Round `dt` to the nearest multiple of `minutes`. Used for the
    header time display: TRMNL devices poll the dashboard every ~5 min
    (Image Display floor on TRMNL+), so showing a precise minute makes
    the clock feel "wrong" when the user looks at the panel 4 minutes
    after the last poll. Rounding to a multiple of 5 reads as casually
    approximate without losing utility."""
    discard = timedelta(
        minutes=dt.minute % minutes,
        seconds=dt.second,
        microseconds=dt.microsecond,
    )
    dt -= discard
    if discard >= timedelta(minutes=minutes / 2):
        dt += timedelta(minutes=minutes)
    return dt


def _cloud_description(pct: int) -> str:
    if pct < 13:
        return "Clear"
    if pct < 31:
        return "Mostly Sunny"
    if pct < 61:
        return "Partly Cloudy"
    if pct < 88:
        return "Mostly Cloudy"
    return "Overcast"


def _precip_description(total_mm: float, precip_type: str) -> str:
    """Short textual condition for the precip row's corner label.
    Tiers loosely match precip_bucket() thresholds in bg_shading.py."""
    label = "Snow" if precip_type == "snow" else "Rain"
    if total_mm < 1.0:
        return f"No {label}"
    if total_mm < 5.0:
        return f"Light {label}"
    if total_mm < 15.0:
        return f"Moderate {label}"
    return f"Heavy {label}"


def _region_precip_label(points, precip_type: str) -> str:
    """Short label centered inside a chart region on the precip row.
    Magnitude combines rain (mm) and snow (cm) on the same axis."""
    total = sum(p.precip_mm for p in points) + sum(p.snow_cm for p in points)
    return _precip_description(total, precip_type)


def _region_cloud_label(points) -> str:
    """Short label centered inside a chart region on the cloud row."""
    avg = sum(p.cloud_pct for p in points) / len(points)
    return _cloud_description(int(round(avg)))


def _forecast_chunks_from_periods(periods: list[ForecastPeriod], now: datetime) -> list[dict]:
    """Map provider-supplied periods to the TODAY/TONIGHT/TOMORROW chunks
    the template renders.

    NWS emits periods in time order: "Today", "Tonight", "Tuesday",
    "Tuesday Night", etc. We pick the first two whose `end` is in the
    future, then label them per their day/night flag + ordering, so a
    night-time invocation gets [TONIGHT, TOMORROW] rather than awkwardly
    showing "TODAY" for a chunk that has already ended.

    Strings are lowercased to match _summarize's style (the header
    template renders the chip label uppercase + body lowercase).
    """
    upcoming = [p for p in periods if p.end > now]
    if not upcoming:
        return []
    out = []
    for i, p in enumerate(upcoming[:2]):
        if i == 0:
            label = "TODAY" if p.is_day else "TONIGHT"
        else:
            label = "TOMORROW" if p.is_day else "TONIGHT"
        out.append({"label": label, "text": (p.short_forecast or "").lower()})
    return out


def _forecast_chunks(hourly, precip_type: str) -> list[dict]:
    """Group `hourly` into 2 contiguous day-or-night periods and produce
    a short prose forecast for each.

    Typical output:
      - currently daytime:  [TODAY summary, TONIGHT summary]
      - currently nighttime: [TONIGHT summary, TOMORROW summary]

    Chunks shorter than ~3 hours are skipped — not enough data to
    summarize meaningfully.
    """
    if not hourly:
        return []

    # Walk hourly and group contiguous same-is_day points.
    groups: list[list] = []
    current: list = [hourly[0]]
    for p in hourly[1:]:
        if p.is_day == current[0].is_day:
            current.append(p)
        else:
            groups.append(current)
            current = [p]
    groups.append(current)

    # Trim short tail chunks (typical 18h window starting in day might
    # produce [today, tonight, ~2h of tomorrow] — chop the tail).
    groups = [g for g in groups if len(g) >= 3]
    if not groups:
        return []

    chunks = []
    for i, g in enumerate(groups[:2]):
        is_day = g[0].is_day
        if i == 0:
            label = "TODAY" if is_day else "TONIGHT"
        else:
            label = "TOMORROW" if is_day else "TONIGHT"
        chunks.append({
            "label": label,
            "text":  _summarize(g, precip_type, is_day),
        })
    return chunks


def _feel_for_temp(temp_f: float, month: int) -> str:
    """Map a reference temperature to a casual feel word, scaled by season.

    Tuned for a Northeastern US (Vermont / New Hampshire) climate where
    winter acclimation drops the "freezing" threshold significantly —
    a Vermont local doesn't call 30°F freezing in February. Swap the
    thresholds if the deployment is in a different climate.

    Bands:
        Winter (Dec-Feb): frigid < 10, cold < 25, chilly < 38, mild < 50,
                          warm < 65, hot >= 65 (rare).
        Summer (Jun-Aug): cool < 60, comfortable < 75, warm < 85,
                          hot < 95, very hot >= 95.
        Spring/Fall: between the two extremes.
    """
    if month in (12, 1, 2):
        if temp_f < 10:  return "frigid"
        if temp_f < 25:  return "cold"
        if temp_f < 38:  return "chilly"
        if temp_f < 50:  return "mild"
        if temp_f < 65:  return "warm"
        return "hot"
    if month in (6, 7, 8):
        if temp_f < 55:  return "cool"
        if temp_f < 70:  return "comfortable"
        if temp_f < 80:  return "warm"
        if temp_f < 92:  return "hot"
        return "very hot"
    # Shoulder seasons (Mar-May, Sep-Nov)
    if temp_f < 25:  return "cold"
    if temp_f < 40:  return "chilly"
    if temp_f < 58:  return "cool"
    if temp_f < 72:  return "comfortable"
    if temp_f < 82:  return "warm"
    return "hot"


def _summarize(points, precip_type: str, is_day: bool) -> str:
    """Short natural-prose summary, e.g. 'warm and sunny',
    'muggy with scattered thunderstorms', 'cold and some light snow'.

    Combines a temperature/humidity feel word with either a sky
    descriptor (when no precip) or a precip phrase (when there is).
    Feel words are season-aware — 30°F reads as 'mild' in February
    and 'freezing' in May because acclimation shifts."""
    temps = [p.temp_f for p in points]
    humidities = [p.humidity_pct for p in points if p.humidity_pct is not None]
    avg_cloud = sum(p.cloud_pct for p in points) / len(points)
    # Combine rain (mm) and snow (cm) for the precip magnitude — same axis.
    total_precip = sum(p.precip_mm for p in points) + sum(p.snow_cm for p in points)

    # Temperature feel — use the day's high or the night's low as the
    # reference, since that's the peak felt-temperature for the period.
    # Season comes from the chunk's first timestamp.
    ref_temp = max(temps) if is_day else min(temps)
    month = points[0].timestamp.month
    feel = _feel_for_temp(ref_temp, month)

    # Override with humidity-driven feel when relevant.
    if humidities:
        avg_humidity = sum(humidities) / len(humidities)
        if avg_humidity >= 75 and ref_temp >= 68:
            feel = "muggy"
        elif avg_humidity >= 80 and ref_temp < 50:
            feel = "damp and cold"

    # Sky descriptor for no-precip case.
    if avg_cloud < 20:
        sky = "sunny" if is_day else "clear"
    elif avg_cloud < 45:
        sky = "partly cloudy"
    elif avg_cloud < 75:
        sky = "mostly cloudy"
    else:
        sky = "overcast"

    # Precip phrase when there's notable precip.
    precip_phrase = None
    if total_precip >= 0.5:
        if precip_type == "snow":
            if total_precip < 3:
                precip_phrase = "some light snow"
            elif total_precip < 10:
                precip_phrase = "snow"
            else:
                precip_phrase = "heavy snow"
        else:
            if total_precip < 3:
                precip_phrase = "some showers"
            elif total_precip < 10:
                precip_phrase = "showers"
            elif total_precip < 20:
                precip_phrase = "heavy rain"
            else:
                precip_phrase = "thunderstorms"

    if precip_phrase:
        return f"{feel} with {precip_phrase}"
    return f"{feel} and {sky}"
