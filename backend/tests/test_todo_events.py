"""todo_write — the whole-list snapshot that makes agent progress durable.

`agent_todo.py` says it plainly: the list is "wiped at end of turn (per-turn
state, not persisted)". Nothing outside the model ever saw it — the user's
Task Center showed no steps, a restart lost the plan, an audit had nothing to
replay. Every successful mutation now appends ONE transcript event carrying
the complete list (dsh whole-value rule: the current list IS the last
`todo_write` event; no deltas to reconcile) and mirrors the same payload into
`agent_runs.metadata_json.todos` — the row the Task Center already receives
over Realtime, so the UI gets it with zero new push channels.

Reads (`op=show`) do not emit: a frame per read floods the log with identical
snapshots. Emission failure never fails the tool — telemetry is never worth a
turn.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agent_framework.agent_todo import AgentTodoList, TodoStatus
from app.services.ai.runner.todo_events import emit_todo_snapshot


def _list():
    tl = AgentTodoList()
    tl.replace([{"content": "step A"}, {"content": "step B", "active_form": "doing B"}])
    tl.update_status(2, TodoStatus.IN_PROGRESS)
    return tl


@pytest.mark.unit
@pytest.mark.asyncio
async def test_snapshot_is_the_whole_list_with_counts():
    rec = AsyncMock()
    await emit_todo_snapshot(rec, _list())
    et, payload = rec.record_event.await_args.args
    assert et == "todo_write"
    assert [t["content"] for t in payload["todos"]] == ["step A", "step B"]
    assert payload["todos"][1] == {
        "id": 2,
        "content": "step B",
        "status": "in_progress",
        "active_form": "doing B",
    }
    assert payload["counts"] == {"total": 2, "completed": 0, "in_progress": 1}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_none_recorder_is_a_no_op():
    await emit_todo_snapshot(None, _list())  # must not raise


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recorder_failure_never_breaks_the_tool():
    rec = AsyncMock()
    rec.record_event.side_effect = RuntimeError("sink down")
    await emit_todo_snapshot(rec, _list())  # must not raise


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_list_still_snapshots():
    """`replace([])` clears the plan — that is state too, and the UI must
    learn it (whole-value: clearing also pushes a frame, never a silence)."""
    rec = AsyncMock()
    tl = AgentTodoList()
    await emit_todo_snapshot(rec, tl)
    _, payload = rec.record_event.await_args.args
    assert payload == {
        "todos": [],
        "counts": {"total": 0, "completed": 0, "in_progress": 0},
    }


# ── wiring: the Skill(todo) tool actually emits ──────────────────────────


class _Repo:  # SkillToolService needs a repo it never touches for `todo`
    pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_replace_and_status_ops_emit_but_show_does_not():
    from app.services.ai.skills.skill_tool_service import SkillToolService

    svc = SkillToolService(skill_repo=_Repo())
    rec = AsyncMock()
    svc.recorder = rec

    await svc.execute(
        {
            "skill": "todo",
            "op": "replace",
            "items": [{"content": "a"}, {"content": "b"}],
        }
    )
    await svc.execute({"skill": "todo", "op": "in_progress", "id": 1})
    await svc.execute({"skill": "todo", "op": "show"})
    await svc.execute({"skill": "todo", "op": "complete", "id": 1})

    kinds = [c.args[0] for c in rec.record_event.await_args_list]
    assert kinds == ["todo_write", "todo_write", "todo_write"], kinds
    last = rec.record_event.await_args_list[-1].args[1]
    assert last["counts"] == {"total": 2, "completed": 1, "in_progress": 0}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_rejected_mutation_does_not_emit():
    """An invalid op leaves the list unchanged — and unchanged state is not
    a new frame."""
    from app.services.ai.skills.skill_tool_service import SkillToolService

    svc = SkillToolService(skill_repo=_Repo())
    rec = AsyncMock()
    svc.recorder = rec
    out = await svc.execute({"skill": "todo", "op": "complete", "id": 99})
    assert "error" in out
    rec.record_event.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_runner_hands_its_recorder_to_the_skill_tool_for_the_turn():
    """Attach for the turn, detach after — a skill tool still holding the
    previous run's recorder would file this run's todos under that run."""
    from unittest.mock import AsyncMock as _AM
    from uuid import UUID

    from app.schemas.ai_library import ComposedSystemPrompt
    from app.services.ai.runner.agent_runner import AgentRunner

    class _Tool:
        recorder = None

        async def execute(self, args):
            return {}

    tool = _Tool()
    adapter = _AM()
    seen = {}

    async def _call(composed, messages, **kw):
        seen["attached"] = tool.recorder is not None
        return {"choices": [{"message": {"content": "ok"}}]}

    adapter.call = _call
    runner = AgentRunner(adapter=adapter, skill_tool=tool)

    class _Rec:
        async def record_event(self, *a):
            pass

        def record_usage(self, **k):
            pass

        async def heartbeat(self):
            pass

        async def check_cancelled(self):
            return False

        def record_skill(self, s):
            pass

    composed = ComposedSystemPrompt(
        agent_id=UUID(int=0),
        agent_slug="t",
        model="m",
        temperature=0.0,
        max_tokens=16,
        system_message="s",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="",
    )
    await runner.run_turn(
        composed, [{"role": "user", "content": "hi"}], recorder=_Rec()
    )
    assert seen.get("attached") is True
    assert tool.recorder is None


# ── mirror: the snapshot also lands on agent_runs.metadata_json.todos ─────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_todo_write_mirrors_into_metadata_json(monkeypatch):
    """The Task Center reads agent_runs over Realtime, not transcript
    events — without the mirror the snapshot is durable but invisible.
    Phase 4: the whole ``view`` is mirrored (plus the legacy ``todos`` key
    until the frontend reads ``view``), by the one writer."""
    from contextlib import asynccontextmanager

    from sqlalchemy.dialects import postgresql

    from app.services.ai.runner import run_recorder as rr

    executed = []

    class _Session:
        async def execute(self, stmt, *a, **k):
            c = stmt.compile(dialect=postgresql.dialect())
            executed.append((str(c), dict(getattr(c, "params", {}))))

    @asynccontextmanager
    async def _ws():
        yield _Session()

    import app.db.session as dbs

    monkeypatch.setattr(dbs, "write_scope", _ws)
    rec = rr.RunRecorder.__new__(rr.RunRecorder)
    rec.run_id = 42
    rec._event_seq = 0
    rec._event_writer = None
    await rec.record_event(
        "todo_write",
        {
            "todos": [
                {"content": "a", "status": "completed"},
                {"content": "b", "status": "in_progress"},
            ],
            "counts": {"total": 2, "completed": 1, "in_progress": 1},
        },
    )
    inserts = [
        (s, p)
        for s, p in executed
        if "agent_run_transcript_events" in s and "INSERT" in s
    ]
    mirrors = [(s, p) for s, p in executed if "jsonb_set" in s]
    assert len(inserts) == 1 and len(mirrors) == 1, executed
    sql, params = mirrors[0]
    assert "agent_runs" in sql and "AS TEXT[]" in sql
    keys = [v for v in params.values() if isinstance(v, str)]
    assert {"view", "cost", "todos"} <= set(keys)
    view = next(v for v in params.values() if isinstance(v, dict) and "phase" in v)
    assert view["step"] == {"done": 1, "total": 2, "label": "b"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mirror_failure_is_contained(monkeypatch):
    """A dead DB must not fail the turn: insert and mirror both swallow."""
    from contextlib import asynccontextmanager

    from app.services.ai.runner import run_recorder as rr

    @asynccontextmanager
    async def _boom():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    import app.db.session as dbs

    monkeypatch.setattr(dbs, "write_scope", _boom)
    writer = rr.RunEventWriter(42)
    seq = await writer.append(
        "todo_write",
        {"todos": [], "counts": {"total": 1, "completed": 0, "in_progress": 0}},
    )  # must not raise
    assert seq is None  # phase 2a: no row landed → no seq to name it
    # ...but the in-process view still folds (readers in this process need it)
    assert writer.views["view"]["step"] == {"done": 0, "total": 1, "label": None}
