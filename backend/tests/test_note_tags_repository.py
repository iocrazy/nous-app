"""NoteTagsRepository — junction diff-sync + per-user counts (ORM boundary).

Boundary-stub style: only the write_scope/read_scope session is faked; the
SQLAlchemy statements (INSERT ... ON CONFLICT DO NOTHING, DELETE ... IN, and the
grouped COUNT join) are built for real, so a cross-transport regression fails
here rather than in prod.

Semantics under test: sync inserts the missing tags, deletes the stale ones,
and is a no-op (select only) when the junction already equals the target set.
"""

from __future__ import annotations

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


import app.repositories.note_tags_repository as mod  # noqa: E402
from app.repositories.note_tags_repository import (  # noqa: E402
    NoteTagsRepository,
    get_note_tags_repository,
)


@pytest.mark.asyncio
async def test_sync_inserts_missing_and_deletes_stale(monkeypatch):
    # existing {11, 12}; target {12, 13} → insert 13, delete 11
    session = _FakeSession(result=_ScalarResult([11, 12]))
    monkeypatch.setattr(mod, "write_scope", _cm(session))
    await NoteTagsRepository().sync_for_note(7, [12, 13])
    sql = " ".join(str(s) for s in session.statements)
    assert "INSERT INTO public.note_tags" in sql
    assert "DELETE FROM public.note_tags" in sql
    assert len(session.statements) == 3  # select + insert + delete


@pytest.mark.asyncio
async def test_sync_noop_when_equal(monkeypatch):
    session = _FakeSession(result=_ScalarResult([11]))
    monkeypatch.setattr(mod, "write_scope", _cm(session))
    await NoteTagsRepository().sync_for_note(7, [11])
    assert len(session.statements) == 1  # only the select, no writes


@pytest.mark.asyncio
async def test_sync_insert_only_when_all_new(monkeypatch):
    session = _FakeSession(result=_ScalarResult([]))
    monkeypatch.setattr(mod, "write_scope", _cm(session))
    await NoteTagsRepository().sync_for_note(7, [11, 12])
    sql = " ".join(str(s) for s in session.statements)
    assert "INSERT INTO public.note_tags" in sql
    assert "DELETE FROM public.note_tags" not in sql
    assert len(session.statements) == 2  # select + insert


@pytest.mark.asyncio
async def test_sync_delete_only_when_all_removed(monkeypatch):
    session = _FakeSession(result=_ScalarResult([11, 12]))
    monkeypatch.setattr(mod, "write_scope", _cm(session))
    await NoteTagsRepository().sync_for_note(7, [])
    sql = " ".join(str(s) for s in session.statements)
    assert "DELETE FROM public.note_tags" in sql
    assert "INSERT INTO public.note_tags" not in sql
    assert len(session.statements) == 2  # select + delete


@pytest.mark.asyncio
async def test_counts_for_user_groups_by_tag(monkeypatch):
    session = _FakeSession(result=_ScalarResult([(11, 3), (12, 1)]))

    # the count query consumes `.all()` directly (row tuples), so a plain list
    # result stands in for the executed rows.
    class _RowsResult:
        def all(self):
            return [(11, 3), (12, 1)]

    session._result = _RowsResult()
    monkeypatch.setattr(mod, "read_scope", _cm(session))
    out = await NoteTagsRepository().counts_for_user(
        "00000000-0000-0000-0000-0000000000aa"
    )
    assert out == {11: 3, 12: 1}
    sql = str(session.statements[0])
    assert "count" in sql.lower()
    assert "GROUP BY" in sql


def test_factory_returns_singleton():
    assert get_note_tags_repository() is get_note_tags_repository()
