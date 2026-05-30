"""Bundled webfonts inlined as `data:` URLs.

`font_css()` returns an `@font-face` CSS block ready to inject into a
rendered page's `<head>`. The renderer (`engine.render.html_to_image`)
injects this before chromium loads the page, so the rendered HTML
never needs to reach the Google Fonts CDN. Templates can still
reference the family names (`'Inter'`, `'Playfair Display'`) the same
way they always did - the inlined `@font-face` rules satisfy them.

Three woff2 files cover everything we use:
- `Inter-latin.woff2` is a variable font that serves weights 400-900
  from a single file. All weights point to the same data URL with a
  `font-weight` range.
- `PlayfairDisplay-latin.woff2` is the variable upright Playfair Display
  serving weights 400-900.
- `PlayfairDisplay-latin-italic.woff2` is the variable italic, weights
  400-700.

Total bundle: ~125 KB on disk; base64-encoded the inlined CSS is ~170 KB.
Tiny next to a render pipeline that's screenshotting a 1872x1404 PNG,
but big enough that we cache the encoded CSS module-level.
"""
from __future__ import annotations

import base64
from functools import cache
from pathlib import Path


_FONTS_DIR = Path(__file__).parent / "fonts"


@cache
def font_css() -> str:
    inter = _data_url("Inter-latin.woff2")
    playfair = _data_url("PlayfairDisplay-latin.woff2")
    playfair_italic = _data_url("PlayfairDisplay-latin-italic.woff2")
    # font-display: block waits briefly for the font before showing
    # fallback - safe with data: URLs because the font is "loaded" the
    # moment the CSS parses. Keeps any chance of a FOUT off the rendered
    # screenshot.
    return f"""
@font-face {{
  font-family: 'Inter';
  font-style: normal;
  font-weight: 400 900;
  font-display: block;
  src: url({inter}) format('woff2');
}}
@font-face {{
  font-family: 'Playfair Display';
  font-style: normal;
  font-weight: 400 900;
  font-display: block;
  src: url({playfair}) format('woff2');
}}
@font-face {{
  font-family: 'Playfair Display';
  font-style: italic;
  font-weight: 400 700;
  font-display: block;
  src: url({playfair_italic}) format('woff2');
}}
""".strip()


def _data_url(filename: str) -> str:
    path = _FONTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"bundled font missing: {path}. The wheel should include the "
            f"engine/fonts/ directory; re-check pyproject force-include."
        )
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:font/woff2;base64,{encoded}"


__all__ = ["font_css"]
