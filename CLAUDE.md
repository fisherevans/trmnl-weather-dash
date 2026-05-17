# CLAUDE.md

Context for a future Claude session working on this repo. The user (Fisher)
is a senior software engineer; treat him as a peer. Be terse, push back when
you disagree, skip filler.

## What this is

A weather-dashboard renderer that produces a 1872×1404, 16-grey PNG for the
TRMNL X 10.3" e-ink panel (4-bit mode). Pipeline: JSON data → Jinja2 template
→ headless Chromium screenshot → Pillow quantization to the 16-level palette.

Not a service. There's no live data source yet — the four `data*.json` files
are hand-crafted scenarios covering different times of day so the layout can
be stress-tested. When live data is wired in, the integration point is
`render.py` (load function) and the `outside.condition` string mapping.

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

- **Single-file uv scripts.** `render.py`, `gen_patterns.py`, `convert_icons.py`,
  `tighten_viewbox.py`, `pattern_studio.py` each declare their own deps via
  PEP 723 (`# /// script ... # ///`). No global venv, no `requirements.txt`,
  no pip in CI. `uv run render.py` just works.
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

## Live data integration (when you get there)

1. Replace `json.loads(...)` in `render.py` with whatever fetches your data.
   Adapt to the shape in `data.json`.
2. Map the API's condition codes to `makin-grey/` filenames. All 58 icon
   stems are documented in `README.md`. Write a small mapper:
   `(api_code, intensity, is_night) → "rainy-2-night"`.
3. Compute `sun.{sunset,sunrise}.hour_index` from absolute event timestamps
   relative to the chart's start hour (negative or `> n_hours` → marker is
   hidden, which is the intended behaviour).
4. Per-hour `temp_f` populates the alternating-temperature axis labels.

## Commands

```bash
# Render the dashboard
uv run render.py                              # data.json → output.png
uv run render.py --data data-morning.json --out morning.png
uv run render.py --no-quantize                # skip 4-bit snap (faster iteration)
uv run render.py --setup                      # one-time: download chromium

# Regenerate background pattern tiles
uv run gen_patterns.py                        # uses function defaults
uv run pattern_studio.py                      # http://localhost:5055/ — live editor

# Process an icon pack (when adding new conditions)
uv run convert_icons.py <raw_dir> <out_dir>   # strip anim, hue-aware greyscale
uv run tighten_viewbox.py <dir>               # auto-center via getBBox

# Stress-test layout across times of day
for f in data data-morning data-evening data-latenight; do
  uv run render.py --data $f.json --out out-$f.png --no-quantize
done
```

## Repository style

- Comments only for the *why*, not the what. Don't narrate.
- Inline CSS in `template.html` is intentional — don't split into a file.
- Tiny single-file scripts are preferred over packages.
- Test by rendering at multiple times of day (the four `data*.json`).
  Visual diff is the only kind of test the user cares about.
- The user reads PRs by looking at the rendered PNG. Show your work that way.
