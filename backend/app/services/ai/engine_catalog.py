# backend/app/services/ai/engine_catalog.py

"""What nous-engine serves right now, read on demand (spec 2026-09-25 §3.2).

One function, one cache. ``GET {base_url}/models?include_unready=1`` answers
both questions the platform catalog used to copy into ``nous_models`` by a
background job: *is this service authorized for this key* (it is in the list)
and *is it loaded* (``ready``). Nothing here writes anything; every caller
reads the snapshot and decides for itself.

Cache and failure semantics
---------------------------
* Snapshots are cached for ``ENGINE_TTL_S`` per ``(base_url, key)``. The key
  is part of the cache key because the list is per credential: two rows on
  one engine with different keys can see different services.
* A failed read (timeout, 5xx, transport, malformed body) falls back to the
  last successful snapshot for that key when it is at most
  ``KEEP_LAST_GOOD_S`` old, marked ``stale=True``; older than that →
  ``services=None, reachable=False``. "Could not reach" is never read as
  "revoked" (空输出不是否定结论).
* 401 → ``unauthorized=True, services=None``; the last good snapshot is
  dropped, because the key itself was refused.
* The failure result is cached for the same TTL as a success. Without that a
  timing-out engine would make every ``GET /ai/settings`` wait the full list
  timeout (and ``TTLCache`` holds one lock across keys, so it would serialize
  every caller behind it).
* A 200 with an EMPTY ``data`` list is kept as-is, but :meth:`EngineSnapshot.lists`
  answers ``None`` (unknown) for it: an empty list is as likely an engine fault
  as "every grant revoked", and dropping every row on it is the costly guess.

``context_window`` / ``capabilities`` may be ``null`` on the wire and are
carried as ``None``; a missing ``ready`` is read as ``True`` (an engine without
``include_unready`` support only lists loaded services).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Optional

import httpx
from loguru import logger

from app.core.cache import TTLCache

ENGINE_TTL_S = 30.0
KEEP_LAST_GOOD_S = 600.0
LIST_TIMEOUT_S = 15.0
_ERROR_TEXT_MAX = 200
UNAUTHORIZED_ERROR = "HTTP 401: platform key rejected by nous-engine"
EMPTY_LIST_ERROR = "engine listed no services (empty list not trusted)"

# ``nous_models.actual_provider`` of rows served by nous-engine.
NOUS_ENGINE_PROVIDER = "nous"


@dataclass(frozen=True)
class EngineService:
    id: str
    type: str
    ready: bool
    context_window: Optional[int]
    capabilities: Optional[Mapping[str, Any]]


@dataclass(frozen=True)
class EngineSnapshot:
    """One read of an engine's service list (or the reason there is none).

    ``services is None`` ⇒ no usable list. ``reachable`` says whether this
    snapshot (or the one it carries over) was really read; ``stale`` marks a
    carried-over snapshot.
    """

    services: Optional[Mapping[str, EngineService]]
    fetched_at: Optional[datetime]
    reachable: bool
    stale: bool = False
    unauthorized: bool = False
    error: Optional[str] = None

    def lists(self, service_id: str) -> Optional[bool]:
        """``True`` listed / ``False`` explicitly not listed / ``None`` unknown.

        Unknown covers no list at all and an empty list (see module doc).
        """
        if not self.services:
            return None
        return service_id in self.services

    def service(self, service_id: str) -> Optional[EngineService]:
        return (self.services or {}).get(service_id)


def normalize_base_url(base_url: Optional[str]) -> str:
    return (base_url or "").strip().rstrip("/")


def _cache_key(base_url: str, api_key: str) -> str:
    digest = hashlib.sha256((api_key or "").encode()).hexdigest()[:16]
    return f"{base_url}#{digest}"


def _optional_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _parse_service(entry: Any) -> Optional[EngineService]:
    if not isinstance(entry, Mapping):
        return None
    service_id = entry.get("id")
    if not isinstance(service_id, str) or not service_id.strip():
        return None
    ready = entry.get("ready")
    caps = entry.get("capabilities")
    return EngineService(
        id=service_id,
        type=str(entry.get("type") or ""),
        ready=ready if isinstance(ready, bool) else True,
        context_window=_optional_int(entry.get("context_window")),
        capabilities=dict(caps) if isinstance(caps, Mapping) else None,
    )


@dataclass(frozen=True)
class _Read:
    services: Optional[dict[str, EngineService]] = None
    error: Optional[str] = None
    unauthorized: bool = False


async def _http_list(base_url: str, api_key: str) -> _Read:
    try:
        async with httpx.AsyncClient(timeout=LIST_TIMEOUT_S) as client:
            resp = await client.get(
                f"{base_url}/models",
                params={"include_unready": "1"},
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except Exception as exc:  # noqa: BLE001 — reported as a typed read error
        return _Read(error=f"{type(exc).__name__}: {str(exc) or '<no message>'}")
    if resp.status_code == 401:
        return _Read(error=UNAUTHORIZED_ERROR, unauthorized=True)
    if resp.status_code != 200:
        return _Read(error=f"HTTP {resp.status_code}: {resp.text[:160]}")
    try:
        payload = resp.json()
    except ValueError:
        return _Read(error="engine /models response is not JSON")
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, list):
        return _Read(error="engine /models response has no 'data' list")
    services: dict[str, EngineService] = {}
    for entry in data:
        parsed = _parse_service(entry)
        if parsed is not None and parsed.id not in services:
            services[parsed.id] = parsed
    return _Read(services=services)


# Test seam: replaced to script engine answers without HTTP.
_fetch: Callable[[str, str], Awaitable[_Read]] = _http_list
_clock: Callable[[], float] = time.monotonic

_cache: TTLCache[EngineSnapshot] = TTLCache(ttl_seconds=ENGINE_TTL_S, maxsize=64)
# cache key → (monotonic time of the read, snapshot)
_last_good: dict[str, tuple[float, EngineSnapshot]] = {}


def reset_engine_cache() -> None:
    """Forget every snapshot (tests; also safe to call at runtime)."""
    _cache.clear()
    _last_good.clear()


async def _load(key: str, base_url: str, api_key: str) -> EngineSnapshot:
    read = await _fetch(base_url, api_key)
    now = _clock()
    if read.services is not None:
        snap = EngineSnapshot(
            services=read.services,
            fetched_at=datetime.now(timezone.utc),
            reachable=True,
            error=None if read.services else EMPTY_LIST_ERROR,
        )
        if read.services:
            _last_good[key] = (now, snap)
        else:
            logger.warning(f"[engine_catalog] {base_url}: {EMPTY_LIST_ERROR}")
        return snap
    error = (read.error or "unknown error")[:_ERROR_TEXT_MAX]
    if read.unauthorized:
        _last_good.pop(key, None)
        logger.warning(f"[engine_catalog] {base_url}: {error}")
        return EngineSnapshot(
            services=None,
            fetched_at=None,
            reachable=False,
            unauthorized=True,
            error=error,
        )
    previous = _last_good.get(key)
    if previous is not None and now - previous[0] <= KEEP_LAST_GOOD_S:
        logger.warning(f"[engine_catalog] {base_url}: {error} — using last snapshot")
        return replace(previous[1], stale=True, error=error)
    logger.warning(f"[engine_catalog] {base_url}: {error} — no usable snapshot")
    return EngineSnapshot(services=None, fetched_at=None, reachable=False, error=error)


async def engine_snapshot(
    base_url: Optional[str], api_key: Optional[str]
) -> EngineSnapshot:
    """The engine's service list for this credential (cached, never raises)."""
    base = normalize_base_url(base_url)
    key = (api_key or "").strip()
    if not base:
        return EngineSnapshot(
            services=None,
            fetched_at=None,
            reachable=False,
            error="no base_url configured",
        )
    cache_key = _cache_key(base, key)
    try:
        return await _cache.get_or_load(cache_key, lambda: _load(cache_key, base, key))
    except Exception as exc:  # noqa: BLE001 — a snapshot read must not raise
        logger.error(f"[engine_catalog] {base}: snapshot load crashed: {exc!r}")
        return EngineSnapshot(
            services=None,
            fetched_at=None,
            reachable=False,
            error=f"{type(exc).__name__}"[:_ERROR_TEXT_MAX],
        )


def engine_credential(row: Mapping[str, Any]) -> tuple[str, str]:
    """``(base_url, api_key)`` a nous-engine row is dispatched with — and so
    the credential whose authorizations decide what it can reach."""
    return normalize_base_url(row.get("base_url")), (row.get("api_key") or "").strip()


async def snapshots_for(
    credentials: Mapping[str, tuple[str, str]],
) -> dict[str, EngineSnapshot]:
    """``name → snapshot`` for ``name → (base_url, api_key)``; each distinct
    credential is read once."""
    by_cred: dict[tuple[str, str], EngineSnapshot] = {}
    out: dict[str, EngineSnapshot] = {}
    for name, cred in credentials.items():
        if cred not in by_cred:
            by_cred[cred] = await engine_snapshot(*cred)
        out[name] = by_cred[cred]
    return out


__all__ = [
    "EngineService",
    "EngineSnapshot",
    "NOUS_ENGINE_PROVIDER",
    "engine_credential",
    "engine_snapshot",
    "normalize_base_url",
    "reset_engine_cache",
    "snapshots_for",
]
