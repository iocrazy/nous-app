"""healthz_lite loop-heartbeat staleness (2026-07-06 P0 follow-up).

The prod event loop froze for 2 hours while the liveness sidecar kept
answering 200 (it runs on its own thread — that was the point, and the
blind spot). These tests pin the fix: once the main loop has beaten at
least once, a stale heartbeat flips /healthz/lite to 503 so autoheal
can restart the container; a loop that NEVER beat keeps the endpoint
at 200 (startup grace — the 2026-05-27 restart-storm guarantee).
"""

from __future__ import annotations

import time
import urllib.request

import pytest

from app.startup import healthz_lite


@pytest.fixture(autouse=True)
def _clean_heartbeat(monkeypatch):
    """Isolate heartbeat state and pin the staleness window per test."""
    healthz_lite.reset()
    monkeypatch.setattr(healthz_lite, "_STALE_S", 120.0)
    yield
    healthz_lite.reset()


def _get(server) -> tuple[int, str]:
    url = f"http://127.0.0.1:{server.server_port}/healthz/lite"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:  # 503 raises
        return e.code, e.read().decode()


def test_never_beaten_is_200_startup_grace():
    """No beat yet → 200. A boot-time blocker must NOT read as dead, or
    autoheal would kill a container whose restart re-hits the same
    blocker (the 2026-05-27 storm)."""
    assert healthz_lite._loop_stale() is None
    server = healthz_lite.start(port=0)
    try:
        status, body = _get(server)
        assert status == 200
        assert body == "ok\n"
    finally:
        healthz_lite.stop(server)


def test_fresh_beat_is_200():
    healthz_lite.beat()
    assert healthz_lite._loop_stale() is None
    server = healthz_lite.start(port=0)
    try:
        status, _ = _get(server)
        assert status == 200
    finally:
        healthz_lite.stop(server)


def test_stale_beat_flips_503(monkeypatch):
    """Loop proved alive once, then stopped beating past the window →
    503 with the age in the body. This is the 2-hour-freeze signal."""
    monkeypatch.setattr(healthz_lite, "_last_beat", time.monotonic() - 3600.0)
    age = healthz_lite._loop_stale()
    assert age is not None and age > 120.0
    server = healthz_lite.start(port=0)
    try:
        status, body = _get(server)
        assert status == 503
        assert body.startswith("loop-stale")
    finally:
        healthz_lite.stop(server)


def test_stale_check_disabled_via_zero_window(monkeypatch):
    """HEALTHZ_LOOP_STALE_S=0 → pure process-liveness (old behavior)."""
    monkeypatch.setattr(healthz_lite, "_STALE_S", 0.0)
    monkeypatch.setattr(healthz_lite, "_last_beat", time.monotonic() - 3600.0)
    assert healthz_lite._loop_stale() is None
    server = healthz_lite.start(port=0)
    try:
        status, _ = _get(server)
        assert status == 200
    finally:
        healthz_lite.stop(server)


def test_beat_refreshes_a_stale_heartbeat(monkeypatch):
    """A recovered loop that beats again immediately reads healthy."""
    monkeypatch.setattr(healthz_lite, "_last_beat", time.monotonic() - 3600.0)
    assert healthz_lite._loop_stale() is not None
    healthz_lite.beat()
    assert healthz_lite._loop_stale() is None
