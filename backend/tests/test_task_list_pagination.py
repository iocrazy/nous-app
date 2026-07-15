"""Track A1 — server-side task list filter/sort/count + active-counts.

Covers the pure search sanitizer + sort map, and (via a fake ORM session
patched onto ``app.db.session.read_scope``) that get_tasks applies the
multi-select filters / search / sort / offset+limit and returns
``(rows, total)``, and that get_active_counts / get_matching_task_ids
build the expected WHERE clauses.

Ported from a chainable PostgREST-style fake query builder to the ORM
session boundary: these methods now open ``read_scope()`` (imported
locally inside each method from ``app.db.session``) and execute
SQLAlchemy Core ``select()`` statements instead of `.table().eq().in_()`
chains. Assertions on filter/sort/limit/offset are re-expressed as
substring checks against the compiled (literal-bound) SQL text of each
recorded statement.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.dialects import postgresql

from app.services.infra.unified_task_manager import (
    _TASK_SORT_MAP,
    VALID_TASK_STATUSES,
    VALID_TASK_TYPES,
    UnifiedTaskManager,
    _sanitize_search,
)

# ─── pure helpers ──────────────────────────────────────────────────


def test_sanitize_search_strips_postgrest_reserved() -> None:
    # commas / parens / star / colon / quotes / backslash → spaces, trimmed
    assert _sanitize_search("a,b(c)*d") == "a b c  d"
    assert _sanitize_search('  he"llo:  ') == "he llo"
    assert _sanitize_search(",,,") == ""


def test_sort_map_covers_the_four_sorts() -> None:
    assert set(_TASK_SORT_MAP) == {
        "created_desc",
        "created_asc",
        "updated_desc",
        "title_asc",
    }
    assert _TASK_SORT_MAP["created_desc"] == ("created_at", True)
    assert _TASK_SORT_MAP["title_asc"] == ("title", False)


def test_valid_enum_sets() -> None:
    assert "download" in VALID_TASK_TYPES
    assert "failed" in VALID_TASK_STATUSES
    assert "bogus" not in VALID_TASK_TYPES


# ─── fake ORM session (read_scope stand-in) ─────────────────────────


class _Result:
    def __init__(self, value):
        self._v = value

    def scalar(self):
        return self._v

    def mappings(self):
        return self

    def scalars(self):
        return self

    def first(self):
        return self._v

    def all(self):
        return self._v


class _Session:
    """Returns one queued result per execute() call, in order, and
    records every executed statement so tests can assert on the
    compiled WHERE / ORDER BY / LIMIT / OFFSET clauses."""

    def __init__(self, results):
        self._results = list(results)
        self.statements: list = []

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return _Result(self._results.pop(0) if self._results else None)


@pytest.fixture
def patch_scopes(monkeypatch):
    def _install(results) -> _Session:
        session = _Session(results)

        @asynccontextmanager
        async def _scope():
            yield session

        import app.db.session as dbs

        monkeypatch.setattr(dbs, "read_scope", _scope)
        monkeypatch.setattr(dbs, "write_scope", _scope)
        return session

    return _install


def _sql(stmt) -> str:
    """Compile a statement to literal SQL text for substring assertions."""
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


# ─── get_tasks ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_tasks_applies_filters_sort_range_and_returns_total(
    patch_scopes,
) -> None:
    # execute() order inside get_tasks: COUNT query, then the paged SELECT.
    session = patch_scopes([137, [{"id": "a"}]])
    mgr = UnifiedTaskManager()

    rows, total = await mgr.get_tasks(
        "user-1",
        types=["download"],
        statuses=["failed"],
        search="boom",
        sort="title_asc",
        limit=10,
        offset=20,
    )
    assert rows == [{"id": "a"}]
    assert total == 137

    assert len(session.statements) == 2
    count_sql = _sql(session.statements[0])
    rows_sql = _sql(session.statements[1])

    for sql in (count_sql, rows_sql):
        assert "task_tracking.user_id = 'user-1'" in sql
        assert "task_tracking.task_type IN ('download')" in sql
        assert "task_tracking.status IN ('failed')" in sql
        # search applied → OR'd ILIKE across title/subtitle/error_msg
        assert sql.count("ILIKE") == 3
        assert "boom" in sql

    assert "ORDER BY public.task_tracking.title ASC" in rows_sql
    assert "LIMIT 10 OFFSET 20" in rows_sql


@pytest.mark.asyncio
async def test_get_tasks_blank_search_skips_or_filter(patch_scopes) -> None:
    session = patch_scopes([0, []])
    mgr = UnifiedTaskManager()
    await mgr.get_tasks("user-1", search="(),")  # all reserved → empty after sanitize
    assert "ILIKE" not in _sql(session.statements[1])


@pytest.mark.asyncio
async def test_get_tasks_default_sort_is_created_desc(patch_scopes) -> None:
    session = patch_scopes([0, []])
    mgr = UnifiedTaskManager()
    await mgr.get_tasks("user-1")
    assert "ORDER BY public.task_tracking.created_at DESC" in _sql(
        session.statements[1]
    )


# ─── get_matching_task_ids ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_matching_task_ids_terminal_only_and_capped(patch_scopes) -> None:
    # limit+1 rows back → capped; ids sliced to limit
    rows = [f"w{i}" for i in range(3)]
    session = patch_scopes([rows])
    mgr = UnifiedTaskManager()
    ids, capped = await mgr.get_matching_task_ids(
        "u", statuses=["failed", "processing"], limit=2
    )
    # requested statuses intersected with terminal → only 'failed' queried
    sql = _sql(session.statements[0])
    assert "task_tracking.status IN ('failed')" in sql
    assert ids == ["w0", "w1"]  # sliced to limit
    assert capped is True


@pytest.mark.asyncio
async def test_get_matching_task_ids_empty_when_no_terminal_status(
    patch_scopes,
) -> None:
    session = patch_scopes([])
    mgr = UnifiedTaskManager()
    ids, capped = await mgr.get_matching_task_ids("u", statuses=["processing"])
    assert ids == [] and capped is False
    # short-circuits before querying
    assert session.statements == []


# ─── get_active_counts ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_active_counts_buckets_by_type(patch_scopes) -> None:
    task_types = ["download", "download", "transcode", None]
    session = patch_scopes([task_types])
    mgr = UnifiedTaskManager()
    out = await mgr.get_active_counts("user-1")
    assert out == {"total": 3, "by_type": {"download": 2, "transcode": 1}}
    # only active statuses queried
    sql = _sql(session.statements[0])
    assert "task_tracking.status IN ('pending', 'processing')" in sql
