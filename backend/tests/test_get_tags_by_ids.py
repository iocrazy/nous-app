"""get_tags_by_ids — full-row in-list read (ORM boundary).

Boundary-stub style: only the read_scope session is faked; the SELECT ...
WHERE id IN (...) AND <visibility predicate> statement is built for real.
Backs the topics feed's tag filter, which resolves pool tag ids to their
name/name_zh word set. The visibility predicate (spec §5) is what keeps a
raw tag_id query param from resolving another user's private tag into a
live filter word (see tests/topics/test_topics_router.py for the
end-to-end cross-user case)."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.models import Tags
from app.repositories.tags_repository import TagsRepository


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self.statements = []
        self._rows = rows

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return _ScalarResult(self._rows)


def _patch_read_scope(monkeypatch, session):
    from app.repositories import tags_repository as mod

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(mod, "read_scope", _scope)


@pytest.mark.asyncio
async def test_get_tags_by_ids_returns_full_rows(monkeypatch):
    session = _FakeSession(
        [
            Tags(id=1, name="Copywriting", name_zh="文案", type="system"),
            Tags(id=2, name="AI", name_zh=None, type="system"),
        ]
    )
    _patch_read_scope(monkeypatch, session)

    rows = await TagsRepository().get_tags_by_ids([1, 2], "caller-uid")

    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {1, 2}
    assert by_id[1]["name"] == "Copywriting" and by_id[1]["name_zh"] == "文案"
    assert by_id[2]["name"] == "AI"
    # id stays a native int (5.3 trap — bigint never str()'d in the repo)
    assert isinstance(by_id[1]["id"], int)
    # one IN() query
    assert "IN (" in str(session.statements[0])


@pytest.mark.asyncio
async def test_get_tags_by_ids_empty_short_circuits(monkeypatch):
    from app.repositories import tags_repository as mod

    def _explode():  # pragma: no cover
        raise AssertionError("read_scope must not open for empty id list")

    monkeypatch.setattr(mod, "read_scope", _explode)
    assert await TagsRepository().get_tags_by_ids([], "caller-uid") == []


@pytest.mark.asyncio
async def test_get_tags_by_ids_scopes_query_to_caller_visible_pool(monkeypatch):
    """Spec §5: the compiled WHERE must restrict rows to
    ``type IN ('system','time') OR (type='user' AND user_id = caller)`` — the
    same predicate ``resolve_note_tags`` uses. Without it, a raw tag_id query
    param could resolve another user's private tag's name/name_zh into a live
    filter word (cross-user existence oracle + private-word-steered filtering).
    Asserted against the compiled statement's SQL text + bound params (not
    literal_binds — the Uuid column's literal renderer requires an already-
    typed uuid.UUID, not the plain str the repo is handed at the API
    boundary), since the fake session below returns preset rows independent
    of the WHERE clause it's handed."""
    caller_uid = "6f6b6f6e-6f6e-4f6f-8f6f-6f6f6f6f6f6f"
    session = _FakeSession([])
    _patch_read_scope(monkeypatch, session)

    await TagsRepository().get_tags_by_ids([1, 2], caller_uid)

    compiled = session.statements[0].compile()
    sql = str(compiled)
    params = compiled.params
    assert "tags.type IN " in sql  # system/time branch
    assert "tags.type = " in sql and "tags.user_id = " in sql  # own-user branch
    assert params["id_1"] == [1, 2]
    assert params["type_1"] == ["system", "time"]
    assert params["type_2"] == "user"
    assert params["user_id_1"] == caller_uid
