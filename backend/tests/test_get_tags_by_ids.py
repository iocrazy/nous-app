"""get_tags_by_ids — full-row in-list read (ORM boundary).

Boundary-stub style: only the read_scope session is faked; the SELECT ...
WHERE id IN (...) statement is built for real. Backs the topics feed's tag
filter, which resolves pool tag ids to their name/name_zh word set.
"""

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

    rows = await TagsRepository().get_tags_by_ids([1, 2])

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
    assert await TagsRepository().get_tags_by_ids([]) == []
