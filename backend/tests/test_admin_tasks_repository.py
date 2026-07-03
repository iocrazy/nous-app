"""Unit tests for AdminTasksRepository (ORM 2.0, model-backed reads + writes).

Post-rollout the repository is the SQLAlchemy 2.0 implementation — reads go
through ``read_scope()`` with ``select(TaskTracking)`` statements (rows converted
to LIST_COLUMNS-shaped dicts by ``_list_row``) and cancel/retry writes go through
``write_scope()`` with a generic ``update(TaskTracking)``. These tests mock
``read_scope`` / ``write_scope`` with a fake session that captures every emitted
``(sql, binds)`` pair and returns in-memory ``TaskTracking`` instances, so the
compiled SQL shape + bind params AND the ``_list_row`` value-type sweep are
asserted WITHOUT a live database (the DSN-gated integration suite in
``tests/integration/test_admin_tasks_repository_orm.py`` exercises the real
round-trip). This keeps fast, always-run coverage of the collapsed ORM bodies.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

import app.repositories.admin.tasks_repository as mod
from app.models import TaskTracking
from app.repositories.admin.tasks_repository import AdminTasksRepository


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeResult:
    """Supports ``.scalars().all()`` (list model reads) and ``.first()`` (get)."""

    def __init__(self, scalar_rows: list[Any], first_row: Any) -> None:
        self._scalar_rows = scalar_rows
        self._first_row = first_row

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._scalar_rows)

    def first(self) -> Any:
        return self._first_row


class _FakeSession:
    """Captures execute/scalar (stmt, binds); returns configured rows / count."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.scalar_rows: list[Any] = []
        self.first_row: Any = None
        self.scalar_value: int | None = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append((str(stmt), stmt.compile().params))
        return _FakeResult(self.scalar_rows, self.first_row)

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
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def repo() -> AdminTasksRepository:
    return AdminTasksRepository()


def _task(**overrides: Any) -> TaskTracking:
    fields: dict[str, Any] = {
        "dbos_workflow_id": uuid.uuid4().hex,
        "user_id": uuid.uuid4(),
        "task_type": "download",
        "status": "completed",
        "phase": "completed",
        "title": "Test Task",
        "created_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
    }
    fields.update(overrides)
    return TaskTracking(**fields)


# ─── counts ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_total_native_int(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 42
    total = await repo.count_total()
    assert type(total) is int and total == 42
    sql, _ = fake_session.calls[-1]
    assert "count(*)" in sql and "task_tracking" in sql
    assert "WHERE" not in sql  # no filter on count_total


@pytest.mark.asyncio
async def test_count_total_zero_when_none(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = None
    assert await repo.count_total() == 0


@pytest.mark.asyncio
async def test_count_by_status_filters_status(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 7
    count = await repo.count_by_status("failed")
    assert count == 7
    sql, binds = fake_session.calls[-1]
    assert "status" in sql
    assert "failed" in binds.values()


# ─── list ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_shape_and_value_type_parity(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    uid = uuid.uuid4()
    fake_session.scalar_rows = [
        _task(
            user_id=uid,
            progress=42,
            speed=1000,
            total_bytes=500000,
            cost_cents=7,
            metadata_={"original_url": "https://example.com/x"},
        )
    ]
    fake_session.scalar_value = 1

    rows, total = await repo.list(page=1, page_size=20)
    assert total == 1
    r = rows[0]
    # strategy-C value-type sweep
    assert type(r["user_id"]) is str and r["user_id"] == str(uid)  # dict-key trap
    assert r["created_at"] == "2026-04-01T00:00:00+00:00"
    assert type(r["progress"]) is int and r["progress"] == 42
    assert type(r["total_bytes"]) is int and r["total_bytes"] == 500000
    assert type(r["cost_cents"]) is int and r["cost_cents"] == 7
    # renamed metadata_ keyed back as "metadata"
    assert r["metadata"] == {"original_url": "https://example.com/x"}
    # exact projection keys
    assert set(r.keys()) == {
        "dbos_workflow_id",
        "user_id",
        "task_type",
        "status",
        "phase",
        "title",
        "subtitle",
        "progress",
        "speed",
        "total_bytes",
        "error_msg",
        "error_code",
        "resource_id",
        "media_id",
        "cost_cents",
        "metadata",
        "created_at",
        "started_at",
        "completed_at",
    }


@pytest.mark.asyncio
async def test_list_total_zero_when_count_none(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = None
    _, total = await repo.list(page=1, page_size=20)
    assert total == 0  # (total or 0)


@pytest.mark.asyncio
async def test_list_threads_status_and_task_type(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list(page=1, page_size=20, status="processing", task_type="download")

    sql, binds = fake_session.calls[-1]  # the paginated SELECT
    assert "status" in sql and "task_type" in sql
    values = set(binds.values())
    assert "processing" in values and "download" in values


@pytest.mark.asyncio
async def test_list_search_or_clause(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list(page=1, page_size=20, search="abc")

    sql, binds = fake_session.calls[-1]
    # ilike over title/subtitle/error_msg/dbos_workflow_id + metadata->>original_url
    assert "LIKE" in sql.upper()
    assert "original_url" in binds.values()
    assert "%abc%" in binds.values()


@pytest.mark.asyncio
async def test_list_digit_search_hits_media_and_resource_id(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list(page=1, page_size=20, search="12345")

    sql, binds = fake_session.calls[-1]
    assert "media_id" in sql and "resource_id" in sql
    # equality binds carry the raw digit string
    assert "12345" in binds.values()


@pytest.mark.asyncio
async def test_list_pagination_math(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list(page=3, page_size=50)

    sql, binds = fake_session.calls[-1]  # the paginated SELECT
    # page=3, page_size=50 → OFFSET 100, LIMIT 50.
    assert 100 in binds.values()
    assert 50 in binds.values()


@pytest.mark.asyncio
async def test_list_default_sort_created_at_desc(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list(page=1, page_size=20)

    sql, _ = fake_session.calls[-1]
    assert "ORDER BY" in sql and "created_at DESC" in sql


@pytest.mark.asyncio
async def test_list_sort_asc_by_chosen_column(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list(page=1, page_size=20, sort_by="started_at", sort_desc=False)

    sql, _ = fake_session.calls[-1]
    assert "started_at ASC" in sql


# ─── get ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_projection(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    class _Row:
        dbos_workflow_id = "w1"
        status = "failed"
        task_type = "download"

    fake_session.first_row = _Row()
    got = await repo.get("w1")
    assert got == {
        "dbos_workflow_id": "w1",
        "status": "failed",
        "task_type": "download",
    }


@pytest.mark.asyncio
async def test_get_absent_returns_none(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    fake_session.first_row = None
    assert await repo.get("nope") is None


# ─── update (write path — verbatim, incl. trigger-owned columns) ──────────


@pytest.mark.asyncio
async def test_update_writes_changes_verbatim(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    # Reproduces the legacy admin retry write — incl. trigger-owned columns.
    await repo.update(
        "w1",
        {
            "status": "pending",
            "phase": "queued",
            "progress": 0,
            "error_msg": None,
            "started_at": None,
        },
    )
    sql, binds = fake_session.calls[-1]
    assert "UPDATE" in sql and "task_tracking" in sql
    # trigger-owned columns are written verbatim (no discipline "fix")
    assert "status" in sql and "phase" in sql and "progress" in sql
    assert binds.get("status") == "pending"
    assert binds.get("phase") == "queued"
    # WHERE binds the PK dbos_workflow_id
    assert "w1" in binds.values()


@pytest.mark.asyncio
async def test_update_renames_metadata_column(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    await repo.update("w1", {"metadata": {"k": "v"}})
    sql, binds = fake_session.calls[-1]
    # DB-column key "metadata" resolves to the renamed attr and compiles to the
    # real "metadata" column (not "metadata_").
    assert "metadata" in sql
    assert {"k": "v"} in binds.values()


@pytest.mark.asyncio
async def test_update_empty_changes_is_noop(
    repo: AdminTasksRepository, fake_session: _FakeSession
) -> None:
    await repo.update("w1", {})
    assert fake_session.calls == []  # no SQL emitted for an empty change set
