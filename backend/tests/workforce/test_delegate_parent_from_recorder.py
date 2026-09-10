"""``Delegate`` hangs its child off the run that is CURRENTLY executing.

Task 7a defect 5 — the same mistake as defect 1, in the other tool that
spawns work. ``DelegateToolService`` read ``parent_run_id`` from the
constructor, and the wiring passes the value answering a DIFFERENT question:
which run THIS turn is a child of, i.e. ``None`` on every root issue / chat
run (``build_agent_runner_stack``'s own docstring says so).

Three things went out wrong on a root run, and the third is not cosmetic:

* the inbox payload's ``parent_run_id`` was ``null``, so the delegated task's
  cost never rolled up to the tree that asked for it;
* the root abort registry never registered the child, so cancelling the root
  run did not fan out to it;
* ``_detect_cycle`` short-circuits on a falsy value — cycle protection was
  therefore OFF for exactly the runs users start.

It stayed invisible because production had zero persistent agents (defect 4),
so every Delegate call was refused before reaching any of this. Fixing that
one makes this one reachable.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.workforce import delegate_tool as _dt_mod
from app.services.workforce.delegate_tool import DelegateToolService

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_rate_limit_history():
    _dt_mod._dispatch_history.clear()
    yield
    _dt_mod._dispatch_history.clear()


@pytest.fixture(autouse=True)
def _enable_delegate(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FEATURE_WORKFORCE_DELEGATE", True)


class _Rec:
    """A root run's recorder: it has a row, and the run has no parent."""

    def __init__(self, run_id=900):
        self.run_id = run_id
        self.issue_id = 7
        self.conversation_id = None


def _service(*, parent_run_id=None, recorder=None, depth=0):
    target = {"id": str(uuid4()), "slug": "summarize", "persistent": True}
    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(return_value=target)
    workforce = MagicMock()
    workforce.enqueue_inbox = AsyncMock(return_value={"id": str(uuid4())})
    workforce.enqueue_outbox = AsyncMock(return_value={"id": str(uuid4())})
    svc = DelegateToolService(
        caller_agent_id=uuid4(),
        caller_user_id=uuid4(),
        parent_run_id=parent_run_id,
        agent_depth=depth,
        agent_repo=agent_repo,
        workforce_repo=workforce,
    )
    svc.parent_recorder = recorder
    svc._detect_cycle = AsyncMock(return_value=None)
    return svc, workforce


async def _delegate(svc):
    return await svc.execute({"agent_slug": "summarize", "prompt": "go"})


# ── the payload the worker rebuilds the child from ─────────────────────


async def test_payload_carries_the_running_run_id():
    svc, workforce = _service(parent_run_id=None, recorder=_Rec(900))
    out = await _delegate(svc)
    assert "error" not in out, out
    payload = workforce.enqueue_inbox.await_args.kwargs["payload"]
    assert payload["parent_run_id"] == "900"


async def test_a_depth_one_delegator_uses_its_own_run_id():
    svc, workforce = _service(parent_run_id="900", recorder=_Rec(51))
    await _delegate(svc)
    payload = workforce.enqueue_inbox.await_args.kwargs["payload"]
    assert payload["parent_run_id"] == "51"


async def test_without_a_recorder_the_constructor_value_still_wins():
    """The worker rebuilds this service from an inbox payload and has no
    recorder; its constructor value is the only id there is."""
    svc, workforce = _service(parent_run_id="900", recorder=None)
    await _delegate(svc)
    payload = workforce.enqueue_inbox.await_args.kwargs["payload"]
    assert payload["parent_run_id"] == "900"


# ── cycle protection must not be off on a root run ─────────────────────


async def test_cycle_detection_walks_from_the_running_run(monkeypatch):
    """A root run has no constructor value, so the walk used to return None
    before reading a single row — A→B→A could not be caught on any turn a
    user actually starts."""
    target_agent_id = uuid4()

    class _Session:
        async def execute(self, stmt):
            row = {
                "id": 900,
                "agent_id": str(target_agent_id),
                "parent_run_id": None,
            }
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *exc):
            return False

    import app.db.session as session_mod

    monkeypatch.setattr(session_mod, "read_scope", lambda: _Scope())

    svc, _ = _service(parent_run_id=None, recorder=_Rec(900))
    del svc._detect_cycle  # the real walker, not the stub

    assert await svc._detect_cycle(target_agent_id=target_agent_id) == 900


async def test_no_run_at_all_still_skips_the_walk():
    """Nothing to walk is not the same as a walk that found nothing — but
    with neither a recorder nor a constructor value there IS no chain."""
    svc, _ = _service(parent_run_id=None, recorder=None)
    del svc._detect_cycle
    assert await svc._detect_cycle(target_agent_id=uuid4()) is None


# ── the runner binds the recorder to BOTH spawning tools ───────────────


def test_the_turn_loops_bind_the_recorder_to_the_delegate_tool():
    from app.services.ai.runner.agent_runner import AgentRunner

    svc, _ = _service(parent_run_id=None, recorder=None)
    runner = SimpleNamespace(
        skill_tool=SimpleNamespace(subagent_task=None), delegate_tool=svc
    )
    rec = _Rec(900)

    AgentRunner._bind_turn_recorder(runner, rec)

    assert svc.parent_recorder is rec
    assert svc.active_parent_run_id == "900"


def test_binding_survives_a_runner_with_no_delegate_tool():
    from app.services.ai.runner.agent_runner import AgentRunner

    runner = SimpleNamespace(
        skill_tool=SimpleNamespace(subagent_task=None), delegate_tool=None
    )
    AgentRunner._bind_turn_recorder(runner, _Rec(900))  # must not raise
