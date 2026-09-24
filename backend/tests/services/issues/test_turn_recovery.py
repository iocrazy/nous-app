"""A turn step that DBOS keeps re-executing is capped, and the count survives.

Recovery re-runs the step that was in flight when the worker died
(``run_issue_agent_step``, ``retries_allowed=False`` does not stop that). The
only bound used to be DBOS ``recovery_attempts``, and every re-execution was a
new billed run (prod: one execute_issue workflow reached recovery_attempts=6).
``run_issue_agent`` now counts attempts per ``<workflow_id>:<step_id>`` in
``execution_state.step_attempts`` and refuses past ISSUE_TURN_MAX_RECOVERIES.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services.issues import turn_recovery as tr

pytestmark = pytest.mark.unit


def test_no_dbos_context_means_no_key():
    assert tr.current_dbos_step_key() is None


def test_key_is_workflow_id_and_step_id(monkeypatch):
    from dbos import DBOS

    monkeypatch.setattr(DBOS, "workflow_id", "issue-9-abc")
    monkeypatch.setattr(DBOS, "step_id", 7)
    assert tr.current_dbos_step_key() == "issue-9-abc:7"


def test_a_workflow_without_a_step_is_not_a_key(monkeypatch):
    """In the workflow body (not inside a step) step_id is None — no key."""
    from dbos import DBOS

    monkeypatch.setattr(DBOS, "workflow_id", "issue-9-abc")
    monkeypatch.setattr(DBOS, "step_id", None)
    assert tr.current_dbos_step_key() is None


def test_increment_statement_is_one_atomic_update():
    from app.services.issues.execution_state import increment_step_attempt_stmt

    sql = str(
        increment_step_attempt_stmt(9, "wf:7").compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert sql.startswith("UPDATE public.issues SET execution_state=")
    assert "jsonb_build_object" in sql
    assert "'step_attempts'" in sql and "'wf:7'" in sql
    assert "AS INTEGER), 0) + 1" in sql  # read-and-add inside the SET
    assert "WHERE public.issues.id = 9 RETURNING" in sql


@pytest.mark.parametrize(
    "attempts,raises",
    [(1, False), (2, False), (3, False), (4, True)],
)
async def test_the_third_recovery_is_refused(monkeypatch, attempts, raises):
    """First execution = attempt 1; recoveries are attempts 2..; the limit is
    two recoveries, so attempt 4 (the third recovery) raises."""
    assert tr.ISSUE_TURN_MAX_RECOVERIES == 2
    monkeypatch.setattr(tr, "record_step_attempt", AsyncMock(return_value=attempts))
    if raises:
        with pytest.raises(tr.IssueTurnRecoveryLimitExceeded) as info:
            await tr.enforce_recovery_limit(9, "wf:7")
        assert info.value.error_code == "issue_turn_recovery_limit"
        assert info.value.attempts == 4
        assert str(info.value).startswith("[issue_turn_recovery_limit]")
    else:
        assert await tr.enforce_recovery_limit(9, "wf:7") == attempts


async def test_an_unrecordable_attempt_does_not_block_the_turn(monkeypatch):
    """The counter is a brake, not a gate: when it cannot be written the turn
    still runs (DBOS recovery_attempts remains the outer bound)."""

    async def _boom(issue_id, step_key):
        raise RuntimeError("db gone")

    import app.services.issues.execution_state as es

    monkeypatch.setattr(es, "increment_step_attempt", _boom)
    assert await tr.record_step_attempt(9, "wf:7") is None
    assert await tr.enforce_recovery_limit(9, "wf:7") is None


def _stub_turn(monkeypatch, m, chat_svc):
    monkeypatch.setattr(m, "get_or_create_issue_session", AsyncMock(return_value="11"))
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())
    monkeypatch.setattr(
        m, "attempt_forced_finish_declaration", AsyncMock(return_value=(None, None))
    )


async def test_run_issue_agent_refuses_before_any_turn_work(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    chat_svc = AsyncMock()
    _stub_turn(monkeypatch, m, chat_svc)
    monkeypatch.setattr(m, "current_dbos_step_key", lambda: "wf:7")
    counted = AsyncMock(return_value=4)
    monkeypatch.setattr(tr, "record_step_attempt", counted)

    with pytest.raises(tr.IssueTurnRecoveryLimitExceeded):
        await m.run_issue_agent(
            issue={"id": 9, "title": "t"}, agent_id="a", user_id="u"
        )
    counted.assert_awaited_once_with(9, "wf:7")
    chat_svc.run_session_turn.assert_not_awaited()


async def test_run_issue_agent_threads_the_step_key_into_the_turn(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={"assistant_message": {"content": "ok"}, "run_id": "5"}
    )
    _stub_turn(monkeypatch, m, chat_svc)
    monkeypatch.setattr(m, "current_dbos_step_key", lambda: "wf:7")
    monkeypatch.setattr(tr, "record_step_attempt", AsyncMock(return_value=2))

    await m.run_issue_agent(issue={"id": 9, "title": "t"}, agent_id="a", user_id="u")
    assert chat_svc.run_session_turn.await_args.kwargs["dbos_step_key"] == "wf:7"


async def test_without_a_dbos_context_the_turn_is_todays_turn(monkeypatch):
    """Negative control: no key → no counting and no new kwarg (fakes that pin
    run_session_turn's exact kwarg set keep working)."""
    from app.services.issues import issue_agent_executor as m

    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={"assistant_message": {"content": "ok"}, "run_id": "5"}
    )
    _stub_turn(monkeypatch, m, chat_svc)
    monkeypatch.setattr(m, "current_dbos_step_key", lambda: None)
    counted = AsyncMock()
    monkeypatch.setattr(tr, "record_step_attempt", counted)

    await m.run_issue_agent(issue={"id": 9, "title": "t"}, agent_id="a", user_id="u")
    counted.assert_not_awaited()
    assert "dbos_step_key" not in chat_svc.run_session_turn.await_args.kwargs


@pytest.mark.parametrize(
    "fn_name",
    ["record_step_attempt", "enforce_recovery_limit", "current_dbos_step_key"],
)
def test_new_helpers_are_not_dbos_steps(fn_name):
    fn = getattr(tr, fn_name)
    assert not hasattr(fn, "dbos_function_name")
    assert inspect.unwrap(fn) is fn


def test_the_limit_error_survives_a_pickle_round_trip():
    """DBOS pickles a step's exception and rebuilds it on replay; the typed
    prefix is the only typed signal on the blocked issue, so it must survive."""
    import pickle

    err = tr.IssueTurnRecoveryLimitExceeded(9, "wf:7", 4)
    back = pickle.loads(pickle.dumps(err))
    assert type(back) is tr.IssueTurnRecoveryLimitExceeded
    assert str(back) == str(err)
    assert str(back).startswith("[issue_turn_recovery_limit]")
    assert back.error_code == "issue_turn_recovery_limit"
    assert (back.issue_id, back.step_key, back.attempts) == (9, "wf:7", 4)
