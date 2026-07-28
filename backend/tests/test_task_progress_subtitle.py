"""GET /task-manager/tasks/{id}/progress — DB-fallback shape must surface
`task_tracking.subtitle` so pollers (e.g. the Chrome extension's prompt-panel)
can show the real caption-stage text instead of a synthesized placeholder.

Ported to the ORM read_scope() boundary, same fake-session pattern as
test_unified_task_manager_missing_row.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.api.task_manager_router import get_task_progress
from app.core.deps import AuthContext


class _Result:
    def __init__(self, value):
        self._v = value

    def mappings(self):
        return self

    def first(self):
        return self._v


class _Session:
    def __init__(self, row):
        self._row = row

    async def execute(self, stmt, params=None):
        return _Result(self._row)


@pytest.fixture
def patch_read_scope(monkeypatch):
    def _install(row):
        session = _Session(row)

        @asynccontextmanager
        async def _scope():
            yield session

        import app.db.session as dbs

        monkeypatch.setattr(dbs, "read_scope", _scope)
        return session

    return _install


async def test_progress_db_fallback_includes_subtitle(monkeypatch, patch_read_scope):
    patch_read_scope(
        {
            "dbos_workflow_id": "wf-1",
            "progress": 42,
            "status": "in_progress",
            "speed": None,
            "total_bytes": None,
            "error_msg": None,
            "subtitle": "Analyzing image…",
        }
    )

    # No Redis in the test env — force the redis lookup to raise so the
    # handler takes the DB-fallback branch (the branch under test here;
    # gen-prompt/caption-stage tasks never write to download_progress:* keys
    # in Redis, so this is also the real-world path for them).
    import app.core.redis as redis_mod

    def _boom():
        raise RuntimeError("no redis in test env")

    monkeypatch.setattr(redis_mod, "get_sync_redis", _boom)

    auth = AuthContext(user_id="u-1", auth_type="api_key")
    result = await get_task_progress("wf-1", auth)

    assert result["status"] == "in_progress"
    assert result["subtitle"] == "Analyzing image…"


async def test_progress_db_fallback_subtitle_none_when_unset(
    monkeypatch, patch_read_scope
):
    patch_read_scope(
        {
            "dbos_workflow_id": "wf-2",
            "progress": 0,
            "status": "queued",
            "speed": None,
            "total_bytes": None,
            "error_msg": None,
            "subtitle": None,
        }
    )

    import app.core.redis as redis_mod

    def _boom():
        raise RuntimeError("no redis in test env")

    monkeypatch.setattr(redis_mod, "get_sync_redis", _boom)

    auth = AuthContext(user_id="u-1", auth_type="api_key")
    result = await get_task_progress("wf-2", auth)

    assert result["subtitle"] is None
