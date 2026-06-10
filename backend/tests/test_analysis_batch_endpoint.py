"""Regression tests for POST /analysis/batch (trigger_batch_analysis).

The batch endpoint previously called a non-existent
``AnalysisRepository.get_media(...)`` per row — it raised AttributeError that the
per-row except swallowed, so the batch silently queued NOTHING. It now fetches
the requested parsed_media in one ``.in_(...)`` query and queues a workflow per
found row. Exercised by calling the endpoint coroutine directly with
monkeypatched supabase / task-manager / workflow dispatch (no app / DB).
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from app.api.analysis_router import BatchAnalyzeRequest, trigger_batch_analysis

# `app.api` re-exports the router object under the name `analysis_router`,
# shadowing the submodule for string-based monkeypatch resolution. Resolve the
# real module from the function we're testing.
_router_mod = sys.modules[trigger_batch_analysis.__module__]


def _auth(user_id: str = "u1"):
    return types.SimpleNamespace(user_id=user_id)


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def in_(self, _col, ids):
        self._requested = list(ids)
        return self

    async def execute(self):
        # Return only the rows whose id was requested (mirrors PostgREST).
        data = [r for r in self._rows if r["id"] in self._requested]
        return types.SimpleNamespace(data=data)


class _FakeSupabase:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _FakeTable(self._rows)


def _patch(monkeypatch, rows):
    """Patch supabase + task manager + workflow dispatch; capture queued ids."""

    async def _fake_admin():
        return _FakeSupabase(rows)

    monkeypatch.setattr("app.db.supabase_client.get_async_supabase_admin", _fake_admin)

    class _FakeMgr:
        async def create(self, **_k):
            return None

    monkeypatch.setattr(
        "app.services.infra.unified_task_manager.get_task_manager",
        lambda: _FakeMgr(),
    )

    queued: list[dict] = []

    async def _fake_dispatch(
        _name, *, dbos_workflow_callable, dbos_workflow_kwargs, workflow_id
    ):
        queued.append(dbos_workflow_kwargs)

    monkeypatch.setattr(_router_mod, "start_workflow_routed", _fake_dispatch)
    return queued


def test_batch_queues_for_found_media(monkeypatch):
    rows = [
        {"id": 101, "title": "A", "description": "", "cover_urls": ["http://c/1.jpg"]},
        {"id": 102, "title": "B", "description": "", "cover_urls": ["http://c/2.jpg"]},
    ]
    queued = _patch(monkeypatch, rows)

    resp = asyncio.run(
        trigger_batch_analysis(
            _auth(), BatchAnalyzeRequest(media_ids=[101, 102], level="L1")
        )
    )

    assert resp["media_count"] == 2
    assert {q["media_id"] for q in queued} == {101, 102}
    # Each queued workflow carries the media's cover + the triggering user.
    assert all(q["cover_url"].startswith("http://c/") for q in queued)
    assert all(q["user_id"] == "u1" for q in queued)


def test_batch_skips_missing_and_coverless_media(monkeypatch):
    rows = [
        {
            "id": 201,
            "title": "has cover",
            "description": "",
            "cover_urls": ["http://c/1.jpg"],
        },
        {"id": 202, "title": "no cover", "description": "", "cover_urls": []},
        # 203 is absent from the DB entirely.
    ]
    queued = _patch(monkeypatch, rows)

    resp = asyncio.run(
        trigger_batch_analysis(
            _auth(), BatchAnalyzeRequest(media_ids=[201, 202, 203], level="L1")
        )
    )

    # Only 201 has both a row and a cover.
    assert resp["media_count"] == 1
    assert [q["media_id"] for q in queued] == [201]


def test_batch_rejects_non_l1(monkeypatch):
    _patch(monkeypatch, [])
    with pytest.raises(Exception) as ei:
        asyncio.run(
            trigger_batch_analysis(
                _auth(), BatchAnalyzeRequest(media_ids=[1], level="L2")
            )
        )
    # HTTPException(400) — only L1 supported.
    assert getattr(ei.value, "status_code", None) == 400
