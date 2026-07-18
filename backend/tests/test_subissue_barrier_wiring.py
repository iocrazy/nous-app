"""The two barrier call sites are wired correctly and best-effort.

  * IssueRepository.transition_status captures the pre-status and fires the hook
    with (issue_id, prev, new) after the write; a hook failure never breaks the
    transition.
  * issue_lifecycle._maybe_fire_subissue_barrier reads the issue's landed status
    and passes prev='in_progress'; a read failure is swallowed.

These exercise the wiring only — the barrier decision logic is covered in
test_subissue_barrier.py.
"""

from __future__ import annotations

import pytest

# ── repository post-callback ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transition_status_fires_hook_with_prev_and_new(monkeypatch):
    from app.repositories import issue_repository as repo_mod

    repo = repo_mod.IssueRepository()

    async def fake_get(issue_id):
        return {"id": issue_id, "status": "in_progress"}

    async def fake_update(issue_id, patch):
        return {"id": issue_id, **patch}

    monkeypatch.setattr(repo, "get_by_id", fake_get)
    monkeypatch.setattr(repo, "update", fake_update)

    calls = []
    import app.services.issues.subissue_barrier as barrier_mod

    async def rec(child_id, prev, new, **kw):
        calls.append((child_id, prev, new))
        return {"fired": False}

    monkeypatch.setattr(barrier_mod, "on_child_issue_terminal", rec)

    out = await repo.transition_status(42, "done")
    assert out["status"] == "done"
    assert calls == [(42, "in_progress", "done")]


@pytest.mark.asyncio
async def test_transition_status_survives_hook_failure(monkeypatch):
    from app.repositories import issue_repository as repo_mod

    repo = repo_mod.IssueRepository()

    async def fake_get(issue_id):
        return {"id": issue_id, "status": "todo"}

    async def fake_update(issue_id, patch):
        return {"id": issue_id, **patch}

    monkeypatch.setattr(repo, "get_by_id", fake_get)
    monkeypatch.setattr(repo, "update", fake_update)

    import app.services.issues.subissue_barrier as barrier_mod

    async def boom(*a, **k):
        raise RuntimeError("barrier exploded")

    monkeypatch.setattr(barrier_mod, "on_child_issue_terminal", boom)

    # The transition still commits and returns despite the hook blowing up.
    out = await repo.transition_status(42, "done")
    assert out["status"] == "done"


# ── workflow-body call site ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_maybe_fire_reads_landed_status_and_calls_hook(monkeypatch):
    import app.workflows.issue_lifecycle as life
    from app.db import engine as db_engine

    async def fake_fetch_one(sql, params):
        assert params == {"id": 77}
        return {"status": "done"}

    monkeypatch.setattr(db_engine, "fetch_one", fake_fetch_one)

    calls = []
    import app.services.issues.subissue_barrier as barrier_mod

    async def rec(child_id, prev, new, **kw):
        calls.append((child_id, prev, new))
        return {"fired": True}

    monkeypatch.setattr(barrier_mod, "on_child_issue_terminal", rec)

    await life._maybe_fire_subissue_barrier(77)
    assert calls == [(77, "in_progress", "done")]


@pytest.mark.asyncio
async def test_maybe_fire_swallows_read_failure(monkeypatch):
    import app.workflows.issue_lifecycle as life
    from app.db import engine as db_engine

    async def boom(sql, params):
        raise RuntimeError("db down")

    monkeypatch.setattr(db_engine, "fetch_one", boom)

    # Must not raise — the workflow's own completion must never be aborted.
    await life._maybe_fire_subissue_barrier(77)
