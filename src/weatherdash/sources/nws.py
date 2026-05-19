"""US National Weather Service (api.weather.gov) provider.

NWS is a three-step API:

  1. GET /points/{lat},{lon}
     -> office, gridX, gridY, and three forecast URLs:
        - forecast            (12-hour day/night periods, with shortForecast)
        - forecastHourly      (hourly periods, but no quantitative precip)
        - forecastGridData    (raw time-series per property; what we want)

  2. GET /gridpoints/{office}/{x},{y}            (the "raw" gridpoint endpoint)
     -> properties.<field>.values[] where each entry is
        {"validTime": "<iso>/<iso-duration>", "value": <num | obj>}
        Used here for hourly numerics: temperature, sky cover,
        quantitative precip (mm), snowfall (m -> cm), weather codes.

  3. GET /gridpoints/{office}/{x},{y}/forecast   (the 12-hour periods endpoint)
     -> properties.periods[] each with name ("Today", "Tonight",
        "Tuesday", ...), startTime, endTime, isDaytime, shortForecast.
        Used here only for the ForecastSource role.

The /points response is cached for the lifetime of the provider — the
gridpoint coords don't move for a given lat/lon.

Implementation notes:
- The raw gridpoint endpoint emits values in variable-length intervals
  (PT1H, PT3H, PT6H, PT12H). We expand each into hourly buckets, with
  precip/snow amounts split evenly across the bucket count.
- Sunrise / sunset aren't in any NWS endpoint; we compute locally with
  astral using lat/lon + configured timezone.
- US only.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from astral import LocationInfo
from astral.sun import sun

from .base import (CurrentObservation, ForecastError, ForecastPeriod,
                   HourlyPoint, NormalizedForecast, SunInfo)

POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"
# Required by NWS: identify the caller. Replace if forking.
USER_AGENT = ("trmnl-weather-dash/0.1 "
              "(https://github.com/fisherevans/trmnl-weather-dash)")


# ── NWS weather-token -> WMO code map ────────────────────────────────────
# NWS's raw gridpoint `weather` field is a list of objects with a
# `weather` token and an `intensity`. We map (token, intensity) tuples
# to WMO codes so the icon mapper in aggregate.py works unchanged. The
# token vocabulary is documented at
# https://www.weather.gov/documentation/services-web-api (see
# `GridpointWeather.values`).
_INTENSITY_RANK = {"very_light": 0, "light": 1, "moderate": 2, "heavy": 3}

# (token, intensity) -> WMO code. Intensity falls back to "moderate" when
# the gridpoint payload omits it.
_TOKEN_TO_WMO: dict[tuple[str, str], int] = {
    # rain / drizzle (continuous)
    ("rain",                "very_light"): 51,
    ("rain",                "light"):      61,
    ("rain",                "moderate"):   63,
    ("rain",                "heavy"):      65,
    ("drizzle",             "very_light"): 51,
    ("drizzle",             "light"):      51,
    ("drizzle",             "moderate"):   53,
    ("drizzle",             "heavy"):      55,
    # showery rain
    ("rain_showers",        "very_light"): 80,
    ("rain_showers",        "light"):      80,
    ("rain_showers",        "moderate"):   81,
    ("rain_showers",        "heavy"):      82,
    # snow (continuous)
    ("snow",                "very_light"): 71,
    ("snow",                "light"):      71,
    ("snow",                "moderate"):   73,
    ("snow",                "heavy"):      75,
    # snow showers
    ("snow_showers",        "light"):      85,
    ("snow_showers",        "moderate"):   85,
    ("snow_showers",        "heavy"):      86,
    # mixed / frozen
    ("rain_snow",           "light"):      67,
    ("rain_snow",           "moderate"):   67,
    ("rain_snow",           "heavy"):      67,
    ("snow_sleet",          "light"):      66,
    ("snow_sleet",          "moderate"):   66,
    ("snow_sleet",          "heavy"):      66,
    ("rain_sleet",          "light"):      66,
    ("rain_sleet",          "moderate"):   66,
    ("rain_sleet",          "heavy"):      66,
    ("sleet",               "light"):      66,
    ("sleet",               "moderate"):   66,
    ("sleet",               "heavy"):      66,
    ("freezing_rain",       "light"):      66,
    ("freezing_rain",       "moderate"):   66,
    ("freezing_rain",       "heavy"):      66,
    ("rain_fzra",           "light"):      66,
    ("snow_fzra",           "light"):      66,
    # thunderstorms (NWS uses `thunderstorms` token)
    ("thunderstorms",       "light"):      95,
    ("thunderstorms",       "moderate"):   95,
    ("thunderstorms",       "heavy"):      96,
    # visibility / haze
    ("fog",                 "light"):      45,
    ("fog",                 "moderate"):   45,
    ("fog",                 "heavy"):      48,
    ("haze",                "light"):      45,
    ("smoke",               "light"):      45,
}


class NWSProvider:
    """Implements both WeatherSource (`fetch`) and ForecastSource (`fetch_periods`).

    A single instance can power both roles in the source-factory layer
    while sharing the /points lookup. Use as either role independently;
    each method does the minimum work it needs.
    """

    def __init__(
        self,
        *,
        timezone: str = "UTC",
        api_key: str | None = None,           # accepted for factory parity, unused
        timeout_s: float = 10.0,
        retries: int = 3,
    ) -> None:
        self._tz_name = timezone
        self._tz = ZoneInfo(timezone)
        self._timeout_s = timeout_s
        self._retries = retries
        self._headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
        # Cached /points response, keyed by (lat, lon) so the same provider
        # instance can serve multiple locations if ever needed.
        self._points_cache: dict[tuple[float, float], dict[str, Any]] = {}

    # ── WeatherSource ─────────────────────────────────────────────────────

    def fetch(self, lat: float, lon: float, hours: int) -> NormalizedForecast:
        try:
            point = self._point(lat, lon)
            grid_url = point["properties"]["forecastGridData"]
            grid = self._get_with_retry(grid_url)
            hourly = self._parse_gridpoint_hourly(grid, hours, lat, lon)
            if not hourly:
                raise ForecastError("NWS gridpoint produced zero hourly points")
            current = self._parse_current(hourly[0], grid)
            sun_info = _compute_sun(lat, lon, self._tz, datetime.now(tz=self._tz))
            return NormalizedForecast(hourly=hourly, current=current, sun=sun_info)
        except (KeyError, TypeError, ValueError) as e:
            raise ForecastError(f"NWS response did not match expected shape: {e}") from e

    # ── ForecastSource ────────────────────────────────────────────────────

    def fetch_periods(self, lat: float, lon: float) -> list[ForecastPeriod]:
        try:
            point = self._point(lat, lon)
            fc_url = point["properties"]["forecast"]
            fc = self._get_with_retry(fc_url)
            periods = fc["properties"]["periods"]
            out: list[ForecastPeriod] = []
            for p in periods:
                start = datetime.fromisoformat(p["startTime"]).astimezone(self._tz)
                end   = datetime.fromisoformat(p["endTime"]).astimezone(self._tz)
                out.append(ForecastPeriod(
                    label=p["name"],
                    start=start,
                    end=end,
                    is_day=bool(p["isDaytime"]),
                    short_forecast=p.get("shortForecast", "") or "",
                ))
            return out
        except (KeyError, TypeError, ValueError) as e:
            raise ForecastError(f"NWS periods response did not match expected shape: {e}") from e

    # ── private ───────────────────────────────────────────────────────────

    def _point(self, lat: float, lon: float) -> dict[str, Any]:
        key = (round(lat, 4), round(lon, 4))
        if key not in self._points_cache:
            self._points_cache[key] = self._get_with_retry(POINTS_URL.format(lat=lat, lon=lon))
        return self._points_cache[key]

    def _get_with_retry(self, url: str) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                with httpx.Client(timeout=self._timeout_s, headers=self._headers) as client:
                    r = client.get(url)
                if r.status_code < 500:
                    if r.status_code >= 400:
                        raise ForecastError(f"NWS {r.status_code}: {r.text[:200]}")
                    return r.json()
                last_exc = ForecastError(f"NWS {r.status_code}: {r.text[:200]}")
            except httpx.RequestError as e:
                last_exc = ForecastError(f"NWS request failed: {e}")
            if attempt < self._retries - 1:
                time.sleep(2 ** attempt)
        assert last_exc is not None
        raise last_exc

    def _parse_gridpoint_hourly(self, grid: dict[str, Any], hours: int,
                                lat: float, lon: float) -> list[HourlyPoint]:
        """Expand the raw gridpoint time-series into hourly buckets.

        Each property in the response has `values: [{validTime, value}]`
        where validTime is "<ISO start>/PT<n>H" (a step duration). We
        produce one HourlyPoint per hour starting at `now` (clamped to
        the first available timestamp in the series), capped at `hours`.
        """
        props = grid["properties"]
        temp_uom    = (props.get("temperature") or {}).get("uom", "wmoUnit:degC")
        precip_uom  = (props.get("quantitativePrecipitation") or {}).get("uom", "wmoUnit:mm")
        snow_uom    = (props.get("snowfallAmount") or {}).get("uom", "wmoUnit:m")
        # All series expand into the same hour grid; we lookup by hour key.
        temp_c   = _expand_to_hours(props.get("temperature"))
        sky      = _expand_to_hours(props.get("skyCover"))
        humidity = _expand_to_hours(props.get("relativeHumidity"))
        # Precip and snow amounts are *totals over the validTime period*
        # in the source. _expand_amount_to_hours divides them evenly so
        # 6mm over PT6H -> 1mm per hour.
        rain_mm = _expand_amount_to_hours(props.get("quantitativePrecipitation"),
                                          uom=precip_uom, to="mm")
        snow_cm = _expand_amount_to_hours(props.get("snowfallAmount"),
                                          uom=snow_uom,   to="cm")
        # `weather` is the categorical hazard list per validTime.
        wx_codes = _expand_weather_to_hours(props.get("weather"))
        # Probability of any precip and wind series.
        precip_prob = _expand_to_hours(props.get("probabilityOfPrecipitation"))
        wind_spd_kmh  = _expand_to_hours(props.get("windSpeed"))
        wind_gust_kmh = _expand_to_hours(props.get("windGust"))
        wind_dir_deg  = _expand_to_hours(props.get("windDirection"))
        uv_idx        = _expand_to_hours(props.get("uvIndex"))

        if not temp_c:
            return []

        # Start at the first hour at or after "now" so the chart shows
        # the forecast going forward, not stale past hours.
        now = datetime.now(tz=self._tz).replace(minute=0, second=0, microsecond=0)
        all_hours = sorted(temp_c.keys())
        start = max(all_hours[0], now)
        # Precompute per-date sunrise/sunset so the per-hour is_day flag
        # respects actual daylight in the configured location (winter
        # sunsets at 4:30PM should read as night, not day).
        is_day_lut = _build_is_day_lut(lat, lon, self._tz, start, hours)
        out: list[HourlyPoint] = []
        for i in range(hours):
            t = start + timedelta(hours=i)
            if t not in temp_c:
                break
            tc = temp_c[t]
            tf = _c_to_f(tc) if "degC" in temp_uom else float(tc)
            cloud = int(round(sky.get(t, 0)))
            hum   = humidity.get(t)
            code  = wx_codes.get(t, 0)
            t_local = t.astimezone(self._tz)
            spd_kmh = wind_spd_kmh.get(t, 0.0)
            gust_kmh = wind_gust_kmh.get(t, spd_kmh)
            wdir_deg = wind_dir_deg.get(t)
            uv_val = uv_idx.get(t)
            out.append(HourlyPoint(
                timestamp=t_local,
                temp_f=tf,
                precip_mm=float(rain_mm.get(t, 0.0)),
                cloud_pct=max(0, min(100, cloud)),
                weather_code=code if code is not None else 0,
                is_day=is_day_lut(t_local),
                humidity_pct=int(round(hum)) if hum is not None else None,
                snow_cm=float(snow_cm.get(t, 0.0)),
                precip_prob_pct=int(round(precip_prob.get(t, 0))),
                wind_mph=_kmh_to_mph(spd_kmh),
                wind_gust_mph=_kmh_to_mph(gust_kmh),
                wind_dir=_deg_to_cardinal(wdir_deg) if wdir_deg is not None else "",
                uv_index=int(round(uv_val)) if uv_val is not None else None,
            ))
        return out

    def _parse_current(self, first: HourlyPoint, grid: dict[str, Any]) -> CurrentObservation:
        """Synthesize a current observation from the first hourly point +
        wind series at that hour. NWS doesn't expose a single 'now'
        observation on /gridpoints; the active station's latest
        observation lives at /stations/{id}/observations/latest, but
        adding that hop isn't worth it for the dashboard's purposes —
        the first forecast hour is close enough."""
        props = grid["properties"]
        wind_speed = _expand_to_hours(props.get("windSpeed"))      # km/h
        wind_gust  = _expand_to_hours(props.get("windGust"))       # km/h
        wind_dir   = _expand_to_hours(props.get("windDirection"))  # degrees
        speed_kmh = wind_speed.get(first.timestamp, 0.0)
        gust_kmh  = wind_gust.get(first.timestamp, speed_kmh)
        dir_deg   = wind_dir.get(first.timestamp, 0.0)
        return CurrentObservation(
            temp_f=first.temp_f,
            humidity_pct=first.humidity_pct or 0,
            wind_mph=_kmh_to_mph(speed_kmh),
            wind_gust_mph=_kmh_to_mph(gust_kmh),
            wind_dir=_deg_to_cardinal(dir_deg),
            weather_code=first.weather_code,
            is_day=first.is_day,
        )


# ── helpers ──────────────────────────────────────────────────────────────


_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def _parse_iso_duration(s: str) -> timedelta:
    """Parse the limited subset of ISO 8601 durations NWS uses.

    Observed forms: PT1H, PT3H, PT6H, PT12H, P1DT6H, etc. Anything more
    exotic (years, months, seconds) raises; we'd rather know than
    silently truncate."""
    m = _DURATION_RE.match(s)
    if not m or s in ("P", "PT"):
        raise ValueError(f"unsupported ISO duration: {s}")
    d = int(m.group(1) or 0)
    h = int(m.group(2) or 0)
    mn = int(m.group(3) or 0)
    if d == 0 and h == 0 and mn == 0:
        raise ValueError(f"unsupported ISO duration: {s}")
    return timedelta(days=d, hours=h, minutes=mn)


def _parse_valid_time(s: str) -> tuple[datetime, timedelta]:
    """'<ISO start>/<ISO duration>' -> (datetime, timedelta)."""
    iso_start, iso_dur = s.split("/", 1)
    return datetime.fromisoformat(iso_start), _parse_iso_duration(iso_dur)


def _expand_to_hours(prop: dict[str, Any] | None) -> dict[datetime, float]:
    """Expand a value-typed gridpoint property to per-hour values.

    Used for instantaneous quantities (temperature, sky %, wind speed)
    where the value repeats across the validTime span — every hour in
    [start, start+duration) gets the same value.
    """
    if not prop:
        return {}
    out: dict[datetime, float] = {}
    for entry in prop.get("values", []):
        start, dur = _parse_valid_time(entry["validTime"])
        n = max(1, int(dur.total_seconds() // 3600))
        for i in range(n):
            out[start + timedelta(hours=i)] = entry["value"]
    return out


def _expand_amount_to_hours(prop: dict[str, Any] | None, *, uom: str, to: str) -> dict[datetime, float]:
    """Expand a TOTAL-over-period property (precip, snowfall) by dividing
    evenly across hourly buckets.

    `uom` is the gridpoint's reported unit; `to` is what the dashboard
    wants ('mm' for rain, 'cm' for snow).
    """
    if not prop:
        return {}
    out: dict[datetime, float] = {}
    for entry in prop.get("values", []):
        start, dur = _parse_valid_time(entry["validTime"])
        n = max(1, int(dur.total_seconds() // 3600))
        v = entry["value"]
        if v is None:
            continue
        v = _convert_amount(float(v), uom, to)
        per_hour = v / n
        for i in range(n):
            out[start + timedelta(hours=i)] = per_hour
    return out


def _expand_weather_to_hours(prop: dict[str, Any] | None) -> dict[datetime, int]:
    """Expand the categorical `weather` series to per-hour WMO codes.

    Each value is a list of weather objects (a mixed-precip period can
    have rain + snow simultaneously). We pick the strongest hazard via
    intensity rank, with a stable preference for snow over rain when
    ranks tie, so a mixed `[rain light, snow light]` reads as snow."""
    if not prop:
        return {}
    out: dict[datetime, int] = {}
    for entry in prop.get("values", []):
        start, dur = _parse_valid_time(entry["validTime"])
        n = max(1, int(dur.total_seconds() // 3600))
        code = _pick_weather_code(entry.get("value") or [])
        for i in range(n):
            out[start + timedelta(hours=i)] = code
    return out


def _pick_weather_code(values: list[dict[str, Any]]) -> int:
    """Pick a single WMO code from a list of NWS weather objects."""
    best_code = 0
    best_rank = -1
    snow_bonus = {"snow", "snow_showers", "snow_sleet", "rain_snow"}
    for v in values:
        token = (v.get("weather") or "").lower()
        intensity = (v.get("intensity") or "moderate").lower()
        rank = _INTENSITY_RANK.get(intensity, 2)
        # Tiebreaker: prefer snow over rain at equal rank so mixed precip
        # surfaces the snow icon (mixed-precip-mostly-snow reads better
        # than mostly-rain on the dashboard).
        if token in snow_bonus:
            rank += 1
        if rank > best_rank:
            code = _TOKEN_TO_WMO.get((token, intensity))
            if code is None:
                # Try with the default intensity if the exact pair isn't mapped.
                code = _TOKEN_TO_WMO.get((token, "moderate"))
            if code is not None:
                best_code = code
                best_rank = rank
    return best_code


def _convert_amount(v: float, uom: str, to: str) -> float:
    """Convert a numeric amount between common UCUM/WMO units."""
    uom = uom.split(":")[-1].lower()
    to = to.lower()
    if to == "mm":
        if uom in ("mm", "millimeter"):
            return v
        if uom in ("m", "meter"):
            return v * 1000.0
        if uom in ("cm", "centimeter"):
            return v * 10.0
    if to == "cm":
        if uom in ("cm", "centimeter"):
            return v
        if uom in ("m", "meter"):
            return v * 100.0
        if uom in ("mm", "millimeter"):
            return v / 10.0
    return v


def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _kmh_to_mph(kmh: float) -> float:
    return kmh * 0.621371


_CARDINALS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def _deg_to_cardinal(deg: float) -> str:
    idx = round((deg or 0) / 22.5) % 16
    return _CARDINALS[idx]


def _build_is_day_lut(lat: float, lon: float, tz: ZoneInfo,
                      start: datetime, hours: int):
    """Build a `(datetime) -> bool` closure for the date range covered
    by the forecast window. Precomputes sunrise/sunset for each unique
    local date once, so per-hour lookups are O(1)."""
    loc = LocationInfo(name="", region="", timezone=str(tz), latitude=lat, longitude=lon)
    by_date: dict[Any, tuple[datetime, datetime]] = {}
    end_t = start + timedelta(hours=hours)
    cur = start.astimezone(tz).date()
    end_d = end_t.astimezone(tz).date()
    while cur <= end_d:
        s = sun(loc.observer, date=cur, tzinfo=tz)
        by_date[cur] = (s["sunrise"], s["sunset"])
        cur += timedelta(days=1)

    def is_day(t: datetime) -> bool:
        t_local = t.astimezone(tz)
        sr, ss = by_date.get(t_local.date(), (None, None))
        if sr is None:
            return 6 <= t_local.hour < 20
        return sr <= t_local < ss
    return is_day


def _compute_sun(lat: float, lon: float, tz: ZoneInfo, now: datetime) -> SunInfo:
    """Compute the next sunrise + next sunset >= `now` using astral."""
    loc = LocationInfo(name="", region="", timezone=str(tz), latitude=lat, longitude=lon)
    sunrises: list[datetime] = []
    sunsets:  list[datetime] = []
    for delta in range(0, 3):
        d = (now + timedelta(days=delta)).date()
        s = sun(loc.observer, date=d, tzinfo=tz)
        sunrises.append(s["sunrise"])
        sunsets.append(s["sunset"])
    return SunInfo(
        sunrise=_first_future(sunrises, now),
        sunset =_first_future(sunsets,  now),
    )


def _first_future(events: list[datetime], now: datetime) -> datetime:
    for e in events:
        if e >= now:
            return e
    return events[-1]
