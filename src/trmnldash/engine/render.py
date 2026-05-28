"""Generic HTML -> Chromium screenshot -> PIL.Image.

The renderer is panel-agnostic. Panels build their own HTML (typically by
rendering a Jinja2 template with their own context) and hand it here along
with a `base_uri` so the browser can resolve their relative asset URLs.
"""
from __future__ import annotations

import subprocess
import sys
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image


def html_to_image(html: str, *, base_uri: str, width: int, height: int) -> Image.Image:
    """Render `html` in a headless Chromium viewport of `width`x`height` and
    return the screenshot as a PIL.Image (mode "RGB").

    `base_uri` is injected as `<base href>` so the page's relative asset
    paths resolve regardless of where the temp HTML lives. The tmp HTML
    is intentionally placed in the system temp dir, not next to assets,
    because in containers the assets dir is often read-only for the
    runtime user.
    """
    from playwright.sync_api import sync_playwright

    base_tag = f'<base href="{base_uri}">'
    html = html.replace("<head>", f"<head>\n  {base_tag}", 1)

    with NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
        f.write(html)
        tmp = Path(f.name)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
            )
            page = ctx.new_page()
            page.goto(tmp.as_uri())
            # Wait for webfonts to settle so the screenshot isn't a
            # flash-of-fallback-font.
            page.wait_for_load_state("networkidle")
            page.evaluate("document.fonts && document.fonts.ready")
            png_bytes = page.screenshot(full_page=False, omit_background=False)
            browser.close()
    finally:
        tmp.unlink(missing_ok=True)
    return Image.open(BytesIO(png_bytes)).convert("RGB")


def setup_browser() -> int:
    """Install bundled chromium via playwright. Called once at deploy time."""
    return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])
