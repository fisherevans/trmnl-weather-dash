#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["flask>=3.0", "shapely>=2.0", "svgpathtools>=1.6"]
# ///
"""Live-tweak the cloud & rain background patterns in the browser.

Launches a local Flask server. The UI exposes every knob in
`gen_patterns.build_cloud_svg` / `build_rain_svg`, regenerates the tile
on every input change (debounced), and previews it tiled at full size
so you can see how it actually repeats. A "Save to disk" button writes
the current params out to `pattern-cloud{,-night}.svg` /
`pattern-rain{,-night}.svg` (which `render.py` then picks up).

usage:
  uv run pattern_studio.py
  open http://localhost:5055/
"""
from __future__ import annotations
from pathlib import Path
import inspect
import json

from flask import Flask, jsonify, request, send_from_directory, Response

import gen_patterns

ROOT = Path(__file__).parent
app = Flask(__name__, static_folder=str(ROOT), static_url_path="")


# --- introspect knob defaults & types from the two builder functions ----------

def _knob_defaults(fn) -> dict:
    """Pull keyword defaults out of a build_* function so the UI's knob set
    stays in lock-step with the source of truth."""
    sig = inspect.signature(fn)
    out = {}
    for name, p in sig.parameters.items():
        if name == "fill":
            continue                            # fill is set separately (day/night)
        if p.default is inspect.Parameter.empty:
            continue
        out[name] = p.default
    return out


CLOUD_DEFAULTS = _knob_defaults(gen_patterns.build_cloud_svg)
RAIN_DEFAULTS  = _knob_defaults(gen_patterns.build_rain_svg)


# --- routes -------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(str(ROOT), "studio.html")


@app.route("/api/defaults")
def defaults():
    return jsonify({
        "cloud": CLOUD_DEFAULTS,
        "rain": RAIN_DEFAULTS,
        "day_fill": gen_patterns.DAY_FILL,
        "night_fill": gen_patterns.NIGHT_FILL,
    })


def _coerce(params: dict, defaults: dict) -> dict:
    """Coerce JSON-string params to the same types as the function defaults."""
    out = {}
    for k, default in defaults.items():
        if k not in params:
            out[k] = default
            continue
        v = params[k]
        if isinstance(default, bool):
            out[k] = bool(v)
        elif isinstance(default, int):
            out[k] = int(v)
        elif isinstance(default, float):
            out[k] = float(v)
        else:
            out[k] = v
    return out


@app.post("/api/cloud")
def gen_cloud():
    p = request.get_json(force=True) or {}
    fill = p.pop("fill", gen_patterns.DAY_FILL)
    params = _coerce(p, CLOUD_DEFAULTS)
    svg = gen_patterns.build_cloud_svg(fill=fill, **params)
    return Response(svg, mimetype="image/svg+xml")


@app.post("/api/rain")
def gen_rain():
    p = request.get_json(force=True) or {}
    fill = p.pop("fill", gen_patterns.DAY_FILL)
    params = _coerce(p, RAIN_DEFAULTS)
    svg = gen_patterns.build_rain_svg(fill=fill, **params)
    return Response(svg, mimetype="image/svg+xml")


@app.post("/api/save")
def save():
    """Write the current params out to the four pattern files."""
    body = request.get_json(force=True) or {}
    cloud_p = _coerce({k: v for k, v in body.get("cloud", {}).items() if k != "fill"}, CLOUD_DEFAULTS)
    rain_p  = _coerce({k: v for k, v in body.get("rain",  {}).items() if k != "fill"}, RAIN_DEFAULTS)

    day = body.get("day_fill",   gen_patterns.DAY_FILL)
    night = body.get("night_fill", gen_patterns.NIGHT_FILL)
    night_seed_bump = int(body.get("night_seed_bump", 4))

    (ROOT / "pattern-cloud.svg").write_text(
        gen_patterns.build_cloud_svg(fill=day, **cloud_p)
    )
    (ROOT / "pattern-cloud-night.svg").write_text(
        gen_patterns.build_cloud_svg(fill=night, **{**cloud_p, "seed": cloud_p["seed"] + night_seed_bump})
    )
    (ROOT / "pattern-rain.svg").write_text(
        gen_patterns.build_rain_svg(fill=day, **rain_p)
    )
    (ROOT / "pattern-rain-night.svg").write_text(
        gen_patterns.build_rain_svg(fill=night, **{**rain_p, "seed": rain_p["seed"] + night_seed_bump})
    )
    return jsonify({"status": "saved", "files": [
        "pattern-cloud.svg", "pattern-cloud-night.svg",
        "pattern-rain.svg", "pattern-rain-night.svg",
    ]})


if __name__ == "__main__":
    print("Pattern studio → http://localhost:5055/")
    app.run(host="127.0.0.1", port=5055, debug=False)
