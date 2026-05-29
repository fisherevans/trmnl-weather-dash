#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "flask>=3.0",
#     "pyyaml>=6.0",
#     "jinja2>=3.1",
#     "pillow>=10.0",
#     "pydantic>=2.0",
#     "httpx>=0.27",
#     "astral>=3.2",
# ]
# ///
"""Live tuner for the weather_landscape panel's font sizes + chart hours.

Launches a local Flask server. Left side: sliders + number inputs for
every knob in TuningConfig + the data fixture picker. Right side: an
iframe rendering the actual template.html at 1872x1404 (CSS-scaled to
fit). Every change re-renders the template - sub-100ms because there's
no Chromium involved; the browser viewing this page IS the renderer.

When the tuning is dialed in, "Copy YAML" emits a snippet to paste
under `dashboard.layout.config.tuning:` in config.yaml. Only the
non-default fields are emitted.

usage:
  uv run scripts/tune_studio.py
  open http://localhost:5056/
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

import yaml

# Add the package to sys.path so this script runs without `uv pip install -e .`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trmnldash.panels.weather_landscape.config import TuningConfig          # noqa: E402
from trmnldash.panels.weather_landscape.render import (ASSETS, render_html)  # noqa: E402


PANEL_DIR = Path(__file__).resolve().parent.parent / "src" / "trmnldash" / "panels" / "weather_landscape"
ASSETS_DIR = PANEL_DIR / "assets"
DATA_FILES = sorted(ROOT.glob("data*.json"))


# ── in-memory state ────────────────────────────────────────────────────────

class State:
    def __init__(self) -> None:
        self.tuning = TuningConfig()
        self.data_file = DATA_FILES[0] if DATA_FILES else None

    def load_data(self) -> dict:
        if self.data_file is None:
            raise FileNotFoundError("no data*.json fixtures found at repo root")
        return json.loads(self.data_file.read_text())


state = State()


# ── knob metadata for the UI ───────────────────────────────────────────────
#
# Each entry: (field_name, min, max, step, group). Defaults come from
# TuningConfig itself so the UI stays in lock-step with the model.

KNOBS = [
    # field,                  min,  max,  step, group
    ("chart_hours",             8,   48,    1, "Chart"),
    ("summary_layout",       None, None, None, "Chart"),    # special: dropdown
    ("col_left_width",        240,  900,    5, "Layout"),
    ("outside_weight",        0.1, 10.0, 0.05, "Layout"),
    ("forecast_weight",       0.1, 10.0, 0.05, "Layout"),
    ("inside_weight",         0.1, 10.0, 0.05, "Layout"),
    ("outside_temp_fs",        40,  400,    1, "OUTSIDE"),
    ("outside_tempsup_fs",     12,  200,    1, "OUTSIDE"),
    ("outside_trend_fs",       12,  120,    1, "OUTSIDE"),
    ("outside_hum_fs",         20,  200,    1, "OUTSIDE"),
    ("inside_temp_fs",         20,  300,    1, "INSIDE"),
    ("inside_tempsup_fs",       8,  100,    1, "INSIDE"),
    ("inside_sep_fs",          12,  120,    1, "INSIDE"),
    ("inside_hum_fs",          16,  200,    1, "INSIDE"),
    ("forecast_big_fs",        40,  300,    1, "TEMP FORECAST"),
    ("forecast_arrow_fs",      20,  200,    1, "TEMP FORECAST"),
    ("forecast_when_fs",       10,  100,    1, "TEMP FORECAST"),
    ("forecast_rh_fs",          8,   80,    1, "TEMP FORECAST"),
]


def _knob_default(field: str):
    return getattr(TuningConfig(), field)


# ── Flask app ──────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/")
def index() -> Response:
    return Response(INDEX_HTML, mimetype="text/html")


@app.route("/api/state")
def api_state():
    return jsonify({
        "tuning": state.tuning.model_dump(),
        "data_files": [f.name for f in DATA_FILES],
        "data_file": state.data_file.name if state.data_file else None,
        "defaults": TuningConfig().model_dump(),
    })


@app.route("/api/tuning", methods=["POST"])
def api_tuning():
    payload = request.get_json(force=True) or {}
    cur = state.tuning.model_dump()
    cur.update(payload)
    try:
        state.tuning = TuningConfig.model_validate(cur)
    except Exception as e:                                   # noqa: BLE001
        return jsonify({"error": str(e)}), 400
    return ("", 204)


@app.route("/api/data-source", methods=["POST"])
def api_data_source():
    name = (request.get_json(force=True) or {}).get("name")
    for f in DATA_FILES:
        if f.name == name:
            state.data_file = f
            return ("", 204)
    return jsonify({"error": f"unknown data file: {name}"}), 400


@app.route("/api/export")
def api_export():
    """Emit YAML carrying only the knobs that diverge from defaults."""
    defaults = TuningConfig().model_dump()
    cur = state.tuning.model_dump()
    delta = {k: v for k, v in cur.items() if v != defaults[k]}
    if not delta:
        return Response("tuning: {}   # all defaults\n", mimetype="text/plain")
    snippet = "tuning:\n" + yaml.safe_dump(delta, sort_keys=False, default_flow_style=False)
    # Indent everything once so it slots cleanly under `config:` in a dashboard YAML.
    indented = "\n".join("  " + line if line else line for line in snippet.splitlines())
    return Response(indented + "\n", mimetype="text/plain")


@app.route("/preview")
def preview() -> Response:
    """Render the template at native 1872x1404 with the current tuning.

    Inject a <base href> pointing at /preview/asset/ so the template's
    relative URLs (bg-*.svg, makin-grey/...) resolve through this app.
    """
    data = state.load_data()
    data["tuning"] = state.tuning.model_dump()
    html = render_html(data)
    html = html.replace("<head>", '<head>\n  <base href="/preview/asset/">', 1)
    # Add a marker comment so a "view source" makes obvious which page this is.
    html = html.replace("<title>", "<!-- tune_studio live preview -->\n  <title>", 1)
    return Response(html, mimetype="text/html")


@app.route("/preview/asset/<path:relpath>")
def preview_asset(relpath: str):
    return send_from_directory(ASSETS_DIR, relpath)


# ── studio HTML ────────────────────────────────────────────────────────────

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>trmnldash · tune studio</title>
<style>
  :root {
    --bg:    #1a1a1a;
    --panel: #262626;
    --line:  #3a3a3a;
    --ink:   #e8e8e8;
    --mute:  #8a8a8a;
    --accent:#7fbf7f;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--ink);
    height: 100vh;
    display: grid;
    grid-template-columns: 380px 1fr;
    overflow: hidden;
  }

  /* ── left: controls ────────────────────────────────────────── */
  .controls {
    overflow-y: auto;
    padding: 16px;
    border-right: 1px solid var(--line);
    background: var(--panel);
  }
  .controls h1 {
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--mute);
    margin-bottom: 12px;
  }
  .controls .row-source {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 16px;
  }
  .controls select, .controls button {
    background: var(--bg);
    color: var(--ink);
    border: 1px solid var(--line);
    border-radius: 4px;
    padding: 6px 10px;
    font-size: 13px;
    cursor: pointer;
  }
  .controls button { background: var(--accent); color: var(--bg); font-weight: 700; }
  .controls button.secondary { background: var(--bg); color: var(--ink); font-weight: 500; }
  .controls .group {
    margin-bottom: 18px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--line);
  }
  .controls .group h2 {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--mute);
    text-transform: uppercase;
    margin-bottom: 8px;
  }
  .knob {
    display: grid;
    grid-template-columns: 110px 1fr 60px;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }
  .knob label { font-size: 12px; color: var(--ink); }
  .knob input[type=range] { width: 100%; }
  .knob input[type=number], .knob select {
    width: 100%;
    background: var(--bg);
    color: var(--ink);
    border: 1px solid var(--line);
    border-radius: 3px;
    padding: 3px 6px;
    font-size: 12px;
    text-align: right;
  }
  .knob.changed label::before { content: "● "; color: var(--accent); }
  .actions { display: flex; gap: 8px; margin-bottom: 16px; }
  .actions button { flex: 1; }
  textarea.export {
    width: 100%;
    height: 200px;
    background: var(--bg);
    color: var(--ink);
    border: 1px solid var(--line);
    border-radius: 4px;
    padding: 8px;
    font-family: 'SF Mono', Monaco, monospace;
    font-size: 11px;
    resize: vertical;
  }

  /* ── right: preview ─────────────────────────────────────────── */
  .preview-wrap {
    overflow: auto;
    background: #555;
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding: 20px;
  }
  .preview-frame {
    width: 1872px;
    height: 1404px;
    border: 0;
    background: white;
    transform-origin: top left;
    box-shadow: 0 0 0 1px #888, 0 12px 40px rgba(0,0,0,0.5);
  }
  .preview-meta {
    position: fixed;
    bottom: 10px;
    right: 14px;
    font-size: 11px;
    color: rgba(255,255,255,0.7);
    background: rgba(0,0,0,0.4);
    padding: 4px 8px;
    border-radius: 3px;
    font-family: 'SF Mono', Monaco, monospace;
  }
</style>
</head>
<body>

<div class="controls">
  <h1>trmnldash · tune studio</h1>

  <div class="row-source">
    <select id="data-file"></select>
    <button class="secondary" id="reset-btn">Reset</button>
  </div>

  <div id="knobs"></div>

  <div class="actions">
    <button id="copy-btn">Copy YAML</button>
    <button class="secondary" id="refresh-btn">Refresh</button>
  </div>
  <textarea class="export" id="export-yaml" readonly></textarea>
</div>

<div class="preview-wrap">
  <iframe class="preview-frame" id="preview" src="/preview"></iframe>
</div>

<div class="preview-meta" id="meta"></div>

<script>
const KNOBS = __KNOBS_JSON__;
let state = null;

async function loadState() {
  state = await (await fetch("/api/state")).json();
  renderKnobs();
  renderDataFiles();
  updateExport();
  fitPreview();
}

function renderKnobs() {
  const groups = {};
  for (const k of KNOBS) {
    (groups[k.group] ||= []).push(k);
  }
  const root = document.getElementById("knobs");
  root.innerHTML = "";
  for (const [name, knobs] of Object.entries(groups)) {
    const wrap = document.createElement("div");
    wrap.className = "group";
    const h = document.createElement("h2");
    h.textContent = name;
    wrap.appendChild(h);
    for (const k of knobs) wrap.appendChild(buildKnob(k));
    root.appendChild(wrap);
  }
}

function buildKnob(k) {
  const row = document.createElement("div");
  row.className = "knob";
  row.dataset.field = k.field;
  const label = document.createElement("label");
  label.textContent = k.field;
  row.appendChild(label);

  if (k.field === "summary_layout") {
    const sel = document.createElement("select");
    for (const opt of ["stacked", "side-by-side"]) {
      const o = document.createElement("option");
      o.value = opt; o.textContent = opt;
      if (state.tuning[k.field] === opt) o.selected = true;
      sel.appendChild(o);
    }
    sel.addEventListener("change", () => update(k.field, sel.value));
    const empty = document.createElement("span");
    row.appendChild(sel);
    row.appendChild(empty);
    return row;
  }

  const range = document.createElement("input");
  range.type = "range";
  range.min = k.min; range.max = k.max; range.step = k.step;
  range.value = state.tuning[k.field];
  const num = document.createElement("input");
  num.type = "number";
  num.min = k.min; num.max = k.max; num.step = k.step;
  num.value = state.tuning[k.field];

  // Float vs int parsing depends on the knob's step. parseFloat handles
  // both, then we round to the step's precision for display.
  const isInt = Number.isInteger(k.step);
  const syncFrom = (src, other) => {
    let v = parseFloat(src.value);
    if (isInt) v = Math.round(v);
    other.value = v;
    update(k.field, v);
  };
  range.addEventListener("input", () => syncFrom(range, num));
  num.addEventListener("change", () => syncFrom(num, range));

  row.appendChild(range);
  row.appendChild(num);
  return row;
}

let updateTimer = null;
async function update(field, value) {
  state.tuning[field] = value;
  markChanged(field);
  clearTimeout(updateTimer);
  updateTimer = setTimeout(async () => {
    await fetch("/api/tuning", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({[field]: value}),
    });
    refreshPreview();
    updateExport();
  }, 80);   // debounce — covers fast slider drags
}

function markChanged(field) {
  const row = document.querySelector(`.knob[data-field="${field}"]`);
  if (!row) return;
  const isDefault = state.tuning[field] === state.defaults[field];
  row.classList.toggle("changed", !isDefault);
}

function renderDataFiles() {
  const sel = document.getElementById("data-file");
  sel.innerHTML = "";
  for (const name of state.data_files) {
    const o = document.createElement("option");
    o.value = name; o.textContent = name;
    if (state.data_file === name) o.selected = true;
    sel.appendChild(o);
  }
  sel.addEventListener("change", async () => {
    await fetch("/api/data-source", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name: sel.value}),
    });
    refreshPreview();
  });
}

async function updateExport() {
  const txt = await (await fetch("/api/export")).text();
  document.getElementById("export-yaml").value = txt;
}

function refreshPreview() {
  const f = document.getElementById("preview");
  // Bust the cache so an unchanged URL forces a reload.
  f.src = "/preview?ts=" + Date.now();
}

function fitPreview() {
  const wrap = document.querySelector(".preview-wrap");
  const frame = document.getElementById("preview");
  const scaleX = (wrap.clientWidth - 40) / 1872;
  const scaleY = (wrap.clientHeight - 40) / 1404;
  const scale = Math.min(scaleX, scaleY);
  frame.style.transform = `scale(${scale})`;
  // Reserve scrollable area so the scaled frame still fits its parent layout.
  frame.style.marginRight = `${1872 * (scale - 1)}px`;
  frame.style.marginBottom = `${1404 * (scale - 1)}px`;
  document.getElementById("meta").textContent =
    `1872×1404 → scale ${scale.toFixed(3)}`;
}
window.addEventListener("resize", fitPreview);

document.getElementById("refresh-btn").addEventListener("click", refreshPreview);

document.getElementById("copy-btn").addEventListener("click", async () => {
  await updateExport();
  const txt = document.getElementById("export-yaml").value;
  await navigator.clipboard.writeText(txt);
  const btn = document.getElementById("copy-btn");
  const orig = btn.textContent;
  btn.textContent = "Copied ✓";
  setTimeout(() => btn.textContent = orig, 1200);
});

document.getElementById("reset-btn").addEventListener("click", async () => {
  const defaults = state.defaults;
  for (const k of Object.keys(defaults)) {
    state.tuning[k] = defaults[k];
  }
  await fetch("/api/tuning", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(defaults),
  });
  renderKnobs();
  updateExport();
  refreshPreview();
});

loadState();
</script>
</body>
</html>
"""

# Substitute the knob list JSON into the template.
INDEX_HTML = INDEX_HTML.replace(
    "__KNOBS_JSON__",
    json.dumps([
        {"field": f, "min": mn, "max": mx, "step": st, "group": g}
        for f, mn, mx, st, g in KNOBS
    ]),
)


if __name__ == "__main__":
    print(f"tune studio: http://localhost:5056/")
    print(f"  data fixtures: {[f.name for f in DATA_FILES]}")
    print(f"  defaults: {TuningConfig().model_dump()}")
    app.run(host="127.0.0.1", port=5056, debug=False)
