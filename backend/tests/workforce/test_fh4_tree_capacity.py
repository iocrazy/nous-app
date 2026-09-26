"""fh4 T3 (E3): at most ``MAX_ACTIVE_SUBAGENTS_PER_TREE`` live children per
root tree, counted under one advisory lock per tree; a child's tree links are
written at INSERT; and a sync child is disposed before anyone is told it ended.

The real-Postgres half (the lock actually serialises two spawns; queued task
rows count) is ``tests/db/test_fh4_tree_capacity_integration.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.ai.runner.subagent_task_service import (
    ENVELOPE_KEYS,
    SubAgentTaskService,
)
from app.services.workforce import tree_capacity as tc
from tests.test_subagent_task_service import _EventRecorder, _wire_sync_spawn

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _ctx():
    return {
        "caller_agent_id": uuid4(),
        "caller_user_id": uuid4(),
        "parent_run_id": "900",
        "agent_depth": 0,
    }


def _full(limit=8, active=8, requested=1):
    return tc.TreeCapacityExceeded(limit=limit, active=active, requested=requested)


@pytest.fixture
def tree_root(monkeypatch):
    root = AsyncMock(return_value=900)
    monkeypatch.setattr(tc, "resolve_tree_root", root)
    return root


# ── limit ─────────────────────────────────────────────────────────────────


def test_limit_defaults_to_eight_and_env_overrides(monkeypatch):
    monkeypatch.delenv("MAX_ACTIVE_SUBAGENTS_PER_TREE", raising=False)
    assert tc.max_active_subagents_per_tree() == 8
    monkeypatch.setenv("MAX_ACTIVE_SUBAGENTS_PER_TREE", "2")
    assert tc.max_active_subagents_per_tree() == 2
    monkeypatch.setenv("MAX_ACTIVE_SUBAGENTS_PER_TREE", "zero")
    assert tc.max_active_subagents_per_tree() == 8


async def test_reserve_refuses_when_the_request_would_cross_the_limit(monkeypatch):
    monkeypatch.setenv("MAX_ACTIVE_SUBAGENTS_PER_TREE", "8")
    monkeypatch.setattr(tc, "_count_active", AsyncMock(return_value=7))
    session = SimpleNamespace(execute=AsyncMock())

    assert await tc.reserve_in_session(session, root=900, requested=1) == 7
    with pytest.raises(tc.TreeCapacityExceeded) as err:
        await tc.reserve_in_session(session, root=900, requested=2)
    assert (err.value.limit, err.value.active, err.value.requested) == (8, 7, 2)
    # The lock is taken before every count.
    assert session.execute.await_count == 2


# ── sync Task ───────────────────────────────────────────────────────────────


def _guarded_recorder(monkeypatch, wired):
    """Make the stub recorder honour ``capacity_guard`` the way the real
    ``_insert_row`` does: the guard runs before the row exists."""
    import app.services.ai.runner.run_recorder as recorder_mod

    base = recorder_mod.RunRecorder

    class _Guarded(base):
        async def __aenter__(self):
            guard = self.kwargs.get("capacity_guard")
            if guard is not None:
                await guard(SimpleNamespace(execute=AsyncMock()))
            wired.log.append("insert")
            return await super().__aenter__()

        async def __aexit__(self, *exc):
            wired.log.append("dispose")
            return await super().__aexit__(*exc)

    monkeypatch.setattr(recorder_mod, "RunRecorder", _Guarded)


async def test_a_sync_task_at_capacity_is_refused_typed_with_no_row(
    monkeypatch, tree_root
):
    wired = _wire_sync_spawn(monkeypatch)
    wired.log = []
    _guarded_recorder(monkeypatch, wired)
    monkeypatch.setattr(tc, "reserve_in_session", AsyncMock(side_effect=_full()))
    rec = _EventRecorder()
    svc = SubAgentTaskService(**_ctx(), parent_recorder=rec)

    out = await svc.spawn({"subagent_type": "librarian", "prompt": "dig"})

    assert out["status"] == "failed" and out["error"] == "tree_capacity_exceeded"
    assert out["limit"] == 8 and out["active"] == 8
    assert set(ENVELOPE_KEYS) <= set(out)
    assert "insert" not in wired.log
    wired.run_turn.assert_not_awaited()
    # C10: a refusal is not a spawn.
    assert rec.events == []


async def test_the_child_row_carries_its_tree_links_at_insert(monkeypatch, tree_root):
    """C9: no post-insert attach UPDATE — the window in which a running child
    had ``root_run_id`` NULL (and so could not be counted) is gone."""
    wired = _wire_sync_spawn(monkeypatch)
    import app.services.workforce.agent_worker as worker_mod

    attach = AsyncMock()
    monkeypatch.setattr(worker_mod, "_attach_to_parent_run", attach, raising=False)
    svc = SubAgentTaskService(**_ctx(), parent_recorder=_EventRecorder())

    await svc.spawn({"subagent_type": "librarian", "prompt": "dig"})

    kwargs = wired.recorders[0].kwargs
    assert kwargs["root_run_id"] == "900"
    assert kwargs["parent_run_id"] == "900"
    assert kwargs["agent_depth"] == 1
    assert callable(kwargs["capacity_guard"])
    attach.assert_not_awaited()


async def test_sync_done_is_emitted_after_the_child_is_disposed(monkeypatch, tree_root):
    """C5: dispose → release → notify. The ``done`` used to be written inside
    the recorder block, before the row went terminal — a reader acting on it
    could still count the child as running."""
    wired = _wire_sync_spawn(monkeypatch)
    wired.log = []
    _guarded_recorder(monkeypatch, wired)
    monkeypatch.setattr(tc, "reserve_in_session", AsyncMock(return_value=0))

    class _Rec(_EventRecorder):
        async def record_event(self, event_type, payload, **kw):
            wired.log.append(event_type)
            await super().record_event(event_type, payload, **kw)

    svc = SubAgentTaskService(**_ctx(), parent_recorder=_Rec())
    out = await svc.spawn({"subagent_type": "librarian", "prompt": "dig"})

    assert out["status"] == "success"
    assert wired.log == ["insert", "subagent_spawned", "dispose", "subagent_done"]


async def test_a_fan_out_that_does_not_fit_is_refused_whole(monkeypatch, tree_root):
    """C2: 7 active, 3 asked → none start. Half a fan-out is a result the
    model did not ask for."""
    check = AsyncMock(side_effect=_full(active=7, requested=3))
    monkeypatch.setattr(tc, "check_tree_capacity", check)
    svc = SubAgentTaskService(**_ctx(), parent_recorder=_EventRecorder())
    spawn_one = AsyncMock()
    monkeypatch.setattr(svc, "_spawn", spawn_one)

    out = await svc.spawn(
        {"tasks": [{"subagent_type": "a", "prompt": "p"} for _ in range(3)]}
    )

    assert out["status"] == "failed" and out["error"] == "tree_capacity_exceeded"
    assert out["active"] == 7 and out["requested"] == 3
    spawn_one.assert_not_awaited()
    check.assert_awaited_once_with(900, 3)


# ── background Task ─────────────────────────────────────────────────────────


class _IssueRec(_EventRecorder):
    issue_id = 7


async def test_a_background_task_at_capacity_writes_no_row_and_no_spawned(
    monkeypatch, tree_root
):
    import app.repositories.agent_workforce_repository as wf_mod

    create = AsyncMock(return_value={"id": "task-1"})
    monkeypatch.setattr(
        wf_mod,
        "get_agent_workforce_repository",
        lambda: SimpleNamespace(create_task=create),
    )

    async def _refuse(root, requested, write):
        raise _full()

    monkeypatch.setattr(tc, "run_within_tree_capacity", _refuse)
    rec = _IssueRec()
    svc = SubAgentTaskService(**_ctx(), parent_recorder=rec)
    monkeypatch.setattr(svc, "_resolve_agent_id", AsyncMock(return_value=uuid4()))

    out = await svc.spawn({"subagent_type": "librarian", "prompt": "x", "await": False})

    assert out["status"] == "failed" and out["error"] == "tree_capacity_exceeded"
    create.assert_not_awaited()
    assert rec.events == []


async def test_a_background_task_reserves_its_slot_with_the_row(monkeypatch, tree_root):
    """C3's producer half: the queued row carries ``root_run_id`` (what the
    count reads) and is written INSIDE the capacity transaction."""
    import app.repositories.agent_workforce_repository as wf_mod

    seen: dict = {}

    async def _create(*, payload, **kw):
        seen["inside"] = seen.get("open", False)
        seen["payload"] = payload
        return {"id": "task-1"}

    monkeypatch.setattr(
        wf_mod,
        "get_agent_workforce_repository",
        lambda: SimpleNamespace(create_task=_create),
    )

    async def _within(root, requested, write):
        seen.update(open=True, root=root, requested=requested)
        try:
            return await write()
        finally:
            seen["open"] = False

    monkeypatch.setattr(tc, "run_within_tree_capacity", _within)
    svc = SubAgentTaskService(**_ctx(), parent_recorder=_IssueRec())
    monkeypatch.setattr(svc, "_resolve_agent_id", AsyncMock(return_value=uuid4()))

    out = await svc.spawn({"subagent_type": "librarian", "prompt": "x", "await": False})

    assert out["status"] == "queued"
    assert seen["inside"] is True
    assert (seen["root"], seen["requested"]) == (900, 1)
    assert seen["payload"]["root_run_id"] == "900"


# ── async ordering (worker) ─────────────────────────────────────────────────


async def test_async_child_is_terminal_before_its_result_is_enqueued():
    """C6: dispose → release → notify on the background path. The slot is the
    running child row, so it is free once ``run_background_task`` returns
    (the child's recorder has closed); the inbox write comes after."""
    from tests.workforce.test_run_one_task_subagent import _run, _task, _wire

    w = _wire()
    log: list[str] = []
    envelope = await w.run_bg()
    w.run_bg.reset_mock()

    async def _child(*a, **kw):
        log.append("child_terminal")
        return envelope

    async def _enqueue(**kw):
        log.append("enqueue")
        return {"id": 1, "content": kw["content"]}

    w.run_bg.side_effect = _child
    w.inbox_repo.enqueue.side_effect = _enqueue

    await _run(w, _task())

    assert log == ["child_terminal", "enqueue"]


# ── Delegate ────────────────────────────────────────────────────────────────


async def test_delegate_refuses_at_capacity(monkeypatch, tree_root):
    from app.core.config import settings
    from app.services.workforce import delegate_tool as dt

    monkeypatch.setattr(settings, "FEATURE_WORKFORCE_DELEGATE", True)
    dt._dispatch_history.clear()
    monkeypatch.setattr(tc, "check_tree_capacity", AsyncMock(side_effect=_full()))
    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(
        return_value={"id": str(uuid4()), "slug": "summarize", "persistent": True}
    )
    workforce = MagicMock()
    workforce.enqueue_inbox = AsyncMock()
    svc = dt.DelegateToolService(
        caller_agent_id=uuid4(),
        caller_user_id=uuid4(),
        parent_run_id="900",
        agent_depth=0,
        agent_repo=agent_repo,
        workforce_repo=workforce,
    )
    svc._detect_cycle = AsyncMock(return_value=None)

    out = await svc.execute({"agent_slug": "summarize", "prompt": "go"})

    assert out["error"] == "tree_capacity_exceeded"
    assert out["limit"] == 8 and out["active"] == 8
    workforce.enqueue_inbox.assert_not_awaited()
    dt._dispatch_history.clear()


# ── RunRecorder seam ────────────────────────────────────────────────────────


def _recording_write_scope(log):
    from contextlib import asynccontextmanager

    class _Session:
        async def execute(self, stmt, params=None):
            log.append(("execute", dict(params or {})))
            return SimpleNamespace(first=lambda: (900000000000051,))

    @asynccontextmanager
    async def _scope():
        yield _Session()

    return _scope


async def test_insert_row_runs_the_guard_first_and_writes_the_tree_links(monkeypatch):
    from app.services.ai.runner.run_recorder import RunRecorder

    log: list = []
    monkeypatch.setattr("app.db.session.write_scope", _recording_write_scope(log))

    async def _guard(session):
        log.append(("guard", None))

    rec = RunRecorder(
        agent_id=uuid4(),
        user_id=uuid4(),
        trigger="subagent_task",
        parent_run_id="900",
        root_run_id="800",
        agent_depth=2,
        capacity_guard=_guard,
    )
    rec._link_task = AsyncMock()
    await rec._insert_row()

    assert [k for k, _ in log] == ["guard", "execute"]
    params = log[1][1]
    assert (params["parent_run_id"], params["root_run_id"]) == (900, 800)
    assert params["agent_depth"] == 2


async def test_a_refused_guard_writes_nothing_and_is_not_retried(monkeypatch):
    from app.services.ai.runner.run_recorder import RunRecorder

    log: list = []
    monkeypatch.setattr("app.db.session.write_scope", _recording_write_scope(log))
    calls: list = []

    async def _guard(session):
        calls.append(1)
        raise _full()

    rec = RunRecorder(
        agent_id=uuid4(),
        user_id=uuid4(),
        trigger="subagent_task",
        parent_run_id="900",
        root_run_id="900",
        agent_depth=1,
        capacity_guard=_guard,
    )
    rec._pre_flight_check_paused = AsyncMock()
    rec._snapshot_price = AsyncMock()

    with pytest.raises(tc.TreeCapacityExceeded):
        await rec.__aenter__()
    assert calls == [1]  # a refusal is an answer, not a transient blip
    assert not [k for k, _ in log if k == "execute"]
    assert rec.run_id is None


async def test_a_root_run_insert_is_unchanged(monkeypatch):
    """Negative control: no parent → no tree columns, no guard."""
    from app.services.ai.runner.run_recorder import RunRecorder

    log: list = []
    monkeypatch.setattr("app.db.session.write_scope", _recording_write_scope(log))
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
    rec._link_task = AsyncMock()
    await rec._insert_row()

    params = log[0][1]
    assert not {"parent_run_id", "root_run_id", "agent_depth"} & set(params)
