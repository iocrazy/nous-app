"""Unit tests for the three log repositories in request_logs_repository.py
(ORM 2.0, model-backed).

Post-rollout the three ``list_with_filters`` reads plus
``RequestLogsRepository.stats_since`` are the SQLAlchemy 2.0 implementation — they
go through ``read_scope()`` with ``select`` statements. ``list_with_filters`` builds
SELECT *-shaped dicts via ``_orm_obj_to_dict``; ``stats_since`` builds a
column-subset dict by hand. These tests mock ``read_scope`` with a fake session
that captures every emitted ``(compiled_sql, binds)`` pair and returns in-memory
rows, so the filter translation (status-group ranges, method/level upper-casing,
NOISE_MODULES exclusion, has_exception IS [NOT] NULL, date windows) AND the
strategy-C value-type parity (id → int, timestamp/created_at/logged_at → ISO str,
renamed metadata_ keyed back as "metadata", uuid → str) are asserted WITHOUT a
live database (the DSN-gated integration suite in
``tests/integration/test_admin_request_logs_repository_orm.py`` exercises the real
round-trip). This keeps fast, always-run coverage of the collapsed ORM bodies.

``RequestLogsRepository.request_log_stats`` stays on the supabase-py
``rpc_request_log_stats`` RPC path and is covered by
``tests/test_admin_stats_rpc_mapping.py`` (unchanged).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

import app.repositories.admin.request_logs_repository as mod
from app.models import ApiRequestLogs, ApplicationLogs, FrontendErrorLogs
from app.repositories.admin.request_logs_repository import (
    AppLogsRepository,
    FrontendErrorLogsRepository,
    RequestLogsRepository,
)


class _FakeResult:
    """Supports both ``.scalars().all()`` (list_with_filters) and ``.all()``
    (stats_since)."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Captures execute/scalar (compiled sql, binds); returns configured rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows: list[Any] = []
        self.scalar_value: int | None = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append((str(stmt), stmt.compile().params))
        return _FakeResult(self.rows)

    async def scalar(self, stmt: Any) -> Any:
        self.calls.append((str(stmt), stmt.compile().params))
        return self.scalar_value


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(session))
    return session


def _binds(session: _FakeSession) -> list[Any]:
    values: list[Any] = []
    for _sql, params in session.calls:
        values.extend(params.values())
    return values


# ─── RequestLogsRepository ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_logs_status_group_2xx_uses_range(
    fake_session: _FakeSession,
) -> None:
    await RequestLogsRepository().list_with_filters(
        page=1, page_size=50, status_group="2xx"
    )
    binds = _binds(fake_session)
    assert 200 in binds and 300 in binds


@pytest.mark.asyncio
async def test_request_logs_status_group_4xx(
    fake_session: _FakeSession,
) -> None:
    await RequestLogsRepository().list_with_filters(
        page=1, page_size=50, status_group="4xx"
    )
    binds = _binds(fake_session)
    assert 400 in binds and 500 in binds


@pytest.mark.asyncio
async def test_request_logs_status_group_5xx(
    fake_session: _FakeSession,
) -> None:
    await RequestLogsRepository().list_with_filters(
        page=1, page_size=50, status_group="5xx"
    )
    binds = _binds(fake_session)
    assert 500 in binds and 600 in binds


@pytest.mark.asyncio
async def test_request_logs_ignores_unknown_status_group(
    fake_session: _FakeSession,
) -> None:
    await RequestLogsRepository().list_with_filters(
        page=1, page_size=50, status_group="9xx"
    )
    # No filters at all → no WHERE clause emitted on any statement.
    assert all("WHERE" not in sql for sql, _ in fake_session.calls)


@pytest.mark.asyncio
async def test_request_logs_method_is_uppercased(
    fake_session: _FakeSession,
) -> None:
    await RequestLogsRepository().list_with_filters(page=1, page_size=50, method="get")
    assert "GET" in _binds(fake_session)


@pytest.mark.asyncio
async def test_request_logs_stats_since_selects_minimal_columns(
    fake_session: _FakeSession,
) -> None:
    await RequestLogsRepository().stats_since(datetime(2026, 4, 1, tzinfo=timezone.utc))
    sql = fake_session.calls[0][0]
    # A compact column subset, not the full SELECT * row.
    assert "method" in sql
    assert "status_code" in sql
    assert "response_time_ms" in sql
    assert "query_params" not in sql


@pytest.mark.asyncio
async def test_request_logs_stats_since_isoformats_timestamp(
    fake_session: _FakeSession,
) -> None:
    ts = datetime(2026, 4, 1, 9, 30, tzinfo=timezone.utc)
    fake_session.rows = [("GET", 200, "/x", 42, ts)]
    out = await RequestLogsRepository().stats_since(
        datetime(2026, 4, 1, tzinfo=timezone.utc)
    )
    assert out == [
        {
            "method": "GET",
            "status_code": 200,
            "path": "/x",
            "response_time_ms": 42,
            "timestamp": ts.isoformat(),
        }
    ]
    assert type(out[0]["timestamp"]) is str


@pytest.mark.asyncio
async def test_request_logs_list_parity_types(
    fake_session: _FakeSession,
) -> None:
    uid = uuid.uuid4()
    ts = datetime(2026, 4, 1, 9, tzinfo=timezone.utc)
    fake_session.rows = [
        ApiRequestLogs(
            id=123,
            method="POST",
            path="/x",
            status_code=201,
            response_time_ms=55,
            timestamp=ts,
            user_id=uid,
        )
    ]
    fake_session.scalar_value = 1
    rows, total = await RequestLogsRepository().list_with_filters(page=1, page_size=50)
    assert total == 1
    r = rows[0]
    assert type(r["id"]) is int and r["id"] == 123
    assert type(r["timestamp"]) is str and r["timestamp"] == ts.isoformat()
    assert type(r["status_code"]) is int and r["status_code"] == 201
    assert r["user_id"] == str(uid)  # uuid → str


# ─── FrontendErrorLogsRepository ────────────────────────────────────


@pytest.mark.asyncio
async def test_frontend_error_logs_filters_by_error_type(
    fake_session: _FakeSession,
) -> None:
    await FrontendErrorLogsRepository().list_with_filters(
        page=1, page_size=50, error_type="runtime"
    )
    assert "runtime" in _binds(fake_session)


@pytest.mark.asyncio
async def test_frontend_error_logs_orders_desc_by_created_at(
    fake_session: _FakeSession,
) -> None:
    await FrontendErrorLogsRepository().list_with_filters(page=1, page_size=50)
    # The paginated page (2nd call) carries the ORDER BY.
    assert any("created_at DESC" in sql for sql, _ in fake_session.calls)


@pytest.mark.asyncio
async def test_frontend_error_logs_renamed_metadata_key_parity(
    fake_session: _FakeSession,
) -> None:
    fake_session.rows = [
        FrontendErrorLogs(
            error_type="runtime",
            created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            metadata_={"k": "v"},
        )
    ]
    fake_session.scalar_value = 1
    rows, _ = await FrontendErrorLogsRepository().list_with_filters(
        page=1, page_size=50
    )
    r = rows[0]
    # The renamed attribute metadata_ is keyed back as the DB column "metadata".
    assert "metadata" in r and "metadata_" not in r
    assert r["metadata"] == {"k": "v"}
    assert type(r["created_at"]) is str


# ─── AppLogsRepository ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_app_logs_excludes_noise_when_no_module_filter(
    fake_session: _FakeSession,
) -> None:
    await AppLogsRepository().list_with_filters(page=1, page_size=50)
    binds = _binds(fake_session)
    # Every module in NOISE_MODULES should be bound in a != filter.
    for noise in AppLogsRepository.NOISE_MODULES:
        assert noise in binds


@pytest.mark.asyncio
async def test_app_logs_skips_noise_filter_when_module_filter_set(
    fake_session: _FakeSession,
) -> None:
    await AppLogsRepository().list_with_filters(page=1, page_size=50, module="my.mod")
    binds = _binds(fake_session)
    # User explicitly asked for a module; don't double-filter noise.
    for noise in AppLogsRepository.NOISE_MODULES:
        assert noise not in binds


@pytest.mark.asyncio
async def test_app_logs_level_is_uppercased(
    fake_session: _FakeSession,
) -> None:
    await AppLogsRepository().list_with_filters(page=1, page_size=50, level="error")
    assert "ERROR" in _binds(fake_session)


@pytest.mark.asyncio
async def test_app_logs_has_exception_true_uses_is_not_null(
    fake_session: _FakeSession,
) -> None:
    await AppLogsRepository().list_with_filters(
        page=1, page_size=50, has_exception=True
    )
    assert any("exception IS NOT NULL" in sql for sql, _ in fake_session.calls)


@pytest.mark.asyncio
async def test_app_logs_has_exception_false_uses_is_null(
    fake_session: _FakeSession,
) -> None:
    await AppLogsRepository().list_with_filters(
        page=1, page_size=50, has_exception=False
    )
    calls = fake_session.calls
    assert any("exception IS NULL" in sql for sql, _ in calls)
    assert not any("exception IS NOT NULL" in sql for sql, _ in calls)
