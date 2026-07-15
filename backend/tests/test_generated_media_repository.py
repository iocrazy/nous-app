from contextlib import asynccontextmanager

import pytest

from app.repositories.generated_media_repository import (
    GeneratedMediaRepository,
    _decode_cursor,
    _encode_cursor,
)

# ── Session-boundary fakes (the repo runs on read_scope/write_scope now) ────


class _Result:
    def __init__(self, *, rows=None, row=None, rowcount=0, scalar=None):
        self._rows = rows or []
        self._row = row
        self.rowcount = rowcount
        self._scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._row

    def scalar(self):
        return self._scalar


class _QueueSession:
    """Pops one canned result per execute() call, capturing statements."""

    def __init__(self, results):
        self._results = list(results)
        self.statements: list = []

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return self._results.pop(0)


def _patch_scopes(monkeypatch, session):
    from app.repositories import generated_media_repository as mod

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(mod, "read_scope", _scope)
    monkeypatch.setattr(mod, "write_scope", _scope)
    return session


def test_cursor_roundtrip():
    c = _encode_cursor("2026-06-21T00:00:00+00:00", 123)
    ts, gid = _decode_cursor(c)
    assert ts == "2026-06-21T00:00:00+00:00" and gid == 123


def test_decode_bad_cursor_returns_none():
    assert _decode_cursor("garbage") is None
    assert _decode_cursor(None) is None


# ---------------------------------------------------------------------------
# C1 — bigint ids must be stringified (CLAUDE.md JS precision trap)
# ---------------------------------------------------------------------------

LARGE_ID = 318252341326512  # > 2^53 — would lose precision as JS number


@pytest.mark.asyncio
async def test_get_stringifies_bigint_id(monkeypatch):
    """get() must return id and scope_id as str, not raw int."""
    fake_row = {
        "id": LARGE_ID,
        "scope_id": 99,
        "creator_id": "00000000-0000-0000-0000-000000000001",
        "agent_id": None,
        "canvas_id": None,
        "parent_resource_id": None,
        "promoted_resource_id": None,
        "media_kind": "image",
        "mime": "image/png",
        "file_path": "generations/x.png",
        "file_size_bytes": 1024,
        "origin_kind": "canvas_run",
        "origin_run_id": None,
        "node_id": None,
        "prompt": None,
        "model": None,
        "provider": None,
        "params": None,
        "cost_cents": None,
        "derivation_kind": None,
        "created_at": "2026-06-21T00:00:00+00:00",
    }
    _patch_scopes(monkeypatch, _QueueSession([_Result(row=fake_row)]))

    row = await GeneratedMediaRepository().get(LARGE_ID, 99)
    assert isinstance(row["id"], str), "id must be a str"
    assert row["id"] == str(LARGE_ID)
    assert isinstance(row["scope_id"], str), "scope_id must be a str"
    assert row["scope_id"] == "99"
    # UUID columns: non-None value must also be str
    assert isinstance(row["creator_id"], str)
    # None values for optional bigint cols must stay None
    assert row["canvas_id"] is None
    assert row["agent_id"] is None


# ---------------------------------------------------------------------------
# M7 — list_for_scope keyset slicing
# ---------------------------------------------------------------------------

_FAKE_ROWS = [
    {
        "id": 318252341326512,
        "created_at": "2026-06-21T00:00:02+00:00",
        "scope_id": 42,
        "creator_id": None,
        "agent_id": None,
        "canvas_id": None,
        "parent_resource_id": None,
        "promoted_resource_id": None,
        "media_kind": "image",
        "mime": "image/png",
        "file_path": "a.png",
        "file_size_bytes": 1,
        "origin_kind": "canvas_run",
        "origin_run_id": None,
        "node_id": None,
        "prompt": None,
        "model": None,
        "provider": None,
        "params": None,
        "cost_cents": None,
        "derivation_kind": None,
    },
    {
        "id": 318252341326511,
        "created_at": "2026-06-21T00:00:01+00:00",
        "scope_id": 42,
        "creator_id": None,
        "agent_id": None,
        "canvas_id": None,
        "parent_resource_id": None,
        "promoted_resource_id": None,
        "media_kind": "image",
        "mime": "image/png",
        "file_path": "b.png",
        "file_size_bytes": 1,
        "origin_kind": "canvas_run",
        "origin_run_id": None,
        "node_id": None,
        "prompt": None,
        "model": None,
        "provider": None,
        "params": None,
        "cost_cents": None,
        "derivation_kind": None,
    },
    {
        "id": 318252341326510,
        "created_at": "2026-06-21T00:00:00+00:00",
        "scope_id": 42,
        "creator_id": None,
        "agent_id": None,
        "canvas_id": None,
        "parent_resource_id": None,
        "promoted_resource_id": None,
        "media_kind": "image",
        "mime": "image/png",
        "file_path": "c.png",
        "file_size_bytes": 1,
        "origin_kind": "canvas_run",
        "origin_run_id": None,
        "node_id": None,
        "prompt": None,
        "model": None,
        "provider": None,
        "params": None,
        "cost_cents": None,
        "derivation_kind": None,
    },
]


@pytest.mark.asyncio
async def test_list_for_scope_returns_next_cursor_when_more_rows(monkeypatch):
    """When the query returns limit+1 rows, items has exactly limit entries
    and next_cursor is a non-None str."""
    _patch_scopes(monkeypatch, _QueueSession([_Result(rows=list(_FAKE_ROWS))]))

    result = await GeneratedMediaRepository().list_for_scope(42, limit=2)

    assert len(result["items"]) == 2, "should trim to limit"
    assert (
        result["next_cursor"] is not None
    ), "next_cursor must be set when more rows exist"
    assert isinstance(result["next_cursor"], str)
    # ids in returned items must be strings (C1 guard)
    assert isinstance(result["items"][0]["id"], str)
    assert isinstance(result["items"][1]["id"], str)


@pytest.mark.asyncio
async def test_list_for_scope_no_next_cursor_when_last_page(monkeypatch):
    """When the query returns <= limit rows, next_cursor is None."""
    _patch_scopes(monkeypatch, _QueueSession([_Result(rows=list(_FAKE_ROWS[:2]))]))

    result = await GeneratedMediaRepository().list_for_scope(42, limit=2)

    assert len(result["items"]) == 2
    assert result["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_for_scope_entity_filter_hits_params_jsonb(monkeypatch):
    """CC5: entity_kind/entity_id filter on the params jsonb columns."""
    session = _patch_scopes(
        monkeypatch, _QueueSession([_Result(rows=list(_FAKE_ROWS[:1]))])
    )

    result = await GeneratedMediaRepository().list_for_scope(
        42, entity_kind="character", entity_id="123456789", limit=12
    )

    assert len(result["items"]) == 1
    stmt = session.statements[0]
    sql = str(stmt)
    # jsonb ->> text extraction on params, with the key + value as binds.
    assert sql.count("->>") == 2
    params = stmt.compile().params
    assert "entity_kind" in params.values() and "character" in params.values()
    assert "entity_id" in params.values() and "123456789" in params.values()


@pytest.mark.asyncio
async def test_list_for_scope_no_entity_filter_by_default(monkeypatch):
    """Without entity args the SQL must not touch the params jsonb."""
    session = _patch_scopes(
        monkeypatch, _QueueSession([_Result(rows=list(_FAKE_ROWS[:1]))])
    )

    await GeneratedMediaRepository().list_for_scope(42, limit=2)
    assert "->>" not in str(session.statements[0])


# ---------------------------------------------------------------------------
# Object-store cleanup on delete (Phase 1d) — dedup refcount guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_filesystem_row_does_not_touch_object_store(monkeypatch):
    """A legacy filesystem row deletes without any object-store call."""
    import app.repositories.generated_media_repository as mod

    # Call order: read file_path → write DELETE. Filesystem path → no refcount.
    _patch_scopes(
        monkeypatch,
        _QueueSession(
            [
                _Result(row={"file_path": "teams/1/chat/2026/07/05/x/a.png"}),
                _Result(rowcount=1),
            ]
        ),
    )
    remove_called = {"n": 0}

    class _Store:
        def __init__(self, *a):
            pass

        async def remove(self, key):
            remove_called["n"] += 1

    monkeypatch.setattr(mod, "ObjectStore", _Store)
    assert await GeneratedMediaRepository().delete(1, 1) is True
    assert remove_called["n"] == 0


@pytest.mark.asyncio
async def test_delete_object_store_row_removes_when_unreferenced(monkeypatch):
    import app.repositories.generated_media_repository as mod

    # read file_path → write DELETE → read refcount (0 → remove).
    _patch_scopes(
        monkeypatch,
        _QueueSession(
            [
                _Result(row={"file_path": "sb://chat-media/t1/ab/cd/h.png"}),
                _Result(rowcount=1),
                _Result(scalar=0),
            ]
        ),
    )
    removed = {"key": None}

    class _Store:
        def __init__(self, bucket):
            self.bucket = bucket

        async def remove(self, key):
            removed["key"] = key

    monkeypatch.setattr(mod, "ObjectStore", _Store)
    assert await GeneratedMediaRepository().delete(1, 1) is True
    assert removed["key"] == "t1/ab/cd/h.png"


@pytest.mark.asyncio
async def test_delete_object_store_row_keeps_object_when_still_referenced(monkeypatch):
    """Dedup: a sibling row shares the object → must NOT remove it."""
    import app.repositories.generated_media_repository as mod

    _patch_scopes(
        monkeypatch,
        _QueueSession(
            [
                _Result(row={"file_path": "sb://chat-media/t1/ab/cd/h.png"}),
                _Result(rowcount=1),
                _Result(scalar=1),  # sibling still references the object
            ]
        ),
    )
    remove_called = {"n": 0}

    class _Store:
        def __init__(self, *a):
            pass

        async def remove(self, key):
            remove_called["n"] += 1

    monkeypatch.setattr(mod, "ObjectStore", _Store)
    assert await GeneratedMediaRepository().delete(1, 1) is True
    assert remove_called["n"] == 0  # kept — still referenced


@pytest.mark.asyncio
async def test_delete_object_cleanup_failure_still_succeeds(monkeypatch):
    """A remove() failure must NOT fail the delete (object leak is acceptable)."""
    import app.repositories.generated_media_repository as mod

    _patch_scopes(
        monkeypatch,
        _QueueSession(
            [
                _Result(row={"file_path": "sb://chat-media/t1/ab/cd/h.png"}),
                _Result(rowcount=1),
                _Result(scalar=0),
            ]
        ),
    )

    class _Store:
        def __init__(self, *a):
            pass

        async def remove(self, key):
            raise RuntimeError("storage down")

    monkeypatch.setattr(mod, "ObjectStore", _Store)
    # still True despite cleanup failure
    assert await GeneratedMediaRepository().delete(1, 1) is True


@pytest.mark.asyncio
async def test_delete_missing_row_returns_false(monkeypatch):
    _patch_scopes(
        monkeypatch,
        _QueueSession([_Result(row=None), _Result(rowcount=0)]),
    )
    assert await GeneratedMediaRepository().delete(1, 1) is False
