"""Unit tests for the project renders listing (PR-10a, spec G14).

GET /projects/{project_id}/renders — project-wide shot renders (images +
videos), derived by joining generated_media.node_id (= str(shot_id) for
shot-origin rows) back through script_shots -> script_scenes ->
script_projects. Covers GeneratedMediaRepository.list_for_project: statement
shape (join chain, kinds filter, episode filter present/absent), cursor
encode/decode round-trip, and bigint-id stringification — all via a stubbed
read_scope() session (the repo runs on the ORM session scopes now; the
SQLAlchemy statement is built for real).
"""

from __future__ import annotations

import datetime
from contextlib import asynccontextmanager

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


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self.statements: list = []

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return _Result(list(self.rows))


def _patch(monkeypatch, session):
    from app.repositories import generated_media_repository as mod

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(mod, "read_scope", _scope)
    return session


def _sql_and_params(session):
    stmt = session.statements[0]
    return str(stmt), stmt.compile().params


# --------------------------------------------------------------------------- #
# list_for_project — statement shape + binds
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_passes_project_id_and_shot_kinds(monkeypatch):
    session = _patch(monkeypatch, _Session())

    await GeneratedMediaRepository().list_for_project("42")

    sql, params = _sql_and_params(session)
    assert 42 in params.values()
    assert "script_projects" in sql
    assert "script_scenes" in sql
    assert "script_shots" in sql
    assert "status !=" in sql  # sp.status != 'deleted'
    # the join bridges node_id (text) to shot id via a CAST
    assert "CAST(public.script_shots.id AS TEXT)" in sql
    # shot origin kinds travel as an IN bind
    kind_lists = [v for v in params.values() if isinstance(v, (list, tuple))]
    assert any(set(v) == {"shot_generate", "shot_video"} for v in kind_lists)


@pytest.mark.asyncio
async def test_episode_filter_present_when_given(monkeypatch):
    session = _patch(monkeypatch, _Session())

    await GeneratedMediaRepository().list_for_project("42", episode_id="7")

    sql, params = _sql_and_params(session)
    assert "episode_id" in sql
    assert 7 in params.values()


@pytest.mark.asyncio
async def test_episode_filter_absent_by_default(monkeypatch):
    session = _patch(monkeypatch, _Session())

    await GeneratedMediaRepository().list_for_project("42")

    sql, _ = _sql_and_params(session)
    assert "episode_id" not in sql


@pytest.mark.asyncio
async def test_cursor_decoded_and_added_to_where(monkeypatch):
    session = _patch(monkeypatch, _Session())
    cursor = _encode_cursor("2026-06-01T00:00:00+00:00", 555)

    await GeneratedMediaRepository().list_for_project("42", cursor=cursor)

    sql, params = _sql_and_params(session)
    # keyset row-value comparison on (created_at, id)
    assert "(public.generated_media.created_at, public.generated_media.id) <" in sql
    assert 555 in params.values()
    # cursor ts bound as a real datetime (asyncpg-strict), not a string
    expected_ts = datetime.datetime.fromisoformat("2026-06-01T00:00:00+00:00")
    assert expected_ts in params.values()


@pytest.mark.asyncio
async def test_invalid_cursor_ignored(monkeypatch):
    session = _patch(monkeypatch, _Session())

    await GeneratedMediaRepository().list_for_project("42", cursor="not-valid-base64!!")

    sql, _ = _sql_and_params(session)
    assert ".created_at, public.generated_media.id) <" not in sql


# --------------------------------------------------------------------------- #
# list_for_project — pagination + serialization
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_limit_plus_one_peek_produces_next_cursor(monkeypatch):
    rows = [
        _row(gen_id=i, created_at=f"2026-07-01T00:00:0{i}+00:00")
        for i in range(3, 0, -1)
    ]
    session = _patch(monkeypatch, _Session(rows))

    out = await GeneratedMediaRepository().list_for_project("42", limit=2)

    _, params = _sql_and_params(session)
    assert 3 in params.values()  # limit(2) + 1 peek row
    assert len(out["items"]) == 2
    assert out["next_cursor"] is not None
    decoded = _decode_cursor(out["next_cursor"])
    assert decoded == ("2026-07-01T00:00:02+00:00", 2)


@pytest.mark.asyncio
async def test_no_next_cursor_when_under_limit(monkeypatch):
    session = _patch(monkeypatch, _Session([_row(gen_id=1)]))

    out = await GeneratedMediaRepository().list_for_project("42", limit=50)

    assert session.statements  # query ran
    assert len(out["items"]) == 1
    assert out["next_cursor"] is None


@pytest.mark.asyncio
async def test_bigint_and_uuid_columns_stringified(monkeypatch):
    _patch(
        monkeypatch,
        _Session(
            [
                _row(
                    gen_id=9007199254740993,  # > 2^53 — JS precision trap
                    scope_id=123,
                    canvas_id=456,
                    parent_resource_id=789,
                    promoted_resource_id=101112,
                    conversation_id=131415,
                    creator_id="22222222-2222-2222-2222-222222222222",
                    agent_id="33333333-3333-3333-3333-333333333333",
                )
            ]
        ),
    )

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
    _patch(monkeypatch, _Session([]))

    out = await GeneratedMediaRepository().list_for_project("42")
    assert out == {"items": [], "next_cursor": None}


@pytest.mark.asyncio
async def test_limit_clamped_to_max_100(monkeypatch):
    session = _patch(monkeypatch, _Session())

    await GeneratedMediaRepository().list_for_project("42", limit=500)

    _, params = _sql_and_params(session)
    assert 101 in params.values()  # clamp(500) -> 100, +1 peek
