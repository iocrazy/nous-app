"""Pins the (pre-existing, dead) video/audio branch of
``resource_fetch_tool._fetch_dispatch`` after Phase A raw-SQL-to-ORM
migration (docs/decisions/2026-08-04-raw-sql-to-orm-full-migration.md).

The second query in this branch joins ``public.videos`` — a table renamed
away by migration 066 (``066_rename_videos_to_parsed_media.sql``); confirmed
absent against the live schema (``information_schema.tables`` has no
``videos`` row; ``resource_summaries`` / ``resource_transcripts`` hold that
content today, keyed by resource_id). This query has therefore always
raised in production. Rather than silently fixing the join target (a real
behavior change, out of scope for a refactor-only ORM pass), it is routed
through the new ``app/db/scoped_sql.py`` migration guardrail with
``system=True`` — preserving the exact byte-for-byte failure while at least
declaring and auditing the access instead of leaving it on an ungoverned
``db_engine`` call.

These tests pin:
  * the call goes through ``scoped_sql.scoped_fetch_all`` with
    ``system=True`` and a non-empty ``reason`` (governed, not bare
    ``db_engine``);
  * a DB error from that call (the real-world outcome — the table doesn't
    exist) propagates up through ``_fetch_dispatch`` and is converted to a
    typed ``{"error": "fetch failed: <ExceptionClassName>"}`` by
    ``resource_fetch``'s broad except — never a silent/opaque failure;
  * an (unrealistic today, but contractually possible) empty result set
    degrades to the "not yet processed" error rather than raising.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _FakeMappingsResult(self._rows)


def _patch_access_check_hit(monkeypatch, *, mime: str):
    """Stand in for the first (access-check) query succeeding with a single
    row of the given mime type — isolates the video/audio branch under test
    from the resources+resource_items+team_members query."""
    import app.db.scope as scope_module
    import app.db.session as session_module

    row = {
        "id": "1",
        "mime": mime,
        "name": "clip",
        "file_path": "some/path",
        "brief": None,
    }

    @asynccontextmanager
    async def _read_scope():
        yield _StubSession(row)

    @asynccontextmanager
    async def _system_request_scope(reason: str):
        yield None

    monkeypatch.setattr(session_module, "read_scope", _read_scope)
    monkeypatch.setattr(scope_module, "system_request_scope", _system_request_scope)


class _StubSession:
    def __init__(self, row):
        self._row = row

    async def execute(self, _stmt):
        return _FakeResult([self._row])


async def test_video_audio_query_declares_system_scope_with_reason(monkeypatch):
    """The dead public.videos query must go through scoped_sql(system=True,
    reason=...) — not a bare db_engine call — even though it always fails."""
    from app.services.ai.tools import resource_fetch_tool as m

    _patch_access_check_hit(monkeypatch, mime="video/mp4")

    captured: dict = {}

    async def _fake_scoped_fetch_all(
        sql, params=None, *, scope=None, system=False, reason=""
    ):
        captured["sql"] = sql
        captured["params"] = params
        captured["system"] = system
        captured["reason"] = reason
        return [{"summary": "a summary", "transcript": None}]

    monkeypatch.setattr("app.db.scoped_sql.scoped_fetch_all", _fake_scoped_fetch_all)

    result = await m._fetch_dispatch(
        resource_id="1", mode="summary", args=None, user_id="u"
    )

    assert captured["system"] is True
    assert captured["reason"], "reason must be non-empty for the audit trail"
    assert "public.videos" in captured["sql"]
    assert result == {
        "content": "a summary",
        "meta": {"name": "clip", "mode": "summary"},
    }


async def test_video_audio_query_db_error_becomes_typed_error(monkeypatch):
    """The realistic outcome today: public.videos doesn't exist, so the
    query raises — resource_fetch's broad except must convert that into a
    typed {"error": ...}, never propagate raw or silently swallow."""
    from app.services.ai.tools import resource_fetch_tool as m

    _patch_access_check_hit(monkeypatch, mime="video/mp4")
    monkeypatch.setattr(
        "app.db.scoped_sql.scoped_fetch_all",
        AsyncMock(side_effect=RuntimeError('relation "videos" does not exist')),
    )

    result = await m.resource_fetch(
        resource_id="1",
        mode="summary",
        args=None,
        user_id="u",
        available_refs={"1"},
        request_cache={},
    )
    assert result == {"error": "fetch failed: RuntimeError"}


async def test_video_audio_query_empty_result_degrades_to_not_available(monkeypatch):
    """Contractual (not realistic) case: an empty result set (rather than a
    raised error) must degrade to the 'not yet processed' message, not
    raise an IndexError — mirrors legacy ``(media_rows or [{}])[0]``."""
    from app.services.ai.tools import resource_fetch_tool as m

    _patch_access_check_hit(monkeypatch, mime="video/mp4")
    monkeypatch.setattr(
        "app.db.scoped_sql.scoped_fetch_all", AsyncMock(return_value=[])
    )

    result = await m._fetch_dispatch(
        resource_id="1", mode="summary", args=None, user_id="u"
    )
    assert result == {"error": "summary not available; resource not yet processed"}
