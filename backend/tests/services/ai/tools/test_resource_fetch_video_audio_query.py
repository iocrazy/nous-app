"""Pins the repaired video/audio branch of
``resource_fetch_tool._fetch_dispatch``.

History: the pre-2026-08 implementation joined ``public.videos`` — a table
renamed away by migration 066 — so this branch always raised in production
(every video/audio fetch degraded to "fetch failed" for months). Phase A's
ORM pass preserved that failure byte-for-byte per refactor-only discipline
and this file used to pin the dead query's governance. The branch has since
been repaired to read the real data sources — ``resource_summaries`` /
``resource_transcripts``, keyed by resource_id — so these tests now pin the
repaired contract:

  * a DB error during the content lookup is converted to a typed
    ``{"error": "fetch failed: <ExceptionClassName>"}`` by
    ``resource_fetch``'s broad except — never a silent/opaque failure;
  * a missing row degrades to the "not yet processed" message rather than
    raising;
  * the phantom ``public.videos`` table is never queried again (guarded
    here and in test_resource_fetch_video_transcripts.py, which also covers
    the happy path end-to-end on a real engine).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _AccessResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _FakeMappingsResult(self._rows)


class _ContentResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


def _patch_sessions(monkeypatch, *, mime: str, content_outcome):
    """First ``read_scope`` session serves the access-check row; subsequent
    sessions serve the content lookup. ``content_outcome`` is either a value
    (returned via ``scalar``) or an Exception instance (raised
    from ``execute``)."""
    import app.db.scope as scope_module
    import app.db.session as session_module

    row = {
        "id": "1",
        "mime": mime,
        "name": "clip",
        "file_path": "some/path",
        "brief": None,
    }

    class _AccessSession:
        async def execute(self, _stmt):
            return _AccessResult([row])

    class _ContentSession:
        async def execute(self, _stmt):
            if isinstance(content_outcome, Exception):
                raise content_outcome
            return _ContentResult(content_outcome)

    calls = {"n": 0}

    @asynccontextmanager
    async def _read_scope():
        calls["n"] += 1
        yield _AccessSession() if calls["n"] == 1 else _ContentSession()

    @asynccontextmanager
    async def _system_request_scope(reason: str):
        yield None

    monkeypatch.setattr(session_module, "read_scope", _read_scope)
    monkeypatch.setattr(scope_module, "system_request_scope", _system_request_scope)


async def test_video_audio_query_db_error_becomes_typed_error(monkeypatch):
    """A DB error during the content lookup must be converted into a typed
    {"error": ...} by resource_fetch's broad except — never propagate raw
    or silently swallow."""
    from app.services.ai.tools import resource_fetch_tool as m

    _patch_sessions(
        monkeypatch,
        mime="video/mp4",
        content_outcome=RuntimeError("connection lost"),
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


async def test_video_audio_missing_row_degrades_to_not_available(monkeypatch):
    """No summary row yet → the 'not yet processed' message, not a raise."""
    from app.services.ai.tools import resource_fetch_tool as m

    _patch_sessions(monkeypatch, mime="video/mp4", content_outcome=None)

    result = await m._fetch_dispatch(
        resource_id="1", mode="summary", args=None, user_id="u"
    )
    assert result == {"error": "summary not available; resource not yet processed"}


async def test_video_audio_happy_path_returns_content(monkeypatch):
    """A present summary row is returned as content with mode metadata."""
    from app.services.ai.tools import resource_fetch_tool as m

    _patch_sessions(monkeypatch, mime="video/mp4", content_outcome="a summary")

    result = await m._fetch_dispatch(
        resource_id="1", mode="summary", args=None, user_id="u"
    )
    assert result == {
        "content": "a summary",
        "meta": {"name": "clip", "mode": "summary"},
    }


def test_phantom_videos_table_never_queried_again():
    import inspect

    from app.services.ai.tools import resource_fetch_tool as m

    src = inspect.getsource(m)
    assert "FROM public.videos" not in src
