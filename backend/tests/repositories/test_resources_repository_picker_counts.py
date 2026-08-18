"""``count_accessible_by_kind_for_user`` — the query behind the tab badges.

Compiled-SQL assertions (Postgres dialect, literal binds), matching this
directory's established capture-the-statement pattern: no DB, but the shape
of the statement is exactly what the fix is about — an aggregate over the
whole visible set, with no ``kinds`` narrowing and no ``LIMIT``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.repositories.resources_repository import ResourcesRepository
from app.services.ai._mime_kind import kind_from_mime

pytestmark = pytest.mark.asyncio


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _FakeMappingsResult(self._rows)


class _CapturingSession:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.captured_sql: str | None = None

    async def execute(self, stmt):
        from sqlalchemy.dialects import postgresql

        self.captured_sql = str(
            stmt.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        return _FakeResult(self._rows)


def _capture(monkeypatch, rows=None) -> _CapturingSession:
    import app.repositories.resources_repository as repo_module

    session = _CapturingSession(rows)

    @asynccontextmanager
    async def _read_scope():
        yield session

    @asynccontextmanager
    async def _system_request_scope(reason: str):
        yield None

    monkeypatch.setattr(repo_module, "read_scope", _read_scope)
    monkeypatch.setattr(repo_module, "system_request_scope", _system_request_scope)
    return session


# ── the two things that made the old badge describe the page ────────


async def test_the_count_query_has_no_limit(monkeypatch):
    """A badge capped at ``limit`` can only ever say "50 or fewer"."""
    session = _capture(monkeypatch)
    await ResourcesRepository().count_accessible_by_kind_for_user(user_id="u1")
    assert "LIMIT" not in (session.captured_sql or "").upper()


async def test_groups_by_kind_and_counts_distinct_resources(monkeypatch):
    session = _capture(monkeypatch)
    await ResourcesRepository().count_accessible_by_kind_for_user(user_id="u1")
    sql = (session.captured_sql or "").lower()
    assert "group by" in sql
    # A resource with two resource_items rows (production has one such row)
    # is still ONE thing to reference — count(*) would double it.
    assert "count(distinct" in sql


# ── the visibility predicate must be the SAME one the rows use ──────


async def test_counts_are_scoped_to_team_membership(monkeypatch):
    """If this drifted from the row query, the badge would advertise
    resources the list refuses to show (or hide ones it does)."""
    session = _capture(monkeypatch)
    await ResourcesRepository().count_accessible_by_kind_for_user(user_id="user-1")
    sql = (session.captured_sql or "").lower()
    assert "team_members" in sql
    assert "'user-1'" in sql
    assert "is_trashed" in sql


async def test_counts_honour_the_search_text(monkeypatch):
    """The badge answers "how many match what I typed", not "how many exist"."""
    session = _capture(monkeypatch)
    await ResourcesRepository().count_accessible_by_kind_for_user(
        user_id="u1", q="story"
    )
    sql = (session.captured_sql or "").lower()
    assert "ilike" in sql
    assert "%story%" in sql


async def test_counts_honour_scope_team_id(monkeypatch):
    session = _capture(monkeypatch)
    await ResourcesRepository().count_accessible_by_kind_for_user(
        user_id="u1", scope_team_id="900123"
    )
    sql = (session.captured_sql or "").lower()
    assert "'900123'" in sql
    assert "kind = 'personal'" in sql


# ── the kind ladder is the SQL twin of kind_from_mime ───────────────


async def test_kind_ladder_mirrors_kind_from_mime(monkeypatch):
    """Same rungs, same order, same catch-all.

    The response's per-row ``kind`` comes from ``kind_from_mime`` while the
    badge comes from this CASE; if they disagreed, a row would be badged
    under a tab that then refuses to list it. That is not hypothetical —
    before the fix the ``doc`` filter matched only ``text/`` and
    ``application/json``, so the three ``application/{zip,octet-stream,
    x-mediahub-gallery}`` resources in production showed as doc rows under
    "All" and were unreachable under Doc.
    """
    session = _capture(monkeypatch)
    await ResourcesRepository().count_accessible_by_kind_for_user(user_id="u1")
    sql = session.captured_sql or ""
    for pattern, label in (
        ("^video/", "video"),
        ("^image/", "image"),
        ("^audio/", "audio"),
        ("^application/pdf$", "pdf"),
    ):
        assert f"'{pattern}') THEN '{label}'" in sql
    assert "ELSE 'doc'" in sql
    # And the Python side really does treat doc as the catch-all.
    assert kind_from_mime("application/zip") == "doc"
    assert kind_from_mime(None) == "doc"


# ── aggregation into the response contract ──────────────────────────


async def test_missing_kinds_are_zero_filled(monkeypatch):
    """A kind with no rows has to render "0", not vanish from the tab strip."""
    _capture(monkeypatch, [{"kind": "video", "n": 3}])
    counts = await ResourcesRepository().count_accessible_by_kind_for_user(user_id="u1")
    assert counts == {"all": 3, "video": 3, "image": 0, "audio": 0, "pdf": 0, "doc": 0}


async def test_all_is_the_sum_of_every_kind(monkeypatch):
    _capture(
        monkeypatch,
        [
            {"kind": "video", "n": 950},
            {"kind": "audio", "n": 293},
            {"kind": "image", "n": 159},
            {"kind": "doc", "n": 19},
        ],
    )
    counts = await ResourcesRepository().count_accessible_by_kind_for_user(user_id="u1")
    assert counts["all"] == 1421
    assert counts["all"] == sum(v for k, v in counts.items() if k != "all")


async def test_empty_library_reports_zeros_not_an_empty_dict(monkeypatch):
    _capture(monkeypatch, [])
    counts = await ResourcesRepository().count_accessible_by_kind_for_user(user_id="u1")
    assert counts == {"all": 0, "video": 0, "image": 0, "audio": 0, "pdf": 0, "doc": 0}


# ── the row query keeps its own contract ────────────────────────────


async def test_row_query_still_filters_by_kind(monkeypatch):
    """The filter moved onto the shared CASE ladder; it must still filter."""
    session = _capture(monkeypatch)
    await ResourcesRepository().list_accessible_for_user(user_id="u1", kinds=["video"])
    sql = session.captured_sql or ""
    assert "^video/" in sql
    assert "IN ('video')" in sql


async def test_row_query_treats_an_unknown_kind_as_no_filter(monkeypatch):
    """Documented contract: unknown kinds are ignored (wildcard), which is
    what the old ``kinds_re = "."`` fallback did. A literal IN ('bogus')
    would silently return nothing instead."""
    session = _capture(monkeypatch)
    await ResourcesRepository().list_accessible_for_user(user_id="u1", kinds=["bogus"])
    assert "'bogus'" not in (session.captured_sql or "")
