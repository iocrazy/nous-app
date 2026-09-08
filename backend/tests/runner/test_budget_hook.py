"""Budget at the step boundary (spec §1-⑤, phase 1 = record + colour)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.ai.runner import run_projection as rp
from app.services.ai.runner.budget_hook import BudgetGateHook, BudgetInfo
from app.services.ai.runner.step_hooks import StepContext, StepDecision

pytestmark = pytest.mark.unit


class _Rec:
    def __init__(self, run_id=42, spent=0.0):
        self.run_id = run_id
        self.views = {"cost": {"spent_cents": spent}, "view": {"question": None}}
        self.events = []

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload, turn, step))


def _hook(budget, prior=0.0, calls=None, wrap_up=False, consume=None, consumed=None):
    async def load(recorder):
        if calls is not None:
            calls.append(recorder.run_id)
        return (
            None
            if budget is None
            else BudgetInfo(budget, prior, wrap_up=wrap_up, issue_id=7)
        )

    async def _consume(recorder, info):
        if consumed is not None:
            consumed.append((recorder.run_id, info.issue_id))
        return True

    return BudgetGateHook(load=load, consume=consume or _consume)


async def _step(hook, rec, step, spent, expect=StepDecision.CONTINUE):
    rec.views["cost"]["spent_cents"] = spent
    ctx = StepContext(turn=1, step=step, recorder=rec)
    assert await hook.before_llm_call(ctx) is expect
    return ctx


@pytest.mark.asyncio
async def test_warn_once_at_80_then_halt_once_at_100_into_a_question():
    rec = _Rec()
    hook = _hook(budget=100)
    for step, spent in enumerate([10.0, 79.9, 80.0, 90.0], start=1):
        await _step(hook, rec, step, spent)
    ctx = await _step(hook, rec, 5, 100.0, expect=StepDecision.STOP)
    assert ctx.stop_reason == "awaiting_input"
    kinds = [(e[0], e[1].get("action"), e[3]) for e in rec.events]
    assert kinds == [
        ("budget_check", "warn", 3),
        ("budget_check", "halt", 5),
        ("question_asked", None, 5),
    ]
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
    assert await bh.load_issue_budget(rec) == BudgetInfo(300, 12.5, False, issue_id=7)

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
    await _step(hook, rec, 2, 0.01, expect=StepDecision.STOP)
    assert [e[1].get("action") for e in rec.events] == ["halt", None]
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
    assert await bh.load_issue_budget(rec) == BudgetInfo(0, 0.0, False, issue_id=7)


# ── phase 2a Task 6: the halt becomes a typed three-way question ──────────


@pytest.mark.asyncio
async def test_halt_asks_budget_question_and_stops():
    rec = _Rec(spent=120.0)
    ctx = StepContext(turn=1, step=2, recorder=rec, parent_run_id=None)
    assert await _hook(budget=100).before_llm_call(ctx) is StepDecision.STOP
    assert ctx.stop_reason == "awaiting_input"
    assert [e[0] for e in rec.events] == ["budget_check", "question_asked"]
    qa = rec.events[1][1]
    assert qa["question_id"] == "budget:42" and qa["kind"] == "budget"
    assert [o["label"] for o in qa["options"]] == ["Top up", "Wrap up", "Cancel"]
    assert qa["allow_free_text"] is False
    assert "120" in qa["prompt"] and "100" in qa["prompt"]


@pytest.mark.asyncio
async def test_halt_asks_only_once_per_run():
    """A retried step boundary after the STOP must not ask twice."""
    rec = _Rec(spent=120.0)
    hook = _hook(budget=100)
    await _step(hook, rec, 2, 120.0, expect=StepDecision.STOP)
    await _step(hook, rec, 3, 120.0, expect=StepDecision.STOP)
    assert [e[0] for e in rec.events] == ["budget_check", "question_asked"]


@pytest.mark.asyncio
async def test_wrap_up_flag_lets_run_through_once():
    rec = _Rec(spent=120.0)
    ctx = StepContext(turn=1, step=2, recorder=rec, parent_run_id=None)
    hook = _hook(budget=100, wrap_up=True)
    assert await hook.before_llm_call(ctx) is StepDecision.CONTINUE
    assert ctx.stop_reason is None
    assert [e[0] for e in rec.events] == ["budget_check"]
    assert rec.events[-1][1]["action"] == "wrap_up"
    # the grace is ONE step: the next boundary halts and asks again
    ctx2 = await _step(hook, rec, 3, 130.0, expect=StepDecision.STOP)
    assert ctx2.stop_reason == "awaiting_input"
    assert [e[0] for e in rec.events] == [
        "budget_check",
        "budget_check",
        "question_asked",
    ]


@pytest.mark.asyncio
async def test_halt_still_stops_when_the_question_cannot_be_recorded():
    """No recorder row → the question is lost, but the run must not keep
    spending: park without buttons (legacy needs_input) rather than continue."""

    class _Refusing(_Rec):
        async def record_event(self, event_type, payload, *, turn=None, step=None):
            if event_type == "question_asked":
                raise RuntimeError("db down")
            await super().record_event(event_type, payload, turn=turn, step=step)

    rec = _Refusing(spent=120.0)
    ctx = StepContext(turn=1, step=2, recorder=rec, parent_run_id=None)
    assert await _hook(budget=100).before_llm_call(ctx) is StepDecision.STOP
    assert ctx.stop_reason == "awaiting_input"


def test_wrap_up_action_folds_into_the_view():
    v = rp.apply(
        rp.empty_views(),
        "budget_check",
        {"spent_cents": 120.0, "budget_cents": 100, "pct": 120.0, "action": "wrap_up"},
        seq=1,
    )
    assert v["view"]["budget"] == {"pct": 120, "state": "wrap_up", "spent_cents": 120.0}
    assert v["cost"]["pct"] == 120


@pytest.mark.asyncio
async def test_default_loader_reports_the_grace_but_does_not_consume_it(monkeypatch):
    """Review F5: claiming happens at the HALT (a run that never reaches 100 %
    must not burn the grace), so the loader is read-only."""
    from app.services.ai.runner import budget_hook as bh

    class _Issues:
        def __init__(self, flag):
            self.flag = flag

        async def get_by_id(self, issue_id):
            return {
                "id": issue_id,
                "budget_cents": 100,
                "execution_state": {"budget_wrap_up": self.flag} if self.flag else {},
            }

    class _Runs:
        async def spent_cents_for_issue(self, **kw):
            return 120.0

    async def resolve(**_):
        return [("issue", 7)]

    merge = AsyncMock()
    import app.repositories.agent_runs_repository as runs_mod
    import app.repositories.issue_repository as issues_mod
    import app.services.ai.runner.inbox as inbox_mod
    import app.services.issues.execution_state as es

    monkeypatch.setattr(runs_mod, "get_agent_runs_repository", lambda: _Runs())
    monkeypatch.setattr(inbox_mod, "resolve_targets", resolve)
    monkeypatch.setattr(es, "merge_execution_state", merge)
    rec = _Rec(run_id=42)
    rec.issue_id, rec.conversation_id = 7, None
    for flag, expect in (
        ({"run_id": "41", "at": "T"}, True),  # unconsumed
        ({"run_id": "41", "at": "T", "consumed_by": "42"}, True),  # mine (retry)
        ({"run_id": "41", "at": "T", "consumed_by": "40"}, False),  # spent earlier
        (None, False),
    ):
        monkeypatch.setattr(issues_mod, "issue_repository", _Issues(flag))
        assert (await bh.load_issue_budget(rec)).wrap_up is expect
    merge.assert_not_awaited()


@pytest.mark.asyncio
async def test_grace_is_claimed_at_the_halt_and_a_lost_claim_halts(monkeypatch):
    consumed = []
    rec = _Rec(spent=120.0)
    hook = _hook(budget=100, wrap_up=True, consumed=consumed)
    await _step(hook, rec, 1, 50.0)  # under budget: no claim yet
    assert consumed == []
    await _step(hook, rec, 2, 120.0)  # halt → claim → through
    assert consumed == [(42, 7)]
    assert rec.events[-1][1]["action"] == "wrap_up"

    async def lost(recorder, info):
        return False  # another run took it

    rec2 = _Rec(run_id=43, spent=120.0)
    ctx = await _step(
        _hook(budget=100, wrap_up=True, consume=lost),
        rec2,
        2,
        120.0,
        expect=StepDecision.STOP,
    )
    assert ctx.stop_reason == "awaiting_input"
    assert [e[0] for e in rec2.events] == ["budget_check", "question_asked"]
    assert rec2.events[0][1]["action"] == "halt"


@pytest.mark.asyncio
async def test_grace_is_one_step_then_the_gate_halts_again():
    """Review F6: "finish in one step" is enforced, not merely requested."""
    from app.services.ai.runner.budget_hook import WRAP_UP_GRACE_STEPS

    rec = _Rec(spent=120.0)
    hook = _hook(budget=100, wrap_up=True)
    await _step(hook, rec, 1, 120.0)  # the grace step
    for extra in range(1, WRAP_UP_GRACE_STEPS):
        await _step(hook, rec, 1 + extra, 125.0)
    ctx = await _step(
        hook, rec, 1 + WRAP_UP_GRACE_STEPS, 130.0, expect=StepDecision.STOP
    )
    assert ctx.stop_reason == "awaiting_input"
    assert [e[0] for e in rec.events] == [
        "budget_check",
        "budget_check",
        "question_asked",
    ]
    assert [e[1].get("action") for e in rec.events[:2]] == ["wrap_up", "halt"]


@pytest.mark.asyncio
async def test_unreadable_budget_fails_open_once_and_is_logged():
    """Review F3: a loader exception must not re-query every step nor stop the
    run; it is cached as "no budget" for this run (explicit fail-open)."""
    calls = []

    async def load(recorder):
        calls.append(recorder.run_id)
        raise RuntimeError("db down")

    hook = BudgetGateHook(load=load)
    rec = _Rec(spent=999.0)
    for step in (1, 2, 3):
        await _step(hook, rec, step, 999.0)
    assert calls == [42] and rec.events == []


@pytest.mark.asyncio
async def test_default_consumer_claims_through_execution_state(monkeypatch):
    import app.services.issues.execution_state as es
    from app.services.ai.runner import budget_hook as bh

    claim = AsyncMock(return_value=True)
    monkeypatch.setattr(es, "claim_budget_wrap_up", claim)
    rec = _Rec(run_id=42)
    assert (
        await bh.claim_wrap_up_grace(rec, BudgetInfo(100, 0.0, True, issue_id=7))
        is True
    )
    claim.assert_awaited_once_with(7, 42)
    claim.side_effect = RuntimeError("db down")
    assert (
        await bh.claim_wrap_up_grace(rec, BudgetInfo(100, 0.0, True, issue_id=7))
        is False
    )
    assert (
        await bh.claim_wrap_up_grace(rec, BudgetInfo(100, 0.0, True, issue_id=None))
        is False
    )


@pytest.mark.asyncio
async def test_halt_question_folds_into_a_marker_the_answer_endpoint_accepts():
    """Review F8: the linkage this task exists for — hook STOP → question_asked
    → view.question → marker → answer_matches — driven through the REAL fold
    and marker builder, not a hard-coded view."""
    from app.agent_framework.input_gate import build_awaiting_marker
    from app.services.ai.runner.question import answer_matches, payload_from_view

    class _Folding(_Rec):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.views = rp.empty_views()
            self.views["cost"] = {"spent_cents": kw.get("spent", 0.0)}
            self.seq = 0

        async def record_event(self, event_type, payload, *, turn=None, step=None):
            await super().record_event(event_type, payload, turn=turn, step=step)
            self.seq += 1
            self.views = rp.apply(self.views, event_type, payload, seq=self.seq)

    rec = _Folding(spent=120.0)
    ctx = StepContext(turn=1, step=2, recorder=rec, parent_run_id=None)
    assert await _hook(budget=100).before_llm_call(ctx) is StepDecision.STOP
    parked = rec.views["view"]["question"]
    assert parked and parked["id"] == "budget:42"
    question = payload_from_view(parked)
    marker = build_awaiting_marker(
        prompt=question["prompt"], issue_id=7, now="T", question=question
    )
    assert marker["question_id"] == "budget:42" and marker["kind"] == "budget"
    assert answer_matches(marker, "Wrap up") and answer_matches(marker, "Cancel")
    assert not answer_matches(marker, "whatever")  # allow_free_text is False


def test_chain_puts_the_budget_gate_before_the_inbox_claim():
    """Review F2: the claim is durable, the injection is not — a step that
    halts on the budget must not have claimed a steer it will never read."""
    import re
    from pathlib import Path

    src = Path("app/services/ai/chat/ai_library_chat_wiring.py").read_text()
    assert re.search(r"BudgetGateHook\(\),\s*InboxClaimHook\(\)", src)
