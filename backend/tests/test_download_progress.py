"""Unit tests for DownloadProgressTracker.

Focuses on the pure formatting + state-transition logic. Redis writes
are routed through a tiny fake that records calls.
"""

from __future__ import annotations

import json

import pytest

from app.services.media.downloader.download_progress import DownloadProgressTracker


class _FakeRedis:
    def __init__(self) -> None:
        self.writes: list[tuple[str, int, str]] = []

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.writes.append((key, ttl, value))


# ─── _format_speed ─────────────────────────────────────────────────


class TestFormatSpeed:
    def test_bytes_per_sec(self) -> None:
        t = DownloadProgressTracker("t1", _FakeRedis())
        assert t._format_speed(512) == "512 B/s"

    def test_kb_per_sec(self) -> None:
        t = DownloadProgressTracker("t1", _FakeRedis())
        assert t._format_speed(2048) == "2.0 KB/s"

    def test_mb_per_sec(self) -> None:
        t = DownloadProgressTracker("t1", _FakeRedis())
        assert t._format_speed(5 * 1024 * 1024) == "5.0 MB/s"

    def test_zero(self) -> None:
        t = DownloadProgressTracker("t1", _FakeRedis())
        assert t._format_speed(0) == "0 B/s"


# ─── complete() ────────────────────────────────────────────────────


def test_complete_writes_final_status() -> None:
    redis = _FakeRedis()
    t = DownloadProgressTracker("t1", redis)
    t.total = 1000
    t.complete()

    assert len(redis.writes) == 1
    key, ttl, payload = redis.writes[0]
    assert key == "download_progress:t1"
    assert ttl == 60
    data = json.loads(payload)
    assert data["percent"] == 100
    assert data["downloaded"] == 1000
    assert data["total"] == 1000
    assert data["status"] == "completed"


# ─── failed() ──────────────────────────────────────────────────────


def test_failed_writes_error_status() -> None:
    redis = _FakeRedis()
    t = DownloadProgressTracker("t1", redis)
    t.downloaded = 200
    t.total = 1000
    t.failed("Network timeout")

    key, ttl, payload = redis.writes[0]
    assert ttl == 300
    data = json.loads(payload)
    assert data["status"] == "failed"
    assert data["error"] == "Network timeout"
    assert data["percent"] == 20.0


def test_failed_with_unknown_total_uses_zero_percent() -> None:
    redis = _FakeRedis()
    t = DownloadProgressTracker("t1", redis)
    t.downloaded = 0
    t.total = 0
    t.failed("dns failed")
    data = json.loads(redis.writes[0][2])
    assert data["percent"] == 0


# ─── update() throttling ───────────────────────────────────────────


def test_first_update_always_writes() -> None:
    redis = _FakeRedis()
    t = DownloadProgressTracker("t1", redis, update_interval=10.0)
    t.update(500, 1000)

    assert len(redis.writes) == 1
    data = json.loads(redis.writes[0][2])
    assert data["percent"] == 50
    assert data["status"] == "downloading"


def test_second_update_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two quick updates should only emit one redis write when throttled."""
    redis = _FakeRedis()
    t = DownloadProgressTracker("t1", redis, update_interval=10.0)

    # Force time.time() to a fixed value so the throttle blocks the second write.
    import time as real_time

    base = real_time.time()

    times = iter([base, base + 0.1])

    def _fake_time() -> float:
        return next(times)

    # Only patch the time module *as used inside update()*.
    monkeypatch.setattr("time.time", _fake_time)

    t.update(100, 1000)
    t.update(200, 1000)

    # Only the first write goes through.
    assert len(redis.writes) == 1
