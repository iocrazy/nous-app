"""The parent of a spawned child is the run that is CURRENTLY executing.

Task 7a defect 1 (2026-09-10 real-stack acceptance). ``SubAgentTaskService``
took ``parent_run_id`` from the constructor, but the wiring passes the value
that answers a DIFFERENT question — which run THIS turn hangs off, which is
``None`` on a root issue/chat run. On every root run the whole return chain
therefore went out with ``parent_run_id=None``: the child's ``agent_runs`` row
never attached, ``subagent_done`` was never written back, ``view.children``
stayed at ``queued`` forever, and continue-by-``child_run_id`` could not pass
``_child_chain_ok``.

The fix resolves the id from the active recorder at spawn time. These tests
pin BOTH directions: a root run (no constructor value) uses its recorder's
run id, and a depth-1 sub-agent uses its OWN run id rather than the ancestor
id it inherited on the payload.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.ai.runner.subagent_task_service import SubAgentTaskService
from tests.test_subagent_task_service import _wire_sync_spawn

pytestmark = pytest.mark.unit


class _Rec:
    """A root issue run's recorder: it has a row (run_id), and the run it
    belongs to has no parent of its own."""

    def __init__(self, run_id=900, issue_id=7):
        self.run_id = run_id
        self.issue_id = issue_id
        self.conversation_id = None
        self.events: list[tuple[str, dict]] = []

    async def record_event(self, event_type, payload, **kw):
        self.events.append((event_type, payload))


def _svc(**kw) -> SubAgentTaskService:
    base = dict(
        caller_agent_id=uuid4(),
        caller_user_id=uuid4(),
        # A ROOT issue run: the wiring has nothing to pass here.
        parent_run_id=None,
    )
    return SubAgentTaskService(**{**base, **kw})


async def _ok(value):
    return value


def _patch_create_task(monkeypatch, sink: dict):
    import app.repositories.agent_workforce_repository as wf_mod

    class _Repo:
        @staticmethod
        async def create_task(*, agent_id, user_id, payload, title=None, **kw):
            sink.update(payload=payload)
            return {"id": "task-1"}

    monkeypatch.setattr(wf_mod, "get_agent_workforce_repository", lambda: _Repo())


# ── async (await=false) ─────────────────────────────────────────────────


async def test_async_payload_carries_the_running_run_id(monkeypatch):
    """The worker rebuilds the service from this payload and writes the
    attach + ``subagent_done`` off ``parent_run_id``. A null there is the
    whole return chain gone."""
    created: dict = {}
    _patch_create_task(monkeypatch, created)

    svc = _svc(parent_recorder=_Rec(run_id=900))
    monkeypatch.setattr(svc, "_resolve_agent_id", lambda slug: _ok(uuid4()))

    out = await svc.spawn(
        {"subagent_type": "librarian", "prompt": "dig", "await": False}
    )

    assert out["status"] == "queued"
    assert created["payload"]["parent_run_id"] == "900"


async def test_async_payload_of_a_depth_one_child_uses_its_own_run_id(
    monkeypatch,
):
    """A sub-agent inherits the ANCESTOR id on its payload; its own children
    hang off IT, not off its parent."""
    created: dict = {}
    _patch_create_task(monkeypatch, created)

    # agent_depth stays 0 so the background form is allowed; what is under
    # test is which id wins, not the depth cap.
    svc = _svc(parent_run_id="900", parent_recorder=_Rec(run_id=51))
    monkeypatch.setattr(svc, "_resolve_agent_id", lambda slug: _ok(uuid4()))

    await svc.spawn({"subagent_type": "librarian", "prompt": "dig", "await": False})

    assert created["payload"]["parent_run_id"] == "51"


async def test_without_a_recorder_the_constructor_value_still_wins(monkeypatch):
    """The worker path builds the service from a payload and has no recorder;
    its constructor value is the only id there is."""
    created: dict = {}
    _patch_create_task(monkeypatch, created)

    svc = SubAgentTaskService(
        caller_agent_id=uuid4(),
        caller_user_id=uuid4(),
        parent_run_id="900",
        issue_id=7,
    )
    monkeypatch.setattr(svc, "_resolve_agent_id", lambda slug: _ok(uuid4()))

    await svc.spawn({"subagent_type": "librarian", "prompt": "dig", "await": False})

    assert created["payload"]["parent_run_id"] == "900"


# ── sync (await=true) ───────────────────────────────────────────────────


async def test_sync_spawn_attaches_the_child_to_the_running_run(monkeypatch):
    """``agent_runs.parent_run_id`` / ``root_run_id`` and the child's own
    stack must all name the run that is executing right now."""
    import app.services.ai.chat.ai_library_chat_wiring as wiring_mod
    import app.services.ai.scope.scope_binding as scope_mod
    import app.services.workforce.agent_worker as worker_mod

    wired = _wire_sync_spawn(monkeypatch)
    attach = AsyncMock()
    monkeypatch.setattr(worker_mod, "_attach_to_parent_run", attach)

    rec = _Rec(run_id=900)
    svc = _svc(parent_recorder=rec)

    out = await svc.spawn({"subagent_type": "librarian", "prompt": "dig"})

    assert out["status"] == "success"
    assert attach.await_args is not None, "a root run's child was never attached"
    assert attach.await_args.kwargs["parent_run_id"] == "900"
    # the child's own stack, its recorder metadata, and the scope it inherits
    assert (
        wiring_mod.build_agent_runner_stack.await_args.kwargs["parent_run_id"] == "900"
    )
    assert wired.recorders[0].kwargs["metadata"]["parent_run_id"] == "900"
    assert scope_mod.resolve_dispatch_scope.await_args.kwargs["parent_run_id"] == "900"


async def test_sync_spawn_of_a_depth_one_child_attaches_to_its_own_run(monkeypatch):
    import app.services.workforce.agent_worker as worker_mod

    _wire_sync_spawn(monkeypatch)
    attach = AsyncMock()
    monkeypatch.setattr(worker_mod, "_attach_to_parent_run", attach)

    svc = _svc(parent_run_id="900", agent_depth=1, parent_recorder=_Rec(run_id=51))

    out = await svc.spawn({"subagent_type": "librarian", "prompt": "dig"})

    assert out["status"] == "success"
    assert attach.await_args.kwargs["parent_run_id"] == "51"


# ── continue (child_run_id) ─────────────────────────────────────────────


async def test_child_chain_check_walks_up_to_the_running_run(monkeypatch):
    """``_child_chain_ok`` short-circuited to False whenever the constructor
    value was empty, so a root run could never continue its OWN child."""

    class _Session:
        async def execute(self, stmt):
            # the child's (parent_run_id, fork_of_run_id, issue_id) — the
            # walker selects all three since Task 7b defect D, and a stub one
            # column short refuses every check instead of answering it
            return SimpleNamespace(first=lambda: (900, None, None))

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *exc):
            return False

    import app.db.session as session_mod

    monkeypatch.setattr(session_mod, "read_scope", lambda: _Scope())

    svc = _svc(parent_recorder=_Rec(run_id=900))
    assert await svc._child_chain_ok("51") is True


# ── the runner → service binding both loops depend on ───────────────────


def _runner_with(svc, delegate=None, *, recorder_reason="cancelled"):
    """A real AgentRunner whose first step boundary stops the turn. The binding
    happens before the loop, so a turn that stops immediately still proves it —
    and nothing calls a model."""
    from app.services.ai.runner.agent_runner import AgentRunner
    from app.services.ai.runner.step_hooks import StepHookChain
    from tests.runner.test_turn_end_reasons import _StopHook

    class _Tool:
        recorder = None
        subagent_task = None

        async def execute(self, args):
            return {}

    tool = _Tool()
    tool.subagent_task = svc
    runner = AgentRunner(
        adapter=SimpleNamespace(),  # no `stream`: production's shape
        skill_tool=tool,
        step_hooks=StepHookChain([_StopHook(recorder_reason)]),
    )
    runner.delegate_tool = delegate
    return runner


async def test_the_buffered_loop_binds_the_recorder_before_any_tool_runs():
    """Behavioural replacement for a source-substring assertion: that version
    stayed green when the call moved into a branch that never executes, and
    went red on a rename that changed nothing.

    ``active_parent_run_id`` reads the recorder, so a loop that does not bind
    it detaches every child that turn spawns."""
    from tests.runner.test_turn_end_reasons import _composed, _Rec

    svc = _svc(parent_recorder=None)
    rec = _Rec()
    rec.run_id = 900
    await _runner_with(svc).run_turn(
        _composed(), [{"role": "user", "content": "q"}], recorder=rec
    )
    assert svc.parent_recorder is rec
    assert svc.active_parent_run_id == "900"


async def test_the_streaming_loop_binds_it_itself(monkeypatch):
    """``stream_turn`` delegates to ``run_turn`` on the production adapter
    shape, so a binding done only there would look identical here. The stub
    ``run_turn`` therefore checks the binding was ALREADY made when the
    streaming loop handed over."""
    from tests.runner.test_turn_end_reasons import _composed, _Rec

    svc = _svc(parent_recorder=None)
    rec = _Rec()
    rec.run_id = 900
    runner = _runner_with(svc)
    bound_at_handover: list = []

    async def _fake_run_turn(*a, **kw):
        bound_at_handover.append(svc.parent_recorder)
        return {"content": "", "raw": {}, "cancelled": True}

    monkeypatch.setattr(runner, "run_turn", _fake_run_turn)

    async for _ in runner.stream_turn(
        _composed(),
        [{"role": "user", "content": "q"}],
        recorder=rec,
        auto_recorder=False,
    ):
        pass

    assert bound_at_handover == [rec], bound_at_handover


async def test_both_tools_are_bound_not_just_the_task_one():
    """``Delegate`` reads the same property — and for it an unbound recorder
    also turns cycle detection off (defect 5)."""
    from tests.runner.test_turn_end_reasons import _composed, _Rec

    svc = _svc(parent_recorder=None)

    class _Delegate:
        parent_recorder = None

    delegate = _Delegate()
    rec = _Rec()
    rec.run_id = 900
    await _runner_with(svc, delegate).run_turn(
        _composed(), [{"role": "user", "content": "q"}], recorder=rec
    )
    assert svc.parent_recorder is rec and delegate.parent_recorder is rec


def test_binding_is_a_no_op_without_a_service_or_a_recorder():
    from app.services.ai.runner.agent_runner import AgentRunner

    svc = _svc(parent_recorder=None)
    runner = SimpleNamespace(skill_tool=SimpleNamespace(subagent_task=svc))

    AgentRunner._bind_turn_recorder(runner, None)
    assert svc.parent_recorder is None

    rec = _Rec(run_id=900)
    AgentRunner._bind_turn_recorder(runner, rec)
    assert svc.parent_recorder is rec
    assert svc.active_parent_run_id == "900"

    # no service installed → nothing to bind, and no crash
    AgentRunner._bind_turn_recorder(
        SimpleNamespace(skill_tool=SimpleNamespace(subagent_task=None)), rec
    )
