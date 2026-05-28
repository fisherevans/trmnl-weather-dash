#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["flask>=3.0"]
# ///
"""Local web UI for picking the bg-shading palette interactively.

Usage:
    uv run scripts/palette_picker.py
    open http://localhost:5066/

The page renders bg-cloud.svg and bg-rain.svg at all 5 intensity
buckets in both day and night regions. Click any swatch in the 4-bit
grayscale picker, then click any fill slot in the previews — the
preview updates live (the fill swap is purely client-side, no roundtrip).

When you're happy with a palette, hit "Export" — the page emits a
Python dict snippet you can paste into INTENSITY_BUCKETS in
src/trmnldash/bg_shading.py.
"""
from __future__ import annotations

from pathlib import Path

from flask import Flask, Response, send_from_directory

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "trmnldash" / "assets"
SCRIPTS = ROOT / "scripts"

app = Flask(__name__)


@app.route("/")
def index() -> Response:
    return send_from_directory(SCRIPTS, "palette_picker.html")


@app.route("/svg/<name>")
def svg(name: str) -> Response:
    # Only serve known bg SVGs.
    if name not in {"bg-cloud.svg", "bg-rain.svg", "bg-snow.svg"}:
        return Response("not found", status=404)
    return Response((ASSETS / name).read_text(), mimetype="image/svg+xml")


if __name__ == "__main__":
    print("palette picker -> http://localhost:5066/")
    app.run(host="127.0.0.1", port=5066, debug=False)
