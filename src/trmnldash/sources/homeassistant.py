"""Home Assistant REST client.

Reads sensor states via `GET /api/states/<entity_id>`. Auth is a
long-lived access token in `Authorization: Bearer <token>`.

Failure model:
- Auth failures (401/403) raise HomeAssistantError. They indicate a
  config problem that won't fix itself on retry.
- Per-sensor failures (404, 5xx after retry, non-numeric state, missing,
  reading "unavailable"/"unknown") log a warning and omit that entity
  from the result. The aggregation layer falls back to the weather API
  or hides the affected card.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import HomeAssistantConfig, require_env

logger = logging.getLogger(__name__)


class HomeAssistantError(Exception):
    """Raised for hard config/auth failures (401, 403). Soft per-sensor
    failures log a warning and skip the entity instead."""


@dataclass(frozen=True)
class SensorReading:
    entity_id: str
    state: float
    unit: str | None
    last_updated: datetime      # tz-aware


# State strings HA returns when a sensor isn't reporting a usable value.
_UNUSABLE_STATES = frozenset({"unavailable", "unknown", "none", ""})


class HomeAssistantClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_s: float = 5.0,
        retries: int = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._timeout_s = timeout_s
        self._retries = retries

    def fetch_states(self, entity_ids: list[str]) -> dict[str, SensorReading]:
        """Best-effort batch read. Returns one entry per entity that
        produced a usable reading; absent entries are logged."""
        out: dict[str, SensorReading] = {}
        if not entity_ids:
            return out
        # Reuse the same client across the batch for connection-keepalive.
        with httpx.Client(timeout=self._timeout_s, headers=self._headers) as client:
            for eid in entity_ids:
                reading = self._fetch_one(client, eid)
                if reading is not None:
                    out[eid] = reading
        return out

    # ──────────────────────────────────────────────────────────────────────

    def _fetch_one(self, client: httpx.Client, entity_id: str) -> SensorReading | None:
        url = f"{self._base_url}/api/states/{entity_id}"
        last_err: str | None = None
        for attempt in range(self._retries):
            try:
                r = client.get(url)
            except httpx.RequestError as e:
                last_err = f"{type(e).__name__}: {e}"
                continue
            if r.status_code in (401, 403):
                raise HomeAssistantError(
                    f"Auth failure ({r.status_code}) from {self._base_url} — check token"
                )
            if r.status_code == 404:
                logger.warning("HA sensor %s not found (404)", entity_id)
                return None
            if r.status_code >= 500:
                last_err = f"HTTP {r.status_code}"
                continue
            if r.status_code != 200:
                logger.warning("HA sensor %s unexpected status %s", entity_id, r.status_code)
                return None
            return _parse_state(r.json())
        logger.warning("HA sensor %s unreachable after %d tries: %s",
                       entity_id, self._retries, last_err)
        return None


def make_ha_client(cfg: HomeAssistantConfig) -> HomeAssistantClient:
    """Build a client from config. Resolves the token env var at call time."""
    token = require_env(cfg.token_env)
    return HomeAssistantClient(base_url=cfg.base_url, token=token)


# ──────────────────────────────────────────────────────────────────────────


def _parse_state(raw: dict[str, Any]) -> SensorReading | None:
    """Parse one HA state payload. Returns None if state isn't usable."""
    entity_id = raw["entity_id"]
    state = raw.get("state")
    state_str = str(state).strip().lower()
    if state_str in _UNUSABLE_STATES:
        logger.warning("HA sensor %s reported state=%r — skipped", entity_id, state)
        return None
    try:
        value = float(state)
    except (ValueError, TypeError):
        logger.warning("HA sensor %s state %r is not numeric — skipped", entity_id, state)
        return None
    attrs = raw.get("attributes") or {}
    unit = attrs.get("unit_of_measurement")
    ts_raw = raw.get("last_updated") or raw.get("last_changed")
    try:
        # HA returns ISO 8601, e.g. "2026-05-16T20:00:00.000000+00:00".
        # The trailing "Z" form also shows up in older payloads.
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        ts = datetime.now(tz=timezone.utc)
    return SensorReading(entity_id=entity_id, state=value, unit=unit, last_updated=ts)
