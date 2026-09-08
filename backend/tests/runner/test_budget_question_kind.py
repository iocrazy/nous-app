"""Phase 2a Task 6: the ``budget`` question kind — Top up / Wrap up / Cancel.

``on_answer`` runs in the reply endpoint BEFORE the wake is delivered, with
``AnswerContext(target=issue_row, user_id, marker)``. It must be idempotent
(the endpoint has a double-send window) and must never trust the label the
endpoint already validated — an unknown value is still a typed rejection."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.ai.runner import question as q
from app.services.ai.runner.question_kinds import budget as bk

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"


def _issue(**kw):
    base = {"id": 7, "ai_session_id": 55, "budget_cents": 100, "execution_state": {}}
    base.update(kw)
    return base


def _ctx(issue, **marker):
    m = {"question_id": "budget:42", "kind": "budget", "run_id": "42"}
    m.update(marker)
    return q.AnswerContext(target=issue, user_id=ME, marker=m)


@pytest.fixture
def deps(monkeypatch):
    runs = SimpleNamespace(spent_cents_for_issue=AsyncMock(return_value=120.0))
    inbox = SimpleNamespace(enqueue=AsyncMock(return_value={"id": 1}))
    issues = SimpleNamespace(
        get_by_id=AsyncMock(return_value=_issue()),
        transition_status=AsyncMock(return_value=_issue(status="cancelled")),
    )
    merge = AsyncMock()
    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.repositories.agent_runs_repository as runs_mod
    import app.repositories.issue_repository as issues_mod
    import app.services.issues.execution_state as es

    monkeypatch.setattr(runs_mod, "get_agent_runs_repository", lambda: runs)
    monkeypatch.setattr(inbox_mod, "get_agent_run_inbox_repository", lambda: inbox)
    monkeypatch.setattr(issues_mod, "issue_repository", issues)
    monkeypatch.setattr(es, "merge_execution_state", merge)
    return SimpleNamespace(runs=runs, inbox=inbox, issues=issues, merge=merge)


def test_budget_kind_is_registered_as_a_singleton():
    assert q.registered_kinds() == ["budget", "user"]
    assert q.on_answer_for("budget") is bk.on_answer
    assert q.question_id_for("budget", 42, 9) == "budget:42"
    assert [o["label"] for o in bk.BUDGET_OPTIONS] == ["Top up", "Wrap up", "Cancel"]


async def test_top_up_re_reads_the_budget_and_rejects_when_still_exhausted(deps):
    deps.issues.get_by_id.return_value = _issue(budget_cents=100)
    deps.runs.spent_cents_for_issue.return_value = 120.0
    with pytest.raises(q.AnswerRejected) as ei:
        await bk.on_answer(_issue(budget_cents=999), "Top up", _ctx(_issue()))
    assert (ei.value.status, ei.value.code) == (409, "budget_still_exhausted")
    # the FRESH row is what counts, not the row the endpoint loaded earlier
    deps.issues.get_by_id.assert_awaited_once_with(7)
    deps.runs.spent_cents_for_issue.assert_awaited_once_with(
        issue_id=7, conversation_id=55
    )


async def test_top_up_passes_once_the_budget_exceeds_the_spend(deps):
    deps.issues.get_by_id.return_value = _issue(budget_cents=500)
    await bk.on_answer(_issue(), "Top up", _ctx(_issue()))
    deps.merge.assert_not_awaited()
    deps.inbox.enqueue.assert_not_awaited()


async def test_top_up_with_the_budget_removed_passes(deps):
    deps.issues.get_by_id.return_value = _issue(budget_cents=None)  # unlimited
    await bk.on_answer(_issue(), "Top up", _ctx(_issue()))


async def test_wrap_up_writes_the_flag_then_queues_the_one_step_steer(deps):
    await bk.on_answer(_issue(), "Wrap up", _ctx(_issue(), run_id="42"))
    deps.merge.assert_awaited_once()
    issue_id, patch = deps.merge.await_args.args
    assert issue_id == 7
    flag = patch["budget_wrap_up"]
    assert flag["run_id"] == "42" and flag["at"] and flag.get("consumed_by") is None
    kw = deps.inbox.enqueue.await_args.kwargs
    assert (kw["target_kind"], kw["target_id"], kw["kind"]) == ("issue", 7, "steer")
    assert kw["user_id"] == ME and kw["content"]["body"] == bk.WRAP_UP_STEER
    assert "one step" in bk.WRAP_UP_STEER


async def test_cancel_transitions_the_issue_with_the_typed_reason(deps):
    await bk.on_answer(_issue(), "Cancel", _ctx(_issue()))
    deps.issues.transition_status.assert_awaited_once_with(7, "cancelled")
    deps.merge.assert_awaited_once_with(7, {"outcome_reason": "budget_exhausted"})
    deps.inbox.enqueue.assert_not_awaited()


async def test_unknown_value_is_a_typed_400(deps):
    with pytest.raises(q.AnswerRejected) as ei:
        await bk.on_answer(_issue(), "Maybe", _ctx(_issue()))
    assert (ei.value.status, ei.value.code) == (400, "answer_shape")


async def test_target_without_an_issue_id_is_a_typed_409(deps):
    with pytest.raises(q.AnswerRejected) as ei:
        await bk.on_answer({"session_id": "5"}, "Top up", _ctx({"session_id": "5"}))
    assert (ei.value.status, ei.value.code) == (409, "no_issue_target")


async def test_top_up_with_an_unreadable_spend_is_a_typed_503(deps):
    """Review F4: a failed SUM is not "nothing spent"."""
    deps.runs.spent_cents_for_issue.side_effect = RuntimeError("db down")
    with pytest.raises(q.AnswerRejected) as ei:
        await bk.on_answer(_issue(), "Top up", _ctx(_issue()))
    assert (ei.value.status, ei.value.code) == (503, "budget_unreadable")


async def test_wrap_up_is_idempotent_for_the_same_question(deps):
    """Review F7: the double-send window must not queue a second steer or
    reset the flag."""
    deps.issues.get_by_id.return_value = _issue(
        execution_state={"budget_wrap_up": {"run_id": "42", "at": "T"}}
    )
    await bk.on_answer(_issue(), "Wrap up", _ctx(_issue(), run_id="42"))
    deps.merge.assert_not_awaited()
    deps.inbox.enqueue.assert_not_awaited()
    # a flag already CONSUMED (an earlier grace) is not the same question
    deps.issues.get_by_id.return_value = _issue(
        execution_state={
            "budget_wrap_up": {"run_id": "42", "at": "T", "consumed_by": "43"}
        }
    )
    await bk.on_answer(_issue(), "Wrap up", _ctx(_issue(), run_id="42"))
    deps.merge.assert_awaited_once()
    deps.inbox.enqueue.assert_awaited_once()
