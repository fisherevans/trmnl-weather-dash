# Contributing

Issues and PRs welcome.

## Local setup

```bash
git clone https://github.com/fisherevans/trmnl-weather-dash
cd trmnl-weather-dash
uv run weatherdash setup        # one-time: install chromium for playwright
uv run weatherdash render       # smoke test: data.json -> output.png
```

`uv run` builds and installs the package into an ephemeral env on first
invocation. No global `pip install` needed.

## Iterating on the UI

The dashboard is a single Jinja2 template with all CSS inline at
[`src/weatherdash/assets/template.html`](src/weatherdash/assets/template.html).
There's no build step. Four data fixtures cover the typical times of day —
render all of them while iterating to catch layout regressions:

```bash
for f in data data-morning data-evening data-latenight; do
  uv run weatherdash render --data $f.json --out out-$f.png --no-quantize
done
```

Use `--no-quantize` during iteration. Skips the 16-grey snap, ~1s per
render off the clock. Always render *with* quantization before
declaring victory — some effects (semi-transparency, fine gradients)
look fine pre-snap and terrible after.

## Adding a weather provider

Implement the `WeatherSource` protocol from
[`src/weatherdash/sources/base.py`](src/weatherdash/sources/base.py) and
wire it into
[`src/weatherdash/sources/factory.py`](src/weatherdash/sources/factory.py).
Issues [#11](https://github.com/fisherevans/trmnl-weather-dash/issues/11) and
[#12](https://github.com/fisherevans/trmnl-weather-dash/issues/12) have full
specs for NWS and Pirate Weather.

The hardest part is mapping the provider's native condition codes onto the
WMO codes the icon mapper expects (`WMO_ICON_MAP` in `aggregate.py`).

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
uv run scripts/convert_icons.py scripts/makin-raw src/weatherdash/assets/makin-grey
uv run scripts/tighten_viewbox.py src/weatherdash/assets/makin-grey
# 3. Update WMO_ICON_MAP in src/weatherdash/aggregate.py if needed
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
