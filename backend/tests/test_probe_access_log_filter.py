"""Container healthcheck probes must not drown out application_logs.

2026-07-25 the backend/worker healthcheck moved from `/health` (a hardcoded
200) to `/api/v1/readyz`, so a real request now runs every 30s per container.
Every one of them lands in `application_logs` through the stdlib→loguru
bridge. Measured 2026-07-26: **1448 of 4643 rows in 6 hours were probes —
31.3%**, and diagnosing an actual incident took three rounds of filtering
before anything useful was visible.

The fix drops only *successful* probes. A failing readyz is precisely the
signal the probe was introduced for (it is what turns a silently-broken DBOS
into a red container), so non-2xx probe lines must survive.
"""

from __future__ import annotations

import pytest

from app.core.utils import is_noisy_probe_access_log as noisy

_OK_READYZ = '127.0.0.1:42186 - "GET /api/v1/readyz HTTP/1.1" 200'
_OK_HEALTHZ = '127.0.0.1:42186 - "GET /api/v1/healthz HTTP/1.1" 200'
_OK_HEALTH = '127.0.0.1:42186 - "GET /health HTTP/1.1" 200'


@pytest.mark.parametrize("line", [_OK_READYZ, _OK_HEALTHZ, _OK_HEALTH])
def test_successful_probes_are_dropped(line: str) -> None:
    assert noisy(line) is True


def test_failing_readyz_is_kept() -> None:
    """503 readyz == DBOS is down. Never drop this — it's the whole point."""
    assert noisy('127.0.0.1:42186 - "GET /api/v1/readyz HTTP/1.1" 503') is False


def test_server_error_probe_is_kept() -> None:
    assert noisy('127.0.0.1:42186 - "GET /health HTTP/1.1" 500') is False


@pytest.mark.parametrize(
    "line",
    [
        '10.0.0.9:59666 - "GET /api/v1/task-manager/tasks?limit=200 HTTP/1.1" 200',
        '10.0.0.9:60228 - "POST /api/v1/media/bilibili_BV1o/fetch HTTP/1.1" 200',
        '10.0.0.9:59690 - "GET /media/331610444669024/cover?token=*** HTTP/1.1" 200',
    ],
)
def test_real_traffic_is_kept(line: str) -> None:
    assert noisy(line) is False


def test_health_prefix_does_not_swallow_other_paths() -> None:
    """`/health` must not match `/healthcheck-report` or similar."""
    assert noisy('1.2.3.4 - "GET /api/v1/health-report HTTP/1.1" 200') is False


def test_non_access_log_text_is_kept() -> None:
    """Only uvicorn access lines have this shape; anything else passes."""
    assert noisy("[dbos] launched (worker pool started, recovery complete)") is False
