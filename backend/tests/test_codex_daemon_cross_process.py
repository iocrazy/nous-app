"""C4 修补 — cross-container dispatch (spec §4's Redis half).

The daemon's socket lives in the gateway process; the generation workflow
runs in the worker container. Presence and job routing must therefore go
through Redis, not process memory:

- presence: `codex_online:<user_id>` marker, TTL-refreshed by heartbeats —
  a zombie socket stops refreshing and the marker dies with it
- jobs:     worker publishes to `codex_jobs:<user_id>`; the gateway that
  holds the socket forwards to the daemon
- results:  gateway publishes to `codex_results:<job_id>`; the waiting
  worker resolves its future
"""

from __future__ import annotations

import json

import pytest

from app.services.codex import daemon_presence


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int]] = {}
        self.published: list[tuple[str, str]] = []

    async def set(self, key, value, ex=None):
        self.store[key] = (str(value), int(ex or 0))

    async def delete(self, key):
        self.store.pop(key, None)

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def publish(self, channel, message):
        self.published.append((channel, message))


@pytest.mark.asyncio
async def test_presence_marker_set_with_ttl_and_cleared(monkeypatch):
    r = _FakeRedis()

    async def _r():
        return r

    monkeypatch.setattr(daemon_presence, "get_async_redis", _r)
    await daemon_presence.mark_online("u1", "d1")
    key = "codex_online:u1"
    assert key in r.store
    # TTL must exceed the 30s heartbeat but expire soon after beats stop.
    assert 60 <= r.store[key][1] <= 120
    assert await daemon_presence.is_online_anywhere("u1") is True
    await daemon_presence.mark_offline("u1", "d1")
    assert await daemon_presence.is_online_anywhere("u1") is False


@pytest.mark.asyncio
async def test_job_publish_and_result_publish_use_expected_channels(monkeypatch):
    r = _FakeRedis()

    async def _r():
        return r

    monkeypatch.setattr(daemon_presence, "get_async_redis", _r)
    await daemon_presence.publish_job("u1", {"type": "job", "job_id": "j1"})
    await daemon_presence.publish_result("j1", {"gen_id": "9"})
    channels = [c for c, _ in r.published]
    assert channels == ["codex_jobs:u1", "codex_results:j1"]
    assert json.loads(r.published[0][1])["job_id"] == "j1"


@pytest.mark.asyncio
async def test_job_claim_is_first_wins(monkeypatch):
    r = _FakeRedis()

    async def _set_nx(key, value, nx=False, ex=None):
        if nx and key in r.store:
            return None
        r.store[key] = (str(value), int(ex or 0))
        return True

    r.set = _set_nx  # type: ignore[method-assign]

    async def _r():
        return r

    monkeypatch.setattr(daemon_presence, "get_async_redis", _r)
    assert await daemon_presence.claim_job("j1", "d1") is True
    assert await daemon_presence.claim_job("j1", "d2") is False
