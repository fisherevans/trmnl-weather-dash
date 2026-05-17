#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Convert color weather-icon SVGs to grayscale silhouettes for e-ink.

- Strips animations (<animate*>, <set>, <script>, @keyframes blocks).
- Walks every fill/stroke/stop-color, parses the hex color, replaces it with
  a luminance-derived grey. Gradients still gradient, just in greys.
- Optionally remaps the lightest greys upward and the darker hues downward
  to give more contrast in the 4-bit panel palette.

usage:  uv run convert_icons.py <in_dir> <out_dir>
"""
from __future__ import annotations
import re, sys
from pathlib import Path

HEX3 = re.compile(r"#([0-9a-fA-F]{3})\b")
HEX6 = re.compile(r"#([0-9a-fA-F]{6})\b")
ANIM_TAGS = re.compile(r"<(animate|animateTransform|animateMotion|set|script)\b[^>]*?(/>|>.*?</\1>)", re.S)
STYLE_BLOCK = re.compile(r"<style[^>]*>.*?</style>", re.S)
CSS_KEYFRAMES = re.compile(r"@keyframes[^{]+\{(?:[^{}]+|\{[^{}]*\})*\}", re.S)
# Strip fixed width/height on the root <svg> so the icon scales with its
# container (CSS sizing wins). viewBox stays intact.
SVG_DIMS = re.compile(r'(<svg\b[^>]*?)\s+(width|height)="[^"]*"', re.I)


def luminance(r: int, g: int, b: int) -> int:
    # ITU-R BT.601 luma
    return round(0.299 * r + 0.587 * g + 0.114 * b)


def color_to_grey(r: int, g: int, b: int) -> int:
    """Hue-aware grayscale mapping designed for weather iconography.

    Warm colors (yellows, oranges, reds — sun/moon/lightning bodies) always
    land on a LIGHT grey so they read as highlights against the panel.
    Cool colors (blues — cloud bodies, rain) graduate from mid-grey down to
    near-black by luminance, giving cloud silhouettes weight against the bg.
    Near-white stays white, near-black stays black.
    """
    if r > 230 and g > 230 and b > 230:
        return 245                  # white
    if r < 30 and g < 30 and b < 30:
        return 15                   # black

    lum = luminance(r, g, b)
    is_warm = r > b + 25            # significantly more red than blue

    if is_warm:
        # Sun/moon/lightning — flat light grey so it pops on dark cloud silhouettes.
        return 200

    # Cool/neutral — graduate by luminance.
    if lum >= 230: return 220
    if lum >= 180: return 150
    if lum >= 130: return 95
    if lum >= 80:  return 65
    if lum >= 40:  return 40
    return 20


def hex_to_grey(match: re.Match) -> str:
    c = match.group(1)
    if len(c) == 3:
        r, g, b = (int(c[i] * 2, 16) for i in range(3))
    else:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    g_ = color_to_grey(r, g, b)
    return f"#{g_:02x}{g_:02x}{g_:02x}"


def convert(svg: str) -> str:
    # 1) drop animation/script tags
    svg = ANIM_TAGS.sub("", svg)
    # 2) drop any <style>...</style> blocks (animation CSS lives there)
    svg = STYLE_BLOCK.sub("", svg)
    # 3) defensive: drop any leftover @keyframes
    svg = CSS_KEYFRAMES.sub("", svg)
    # 3a) drop blur/shadow filter applications + their <filter> defs (Makin
    # uses these for soft shadows that don't survive 4-bit quantization).
    svg = re.sub(r'\s*filter\s*=\s*"[^"]*"', "", svg)
    svg = re.sub(r"<filter\b[^>]*>.*?</filter>", "", svg, flags=re.S)
    # 4) recolor — long form first so partial matches don't fire twice
    svg = HEX6.sub(hex_to_grey, svg)
    svg = HEX3.sub(hex_to_grey, svg)
    # 5) Normalize root <svg> sizing: if it has width/height but no viewBox,
    # promote them into a viewBox so CSS can scale the icon freely. Otherwise
    # just drop the explicit dims.
    m = re.search(r"<svg\b([^>]*)>", svg, re.I)
    if m:
        attrs = m.group(1)
        has_viewbox = re.search(r"\bviewBox\s*=", attrs, re.I) is not None
        w_m = re.search(r'\bwidth\s*=\s*"([\d.]+)(?:px)?"', attrs, re.I)
        h_m = re.search(r'\bheight\s*=\s*"([\d.]+)(?:px)?"', attrs, re.I)
        new_attrs = re.sub(r'\s+(?:width|height)\s*=\s*"[^"]*"', "", attrs, flags=re.I)
        if not has_viewbox and w_m and h_m:
            new_attrs += f' viewBox="0 0 {w_m.group(1)} {h_m.group(1)}"'
        svg = svg[: m.start()] + f"<svg{new_attrs}>" + svg[m.end():]
    return svg


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: convert_icons.py <in_dir> <out_dir>", file=sys.stderr)
        sys.exit(1)
    in_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(in_dir.glob("*.svg")):
        dst = out_dir / src.name
        dst.write_text(convert(src.read_text()))
        print(f"  {src.name} -> {dst}")


if __name__ == "__main__":
    main()
