"""InspirationNotesRepository — ORM boundary (migrated off supabase-py).

Boundary-stub style: only the read_scope/write_scope session is faked; the
SQLAlchemy statement (table, filters, keyset, bind coercion, RETURNING, and the
two aggregate SQL functions) is built for real, so a cross-transport
regression fails here rather than in prod.

Parity: the old PostgREST dicts had BIGINT id as int, uuid user_id as str,
tags as a list, timestamps/date as ISO strings. ``_serialize`` reproduces that.
"""

from __future__ import annotations

import datetime
import uuid
from contextlib import asynccontextmanager

import pytest


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _MappingResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _RowResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, result=None):
        self.statements = []
        self.params = []
        self.added = []
        self._result = result

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        self.params.append(params)
        return self._result

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass


def _cm(session):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


# ── _serialize parity ──────────────────────────────────────────────────────


def test_serialize_uuid_date_datetime_and_natives():
    from app.repositories.inspiration_repository import _serialize

    uid = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
    out = _serialize(
        {
            "id": 9007199254740995,
            "user_id": uid,
            "tags": ["a", "b"],
            "ref_hotspot": {"k": 1},
            "note_date": datetime.date(2026, 7, 7),
            "created_at": datetime.datetime(2026, 7, 7, tzinfo=datetime.timezone.utc),
            "deleted_at": None,
        }
    )
    assert out["id"] == 9007199254740995  # bigint native
    assert out["user_id"] == "00000000-0000-0000-0000-0000000000aa"
    assert out["tags"] == ["a", "b"]  # text[] list passes through
    assert out["ref_hotspot"] == {"k": 1}  # jsonb dict passes through
    assert out["note_date"] == "2026-07-07"  # date → ISO
    assert out["created_at"] == "2026-07-07T00:00:00+00:00"
    assert out["deleted_at"] is None


# ── create ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_builds_note_with_date_object(monkeypatch):
    from app.repositories import inspiration_repository as mod
    from app.repositories.inspiration_repository import InspirationNotesRepository

    session = _FakeSession()
    monkeypatch.setattr(mod, "write_scope", _cm(session))

    await InspirationNotesRepository().create(
        user_id="u1",
        content_md="x #a",
        tags=["a"],
        note_date="2026-07-07",
        ref_hotspot=None,
    )

    obj = session.added[0]
    assert obj.user_id == "u1" and obj.tags == ["a"]
    # note_date str coerced to a real date object (asyncpg-strict DATE bind).
    assert obj.note_date == datetime.date(2026, 7, 7)
    assert getattr(obj, "ref_hotspot", None) is None  # None → DB default


# ── list ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_applies_keyset_and_filters(monkeypatch):
    from app.repositories import inspiration_repository as mod
    from app.repositories.inspiration_repository import InspirationNotesRepository

    session = _FakeSession(_ScalarResult([]))
    monkeypatch.setattr(mod, "read_scope", _cm(session))

    await InspirationNotesRepository().list(
        "u1", date="2026-07-07", tag="hooks", q="ferry", limit=20, before_id="99"
    )

    stmt = session.statements[0]
    sql = str(stmt)
    assert "FROM public.inspiration_notes" in sql
    assert "deleted_at IS NULL" in sql
    assert "note_date =" in sql
    assert "@>" in sql  # tags @> ARRAY[:tag]
    assert "like lower(" in sql.lower()  # content_md ILIKE %q% (case-insensitive)
    assert "inspiration_notes.id <" in sql  # keyset: id < before_id
    assert "ORDER BY" in sql and "DESC" in sql
    params = stmt.compile().params
    assert 99 in params.values()  # before_id str → int
    # date filter bound as a real date object.
    assert datetime.date(2026, 7, 7) in params.values()


# ── get_by_id / soft_delete ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_id_none_when_missing(monkeypatch):
    from app.repositories import inspiration_repository as mod
    from app.repositories.inspiration_repository import InspirationNotesRepository

    monkeypatch.setattr(mod, "read_scope", _cm(_FakeSession(_ScalarResult([]))))
    assert await InspirationNotesRepository().get_by_id("123") is None


@pytest.mark.asyncio
async def test_soft_delete_sets_deleted_at(monkeypatch):
    from app.repositories import inspiration_repository as mod
    from app.repositories.inspiration_repository import InspirationNotesRepository

    session = _FakeSession(_RowResult((1,)))
    monkeypatch.setattr(mod, "write_scope", _cm(session))

    ok = await InspirationNotesRepository().soft_delete("123")

    assert ok is True
    stmt = session.statements[0]
    sql = str(stmt).lower()
    assert "update public.inspiration_notes" in sql
    assert "deleted_at" in sql and "returning" in sql
    # deleted_at bound as a datetime object, not an ISO string.
    assert any(isinstance(v, datetime.datetime) for v in stmt.compile().params.values())


@pytest.mark.asyncio
async def test_soft_delete_false_when_no_row(monkeypatch):
    from app.repositories import inspiration_repository as mod
    from app.repositories.inspiration_repository import InspirationNotesRepository

    monkeypatch.setattr(mod, "write_scope", _cm(_FakeSession(_RowResult(None))))
    assert await InspirationNotesRepository().soft_delete("123") is False


# ── update ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_only_sets_provided_fields(monkeypatch):
    from app.repositories import inspiration_repository as mod
    from app.repositories.inspiration_repository import InspirationNotesRepository

    session = _FakeSession(_MappingResult([{"id": 1, "pinned": True}]))
    monkeypatch.setattr(mod, "write_scope", _cm(session))

    await InspirationNotesRepository().update("1", pinned=True)

    stmt = session.statements[0]
    sql = str(stmt).lower()
    assert "update public.inspiration_notes" in sql
    # Only the provided fields (+ updated_at) are in the SET clause; content_md
    # was not passed, so it must not be bound (it appears only in RETURNING).
    set_clause = sql.split("returning")[0]
    assert "pinned" in set_clause and "updated_at" in set_clause
    assert "content_md" not in set_clause


# ── aggregate SQL functions (replace client.rpc) ───────────────────────────


@pytest.mark.asyncio
async def test_activity_calls_sql_function(monkeypatch):
    from app.repositories import inspiration_repository as mod
    from app.repositories.inspiration_repository import InspirationNotesRepository

    row = {"day": datetime.date(2026, 7, 7), "cnt": 3}
    session = _FakeSession(_MappingResult([row]))
    monkeypatch.setattr(mod, "read_scope", _cm(session))

    rows = await InspirationNotesRepository().activity("u1", "2026-04-01", "2026-07-07")

    assert rows == [{"day": "2026-07-07", "cnt": 3}]  # date → ISO
    sql = str(session.statements[0])
    assert "inspiration_activity" in sql
    assert session.params[0] == {
        "p_user_id": "u1",
        "p_from": "2026-04-01",
        "p_to": "2026-07-07",
    }


@pytest.mark.asyncio
async def test_tag_counts_calls_sql_function(monkeypatch):
    from app.repositories import inspiration_repository as mod
    from app.repositories.inspiration_repository import InspirationNotesRepository

    session = _FakeSession(_MappingResult([{"tag": "hooks", "cnt": 5}]))
    monkeypatch.setattr(mod, "read_scope", _cm(session))

    rows = await InspirationNotesRepository().tag_counts("u1")

    assert rows == [{"tag": "hooks", "cnt": 5}]
    assert "inspiration_tag_counts" in str(session.statements[0])
    assert session.params[0] == {"p_user_id": "u1"}


def test_repo_has_no_supabase_client():
    import inspect

    from app.repositories import inspiration_repository as mod

    src = inspect.getsource(mod)
    # get_async_supabase_admin is the only way to obtain a supabase-py client;
    # its absence proves the transport is gone. (The docstring still names the
    # old ``client.rpc`` path for context, so we don't substring-check that.)
    assert "get_async_supabase_admin" not in src
    assert "await client.table" not in src
