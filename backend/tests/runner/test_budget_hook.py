"""Budget at the step boundary (spec §1-⑤, phase 1 = record + colour)."""

from __future__ import annotations

import pytest

from app.services.ai.runner import run_projection as rp
from app.services.ai.runner.budget_hook import BudgetGateHook
from app.services.ai.runner.step_hooks import StepContext, StepDecision

pytestmark = pytest.mark.unit


class _Rec:
    def __init__(self, run_id=42, spent=0.0):
        self.run_id = run_id
        self.views = {"cost": {"spent_cents": spent}}
        self.events = []

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload, turn, step))


def _hook(budget, prior=0.0, calls=None):
    async def load(recorder):
        if calls is not None:
            calls.append(recorder.run_id)
        return None if budget is None else (budget, prior)

    return BudgetGateHook(load=load)


async def _step(hook, rec, step, spent):
    rec.views["cost"]["spent_cents"] = spent
    ctx = StepContext(turn=1, step=step, recorder=rec)
    assert (
        await hook.before_llm_call(ctx) is StepDecision.CONTINUE
    )  # phase 1 never stops
    return ctx


@pytest.mark.asyncio
async def test_warn_once_at_80_then_halt_once_at_100_and_never_stops():
    rec = _Rec()
    hook = _hook(budget=100)
    for step, spent in enumerate([10.0, 79.9, 80.0, 90.0, 100.0, 150.0], start=1):
        await _step(hook, rec, step, spent)
    kinds = [(e[1]["action"], e[3]) for e in rec.events]
    assert kinds == [("warn", 3), ("halt", 5)]
    warn = rec.events[0][1]
    assert warn == {
        "spent_cents": 80.0,
        "budget_cents": 100,
        "pct": 80.0,
        "action": "warn",
    }
    assert rec.events[0][2] == 1  # turn coordinate


@pytest.mark.asyncio
async def test_no_budget_means_no_event_and_loader_runs_once_per_run():
    calls = []
    rec = _Rec()
    hook = _hook(budget=None, calls=calls)
    for step in (1, 2, 3):
        await _step(hook, rec, step, 999.0)
    assert rec.events == [] and calls == [42]


@pytest.mark.asyncio
async def test_prior_runs_spend_counts_toward_the_issue_budget():
    rec = _Rec()
    hook = _hook(budget=200, prior=150.0)
    await _step(hook, rec, 1, 20.0)  # 170 / 200 = 85 %
    assert [e[1]["action"] for e in rec.events] == ["warn"]
    assert rec.events[0][1]["spent_cents"] == 170.0


@pytest.mark.asyncio
async def test_child_runs_and_recorderless_steps_are_ignored():
    rec = _Rec(spent=500.0)
    hook = _hook(budget=100)
    await hook.before_llm_call(
        StepContext(turn=1, step=1, recorder=rec, parent_run_id="9")
    )
    await hook.before_llm_call(StepContext(turn=1, step=1, recorder=None))
    assert rec.events == []


def test_budget_check_turns_the_view_yellow_then_red():
    v = rp.apply(
        rp.empty_views(),
        "budget_check",
        {"spent_cents": 82.0, "budget_cents": 100, "pct": 82.0, "action": "warn"},
        seq=1,
    )
    assert v["view"]["budget"] == {"pct": 82, "state": "warn", "spent_cents": 82.0}
    v = rp.apply(
        v,
        "budget_check",
        {"spent_cents": 101.0, "budget_cents": 100, "pct": 101.0, "action": "halt"},
        seq=2,
    )
    assert v["view"]["budget"]["state"] == "over" and v["cost"]["pct"] == 101


@pytest.mark.asyncio
async def test_default_loader_reads_issue_budget_and_prior_spend(monkeypatch):
    from app.services.ai.runner import budget_hook as bh

    class _Issues:
        async def get_by_id(self, issue_id):
            return {"id": issue_id, "budget_cents": 300}

    class _Runs:
        async def spent_cents_for_issue(
            self, *, issue_id, conversation_id, exclude_run_id
        ):
            assert (issue_id, conversation_id, exclude_run_id) == (7, 9, 42)
            return 12.5

    async def resolve(*, issue_id=None, conversation_id=None):
        return [("conversation", 9), ("issue", 7)]

    import app.repositories.agent_runs_repository as runs_mod
    import app.repositories.issue_repository as issues_mod
    import app.services.ai.runner.inbox as inbox_mod

    monkeypatch.setattr(issues_mod, "issue_repository", _Issues())
    monkeypatch.setattr(runs_mod, "get_agent_runs_repository", lambda: _Runs())
    monkeypatch.setattr(inbox_mod, "resolve_targets", resolve)
    rec = _Rec(run_id=42)
    rec.conversation_id = 9
    rec.issue_id = None
    assert await bh.load_issue_budget(rec) == (300, 12.5)

    class _NoBudget(_Issues):
        async def get_by_id(self, issue_id):
            return {"id": issue_id, "budget_cents": None}

    monkeypatch.setattr(issues_mod, "issue_repository", _NoBudget())
    assert await bh.load_issue_budget(rec) is None


@pytest.mark.asyncio
async def test_zero_budget_is_a_real_budget_any_spend_is_over(monkeypatch):
    rec = _Rec()
    hook = _hook(budget=0)
    await _step(hook, rec, 1, 0.0)  # nothing spent yet → silent
    await _step(hook, rec, 2, 0.01)
    assert [e[1]["action"] for e in rec.events] == ["halt"]
    assert rec.events[0][1]["pct"] == 100.0


@pytest.mark.asyncio
async def test_default_loader_keeps_a_zero_budget(monkeypatch):
    from app.services.ai.runner import budget_hook as bh

    class _Issues:
        async def get_by_id(self, issue_id):
            return {"id": issue_id, "budget_cents": 0}

    class _Runs:
        async def spent_cents_for_issue(self, **kw):
            return 0.0

    async def resolve(**_):
        return [("issue", 7)]

    import app.repositories.agent_runs_repository as runs_mod
    import app.repositories.issue_repository as issues_mod
    import app.services.ai.runner.inbox as inbox_mod

    monkeypatch.setattr(issues_mod, "issue_repository", _Issues())
    monkeypatch.setattr(runs_mod, "get_agent_runs_repository", lambda: _Runs())
    monkeypatch.setattr(inbox_mod, "resolve_targets", resolve)
    rec = _Rec(run_id=42)
    rec.issue_id = 7
    rec.conversation_id = None
    assert await bh.load_issue_budget(rec) == (0, 0.0)
