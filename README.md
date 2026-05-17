# weatherdash

A monochrome weather dashboard rendered to a static PNG for the
[TRMNL X](https://shop.trmnl.com/products/trmnl-x) 10.3" e-ink panel
(native **1872×1404, 16-level grayscale**).

Loads weather data from a JSON file, renders an HTML template with Jinja2,
screenshots it via headless Chromium at the panel's native resolution, and
snaps every pixel to the 16-grey palette the panel actually displays in
4-bit mode.

![sample render](out-data.png)

## Layout

Left column (narrow, 460px):

- **OUTSIDE** — big current temperature & humidity, current-condition weather
  icon (Makin pack converted to greyscale), wind line.
- **TEMPERATURE FORECAST** — day's high / low with arrow + time-of-day.
- **INSIDE** — secondary compact reading.

Right column (chart):

- **PRECIPITATION & CLOUD COVER FORECAST** — back-to-back bar chart over the
  next 18 hours. Precipitation bars grow up from the shared time axis; cloud
  cover bars grow down. SVG background patterns (rain drops above, clouds
  below) carry the chart's identity. The night portion of the chart is
  shaded a darker grey and uses a parallel night-tinted pattern so the
  texture continues. SUNSET / SUNRISE marker pills sit at their exact times;
  Makin sun/moon icons mark the top-left of each day/night region.
- The time axis alternates *hour* (`11A`, `1P`, ...) and *temperature* (`67°`)
  labels so it carries both signals.

## Quick start

```bash
# One-time: download the bundled chromium playwright uses for screenshots
uv run render.py --setup

# Render the default scenario
uv run render.py                   # -> output.png

# Render a different time-of-day scenario
uv run render.py --data data-morning.json --out morning.png

# Skip the 4-bit quantization (useful while iterating)
uv run render.py --no-quantize
```

The output is exactly **1872×1404, grayscale ("L"), 16 unique grey levels
evenly spaced 0–255** — what the panel wants.

## Files

| Path | What it is |
| --- | --- |
| `render.py` | Main entry. Single-file `uv run` script (PEP 723 inline deps). Loads JSON, renders Jinja2 template, screenshots via playwright, quantizes to 16-level greyscale. |
| `template.html` | The Jinja2 dashboard layout. Inline CSS — designed to be edited directly. Loads `Inter` + `Playfair Display` from Google Fonts. |
| `data.json` | Sample weather payload. `data-morning.json`, `data-evening.json`, `data-latenight.json` are alternate time-of-day scenarios used to stress-test the layout. |
| `gen_patterns.py` | Generates `pattern-{cloud,rain}{,-night}.svg` from the artist-supplied `shape-cloud-a.svg`, `shape-cloud-b.svg`, `shape-drop.svg`. Jittered grid placement with horizontal-flip variation (clouds) or angle variation (rain), plus edge-wrapping so tiles seam together. |
| `pattern_studio.py` + `studio.html` | Local Flask app for live-tweaking the pattern parameters. `uv run pattern_studio.py` → http://localhost:5055/. Drag knobs, see the tile preview update, click *Save to disk* to write the four pattern SVGs. |
| `convert_icons.py` | Hue-aware greyscale converter for source weather-icon packs. Strips animations/filters, remaps fills via warm/cool detection (so a yellow sun → light grey, blue clouds → dark greys). |
| `tighten_viewbox.py` | Uses playwright's `getBBox()` to crop each icon's viewBox to its actual drawn content — auto-centers any icon whose artist placed it in a corner of a larger canvas. |
| `makin-raw/`, `makin-grey/` | Source + processed icons from [Makin-Things/weather-icons](https://github.com/Makin-Things/weather-icons) (amCharts-derived, MIT). All 58 condition icons (`clear-day`, `cloudy-2-night`, `rainy-3`, `snow-and-sleet-mix`, `severe-thunderstorm`, `hurricane`, etc.). |
| `meteocons-raw/`, `meteocons-grey/` | Same pipeline applied to [basmilius/weather-icons](https://github.com/basmilius/weather-icons) (Meteocons, MIT). Kept for reference / future swap. |
| `shape-cloud-a.svg`, `shape-cloud-b.svg`, `shape-drop.svg` | The artist-supplied silhouettes that the pattern generator tiles. |

## Editing the patterns

Live UI:

```bash
uv run pattern_studio.py
# http://localhost:5055/
```

Every keyword arg on `build_cloud_svg` / `build_rain_svg` shows up as a
knob. The preview tiles the SVG so you can see how seams behave; toggle
*DAY* / *NIGHT* on each panel to see the night-fill variant. *Save to disk*
writes all four files at once.

Or batch from the command line:

```bash
uv run gen_patterns.py
```

That uses whatever the function defaults are and regenerates the four
pattern files.

## Adding a weather condition

1. The dashboard reads `outside.condition` from the data and resolves it as
   `<img src="makin-grey/{condition}.svg">`.
2. If you want a condition that isn't already in `makin-grey/`, drop the
   source SVG into `makin-raw/` and run:
   ```bash
   uv run convert_icons.py makin-raw makin-grey
   uv run tighten_viewbox.py makin-grey
   ```
3. Set the new condition string in your data JSON.

The full set already cached:
`clear-day`, `clear-night`, `cloudy`, `cloudy-1/2/3-day`, `cloudy-1/2/3-night`,
`dust`, `fog`, `fog-day`, `fog-night`, `frost`, `frost-day`, `frost-night`,
`hail`, `haze`, `haze-day`, `haze-night`, `hurricane`,
`isolated-thunderstorms`, `isolated-thunderstorms-day`,
`isolated-thunderstorms-night`, `partly-cloudy-day`, `partly-cloudy-night`,
`rain`, `rain-and-sleet-mix`, `rain-and-snow-mix`, `rainy-1`, `rainy-1-day`,
`rainy-1-night`, `rainy-2`, `rainy-2-day`, `rainy-2-night`, `rainy-3`,
`rainy-3-day`, `rainy-3-night`, `scattered-thunderstorms`,
`scattered-thunderstorms-day`, `scattered-thunderstorms-night`,
`severe-thunderstorm`, `snow`, `snow-and-sleet-mix`, `snowy-1`,
`snowy-1-day`, `snowy-1-night`, `snowy-2`, `snowy-2-day`, `snowy-2-night`,
`snowy-3`, `snowy-3-day`, `snowy-3-night`, `thunder`, `thunderstorms`,
`tornado`, `tropical-storm`, `wind`.

## Data format

See `data.json` for the canonical shape. Notable fields:

- `outside.condition` — filename stem under `makin-grey/`.
- `outside.wind.{speed_mph, gust_mph, direction}` — small wind line.
- `precip_type` — `"rain"` or `"snow"`; picks the precip-row background pattern.
- `sun.{sunset,sunrise}.{time,hour_index}` — `time` is the display string,
  `hour_index` is the *fractional* offset (e.g. `9.267` for 8:16 PM on a
  chart that starts at 11 AM). Markers outside `[0, n_hours]` are hidden.
- `hourly[]` — 18 entries with `hour`, `precip_mm`, `cloud_pct`, `is_night`,
  `temp_f`. Contiguous runs of matching `is_night` become day/night regions
  that each get their own corner icon + night-shade.

## Licenses

- Code: yours.
- Weather icons in `makin-raw/`: MIT (Makin-Things/weather-icons).
- Weather icons in `meteocons-raw/`: MIT (basmilius/weather-icons).
