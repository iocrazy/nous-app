"""Unit tests for the project renders listing (PR-10a, spec G14).

GET /projects/{project_id}/renders — project-wide shot renders (images +
videos), derived by joining generated_media.node_id (= str(shot_id) for
shot-origin rows) back through script_shots -> script_scenes ->
script_projects. Covers GeneratedMediaRepository.list_for_project: SQL
shape (join chain, kinds filter, episode filter present/absent), cursor
encode/decode round-trip, and bigint-id stringification — all via a
monkeypatched db_engine.fetch_all (no live DB).
"""

from __future__ import annotations

import pytest

from app.repositories.generated_media_repository import (
    GeneratedMediaRepository,
    _decode_cursor,
    _encode_cursor,
)

pytestmark = pytest.mark.unit


def _row(gen_id=1, created_at="2026-07-01T00:00:00+00:00", **overrides):
    base = {
        "id": gen_id,
        "scope_id": 10,
        "creator_id": "11111111-1111-1111-1111-111111111111",
        "media_kind": "image",
        "mime": "image/png",
        "file_path": "objstore://x",
        "file_size_bytes": 100,
        "origin_kind": "shot_generate",
        "origin_run_id": None,
        "agent_id": None,
        "canvas_id": None,
        "node_id": "999",
        "prompt": None,
        "model": None,
        "provider": None,
        "params": {},
        "cost_cents": None,
        "parent_resource_id": None,
        "derivation_kind": None,
        "promoted_resource_id": None,
        "conversation_id": None,
        "created_at": created_at,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# list_for_project — SQL shape + params
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_passes_project_id_and_shot_kinds(monkeypatch):
    import app.repositories.generated_media_repository as mod

    captured: dict = {}

    async def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(mod.db_engine, "fetch_all", fake_fetch_all)

    await GeneratedMediaRepository().list_for_project("42")

    assert captured["params"]["project_id"] == 42
    assert "episode_id" not in captured["params"]
    assert "script_projects" in captured["sql"]
    assert "script_scenes" in captured["sql"]
    assert "script_shots" in captured["sql"]
    assert "sp.project_id = :project_id" in captured["sql"]
    assert "sp.status != 'deleted'" in captured["sql"]
    assert "'shot_generate'" in captured["sql"]
    assert "'shot_video'" in captured["sql"]
    assert "gm.node_id = CAST(ss.id AS TEXT)" in captured["sql"]


@pytest.mark.asyncio
async def test_episode_filter_present_when_given(monkeypatch):
    import app.repositories.generated_media_repository as mod

    captured: dict = {}

    async def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(mod.db_engine, "fetch_all", fake_fetch_all)

    await GeneratedMediaRepository().list_for_project("42", episode_id="7")

    assert captured["params"]["episode_id"] == 7
    assert "sp.episode_id = :episode_id" in captured["sql"]


@pytest.mark.asyncio
async def test_episode_filter_absent_by_default(monkeypatch):
    import app.repositories.generated_media_repository as mod

    captured: dict = {}

    async def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(mod.db_engine, "fetch_all", fake_fetch_all)

    await GeneratedMediaRepository().list_for_project("42")

    assert "episode_id" not in captured["params"]
    assert "sp.episode_id = :episode_id" not in captured["sql"]


@pytest.mark.asyncio
async def test_cursor_decoded_and_added_to_where(monkeypatch):
    import app.repositories.generated_media_repository as mod

    captured: dict = {}
    cursor = _encode_cursor("2026-06-01T00:00:00+00:00", 555)

    async def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(mod.db_engine, "fetch_all", fake_fetch_all)

    await GeneratedMediaRepository().list_for_project("42", cursor=cursor)

    assert captured["params"]["c_ts"] == "2026-06-01T00:00:00+00:00"
    assert captured["params"]["c_id"] == 555
    assert (
        "(gm.created_at, gm.id) < (CAST(:c_ts AS timestamptz), :c_id)"
        in captured["sql"]
    )


@pytest.mark.asyncio
async def test_invalid_cursor_ignored(monkeypatch):
    import app.repositories.generated_media_repository as mod

    captured: dict = {}

    async def fake_fetch_all(sql, params):
        captured["params"] = params
        return []

    monkeypatch.setattr(mod.db_engine, "fetch_all", fake_fetch_all)

    await GeneratedMediaRepository().list_for_project("42", cursor="not-valid-base64!!")

    assert "c_ts" not in captured["params"]
    assert "c_id" not in captured["params"]


# --------------------------------------------------------------------------- #
# list_for_project — pagination + serialization
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_limit_plus_one_peek_produces_next_cursor(monkeypatch):
    import app.repositories.generated_media_repository as mod

    rows = [
        _row(gen_id=i, created_at=f"2026-07-01T00:00:0{i}+00:00")
        for i in range(3, 0, -1)
    ]

    async def fake_fetch_all(sql, params):
        assert params["limit"] == 3  # limit(2) + 1 peek row
        return rows

    monkeypatch.setattr(mod.db_engine, "fetch_all", fake_fetch_all)

    out = await GeneratedMediaRepository().list_for_project("42", limit=2)

    assert len(out["items"]) == 2
    assert out["next_cursor"] is not None
    decoded = _decode_cursor(out["next_cursor"])
    assert decoded == ("2026-07-01T00:00:02+00:00", 2)


@pytest.mark.asyncio
async def test_no_next_cursor_when_under_limit(monkeypatch):
    import app.repositories.generated_media_repository as mod

    async def fake_fetch_all(sql, params):
        return [_row(gen_id=1)]

    monkeypatch.setattr(mod.db_engine, "fetch_all", fake_fetch_all)

    out = await GeneratedMediaRepository().list_for_project("42", limit=50)

    assert len(out["items"]) == 1
    assert out["next_cursor"] is None


@pytest.mark.asyncio
async def test_bigint_and_uuid_columns_stringified(monkeypatch):
    import app.repositories.generated_media_repository as mod

    async def fake_fetch_all(sql, params):
        return [
            _row(
                gen_id=9007199254740993,  # > 2^53, would lose precision as a JS number
                scope_id=123,
                canvas_id=456,
                parent_resource_id=789,
                promoted_resource_id=101112,
                conversation_id=131415,
                creator_id="22222222-2222-2222-2222-222222222222",
                agent_id="33333333-3333-3333-3333-333333333333",
            )
        ]

    monkeypatch.setattr(mod.db_engine, "fetch_all", fake_fetch_all)

    out = await GeneratedMediaRepository().list_for_project("42")
    item = out["items"][0]

    assert item["id"] == "9007199254740993"
    assert isinstance(item["id"], str)
    assert item["scope_id"] == "123"
    assert item["canvas_id"] == "456"
    assert item["parent_resource_id"] == "789"
    assert item["promoted_resource_id"] == "101112"
    assert item["conversation_id"] == "131415"
    assert item["creator_id"] == "22222222-2222-2222-2222-222222222222"
    assert item["agent_id"] == "33333333-3333-3333-3333-333333333333"


@pytest.mark.asyncio
async def test_empty_result_when_none_returned(monkeypatch):
    import app.repositories.generated_media_repository as mod

    async def fake_fetch_all(sql, params):
        return None

    monkeypatch.setattr(mod.db_engine, "fetch_all", fake_fetch_all)

    out = await GeneratedMediaRepository().list_for_project("42")
    assert out == {"items": [], "next_cursor": None}


@pytest.mark.asyncio
async def test_limit_clamped_to_max_100(monkeypatch):
    import app.repositories.generated_media_repository as mod

    async def fake_fetch_all(sql, params):
        assert params["limit"] == 101  # clamp(500) -> 100, +1 peek
        return []

    monkeypatch.setattr(mod.db_engine, "fetch_all", fake_fetch_all)

    await GeneratedMediaRepository().list_for_project("42", limit=500)
