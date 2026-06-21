import pytest

from app.repositories.generated_media_repository import (
    GeneratedMediaRepository,
    _decode_cursor,
    _encode_cursor,
)


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

    async def _fake_fetch_one(sql, params):
        return {
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

    import app.db.engine as _engine

    monkeypatch.setattr(_engine, "fetch_one", _fake_fetch_one)

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
    """When fetch_all returns limit+1 rows, items has exactly limit entries
    and next_cursor is a non-None str."""
    import app.db.engine as _engine

    async def _fake_fetch_all(sql, params):
        # Returns 3 rows (limit+1 when limit=2)
        return list(_FAKE_ROWS)

    monkeypatch.setattr(_engine, "fetch_all", _fake_fetch_all)

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
    """When fetch_all returns <= limit rows, next_cursor is None."""
    import app.db.engine as _engine

    async def _fake_fetch_all(sql, params):
        # Returns only 2 rows (exactly limit, no overflow)
        return list(_FAKE_ROWS[:2])

    monkeypatch.setattr(_engine, "fetch_all", _fake_fetch_all)

    result = await GeneratedMediaRepository().list_for_scope(42, limit=2)

    assert len(result["items"]) == 2
    assert result["next_cursor"] is None
