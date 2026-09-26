"""fh5 T5: an inbox item claimed by a step whose LLM call never answered goes
back to the queue — exactly once per failure, at most twice per item.

Before: ``InboxClaimHook`` stamped ``claimed_at`` and injected the item into
``messages``; when the call then raised (``AllModelsFailed``) or the process
died, the injected copy was discarded with ``messages`` and the stamp stayed,
so nothing ever delivered the steer again (recon-d §1b/§1d).

Now:
* the recorder tracks the ids claimed by the step in flight; ``_step_ended``
  (the LLM answered) clears them;
* ``_finish`` releases what is left — only when it closed the row itself and
  only for ``failed`` (cancelled = "stop", completed = consumed);
* the writers that close a run without ``_finish`` (heartbeat_lost sweep,
  worker shutdown, liveness ``_mark_dead`` / startup reconcile) release the
  run's items whose ``claimed_step`` has no ``step_end``;
* a recovery run releases its superseded predecessor's items the same way;
* the counter in ``content.redelivered`` caps it: at 2 the item is expired
  with an ERROR instead; an item on a done / cancelled / hidden issue is never
  resurrected.
"""

from __future__ import annotations

import contextlib
import inspect
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories import agent_run_inbox_redelivery as redelivery
from app.services.ai.runner.run_recorder import RunRecorder

pytestmark = pytest.mark.unit


@pytest.fixture
def caplog_loguru():
    """Loguru records (level name + message) emitted during the test."""
    from loguru import logger

    records: list[dict] = []
    sink = logger.add(
        lambda m: records.append(
            {"level": m.record["level"].name, "message": m.record["message"]}
        ),
        level="DEBUG",
    )
    try:
        yield records
    finally:
        logger.remove(sink)


def _sql(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


# ── statement shape (RED 5) ──────────────────────────────────────────────


def test_run_scoped_release_is_guarded_by_owner_liveness_and_terminal_issue():
    stmts = redelivery.unclaim_for_run_stmt(777, [11, 12])
    unclaim, expire = _sql(stmts.unclaim), _sql(stmts.expire)
    for sql in (unclaim, expire):
        assert "agent_run_inbox.claimed_run_id = 777" in sql
        assert "agent_run_inbox.expired_at IS NULL" in sql
        assert "agent_run_inbox.id IN (11, 12)" in sql
        # never resurrect an item whose issue is over
        assert (
            "'done'" in sql and "'cancelled'" in sql and "hidden_at IS NOT NULL" in sql
        )
    assert "claimed_at=NULL" in unclaim and "claimed_run_id=NULL" in unclaim
    assert "claimed_step=NULL" in unclaim and "claimed_turn=NULL" in unclaim
    assert "redelivered" in unclaim and "||" in unclaim
    assert "< 2" in unclaim
    assert "expired_at=" in expire and ">= 2" in expire
    assert "claimed_at=NULL" not in expire
    assert "RETURNING" in unclaim and "RETURNING" in expire


def test_orphan_release_requires_no_step_end_at_the_claimed_step():
    stmts = redelivery.unclaim_orphaned_by_run_stmt([5, 6])
    for sql in (_sql(stmts.unclaim), _sql(stmts.expire)):
        assert "agent_run_inbox.claimed_run_id IN (5, 6)" in sql
        assert "agent_run_inbox.expired_at IS NULL" in sql
        assert "NOT (EXISTS" in sql
        assert "agent_run_transcript_events.event_type = 'step_end'" in sql
        assert (
            "public.agent_run_transcript_events.run_id = "
            "public.agent_run_inbox.claimed_run_id" in sql
        )
        assert (
            "public.agent_run_transcript_events.step = "
            "public.agent_run_inbox.claimed_step" in sql
        )
        assert "hidden_at IS NOT NULL" in sql


def test_terminal_statuses_match_the_dispatch_gate():
    from app.services.issues.inbox_or_dispatch import TERMINAL_STATUSES

    assert tuple(redelivery.TERMINAL_ISSUE_STATUSES) == tuple(TERMINAL_STATUSES)


# ── release executor: cap + ERROR (RED 6) ────────────────────────────────


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def _scope(monkeypatch, *, expired_rows, unclaimed_rows, executed):
    class _S:
        async def execute(self, stmt):
            executed.append(stmt)
            sql = str(stmt)
            return _Rows(expired_rows if "expired_at=" in sql else unclaimed_rows)

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr(redelivery, "write_scope", _ws)


async def test_capped_item_is_expired_with_one_error(monkeypatch, caplog_loguru):
    executed: list = []
    _scope(
        monkeypatch,
        expired_rows=[(11, "issue", 9, 2)],
        unclaimed_rows=[(12, "issue", 9, 1)],
        executed=executed,
    )
    out = await redelivery.release_claims_for_run(777, [11, 12], reason="failed")
    assert out == redelivery.RedeliveryOutcome(redelivered=(12,), expired=(11,))
    assert len(executed) == 2
    errors = [r for r in caplog_loguru if r["level"] == "ERROR"]
    assert len(errors) == 1
    assert "11" in errors[0]["message"] and "issue 9" in errors[0]["message"]


async def test_nothing_to_release_issues_no_statement(monkeypatch):
    executed: list = []
    _scope(monkeypatch, expired_rows=[], unclaimed_rows=[], executed=executed)
    out = await redelivery.release_claims_for_run(777, [], reason="failed")
    assert out == redelivery.RedeliveryOutcome()
    out = await redelivery.release_orphaned_claims([], reason="heartbeat_lost")
    assert out == redelivery.RedeliveryOutcome()
    assert executed == []


async def test_a_database_error_is_logged_not_raised(monkeypatch, caplog_loguru):
    @contextlib.asynccontextmanager
    async def _boom():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(redelivery, "write_scope", _boom)
    out = await redelivery.release_orphaned_claims([5], reason="heartbeat_lost")
    assert out == redelivery.RedeliveryOutcome()
    assert any(
        r["level"] == "ERROR" and "db down" in r["message"] for r in caplog_loguru
    )


# ── recorder: in-memory set (RED 1) ──────────────────────────────────────


def _recorder() -> RunRecorder:
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
    rec.run_id = "777"
    return rec


def test_answered_step_clears_the_claims_and_a_new_step_starts_fresh():
    rec = _recorder()
    rec.note_inbox_claimed([11, 12])
    assert rec.inbox_claims_in_flight == (11, 12)
    rec.note_step_answered()
    assert rec.inbox_claims_in_flight == ()
    rec.note_inbox_claimed([13])
    assert rec.inbox_claims_in_flight == (13,)


def test_stub_recorders_built_with_new_still_read_an_empty_set():
    """Many tests build ``RunRecorder.__new__(RunRecorder)``; the field must be
    a class-level default, not a ``default_factory``."""
    assert RunRecorder.__new__(RunRecorder).inbox_claims_in_flight == ()


# ── recorder: _finish releases only failed-and-ours (RED 2/3/4) ──────────


class _Result:
    def __init__(self, rowcount):
        self.rowcount = rowcount


async def _finish(monkeypatch, *, status, rowcount=1, claimed=(11, 12)):
    class _S:
        async def execute(self, stmt):
            return _Result(rowcount)

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr("app.db.session.write_scope", _ws)
    rec = _recorder()
    rec.note_inbox_claimed(list(claimed))
    release = AsyncMock(return_value=redelivery.RedeliveryOutcome())
    with (
        patch.object(redelivery, "release_claims_for_run", release),
        patch("app.services.ai_usage.record_usage", AsyncMock()),
        patch("app.services.search.projection.project_run_best_effort", AsyncMock()),
        patch("app.services.ai.billing.tree_charge.settle_tree_if_closed", AsyncMock()),
    ):
        await rec._finish(status=status)
    return release


async def test_failed_run_releases_exactly_the_unanswered_claims(monkeypatch):
    release = await _finish(monkeypatch, status="failed")
    release.assert_awaited_once()
    args, kwargs = release.await_args
    assert (args, kwargs["reason"]) == ((777, (11, 12)), "failed")


@pytest.mark.parametrize("status", ["cancelled", "completed"])
async def test_cancelled_and_completed_keep_their_claims(monkeypatch, status):
    release = await _finish(monkeypatch, status=status)
    release.assert_not_awaited()


async def test_a_run_closed_by_someone_else_does_not_release(monkeypatch):
    release = await _finish(monkeypatch, status="failed", rowcount=0)
    release.assert_not_awaited()


async def test_a_failed_run_with_nothing_in_flight_does_not_release(monkeypatch):
    release = await _finish(monkeypatch, status="failed", claimed=())
    release.assert_not_awaited()


# ── hook + runner seam (RED 1) ───────────────────────────────────────────


async def test_the_claim_hook_records_the_claimed_ids_on_the_recorder():
    import datetime as dt

    from app.services.ai.runner.inbox import InboxItem
    from app.services.ai.runner.inbox_hook import InboxClaimHook
    from app.services.ai.runner.step_hooks import StepContext

    now = dt.datetime(2026, 9, 26, tzinfo=dt.timezone.utc)
    items = [
        InboxItem(
            id=310819108761499 + i,
            target_kind="issue",
            target_id=7,
            kind="steer",
            content={"body": "b"},
            created_at=now,
            user_id="u",
        )
        for i in range(2)
    ]

    async def claim(tg, run_id, turn, step):
        return items

    async def resolve(*, issue_id=None, conversation_id=None):
        return [("issue", 7)]

    rec = _recorder()
    rec.issue_id = 7
    rec.record_event = AsyncMock()
    hook = InboxClaimHook(claim=claim, resolve=resolve)
    await hook.before_llm_call(
        StepContext(turn=1, step=3, recorder=rec, parent_run_id=None)
    )
    assert rec.inbox_claims_in_flight == (310819108761499, 310819108761500)


async def test_step_ended_marks_the_step_answered():
    from app.services.ai.runner.agent_runner import AgentRunner

    rec = _recorder()
    rec.note_inbox_claimed([11])
    rec.record_event = AsyncMock()
    runner = AgentRunner.__new__(AgentRunner)
    await runner._step_ended(rec, None, 1, 0.0, {}, "stop")
    assert rec.inbox_claims_in_flight == ()


# ── crash writers (RED 7) ────────────────────────────────────────────────


class _FlipRow:
    def __init__(self, run_id):
        self.id = run_id


async def test_heartbeat_lost_and_worker_shutdown_release_the_flipped_runs():
    from app.repositories import agent_runs_repository as runs_mod

    release = AsyncMock(return_value=redelivery.RedeliveryOutcome())
    with (
        patch.object(redelivery, "release_orphaned_claims", release),
        patch("app.services.search.projection.project_run_id_best_effort", AsyncMock()),
        patch(
            "app.services.liveness.crash_rollup.record_crash_terminal_runs",
            AsyncMock(),
        ),
    ):
        swept = await runs_mod._after_crash_flip(
            [_FlipRow(5), _FlipRow(6)], reason="heartbeat_lost"
        )
    assert swept == [5, 6]
    release.assert_awaited_once_with([5, 6], reason="heartbeat_lost")


def test_both_crash_writers_pass_their_reason():
    from app.repositories import agent_runs_repository as runs_mod

    repo = runs_mod.AgentRunsRepository
    assert 'reason="heartbeat_lost"' in inspect.getsource(repo.mark_heartbeat_lost_ids)
    assert "reason=WORKER_SHUTDOWN_ERROR_CODE" in inspect.getsource(
        repo.mark_worker_shutdown_ids
    )


async def test_mark_dead_releases_the_killed_run(monkeypatch):
    from app.workflows import liveness_scanner

    class _S:
        async def execute(self, stmt):
            class _R:
                def fetchall(self):
                    return [_FlipRow(5)]

            return _R()

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr("app.db.session.write_scope", _ws)
    release = AsyncMock(return_value=redelivery.RedeliveryOutcome())
    with (
        patch.object(redelivery, "release_orphaned_claims", release),
        patch("app.services.search.projection.project_run_id_best_effort", AsyncMock()),
        patch(
            "app.services.liveness.crash_rollup.record_crash_terminal_runs",
            AsyncMock(),
        ),
        patch("app.services.ai.billing.tree_charge.settle_tree_if_closed", AsyncMock()),
    ):
        await liveness_scanner._mark_dead(5, "stuck", reason="liveness_dead")
    release.assert_awaited_once_with([5], reason="liveness_dead")


async def test_startup_reconcile_releases_the_stranded_runs(monkeypatch):
    from app.db import engine as db_engine
    from app.services.liveness import reconcile

    class _S:
        async def execute(self, stmt):
            class _R:
                def fetchall(self):
                    return [_FlipRow(5), _FlipRow(8)]

            return _R()

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr(db_engine, "is_configured", lambda: True)
    monkeypatch.setattr("app.db.session.write_scope", _ws)
    release = AsyncMock(return_value=redelivery.RedeliveryOutcome())
    with (
        patch.object(redelivery, "release_orphaned_claims", release),
        patch("app.services.search.projection.project_run_id_best_effort", AsyncMock()),
        patch(
            "app.services.liveness.crash_rollup.record_crash_terminal_runs",
            AsyncMock(),
        ),
        patch("app.services.ai.billing.tree_charge.settle_tree_if_closed", AsyncMock()),
    ):
        await reconcile.reconcile_stranded_runs()
    release.assert_awaited_once_with([5, 8], reason="stranded_on_restart")


# ── recovery takes over the predecessor's items (RED 8) ──────────────────


async def test_recovery_run_releases_the_superseded_runs_items(monkeypatch):
    from app.services.ai.runner import step_recovery

    order: list = []

    async def _noop(self):
        return None

    async def _insert(self):
        order.append("insert")
        self.run_id = "777"

    async def _find(step_key, *, issue_id, user_id):
        return {"id": 555, "status": "running"}

    async def _stamp(old_id, new_id):
        order.append("stamp")

    async def _release(run_ids, *, reason):
        order.append(("release", list(run_ids), reason))
        return redelivery.RedeliveryOutcome()

    monkeypatch.setattr(RunRecorder, "_pre_flight_check_paused", _noop)
    monkeypatch.setattr(RunRecorder, "_snapshot_price", _noop)
    monkeypatch.setattr(RunRecorder, "_insert_row", _insert)
    monkeypatch.setattr(step_recovery, "find_prior_run", _find)
    monkeypatch.setattr(step_recovery, "stamp_superseded", _stamp)
    monkeypatch.setattr(redelivery, "release_orphaned_claims", _release)
    rec = RunRecorder(
        agent_id=uuid4(),
        user_id=uuid4(),
        trigger="issue_dispatch",
        issue_id=9,
        dbos_step_key="wf:7",
    )
    await rec._start_once()
    assert ("release", [555], "recovered") in order


async def test_a_first_execution_releases_nothing(monkeypatch):
    from app.services.ai.runner import step_recovery

    async def _noop(self):
        return None

    async def _insert(self):
        self.run_id = "777"

    async def _find(step_key, *, issue_id, user_id):
        return None

    release = AsyncMock()
    monkeypatch.setattr(RunRecorder, "_pre_flight_check_paused", _noop)
    monkeypatch.setattr(RunRecorder, "_snapshot_price", _noop)
    monkeypatch.setattr(RunRecorder, "_insert_row", _insert)
    monkeypatch.setattr(step_recovery, "find_prior_run", _find)
    monkeypatch.setattr(redelivery, "release_orphaned_claims", release)
    await RunRecorder(
        agent_id=uuid4(),
        user_id=uuid4(),
        trigger="issue_dispatch",
        issue_id=9,
        dbos_step_key="wf:7",
    )._start_once()
    release.assert_not_awaited()


# ── no new DBOS step ─────────────────────────────────────────────────────


@pytest.mark.parametrize("fn", ["release_claims_for_run", "release_orphaned_claims"])
def test_the_release_functions_are_not_dbos_steps(fn):
    f = getattr(redelivery, fn)
    assert not hasattr(f, "dbos_function_name")
    assert inspect.unwrap(f) is f


# ── what the model sees on re-delivery ───────────────────────────────────


@pytest.mark.parametrize(
    "kind,content",
    [
        ("steer", {"body": "focus on act two"}),
        ("pause", {"reason": "user asked"}),  # no text/body: the JSON fallback
    ],
)
def test_a_redelivered_item_renders_byte_identical_to_its_first_delivery(kind, content):
    import datetime as dt

    from app.services.ai.runner.inbox import InboxItem, render_inbox_message

    def _render(c):
        return render_inbox_message(
            InboxItem(
                id=310819108761499,
                target_kind="issue",
                target_id=7,
                kind=kind,
                content=c,
                created_at=dt.datetime(2026, 9, 26, tzinfo=dt.timezone.utc),
                user_id="u",
            )
        )

    again = {**content, redelivery.REDELIVERED_KEY: 1}
    assert _render(again) == _render(content)
    assert "redelivered" not in _render(again)
