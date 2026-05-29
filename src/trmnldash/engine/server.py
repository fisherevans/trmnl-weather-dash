"""Long-running service: one HTTP listener + N render schedulers.

Each dashboard runs on its own asyncio task at its own
`render.refresh_minutes` interval. The actual Chromium screenshot is
serialised through a global lock so we never have two playwright
sessions racing - in practice schedule overlap is rare (1 min weather
vs 5 min calendar = ~5x as much idle time as render time), and serial
renders keep memory bounded.

Each dashboard is mounted at `GET /<dashboard.serve.secret_path>/dashboard.png`
on the shared `serve.host:port`. Different dashboards use different
secret_paths so the routes never collide; config-load validates this.

`/healthz` is all-must-be-healthy: 200 only when every dashboard has at
least one successful render and the most recent render of each one
succeeded. A single failed dashboard takes the whole process unhealthy.

Atomic writes per dashboard: each render writes to `<output>.tmp.png`
then os.replace()s onto the real path. HTTP clients never see a
partial file.

Render failures don't crash the process - the previous image stays in
place for that dashboard, failure shows up in /healthz, the next
interval retries.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import signal
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import formatdate
from pathlib import Path

from aiohttp import web

from ..config import Config, DashboardEntry
from .pipeline import RenderStats, run_once

logger = logging.getLogger(__name__)


@dataclass
class _DashboardState:
    """Per-dashboard runtime state. The server holds one of these per
    entry in `config.dashboards`."""
    entry: DashboardEntry

    # /healthz signal
    last_render_at: datetime | None = None
    last_render_status: str = "pending"           # "pending" | "ok" | "error"
    last_render_error: str | None = None
    consecutive_failures: int = 0
    total_renders: int = 0
    total_failures: int = 0

    # Per-output fingerprint cache - invalidated when output mtime changes.
    etag: str | None = None
    last_modified: str = ""
    size: int = 0
    etag_mtime: float = 0.0

    @property
    def output_path(self) -> Path:
        return self.entry.render.output_path

    @property
    def is_healthy(self) -> bool:
        return self.total_renders > 0 and self.last_render_status == "ok"


class TrmnldashServer:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._dashboards: dict[str, _DashboardState] = {
            entry.name: _DashboardState(entry=entry)
            for entry in config.dashboards
        }
        # Global Chromium lock - serialises the actual screenshot step
        # across dashboards so we never have two playwright sessions in
        # flight simultaneously.
        self._render_lock = asyncio.Lock()
        self._shutdown = asyncio.Event()

    # ── public API ─────────────────────────────────────────────────────────

    async def run(self) -> None:
        scheduler_tasks = [
            asyncio.create_task(self._scheduler_loop(name), name=f"scheduler[{name}]")
            for name in self._dashboards
        ]
        try:
            await self._serve_until_shutdown()
        finally:
            for t in scheduler_tasks:
                t.cancel()
            for t in scheduler_tasks:
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    def request_shutdown(self) -> None:
        self._shutdown.set()

    # ── scheduler ──────────────────────────────────────────────────────────

    async def _scheduler_loop(self, name: str) -> None:
        """Render `name` once immediately, then every refresh_minutes until shutdown."""
        state = self._dashboards[name]
        interval_s = state.entry.render.refresh_minutes * 60
        while not self._shutdown.is_set():
            await self._render_in_executor(state)
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=interval_s)
                return
            except asyncio.TimeoutError:
                pass

    async def _render_in_executor(self, state: _DashboardState) -> None:
        # Global Chromium lock - serialise across dashboards. The fetch
        # step is included in the lock too; in principle we could overlap
        # fetches but the wins are small and the lock keeps the model
        # simple.
        loop = asyncio.get_running_loop()
        async with self._render_lock:
            try:
                stats = await loop.run_in_executor(None, self._do_render_sync, state)
            except Exception as e:                          # noqa: BLE001 - log + continue
                logger.exception("render failed for dashboard %s", state.entry.name)
                state.last_render_status = "error"
                state.last_render_error = f"{type(e).__name__}: {e}"
                state.consecutive_failures += 1
                state.total_failures += 1
                return
        state.last_render_at = datetime.now(tz=timezone.utc)
        state.last_render_status = "ok"
        state.last_render_error = None
        state.consecutive_failures = 0
        state.total_renders += 1
        # Invalidate fingerprint cache so next request recomputes.
        state.etag = None
        panel_summary = " ".join(
            f"{p.name}(fetch={p.fetch_ms:.0f}ms,render={p.render_ms:.0f}ms)"
            for p in stats.panels
        ) or "(no-panels)"
        logger.info(
            "rendered %s -> %s in %dms - %s compose=%dms quantize=%dms",
            state.entry.name, state.output_path, stats.total_ms,
            panel_summary, stats.compose_ms, stats.quantize_ms,
        )

    def _do_render_sync(self, state: _DashboardState) -> RenderStats:
        """Runs in a thread (playwright sync_api is blocking). Atomic write."""
        state.output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state.output_path.with_suffix(".tmp.png")
        stats = run_once(state.entry, tmp, quantize=True)
        os.replace(tmp, state.output_path)
        stats.output_path = state.output_path
        return stats

    # ── HTTP server ────────────────────────────────────────────────────────

    async def _serve_until_shutdown(self) -> None:
        app = web.Application()
        for state in self._dashboards.values():
            secret = state.entry.serve.secret_path
            png_route = f"/{secret}/dashboard.png"
            handler = self._make_png_handler(state)
            app.router.add_route("GET", png_route, handler)
            app.router.add_route("HEAD", png_route, handler)
        app.router.add_get("/healthz", self.handle_healthz)

        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, self.config.serve.host, self.config.serve.port)
        await site.start()
        for state in self._dashboards.values():
            logger.info(
                "serving dashboard %s at http://%s:%d/%s/dashboard.png",
                state.entry.name, self.config.serve.host,
                self.config.serve.port, state.entry.serve.secret_path,
            )
        try:
            await self._shutdown.wait()
        finally:
            await runner.cleanup()

    def _make_png_handler(self, state: _DashboardState):
        async def handle(request: web.Request) -> web.Response:
            if not state.output_path.exists():
                return web.Response(status=503, text="no render available yet\n")
            loop = asyncio.get_running_loop()
            etag, last_modified, size = await loop.run_in_executor(None, self._fingerprint, state)

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
            body = await loop.run_in_executor(None, state.output_path.read_bytes)
            return web.Response(body=body, headers=headers)

        return handle

    async def handle_healthz(self, _request: web.Request) -> web.Response:
        all_healthy = all(s.is_healthy for s in self._dashboards.values())
        now = datetime.now(tz=timezone.utc)
        payload = {
            "healthy": all_healthy,
            "dashboards": {
                name: {
                    "status":               s.last_render_status,
                    "last_render_at":       s.last_render_at.isoformat() if s.last_render_at else None,
                    "age_seconds":          (now - s.last_render_at).total_seconds() if s.last_render_at else None,
                    "consecutive_failures": s.consecutive_failures,
                    "total_renders":        s.total_renders,
                    "total_failures":       s.total_failures,
                    "refresh_minutes":      s.entry.render.refresh_minutes,
                    "last_error":           s.last_render_error,
                }
                for name, s in self._dashboards.items()
            },
        }
        return web.json_response(payload, status=200 if all_healthy else 503)

    # ── helpers ────────────────────────────────────────────────────────────

    def _fingerprint(self, state: _DashboardState) -> tuple[str, str, int]:
        stat = state.output_path.stat()
        if state.etag is not None and stat.st_mtime == state.etag_mtime:
            return state.etag, state.last_modified, state.size
        h = hashlib.sha256()
        with state.output_path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        state.etag = f'"{h.hexdigest()[:16]}"'
        state.last_modified = formatdate(stat.st_mtime, usegmt=True)
        state.size = stat.st_size
        state.etag_mtime = stat.st_mtime
        return state.etag, state.last_modified, state.size


# ── sync entrypoint (CLI) ────────────────────────────────────────────────────


def run_server(config: Config) -> None:
    """Block until shutdown. Hooked up to SIGTERM/SIGINT for clean Docker stop."""
    server = TrmnldashServer(config)
    loop = asyncio.new_event_loop()

    def _on_signal() -> None:
        logger.info("shutdown signal received")
        server.request_shutdown()

    try:
        loop.add_signal_handler(signal.SIGTERM, _on_signal)
        loop.add_signal_handler(signal.SIGINT, _on_signal)
    except NotImplementedError:
        pass

    try:
        loop.run_until_complete(server.run())
    finally:
        loop.close()
