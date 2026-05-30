# Contributing

Issues and PRs welcome.

## Local setup

```bash
git clone https://github.com/fisherevans/trmnl-weather-dash
cd trmnl-weather-dash
uv run trmnldash setup        # one-time: install chromium for playwright
uv run trmnldash render       # smoke test: data.json -> output.png
```

`uv run` builds and installs the package into an ephemeral env on first
invocation. No global `pip install` needed.

## Iterating on the UI

The dashboard is a single Jinja2 template with all CSS inline at
[`src/trmnldash/panels/weather_landscape/template.html`](src/trmnldash/panels/weather_landscape/template.html).
There's no build step. Four data fixtures cover the typical times of day —
render all of them while iterating to catch layout regressions:

```bash
for f in data data-morning data-evening data-latenight; do
  uv run trmnldash render --data $f.json --out out-$f.png --no-quantize
done
```

Use `--no-quantize` during iteration. Skips the 16-grey snap, ~1s per
render off the clock. Always render *with* quantization before
declaring victory — some effects (semi-transparency, fine gradients)
look fine pre-snap and terrible after.

### Live font + layout tuning

For interactive font-size + chart-hours + summary-layout tuning, the
`tune_studio` script launches a local Flask server with a left-side
form and a right-side iframe rendering the actual template:

```bash
uv run scripts/tune_studio.py
open http://localhost:5056/
```

Every slider change re-renders the template in the iframe in
sub-100ms (no Chromium round-trip — the iframe IS the renderer). When
the tuning's dialed in, the "Copy YAML" button emits a snippet to
paste under `dashboard.layout.config.tuning:` in your config.yaml.

Knobs live in
[`panels/weather_landscape/config.py`](src/trmnldash/panels/weather_landscape/config.py)
(`TuningConfig`). The studio introspects the model so adding a knob is
one field there + one entry in the `KNOBS` list in `tune_studio.py`.

## Adding a weather provider

Implement the `WeatherSource` protocol from
[`src/trmnldash/sources/base.py`](src/trmnldash/sources/base.py) and wire
it into the dispatch clause in
[`src/trmnldash/sources/factory.py`](src/trmnldash/sources/factory.py).
The two shipped providers (`openmeteo.py`, `nws.py`) are the reference
implementations.

The hardest part is mapping the provider's native condition codes onto the
WMO codes the icon mapper expects (`WMO_ICON_MAP` in
`panels/weather_landscape/aggregate.py`).

## Adding a weather icon

The dashboard renders `outside.condition` as
`<img src="makin-grey/{condition}.svg">`. The 58 stems shipping with the
package are derived from
[Makin-Things/weather-icons](https://github.com/Makin-Things/weather-icons)
via `scripts/convert_icons.py` (hue-aware grayscale) and
`scripts/tighten_viewbox.py` (auto-center).

To add a new condition:

```bash
# 1. Drop the source SVG into scripts/makin-raw/
# 2. Run the conversion pipeline
uv run scripts/convert_icons.py scripts/makin-raw src/trmnldash/panels/weather_landscape/assets/makin-grey
uv run scripts/tighten_viewbox.py src/trmnldash/panels/weather_landscape/assets/makin-grey
# 3. Update WMO_ICON_MAP in src/trmnldash/panels/weather_landscape/aggregate.py if needed
```

## Commit hygiene

- Short title (under ~70 chars). Body explains *why*, not *what*. The
  diff covers *what*.
- One logical change per commit.
- Reference the issue number with `(closes #N)` or `(refs #N)` in the
  body so GitHub stitches things together.

## Visual regression

There's no automated visual test (visual diff for an e-ink panel doesn't
trivially boil down to a numeric assertion). The discipline is: render
all four fixtures and look at them. PRs that touch `template.html`,
`render.py`, `aggregate.py`, or any of the background SVGs should
include before/after images in the PR body.

Two helpers write a browsable index alongside the PNGs:

```bash
uv run scripts/visual_regression.py        # 21 fabricated scenarios -> out/scenarios/
uv run scripts/live_demo.py                # 10 real US cities       -> out/live/
```

Both finish by printing `file://...` to view directly. To view from
another device on the network (phone, tablet), serve the output dir
with the stdlib HTTP server - no install:

```bash
uv run python -m http.server 8000 --directory out/live
# then open http://<host>:8000/ in any browser
```

Same `out/scenarios` for the regression set. The PNGs are cache-busted
in the index via `?v=<mtime>` so a re-render under the same URL just
shows up on refresh.
