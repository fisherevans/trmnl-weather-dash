# CLAUDE.md

Context for a future Claude session working on this repo. The user (Fisher)
is a senior software engineer; treat him as a peer. Be terse, push back when
you disagree, skip filler.

## What this is

A weather-dashboard renderer that produces a 1872×1404, 16-grey PNG for the
TRMNL X 10.3" e-ink panel (4-bit mode). Pipeline: JSON data → Jinja2 template
→ headless Chromium screenshot → Pillow quantization to the 16-level palette.

The four `data*.json` files are hand-crafted scenarios covering different
times of day so the layout can be stress-tested. Live data sources are
being wired in via issues #2-#7; the integration point is
`weatherdash.aggregate.build_context` (produces a dict matching the
existing `data.json` shape) and the condition-string mapping in
`weatherdash.aggregate` (#5).

## Architecture

```
data*.json     ─┐
                ├──> render.py ──> Chromium ──> raw PNG ──> Pillow quantize ──> out-*.png
template.html  ─┘                  (playwright)
   │
   ├── <img src="makin-grey/{condition}.svg">     # current weather icon
   ├── <img src="makin-grey/{clear-day|clear-night}.svg">   # region corner icons
   ├── background-image: url("pattern-{cloud,rain}{,-night}.svg")
   └── all CSS inline in the template
```

Tile-pattern pipeline (one-time / on-edit):

```
shape-cloud-a.svg  ─┐
shape-cloud-b.svg  ─┼─> gen_patterns.py ──> pattern-{cloud,rain}{,-night}.svg
shape-drop.svg     ─┘     (jittered grid, edge-wrapping, day/night fill)
                          │
                          └── live editor: pattern_studio.py + studio.html
```

Icon pipeline (one-time per pack):

```
{pack}-raw/*.svg ──> convert_icons.py ──> {pack}-grey/*.svg ──> tighten_viewbox.py ──> {pack}-grey/*.svg
                     (strip animations,                          (browser getBBox(),
                      hue-aware greyscale)                        center via viewBox)
```

## Key design decisions

- **uv-only Python.** The renderer is a small package (`src/weatherdash/`)
  with deps declared in `pyproject.toml`. `uv run weatherdash render` builds
  + installs into an ephemeral env transparently. The one-off generators in
  `scripts/` are still single-file PEP 723 uv scripts. No global venv, no
  `requirements.txt`, no pip in CI.
- **Template inlines its CSS.** No separate stylesheet, no build step. Edits
  happen in `template.html` directly. The dashboard is *one HTML file* +
  *one Python orchestrator*; everything else is data or assets.
- **4-bit panel target drives a lot of decisions.** Pure black/white silhouettes
  are favored. Patterns avoid semi-transparency (dithers badly after
  quantization). The night-shade is a solid overlay, not opacity. SVG fills
  are hand-picked greys that all land on distinct levels of the 16-level
  palette after Pillow snaps them.
- **Pattern tiling uses edge-wrap, not symmetric mirroring.** When a placed
  shape crosses a tile edge, a duplicate is emitted at the opposite edge
  (offset by ±tile_w / ±tile_h). The corners need diagonal copies too.
  `_edge_offsets()` in `gen_patterns.py` handles all 8 cases.
- **Region icons (sun/moon at top of each day/night region) come from the
  same Makin pack** as the OUTSIDE weather icon. `clear-day` and `clear-night`
  specifically. Originally hand-rolled SVGs — they look thin next to the
  rendered Makin set, so they got replaced.
- **Hue-aware greyscale mapping.** `convert_icons.color_to_grey` treats warm
  vs cool hues differently: warm (sun/moon/lightning yellow) → flat light
  grey (so it pops on dark cloud silhouettes); cool (cloud blues) → graduated
  mid/dark greys. Pure luminance doesn't give this hierarchy.
  - **Exception:** `clear-day.svg` and `clear-night.svg` are *only* the sun
    or moon (no cloud behind). The light-gray output makes them disappear
    against the panel background — those two are manually remapped to a
    dark mid-gray (`#5F5F5F`) so they read as a prominent icon on OUTSIDE.
- **Background SVG palette: density-shifted at render time, capped at
  level ≤ 12.** The chart's `bg-{cloud,rain,snow}.svg` files ship in the
  artist's original 3-fill palette (`#666 / #999 / #BBB` = quantize levels
  6 / 9 / 11). `bg_shading.py` rewrites these fills at render time per the
  forecast intensity — bucket 4 keeps the artist values, bucket 0 lifts
  them to `#AAA / #BBB / #CCC` (levels 10 / 11 / 12). The output is
  inlined into the template as a `data:` URL.
  - The level-12 ceiling is load-bearing: day bg quantizes to level 14,
    night-shade (`#D8D8D8`) to level 13. A fill at level 13 vanishes
    against the night-shade; a fill at 14 vanishes against the day bg.
    Capping the lightest fill at 12 keeps shapes visible in both regions.
  - Each bucket must hit 3 distinct quantize levels. If two fills round
    to the same level the 3-color SVG collapses to 2 colors and shapes
    that should be distinguishable merge.
  - The night-shade reuses the same shaded SVG (same `--row-bg` CSS
    variable) so the pattern continues unchanged across the day/night
    boundary; only the background-color tints the region.

## Things to know before changing anything

- **Don't add a build step.** The user explicitly chose a uv-script setup.
  Adding webpack/vite/etc. is an instant no.
- **Don't use semi-transparent fills.** They get dithered by the 4-bit
  quantizer and look terrible on the panel. If you need a darker effect on
  a patterned area, layer a solid overlay element (see `.night-shade` in
  `template.html`).
- **Bars in the night region need z-index > night-shade.** This used to be a
  bug — `.chart-row` had its own `z-index` which trapped `.bar-col`'s. The
  current setup (no z-index on `.chart-row`, explicit z-index on `.bar-col`
  and `.night-shade`) is intentional. Don't restore the `z-index` on
  `.chart-row`.
- **`card-body` for the chart uses flex column, not grid.** Earlier it used
  grid with `grid-template-rows` only; with no explicit columns, the
  implicit column track sized to the widest item's *intrinsic* width (i.e.
  `chart-foot`'s text), which left a panel-coloured gap on the right side
  of every row. Flex column stretches every child to 100% cross-axis. Don't
  switch this back.
- **`outside-body .weather-icon` has a hard `max-height: 240px`.** Without
  it, Makin icons with portrait aspect ratios push OUTSIDE taller than its
  `1fr` allocation, kicking the INSIDE compact card off the page. Don't
  remove the cap.
- **Time-axis alternates hour ↔ temp per the user's spec.** Even index → hour
  (`11A`, light-weight uppercased), odd index → temp (`67°`, bold). Don't
  show both per slot — the user explicitly asked for alternation. The hour
  for an odd-index bar is meant to be inferred from neighbours.

## Repo layout

```
src/weatherdash/         # the installable package
├── cli.py               # entrypoint (subcommands: render, setup, ...)
├── render.py            # template -> Chromium -> 4-bit PNG pipeline
└── assets/              # template.html, bg-*.svg, makin-grey/ (ship with package)
scripts/                 # one-off generators (PEP 723 single-file uv scripts)
├── gen_patterns.py
├── pattern_studio.py
├── convert_icons.py
├── tighten_viewbox.py
└── convert_bg.py
data*.json               # dev fixtures (replaced by live data sources later)
pyproject.toml           # hatchling-built package; `weatherdash` console script
```

## Live data integration

End-to-end shape:
1. `weatherdash.config` loads a YAML config (location, weather provider,
   forecast provider, HA sensors).
2. `weatherdash.sources.factory` builds the configured `WeatherSource`
   and (optionally) `ForecastSource`. Sources sit behind protocols
   defined in `sources/base.py`; see "Source plugin shape" below.
3. `weatherdash.sources.homeassistant` fetches HA sensor states.
4. `weatherdash.aggregate.build_context` merges them into the dict
   `render_to_png(data, ...)` already expects (same shape as `data.json`).
5. `weatherdash.serve` runs a scheduler + HTTP server in one process.

Condition-code mapping: every provider normalizes to WMO codes (Open-
Meteo's native space). `aggregate.WMO_ICON_MAP` maps WMO + is_day to
`makin-grey/` filenames. All 58 stems documented in `README.md`.
Sunset/sunrise `hour_index` is computed relative to the chart's start
hour; negative or `>= n_hours` hides the marker.

### Source plugin shape

Two independent roles, two protocols in `sources/base.py`:

- `WeatherSource.fetch(lat, lon, hours) -> NormalizedForecast` — hourly
  numerics + current obs + sun events. Drives the chart. Implementations:
  `OpenMeteoProvider`, `NWSProvider`.
- `ForecastSource.fetch_periods(lat, lon) -> list[ForecastPeriod]` —
  human-written prose for 12-hour day/night blocks. Optional; when
  absent, `aggregate._summarize` derives equivalent strings from hourly
  numerics. Implementations: `NWSProvider` (via `/gridpoints/.../forecast`).

`NWSProvider` implements both. `factory.make_forecast_source` reuses
the same instance when both roles are NWS so the `/points` lookup is
shared rather than re-fetched.

`weather.provider` (yaml) picks the hourly source; `weather.forecast_provider`
picks the prose source (`derive` = use `_summarize`, `nws` = use NWS
shortForecast strings). The two are orthogonal — Open-Meteo hourly +
NWS prose is a valid combo, for example.

## Commands

```bash
# Render the dashboard from a data.json fixture
uv run weatherdash render                                 # data.json → output.png
uv run weatherdash render --data data-morning.json --out morning.png
uv run weatherdash render --no-quantize                   # skip the 4-bit snap
uv run weatherdash setup                                  # one-time: install chromium

# One-off generators (still single-file uv scripts in scripts/)
uv run scripts/gen_patterns.py
uv run scripts/pattern_studio.py                          # http://localhost:5055/

# Process an icon pack (when adding new conditions)
uv run scripts/convert_icons.py <raw_dir> <out_dir>
uv run scripts/tighten_viewbox.py <dir>

# Stress-test layout across times of day
for f in data data-morning data-evening data-latenight; do
  uv run weatherdash render --data $f.json --out out-$f.png --no-quantize
done
```

## Repository style

- Comments only for the *why*, not the what. Don't narrate.
- Inline CSS in `template.html` is intentional — don't split into a file.
- Tiny single-file scripts are preferred over packages.
- Test by rendering at multiple times of day (the four `data*.json`).
  Visual diff is the only kind of test the user cares about.
- The user reads PRs by looking at the rendered PNG. Show your work that way.
