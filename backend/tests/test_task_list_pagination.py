"""Track A1 — server-side task list filter/sort/count + active-counts.

Covers the pure search sanitizer + sort map, and (via a chainable fake query
builder) that get_tasks applies the multi-select filters / search / sort /
range and returns ``(rows, total)``, and that get_active_counts buckets by
type.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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


# ─── chainable fake query builder ──────────────────────────────────


class _FakeQuery:
    def __init__(self, rows, count):
        self._rows = rows
        self._count = count
        self.calls: list[tuple] = []

    def select(self, *a, **k):
        self.calls.append(("select", a, k))
        return self

    def eq(self, *a):
        self.calls.append(("eq", a))
        return self

    def in_(self, *a):
        self.calls.append(("in_", a))
        return self

    def or_(self, *a):
        self.calls.append(("or_", a))
        return self

    def order(self, *a, **k):
        self.calls.append(("order", a, k))
        return self

    def range(self, *a):
        self.calls.append(("range", a))
        return self

    async def execute(self):
        return SimpleNamespace(data=self._rows, count=self._count)


class _FakeClient:
    def __init__(self, q):
        self._q = q

    def table(self, name):
        self._q.calls.append(("table", name))
        return self._q


def _mgr_with(rows, count):
    q = _FakeQuery(rows, count)
    mgr = UnifiedTaskManager()
    mgr._get_client = AsyncMock(return_value=_FakeClient(q))
    return mgr, q


# ─── get_tasks ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_tasks_applies_filters_sort_range_and_returns_total() -> None:
    mgr, q = _mgr_with([{"id": "a"}], 137)
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

    kinds = [c[0] for c in q.calls]
    assert ("in_", ("task_type", ["download"])) in q.calls
    assert ("in_", ("status", ["failed"])) in q.calls
    assert any(k == "or_" for k in kinds)  # search applied
    assert ("order", ("title",), {"desc": False}) in q.calls
    assert ("range", (20, 29)) in q.calls
    # count="exact" requested on select
    assert any(c[0] == "select" and c[2].get("count") == "exact" for c in q.calls)


@pytest.mark.asyncio
async def test_get_tasks_blank_search_skips_or_filter() -> None:
    mgr, q = _mgr_with([], 0)
    await mgr.get_tasks("user-1", search="(),")  # all reserved → empty after sanitize
    assert not any(c[0] == "or_" for c in q.calls)


@pytest.mark.asyncio
async def test_get_tasks_default_sort_is_created_desc() -> None:
    mgr, q = _mgr_with([], 0)
    await mgr.get_tasks("user-1")
    assert ("order", ("created_at",), {"desc": True}) in q.calls


# ─── get_active_counts ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_active_counts_buckets_by_type() -> None:
    rows = [
        {"task_type": "download"},
        {"task_type": "download"},
        {"task_type": "transcode"},
        {"task_type": None},  # ignored
    ]
    mgr, q = _mgr_with(rows, len(rows))
    out = await mgr.get_active_counts("user-1")
    assert out == {"total": 3, "by_type": {"download": 2, "transcode": 1}}
    # only active statuses queried
    assert ("in_", ("status", ["pending", "processing"])) in q.calls
