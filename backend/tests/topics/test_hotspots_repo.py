"""HotspotsRepository — build_rows / sanitize_search (pure) + ORM boundary.

Boundary-stub style for the DB methods: only the read_scope/write_scope
session is faked; the SQLAlchemy statement (the topic_groups LEFT JOIN, the
free-text OR, the heat accumulator, RETURNING, bind coercion) is built for
real. The subtle parity here is the embedded ``topic_groups`` nested-dict
shape — reproduced from the join, not from a PostgREST embed.
"""

from __future__ import annotations

import datetime
import decimal
from contextlib import asynccontextmanager

import pytest

from app.repositories.hotspots_repository import (
    HotspotsRepository,
    _coerce,
    _shape_joined_row,
    sanitize_search,
)
from app.services.topics.adapters.base import HotspotCandidate

# ── build_rows / sanitize_search — pure (unchanged) ─────────────────────────


def test_build_rows_sets_dedup_and_global_user():
    rows = HotspotsRepository().build_rows(
        [HotspotCandidate(title="A", url="https://x.com/a", source_label="S")],
        source_id="42",
        category="model",
    )
    r = rows[0]
    assert r["user_id"] is None and r["source_id"] == "42"
    assert r["title"] == "A" and r["category"] == "model" and r["dedup_key"]


def test_build_rows_seeds_rank_timeline_and_heat():
    rows = HotspotsRepository().build_rows(
        [HotspotCandidate(title="Top", url="u1", rank=1)], source_id="1", category=None
    )
    tl = rows[0]["rank_timeline"]
    assert len(tl) == 1 and tl[0]["rank"] == 1
    assert rows[0]["heat"] and rows[0]["heat"] > 0


def test_sanitize_search_strips_delimiters_and_escapes_wildcards():
    assert sanitize_search("  hello  world  ") == "hello world"
    assert sanitize_search("a,b(c)d") == "a b c d"
    assert sanitize_search("50%_off") == r"50\%\_off"
    assert sanitize_search(None) == ""
    assert len(sanitize_search("x" * 500)) == 100


# ── parity helpers ──────────────────────────────────────────────────────────


def test_coerce_value_types():
    assert _coerce(decimal.Decimal("3.5")) == 3.5
    dt = datetime.datetime(2026, 7, 14, tzinfo=datetime.timezone.utc)
    assert _coerce(dt) == "2026-07-14T00:00:00+00:00"
    assert _coerce(["a", "b"]) == ["a", "b"]  # text[] passthrough


def test_shape_joined_row_builds_nested_group_or_none():
    # matched group (source_count NOT NULL) → nested object
    m = {"id": 1, "title": "x", "source_count": 3, "source_labels": ["A", "B"]}
    out = _shape_joined_row(m)
    assert "source_count" not in out and "source_labels" not in out
    assert out["topic_groups"] == {"source_count": 3, "source_labels": ["A", "B"]}
    # unclustered (LEFT JOIN miss → source_count None) → None embed
    m2 = {"id": 2, "title": "y", "source_count": None, "source_labels": None}
    assert _shape_joined_row(m2)["topic_groups"] is None


# ── ORM boundary ────────────────────────────────────────────────────────────


class _MappingResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _RowsResult:
    """Row-object result (attribute access), for the heat preload + RETURNING."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return _ScalarView(self._rows)


class _ScalarView:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, result=None):
        self.statements = []
        self.params = []
        self._result = result

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        self.params.append(params)
        return self._result


def _cm(session):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.mark.asyncio
async def test_list_for_date_joins_group_and_search_or(monkeypatch):
    from app.repositories import hotspots_repository as mod

    session = _FakeSession(
        _MappingResult(
            [
                {
                    "id": 1,
                    "title": "GPT-5 launch",
                    "source_count": 2,
                    "source_labels": ["X"],
                }
            ]
        )
    )
    monkeypatch.setattr(mod, "read_scope", _cm(session))

    out = await HotspotsRepository().list_for_date(None, None, q="gpt-5")

    assert out[0]["title"] == "GPT-5 launch"
    assert out[0]["topic_groups"] == {"source_count": 2, "source_labels": ["X"]}
    sql = str(session.statements[0]).lower()
    # LEFT JOIN topic_groups for the embed
    assert "left outer join public.topic_groups" in sql
    # embedding column is NOT selected (waste-avoidance; no consumer reads it)
    assert "hotspots.embedding" not in sql
    # all four search columns OR'd with ILIKE
    for col in ("title", "content_original", "ai_summary", "source_label"):
        assert f"hotspots.{col}" in sql
    assert "like lower(" in sql  # ILIKE renders as lower() LIKE lower()


@pytest.mark.asyncio
async def test_list_for_date_no_search_skips_or(monkeypatch):
    from app.repositories import hotspots_repository as mod

    session = _FakeSession(_MappingResult([]))
    monkeypatch.setattr(mod, "read_scope", _cm(session))
    await HotspotsRepository().list_for_date(None, None, q="   ")
    assert "ilike" not in str(session.statements[0]).lower()


@pytest.mark.asyncio
async def test_list_for_date_empty_source_ids_short_circuits(monkeypatch):
    from app.repositories import hotspots_repository as mod

    def _explode():  # pragma: no cover
        raise AssertionError("read_scope must not open for empty allowlist")

    monkeypatch.setattr(mod, "read_scope", _explode)
    assert await HotspotsRepository().list_for_date(None, None, source_ids=[]) == []


@pytest.mark.asyncio
async def test_list_by_ids_short_circuits_and_filters_in(monkeypatch):
    from app.repositories import hotspots_repository as mod

    assert await HotspotsRepository().list_by_ids([]) == []

    session = _FakeSession(
        _MappingResult([{"id": 7, "source_count": None, "source_labels": None}])
    )
    monkeypatch.setattr(mod, "read_scope", _cm(session))
    out = await HotspotsRepository().list_by_ids(["7", "8"])
    assert out[0]["id"] == 7 and out[0]["topic_groups"] is None
    stmt = session.statements[0]
    assert "IN (" in str(stmt)
    # str ids coerced to int for the bigint IN bind.
    assert [7, 8] in [v for v in stmt.compile().params.values() if isinstance(v, list)]


@pytest.mark.asyncio
async def test_upsert_with_heat_appends_for_existing(monkeypatch):
    from app.repositories import hotspots_repository as mod

    read_session = _FakeSession(
        _RowsResult(
            [_Row(id=100, dedup_key="k1", rank_timeline=[{"rank": 2, "at": "t0"}])]
        )
    )
    write_session = _FakeSession()
    monkeypatch.setattr(mod, "read_scope", _cm(read_session))
    monkeypatch.setattr(mod, "write_scope", _cm(write_session))

    rows = [
        {"dedup_key": "k1", "rank_timeline": [{"rank": 1, "at": "t1"}], "heat": 0.9},
        {"dedup_key": "k2", "rank_timeline": [{"rank": 5, "at": "t1"}], "heat": 0.6},
    ]
    new_count = await HotspotsRepository().upsert_with_heat(rows)

    # k1 existed → updated (timeline appended prior+new), k2 new → inserted.
    assert new_count == 1
    # two writes: the heat UPDATE for k1, then the insert of new rows.
    assert len(write_session.statements) == 2
    update_sql = str(write_session.statements[0]).lower()
    assert "update public.hotspots" in update_sql
    insert_sql = str(write_session.statements[1]).lower()
    assert "insert into public.hotspots" in insert_sql
    assert "on conflict" in insert_sql and "do nothing" in insert_sql


@pytest.mark.asyncio
async def test_upsert_with_heat_empty_is_noop(monkeypatch):
    assert await HotspotsRepository().upsert_with_heat([]) == 0


@pytest.mark.asyncio
async def test_upsert_ignore_counts_returning(monkeypatch):
    from app.repositories import hotspots_repository as mod

    session = _FakeSession(_RowsResult([(1,), (2,)]))
    monkeypatch.setattr(mod, "write_scope", _cm(session))

    rows = HotspotsRepository().build_rows(
        [HotspotCandidate(title="A", url="u1"), HotspotCandidate(title="B", url="u2")],
        source_id="9",
        category=None,
    )
    n = await HotspotsRepository().upsert_ignore(rows)

    assert n == 2
    sql = str(session.statements[0]).lower()
    assert "insert into public.hotspots" in sql
    assert "on conflict" in sql and "do nothing" in sql and "returning" in sql


@pytest.mark.asyncio
async def test_list_unembedded_filters_null_embedding(monkeypatch):
    from app.repositories import hotspots_repository as mod

    session = _FakeSession(_MappingResult([{"id": 1, "title": "A"}]))
    monkeypatch.setattr(mod, "read_scope", _cm(session))
    out = await HotspotsRepository().list_unembedded(limit=10)
    assert out[0]["id"] == 1
    assert "embedding IS NULL" in str(session.statements[0])


@pytest.mark.asyncio
async def test_patch_embedding_uses_vector_cast_literal(monkeypatch):
    from app.repositories import hotspots_repository as mod

    session = _FakeSession()
    monkeypatch.setattr(mod, "write_scope", _cm(session))
    await HotspotsRepository().patch_embedding("42", [0.5, -1.0, 2.0])
    sql = str(session.statements[0])
    assert "CAST(:emb AS vector)" in sql
    assert session.params[0]["emb"] == "[0.5,-1.0,2.0]"
    assert session.params[0]["id"] == 42  # str→int


@pytest.mark.asyncio
async def test_patch_embedding_empty_is_noop(monkeypatch):
    from app.repositories import hotspots_repository as mod

    def _explode():  # pragma: no cover
        raise AssertionError("write_scope must not open for empty vector")

    monkeypatch.setattr(mod, "write_scope", _explode)
    await HotspotsRepository().patch_embedding("1", [])


def test_repo_has_no_supabase_client():
    import inspect

    from app.repositories import hotspots_repository as mod

    src = inspect.getsource(mod)
    assert "get_async_supabase_admin" not in src
    assert "client.table" not in src
