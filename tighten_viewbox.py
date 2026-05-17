#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.44"]
# ///
"""Tighten an SVG's viewBox to its actual drawn content.

Many weather-icon packs place the artwork in a corner of a larger canvas
(e.g. Makin-Things' 56x48 viewBox with a sun parked at translate(16,14)),
so when scaled into a square cell the icon looks small AND off-center.
This script loads each SVG in a real browser, calls `getBBox()` on the
root element to get the rendered content bounds, then rewrites the
viewBox to those bounds (plus a small margin). The artwork now fills
its viewBox AND is implicitly centered.

usage:  uv run tighten_viewbox.py <dir> [<dir> ...]
"""
from __future__ import annotations
import re
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

PAD = 0.04   # 4% of the larger dimension as a soft margin

def tighten_dir(d: Path, page) -> None:
    for svg_path in sorted(d.glob("*.svg")):
        svg = svg_path.read_text()
        # Render SVG inside a fixed canvas so getBBox returns user-space coords.
        html = f"<html><body style='margin:0;background:#fff'>{svg}</body></html>"
        page.set_content(html)
        bbox = page.evaluate(
            """() => {
                const s = document.querySelector('svg');
                const b = s.getBBox();
                return {x: b.x, y: b.y, w: b.width, h: b.height};
            }"""
        )
        if bbox["w"] <= 0 or bbox["h"] <= 0:
            print(f"  {svg_path.name}: empty bbox, skipped")
            continue
        pad = max(bbox["w"], bbox["h"]) * PAD
        x, y = bbox["x"] - pad, bbox["y"] - pad
        w, h = bbox["w"] + 2 * pad, bbox["h"] + 2 * pad
        new_vb = f"{x:.2f} {y:.2f} {w:.2f} {h:.2f}"
        if re.search(r'viewBox="[^"]*"', svg):
            svg = re.sub(r'viewBox="[^"]*"', f'viewBox="{new_vb}"', svg)
        else:
            svg = re.sub(r"<svg\b", f'<svg viewBox="{new_vb}"', svg, count=1)
        svg_path.write_text(svg)
        print(f"  {svg_path.name}: viewBox={new_vb}")


def main() -> None:
    targets = [Path(d) for d in sys.argv[1:]] or [Path("makin-grey"), Path("meteocons-grey")]
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        for d in targets:
            if not d.exists():
                print(f"skipping (missing) {d}")
                continue
            print(f"== {d} ==")
            tighten_dir(d, page)
        b.close()


if __name__ == "__main__":
    main()
