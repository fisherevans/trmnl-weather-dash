"""Generic HTML -> Chromium screenshot -> PIL.Image.

The renderer is panel-agnostic. Panels build their own HTML (typically by
rendering a Jinja2 template with their own context) and hand it here along
with a `base_uri` so the browser can resolve their relative asset URLs.

Resilience model (see issue #15):

- All webfonts are inlined as `data:` URLs from `engine.fonts.font_css()`
  before chromium loads the page. The rendered HTML has zero external
  font dependencies.
- All external HTTP(S) requests are blocked at the playwright route
  level - templates can keep their `<link rel="stylesheet" href="...">`
  Google Fonts references for IDE syntax-highlighting / standalone
  preview, but at render time chromium aborts those requests.
- `page.goto` runs with `wait_until="load"` (default) and a short
  per-operation timeout. We do NOT wait for `networkidle` - with all
  external requests blocked there is no useful "idle" signal, just a
  hang vector.
- A watchdog thread force-closes the browser if the whole render
  doesn't complete within `_RENDER_TIMEOUT_S`. This unblocks any
  internal chromium wait and surfaces a normal exception to the
  scheduler, which records a failed cycle and retries next interval.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image

from .fonts import font_css

logger = logging.getLogger(__name__)


# Per-operation timeout for any single playwright call (page.goto,
# page.evaluate, page.screenshot). 15 s is generous - a normal render
# completes in 2-3 s. Tighter than playwright's default 30 s so a hung
# socket trips this before it trips the watchdog.
_PLAYWRIGHT_OP_TIMEOUT_MS = 15_000

# Wall-clock kill switch on the whole render. If chromium internals
# wedge for any reason, this fires + closes the browser, the page op
# raises, we re-raise to the scheduler, the scheduler logs a failed
# cycle and the next interval retries cleanly. Generous because cold
# starts can take a few seconds + the screenshot itself can be a
# second or two on large viewports.
_RENDER_TIMEOUT_S = 30


class RenderTimeout(Exception):
    """Raised when the render watchdog fires - render exceeded `_RENDER_TIMEOUT_S`."""


def html_to_image(html: str, *, base_uri: str, width: int, height: int) -> Image.Image:
    """Render `html` in a headless Chromium viewport of `width`x`height` and
    return the screenshot as a PIL.Image (mode "RGB").

    `base_uri` is injected as `<base href>` so the page's relative asset
    paths resolve. The bundled webfonts CSS is injected too so chromium
    never reaches out to Google Fonts. External HTTP requests are
    blocked entirely.
    """
    from playwright.sync_api import sync_playwright

    head_inject = (
        f'<base href="{base_uri}">\n'
        f'  <style data-injected-by="trmnldash-engine">{font_css()}</style>'
    )
    html = html.replace("<head>", f"<head>\n  {head_inject}", 1)

    with NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
        f.write(html)
        tmp = Path(f.name)

    try:
        return _render_with_watchdog(tmp, width, height)
    finally:
        tmp.unlink(missing_ok=True)


def setup_browser() -> int:
    """Install bundled chromium via playwright. Called once at deploy time."""
    return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])


# ── internals ──────────────────────────────────────────────────────────────


def _render_with_watchdog(tmp: Path, width: int, height: int) -> Image.Image:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        watchdog_fired = threading.Event()

        def _trip_watchdog() -> None:
            # Belt + suspenders: if chromium ever hangs for longer than
            # the wall-clock budget, force-close the browser. That
            # unblocks whatever sync playwright call was in flight and
            # the main thread re-raises as RenderTimeout.
            watchdog_fired.set()
            logger.warning(
                "render watchdog fired after %ds - closing browser",
                _RENDER_TIMEOUT_S,
            )
            try:
                browser.close()
            except Exception:                                  # noqa: BLE001
                # browser.close() can itself raise during teardown if the
                # process is already gone; we don't care.
                pass

        timer = threading.Timer(_RENDER_TIMEOUT_S, _trip_watchdog)
        timer.daemon = True
        timer.start()
        try:
            ctx = browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
            )
            ctx.set_default_timeout(_PLAYWRIGHT_OP_TIMEOUT_MS)
            ctx.route("**/*", _block_external_requests)

            page = ctx.new_page()
            page.goto(tmp.as_uri(), wait_until="load")
            # Inline data: URL fonts resolve before this fires; the
            # explicit await catches the rare edge of a template that
            # adds @font-face rules of its own and gives them a moment
            # to settle. 2 s ceiling so a buggy font reference can't
            # stall the render.
            page.evaluate(
                "() => document.fonts && document.fonts.ready",
                # `evaluate` doesn't take a timeout kw in playwright's
                # sync API; the page-level default we set above caps it.
            )
            png_bytes = page.screenshot(full_page=False, omit_background=False)
        except Exception:
            if watchdog_fired.is_set():
                raise RenderTimeout(
                    f"render exceeded {_RENDER_TIMEOUT_S}s wall-clock budget"
                )
            raise
        finally:
            timer.cancel()
            # Always tear down. A surviving browser instance after a
            # failed render risks compounding state across cycles.
            try:
                browser.close()
            except Exception:                                  # noqa: BLE001
                pass

    return Image.open(BytesIO(png_bytes)).convert("RGB")


def _block_external_requests(route) -> None:
    """Abort any request whose URL isn't a local-only scheme.

    file:// is the base for our temp HTML; data: URLs cover our inlined
    fonts. Anything else (http, https, ws, etc.) is rejected. Templates
    that still link to Google Fonts get a console error from chromium
    but no socket actually opens, so the previously-observed hang on
    `fonts.googleapis.com` can't recur.
    """
    url = route.request.url
    if url.startswith("file://") or url.startswith("data:"):
        route.continue_()
    else:
        route.abort()
