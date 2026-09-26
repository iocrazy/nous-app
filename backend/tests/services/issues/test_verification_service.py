"""verify → execution_state / message / event → outcome conversion."""

from unittest.mock import AsyncMock

import pytest

from app.services.issues.verification import service as svc
from app.services.issues.verification.evidence import EvidenceBundle
from app.services.issues.verification.judge import JudgeResult, JudgeTimeout
from app.services.issues.verification.predicates import PredicateResult

pytestmark = pytest.mark.unit  # asyncio_mode = "auto" runs the async tests

KW = dict(
    issue_id=5,
    session_id="s",
    user_id="22222222-2222-2222-2222-222222222222",
    trigger="issue_dispatch",
    attribution="direct_human",
)


@pytest.fixture
def wired(monkeypatch):
    bundle = EvidenceBundle(5, "r1", (), (), (), 0, "text", False, (), ())
    monkeypatch.setattr(svc, "build_evidence_bundle", AsyncMock(return_value=bundle))
    monkeypatch.setattr(svc, "run_predicates", lambda trigger, b: ())
    j = AsyncMock(return_value=JudgeResult("pass", (), 0.9, "v1"))
    monkeypatch.setattr(svc, "judge", j)
    monkeypatch.setattr(svc, "verification_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        svc, "load_acceptance_criteria", AsyncMock(return_value=("12 beats", "user"))
    )
    monkeypatch.setattr(
        svc,
        "_load_issue_row",
        AsyncMock(return_value={"execution_state": {}, "assignee_agent_id": "a1"}),
    )
    merge = AsyncMock()
    monkeypatch.setattr(svc, "merge_execution_state", merge)
    msg = AsyncMock()
    monkeypatch.setattr(svc, "_record_verdict_message", msg)
    ev = AsyncMock()
    monkeypatch.setattr(svc, "_record_verification_event", ev)
    return j, merge, msg, ev


async def _apply(outcome="completed", result=None, **over):
    return await svc.apply_completion_verification(
        outcome=outcome,
        reason="done",
        result=result or {"run_id": "r1"},
        content="text",
        **{**KW, **over},
    )


async def test_pass_keeps_completed_and_records(wired):
    j, merge, msg, ev = wired
    outcome, reason, v = await _apply()
    assert (outcome, reason) == ("completed", "done")
    assert (
        v["verdict"] == "pass"
        and v["attempt"] == 1
        and v["retry"] is False
        and v["verifier_run_id"] == "v1"
    )
    patch = merge.await_args.args[1]
    assert patch["verify_attempts"] == 1 and patch["verification"] == v
    msg.assert_awaited_once()
    ev.assert_awaited_once_with("r1", v)


async def test_fail_first_time_becomes_continue_with_retry(wired):
    j, merge, msg, ev = wired
    j.return_value = JudgeResult(
        "fail", ({"criterion": "12 beats", "why": "only 3"},), 0.8, "v1"
    )
    outcome, reason, v = await _apply()
    assert outcome == "continue" and reason == "verifier_rejected: 12 beats: only 3"
    assert v["retry"] is True and v["attempt"] == 1


async def test_fail_past_the_cap_stays_completed(wired):
    j, merge, msg, ev = wired
    j.return_value = JudgeResult("fail", ({"criterion": "c", "why": "w"},), 0.8, "v1")
    svc._load_issue_row.return_value = {"execution_state": {"verify_attempts": 2}}
    outcome, reason, v = await _apply()
    assert (
        outcome == "completed"
        and v["verdict"] == "fail"
        and v["retry"] is False
        and v["attempt"] == 3
    )


async def test_violated_predicate_short_circuits_without_judge(wired, monkeypatch):
    j, *_ = wired
    monkeypatch.setattr(
        svc,
        "run_predicates",
        lambda t, b: (
            PredicateResult("shots_exist", "violated", {"shot_deliverables": 0}),
        ),
    )
    outcome, reason, v = await _apply()
    assert (
        outcome == "continue"
        and v["source"] == "predicate"
        and v["unmet"][0]["criterion"] == "shots_exist"
    )
    j.assert_not_awaited()


async def test_judge_failure_is_unverified_never_pass(wired):
    j, *_ = wired
    j.side_effect = JudgeTimeout("slow")
    outcome, reason, v = await _apply()
    assert (
        outcome == "completed"
        and v["verdict"] == "unverified"
        and v["reason"] == "verifier_timeout"
    )


async def test_no_criteria_runs_predicates_on_the_text_and_skips_judge(wired):
    j, *_ = wired
    svc.load_acceptance_criteria.return_value = (None, None)
    outcome, reason, v = await _apply()
    assert (
        v["verdict"] == "unverified"
        and v["reason"] == "criteria_missing"
        and v["criteria_source"] is None
    )
    j.assert_not_awaited()


@pytest.mark.parametrize(
    "result",
    [
        {"stop_reason": "cancelled"},
        {"stop_reason": "paused"},
        {"awaiting_input": True, "question": {"kind": "budget"}},
    ],
)
async def test_not_sent_to_review(wired, result):
    j, merge, *_ = wired
    outcome, reason, v = await _apply(result={**result, "run_id": "r1"})
    assert (outcome, v) == ("completed", None)
    j.assert_not_awaited()
    merge.assert_not_awaited()


async def test_other_outcomes_untouched(wired):
    assert await _apply(outcome="needs_input") == ("needs_input", "done", None)
    assert await _apply(outcome=None) == (None, "done", None)


async def test_disabled_switch_is_a_no_op(wired):
    svc.verification_enabled.return_value = False
    assert await _apply() == ("completed", "done", None)


async def test_any_exception_degrades_to_unverified(wired):
    svc.build_evidence_bundle.side_effect = RuntimeError("boom")
    outcome, reason, v = await _apply()
    assert (
        outcome == "completed"
        and v["verdict"] == "unverified"
        and v["reason"] == "verifier_error"
    )


def test_public_helpers_are_not_dbos_steps():
    from app.services.issues import verification as v

    for fn in (
        v.apply_completion_verification,
        v.pending_verifier_feedback,
        svc.verify_completion,
    ):
        assert not hasattr(
            fn, "__wrapped__"
        ), fn  # DBOS.step wraps; plain coroutines do not


# ── follow-up: reply road has no retry turn; counter resets per dispatch ──


async def test_allow_retry_false_keeps_completed_and_stores_no_retry(wired):
    j, merge, *_ = wired
    j.return_value = JudgeResult("fail", ({"criterion": "c", "why": "w"},), 0.8, "v1")
    outcome, reason, v = await _apply(allow_retry=False)
    assert (outcome, reason) == ("completed", "done")
    assert v["verdict"] == "fail" and v["retry"] is False and v["attempt"] == 1
    assert (
        merge.await_args.args[1]["verification"]["retry"] is False
    )  # no pending feedback later


async def test_reset_verify_attempts_zeroes_the_counter(monkeypatch):
    merge = AsyncMock()
    monkeypatch.setattr(svc, "merge_execution_state", merge)
    await svc.reset_verify_attempts(5)
    merge.assert_awaited_once_with(5, {"verify_attempts": 0})


async def test_reset_verify_attempts_never_raises(monkeypatch):
    monkeypatch.setattr(
        svc, "merge_execution_state", AsyncMock(side_effect=RuntimeError("db"))
    )
    await svc.reset_verify_attempts(5)  # logged, not raised
