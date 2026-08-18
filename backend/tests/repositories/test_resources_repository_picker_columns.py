"""``list_accessible_for_user`` selects what the @-picker needs to render.

The router can only fill ``thumbnail_url`` / the AI status fields if the repo
actually reads those columns (RECON#7 — it read none of them, which is why
``thumbnail_url`` was a hardcoded ``None``). Compiled-SQL assertions, no DB,
matching this directory's established capture-the-statement pattern.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.repositories.resources_repository import ResourcesRepository

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


async def test_selects_the_thumbnail_signals(monkeypatch):
    session = _capture(monkeypatch)
    await ResourcesRepository().list_accessible_for_user(user_id="u1")
    sql = session.captured_sql or ""
    assert "thumbnail_path" in sql
    assert "cover_image_path" in sql
    assert "media_id" in sql


async def test_selects_the_ai_status_columns(monkeypatch):
    session = _capture(monkeypatch)
    await ResourcesRepository().list_accessible_for_user(user_id="u1")
    sql = session.captured_sql or ""
    assert "transcript_status" in sql
    assert "summary_status" in sql


async def test_does_not_select_storage_or_credential_columns(monkeypatch):
    """Tripwire twin of the router's: the picker query has no business
    reading the original file path (nor the content hash) — nothing
    downstream needs them, and every extra column is one more thing a future
    ``**row`` spread can leak."""
    session = _capture(monkeypatch)
    await ResourcesRepository().list_accessible_for_user(user_id="u1")
    sql = session.captured_sql or ""
    assert "file_path" not in sql
    assert "file_hash" not in sql


async def test_returned_rows_carry_the_new_keys(monkeypatch):
    row = {
        "id": "1",
        "name": "clip.mp4",
        "mime": "video/mp4",
        "size": 1,
        "updated_at": "2026-08-01T00:00:00Z",
        "scope_id": "9",
        "scope_type": "personal",
        "thumbnail_path": "t.jpg",
        "cover_image_path": None,
        "media_id": 7,
        "transcript_status": "processing",
        "summary_status": "none",
    }
    _capture(monkeypatch, [row])
    out = await ResourcesRepository().list_accessible_for_user(user_id="u1")
    assert out[0]["thumbnail_path"] == "t.jpg"
    assert out[0]["transcript_status"] == "processing"
