"""Tests for the scale-Tier-2 admin stats RPCs (mig 270/271) + router mapping.

The aggregation moved from fetch-all-then-count-in-Python to a server-side SQL
RPC (correct at 100k+ log volume). These tests pin two things the SQL dry-run
against prod can't: (1) the repo calls the right RPC with the right params, and
(2) the router maps the RPC's jsonb keys onto the response models exactly (a
key/field-name drift here would 500 the dashboard).
"""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from typing import Any

import pytest

from app.repositories.admin.monitoring_repository import MonitoringRepository
from app.repositories.admin.request_logs_repository import RequestLogsRepository

# The admin package __init__ rebinds `monitoring_router` / `request_logs_router`
# to their APIRouter objects, which shadows the submodule attribute. Pull the
# real modules from the import system so monkeypatch targets the right namespace.
mon_router = importlib.import_module("app.api.admin.monitoring_router")
rl_router = importlib.import_module("app.api.admin.request_logs_router")


# --------------------------------------------------------------------------- #
# Fake supabase rpc client
# --------------------------------------------------------------------------- #
class _FakeRpcResult:
    def __init__(self, data: Any) -> None:
        self.data = data


class _FakeRpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> _FakeRpcResult:
        return _FakeRpcResult(self._data)


class _FakeRpcClient:
    def __init__(self, data: Any) -> None:
        self._data = data
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        self.rpc_calls.append((name, params))
        return _FakeRpcBuilder(self._data)


def _bind_client(repo: Any, client: _FakeRpcClient) -> None:
    async def _client():
        return client

    repo._client = _client  # type: ignore[method-assign]


# --------------------------------------------------------------------------- #
# Repo-method tests: correct RPC name + params + passthrough
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_request_log_stats_calls_rpc() -> None:
    payload = {
        "total": 3,
        "by_method": [],
        "by_status": [],
        "top_paths": [],
        "by_hour": [],
    }
    client = _FakeRpcClient(payload)
    repo = RequestLogsRepository()
    _bind_client(repo, client)

    since = datetime(2026, 6, 1, tzinfo=timezone.utc)
    out = await repo.request_log_stats(since)

    assert out == payload
    assert client.rpc_calls == [
        ("rpc_request_log_stats", {"p_since": since.isoformat()})
    ]


@pytest.mark.asyncio
async def test_request_log_stats_returns_empty_dict_on_null_data() -> None:
    client = _FakeRpcClient(None)
    repo = RequestLogsRepository()
    _bind_client(repo, client)
    out = await repo.request_log_stats(datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert out == {}


@pytest.mark.asyncio
async def test_monitoring_stats_calls_rpc_with_bucket() -> None:
    payload = {"overview": {}, "request_trend": []}
    client = _FakeRpcClient(payload)
    repo = MonitoringRepository()
    _bind_client(repo, client)

    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 6, 2, tzinfo=timezone.utc)
    out = await repo.monitoring_stats(start, end, 60)

    assert out == payload
    assert client.rpc_calls == [
        (
            "rpc_monitoring_stats",
            {
                "p_start": start.isoformat(),
                "p_end": end.isoformat(),
                "p_bucket_minutes": 60,
            },
        )
    ]


# --------------------------------------------------------------------------- #
# Router-mapping tests: RPC jsonb -> response models, keys must line up
# --------------------------------------------------------------------------- #
_RL_PAYLOAD = {
    "total": 50,
    "by_method": [{"method": "GET", "count": 40}, {"method": "POST", "count": 10}],
    "by_status": [
        {"status_group": "2xx", "count": 48},
        {"status_group": "4xx", "count": 2},
    ],
    "top_paths": [{"path": "/a", "count": 30, "avg_response_time_ms": 120}],
    "by_hour": [{"hour": "2026-06-07T12", "count": 50}],
}

_MON_PAYLOAD = {
    "overview": {
        "total_requests": 100,
        "error_rate": 2.5,
        "avg_response_ms": 150.0,
        "app_error_count": 3,
        "frontend_error_count": 1,
    },
    "request_trend": [{"time": "2026-06-07T12:00", "requests": 60, "errors": 1}],
    "top_slow_apis": [{"path": "/x", "avg_ms": 200.0, "p95_ms": 400, "count": 5}],
    "top_error_endpoints": [{"path": "/y", "error_count": 2, "last_status": 500}],
    "log_level_distribution": {"INFO": 90, "ERROR": 3},
    "top_error_modules": [{"module": "media", "count": 2}],
    "recent_errors": [
        {
            "level": "ERROR",
            "module": "media",
            "message": "boom",
            "logged_at": "2026-06-07T12:00:00+00:00",
        }
    ],
}


class _FakeRepo:
    def __init__(self, payload: Any, method: str) -> None:
        self._payload = payload
        self._method = method

    async def request_log_stats(self, *_a: Any, **_k: Any) -> Any:
        return self._payload

    async def monitoring_stats(self, *_a: Any, **_k: Any) -> Any:
        return self._payload


@pytest.mark.asyncio
async def test_request_log_stats_endpoint_maps_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        rl_router, "get_request_logs_repository", lambda: _FakeRepo(_RL_PAYLOAD, "rl")
    )
    resp = await rl_router.get_request_log_stats(auth=object(), hours=24)

    assert resp.total == 50
    assert [m.method for m in resp.by_method] == ["GET", "POST"]
    assert [s.status_group for s in resp.by_status] == ["2xx", "4xx"]
    assert resp.top_paths[0].path == "/a"
    assert resp.top_paths[0].avg_response_time_ms == 120
    assert resp.by_hour[0].hour == "2026-06-07T12"


@pytest.mark.asyncio
async def test_request_log_stats_endpoint_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        rl_router,
        "get_request_logs_repository",
        lambda: _FakeRepo({"total": 0}, "rl"),
    )
    resp = await rl_router.get_request_log_stats(auth=object(), hours=24)
    assert resp.total == 0
    assert resp.by_method == []
    assert resp.top_paths == []


@pytest.mark.asyncio
async def test_monitoring_stats_endpoint_maps_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        mon_router, "get_monitoring_repository", lambda: _FakeRepo(_MON_PAYLOAD, "mon")
    )
    resp = await mon_router.get_monitoring_stats(
        auth=object(), period="24h", start_date=None, end_date=None
    )

    assert resp.overview.total_requests == 100
    assert resp.overview.error_rate == 2.5
    assert resp.overview.frontend_error_count == 1
    assert resp.request_trend[0].time == "2026-06-07T12:00"
    assert resp.top_slow_apis[0].p95_ms == 400
    assert resp.top_error_endpoints[0].last_status == 500
    assert resp.log_level_distribution == {"INFO": 90, "ERROR": 3}
    assert resp.top_error_modules[0].module == "media"
    assert resp.recent_errors[0].message == "boom"


@pytest.mark.asyncio
async def test_monitoring_stats_endpoint_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        mon_router, "get_monitoring_repository", lambda: _FakeRepo({}, "mon")
    )
    resp = await mon_router.get_monitoring_stats(
        auth=object(), period="24h", start_date=None, end_date=None
    )
    assert resp.overview.total_requests == 0
    assert resp.request_trend == []
    assert resp.top_slow_apis == []
    assert resp.log_level_distribution == {}
