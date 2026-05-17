"""Long-running service: scheduler + HTTP server in one asyncio process.

Scheduler re-renders every config.render.refresh_minutes. The HTTP
server always serves whatever the most recent render produced —
TRMNL's Image Display plugin polls this URL on its own refresh cycle
and gets conditional-GET-aware caching (ETag, Last-Modified).

Atomic writes: each render writes to a `.tmp.png` sibling then
os.replace()s onto output_path. HTTP clients never see a partial file.

Render failures don't crash the process — the previous image stays in
place, the failure shows up in /healthz, and the next interval tries
again.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import signal
from datetime import datetime, timezone
from email.utils import formatdate
from pathlib import Path

from aiohttp import web

from .config import Config
from .pipeline import RenderStats, run_once

logger = logging.getLogger(__name__)


class WeatherServer:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.output_path: Path = config.render.output_path

        # Per-run state for /healthz.
        self.last_render_at: datetime | None = None
        self.last_render_status: str = "pending"     # "pending" | "ok" | "error"
        self.last_render_error: str | None = None
        self.consecutive_failures: int = 0
        self.total_renders: int = 0
        self.total_failures: int = 0

        # Cached fingerprint of output_path, invalidated on each render.
        self._etag: str | None = None
        self._last_modified: str = ""
        self._size: int = 0
        self._etag_mtime: float = 0.0

        self._shutdown = asyncio.Event()

    # ── public API ─────────────────────────────────────────────────────────

    async def run(self) -> None:
        scheduler_task = asyncio.create_task(self._scheduler_loop(), name="scheduler")
        try:
            await self._serve_until_shutdown()
        finally:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass

    def request_shutdown(self) -> None:
        self._shutdown.set()

    # ── scheduler ──────────────────────────────────────────────────────────

    async def _scheduler_loop(self) -> None:
        """Render once immediately, then every refresh_minutes until shutdown."""
        interval_s = self.config.render.refresh_minutes * 60
        while not self._shutdown.is_set():
            await self._render_in_executor()
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=interval_s)
                return                                  # shutdown during sleep
            except asyncio.TimeoutError:
                pass                                    # interval elapsed, next cycle

    async def _render_in_executor(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            stats = await loop.run_in_executor(None, self._do_render_sync)
        except Exception as e:                          # noqa: BLE001 - want to log + continue
            logger.exception("render failed")
            self.last_render_status = "error"
            self.last_render_error = f"{type(e).__name__}: {e}"
            self.consecutive_failures += 1
            self.total_failures += 1
            return
        self.last_render_at = datetime.now(tz=timezone.utc)
        self.last_render_status = "ok"
        self.last_render_error = None
        self.consecutive_failures = 0
        self.total_renders += 1
        # Invalidate etag cache so the next request recomputes it.
        self._etag = None
        logger.info(
            "rendered %s in %dms (weather=%dms ha=%dms render=%dms)",
            self.output_path, stats.total_ms, stats.weather_ms,
            stats.ha_ms, stats.render_ms,
        )

    def _do_render_sync(self) -> RenderStats:
        """Runs in a thread (playwright sync_api is blocking). Writes atomically."""
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.output_path.with_suffix(".tmp.png")
        stats = run_once(self.config, tmp, quantize=True)
        os.replace(tmp, self.output_path)
        stats.output_path = self.output_path
        return stats

    # ── HTTP server ────────────────────────────────────────────────────────

    async def _serve_until_shutdown(self) -> None:
        app = web.Application()
        secret = self.config.serve.secret_path
        png_route = f"/{secret}/dashboard.png" if secret else "/dashboard.png"
        app.router.add_route("GET", png_route, self.handle_dashboard)
        app.router.add_route("HEAD", png_route, self.handle_dashboard)
        app.router.add_get("/healthz", self.handle_healthz)

        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, self.config.serve.host, self.config.serve.port)
        await site.start()
        logger.info("serving dashboard at http://%s:%d%s",
                    self.config.serve.host, self.config.serve.port, png_route)
        try:
            await self._shutdown.wait()
        finally:
            await runner.cleanup()

    async def handle_dashboard(self, request: web.Request) -> web.Response:
        if not self.output_path.exists():
            return web.Response(status=503, text="no render available yet\n")
        loop = asyncio.get_running_loop()
        etag, last_modified, size = await loop.run_in_executor(None, self._fingerprint)

        # Content-based ETag short-circuits when the bytes don't change
        # (re-render of identical data). Time-based If-Modified-Since
        # short-circuits when mtime matches.
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304, headers={"ETag": etag, "Last-Modified": last_modified})
        ims = request.headers.get("If-Modified-Since")
        if ims and ims == last_modified:
            return web.Response(status=304, headers={"ETag": etag, "Last-Modified": last_modified})

        headers = {
            "ETag": etag,
            "Last-Modified": last_modified,
            "Cache-Control": "public, max-age=60",
            "Content-Type": "image/png",
            "Content-Length": str(size),
        }
        if request.method == "HEAD":
            return web.Response(status=200, headers=headers)
        body = await loop.run_in_executor(None, self.output_path.read_bytes)
        return web.Response(body=body, headers=headers)

    async def handle_healthz(self, _request: web.Request) -> web.Response:
        age: float | None = None
        if self.last_render_at is not None:
            age = (datetime.now(tz=timezone.utc) - self.last_render_at).total_seconds()
        return web.json_response({
            "last_render_at":         self.last_render_at.isoformat() if self.last_render_at else None,
            "last_render_status":     self.last_render_status,
            "last_render_error":      self.last_render_error,
            "age_seconds":            age,
            "consecutive_failures":   self.consecutive_failures,
            "total_renders":          self.total_renders,
            "total_failures":         self.total_failures,
            "refresh_minutes":        self.config.render.refresh_minutes,
        })

    # ── helpers ────────────────────────────────────────────────────────────

    def _fingerprint(self) -> tuple[str, str, int]:
        """Return (etag, Last-Modified, size). Cached until mtime changes."""
        stat = self.output_path.stat()
        if self._etag is not None and stat.st_mtime == self._etag_mtime:
            return self._etag, self._last_modified, self._size
        h = hashlib.sha256()
        with self.output_path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        self._etag = f'"{h.hexdigest()[:16]}"'
        self._last_modified = formatdate(stat.st_mtime, usegmt=True)
        self._size = stat.st_size
        self._etag_mtime = stat.st_mtime
        return self._etag, self._last_modified, self._size


# ── sync entrypoint (CLI) ────────────────────────────────────────────────────


def run_server(config: Config) -> None:
    """Block until shutdown. Hooked up to SIGTERM/SIGINT for clean Docker stop."""
    server = WeatherServer(config)
    loop = asyncio.new_event_loop()

    def _on_signal() -> None:
        logger.info("shutdown signal received")
        server.request_shutdown()

    try:
        loop.add_signal_handler(signal.SIGTERM, _on_signal)
        loop.add_signal_handler(signal.SIGINT, _on_signal)
    except NotImplementedError:
        # Windows or some embedded contexts — Ctrl-C still propagates.
        pass

    try:
        loop.run_until_complete(server.run())
    finally:
        loop.close()
