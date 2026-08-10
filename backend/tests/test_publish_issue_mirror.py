"""Unit tests for the publish→issue mirror sweeper."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

import app.workflows.publish_issue_mirror as wf

_NOW = datetime(2026, 8, 10, 5, 1, tzinfo=timezone.utc)
_FUTURE = _NOW + timedelta(hours=3)  # the real douyin case: live 3h later
_PAST = _NOW - timedelta(minutes=1)


class TestDecisionHelpers:
    def test_initial_status_by_phase(self):
        assert wf.issue_status_for_phase("queued") == "todo"
        assert wf.issue_status_for_phase("in_progress") == "in_progress"
        assert wf.issue_status_for_phase("failed") == "blocked"
        assert wf.issue_status_for_phase("lost") == "blocked"
        assert wf.issue_status_for_phase("cancelled") is None
        assert wf.issue_status_for_phase(None) is None

    def test_completed_batches_are_mirrored_p1_1(self):
        """P1-1 regression guard. A batch that finishes inside the sweeper's
        2-minute window used to return None here, so every SUCCESSFUL publish
        was skipped forever and the to-do only ever showed failures. It must
        now produce a non-terminal status — the terminal sync closes it (that
        path is what stamps completed_at)."""
        assert wf.issue_status_for_phase("completed") == "in_progress"
        assert (
            wf.issue_status_for_phase("completed", scheduled_at=_FUTURE, now=_NOW)
            == "in_progress"
        )

    def test_terminal_sync_targets(self):
        assert wf.terminal_sync_action("completed", "in_progress") == "done"
        assert wf.terminal_sync_action("failed", "in_progress") == "blocked"
        assert wf.terminal_sync_action("lost", "todo") == "blocked"
        assert wf.terminal_sync_action("cancelled", "todo") == "cancelled"
        # Already-settled issues are never touched again.
        assert wf.terminal_sync_action("completed", "done") is None
        assert wf.terminal_sync_action("failed", "blocked") is None
        assert wf.terminal_sync_action("completed", "cancelled") is None

    def test_schedule_gate_holds_completed_open_p1_2(self):
        """P1-2 regression guard. ``phase='completed'`` only means OUR browser
        job finished typing the schedule into the platform; the post goes live
        hours later. Closing on that would show done up to 3h early."""
        assert (
            wf.terminal_sync_action(
                "completed", "in_progress", scheduled_at=_FUTURE, now=_NOW
            )
            is None
        )
        # Once the go-live instant passes, the same batch is allowed to close.
        assert (
            wf.terminal_sync_action(
                "completed", "in_progress", scheduled_at=_PAST, now=_NOW
            )
            == "done"
        )
        # No schedule at all = immediate publish; unchanged behaviour.
        assert (
            wf.terminal_sync_action(
                "completed", "in_progress", scheduled_at=None, now=_NOW
            )
            == "done"
        )
        # The gate is only about "not live yet" — a failed scheduled batch
        # still needs a human right now.
        assert (
            wf.terminal_sync_action(
                "failed", "in_progress", scheduled_at=_FUTURE, now=_NOW
            )
            == "blocked"
        )

    def test_awaiting_helper_normalizes_naive_timestamps(self):
        naive_future = _FUTURE.replace(tzinfo=None)
        assert wf.is_awaiting_platform_schedule(naive_future, _NOW) is True
        assert (
            wf.is_awaiting_platform_schedule(_PAST.replace(tzinfo=None), _NOW) is False
        )
        assert wf.is_awaiting_platform_schedule(None, _NOW) is False
        assert wf.is_awaiting_platform_schedule("not-a-datetime", _NOW) is False

    def test_scheduled_note_carries_the_go_live_instant(self):
        note = wf.build_scheduled_note(_FUTURE)
        assert note is not None
        assert "2026-08-10 08:01" in note  # human-readable UTC
        assert _FUTURE.isoformat() in note  # exact instant, no rounding
        assert wf.build_scheduled_note(None) is None

    def test_origin_id_keeps_snowflake_string(self):
        big = "9007199254740993"
        assert wf.build_publish_origin_id(big) == f"publish:{big}"


class _FakeIssueRepo:
    def __init__(self, existing=None):
        self._existing = existing or {}
        self.created = []
        self.transitions = []
        self.descriptions: dict[int, str] = {}

    async def list_by_origin(self, origin_kind, origin_id, *, include_hidden=False):
        return list(self._existing.get(origin_id, []))

    async def atomic_create(self, payload):
        self.created.append(payload)
        return {**payload, "id": 900 + len(self.created)}

    async def transition_status(self, issue_id, new_status, *, dbos_workflow_id=None):
        self.transitions.append((issue_id, new_status))
        return {"id": issue_id, "status": new_status}

    # P1-3: the mirror annotates an issue before blocking it, so the reason the
    # read-back gave survives into what the user reads.
    async def get_by_id(self, issue_id):
        return {"id": issue_id, "description": self.descriptions.get(issue_id, "")}

    async def update(self, issue_id, patch):
        if "description" in patch:
            self.descriptions[issue_id] = patch["description"]
        return {"id": issue_id, **patch}


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeRowsResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_FakeRowsResult":
        return self

    def all(self) -> list[dict]:
        return self._rows


class _FakeExecResult:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount


class _FakeSession:
    """Distinguishes the two SELECTs by their compiled WHERE clause (same
    idiom the pre-ORM raw-SQL test used: ``"issue_id IS NULL" in sql``) and
    records every UPDATE's bind params AND compiled SQL — the SQL capture
    matters because the ``issue_id IS NULL`` overwrite guard on the stamp
    UPDATE produces no bind param of its own (final review fault injection:
    dropping that guard left every test green when only params were
    inspected)."""

    def __init__(self, *, unmirrored=None, mirrored_open=None) -> None:
        self._unmirrored = unmirrored or []
        self._mirrored_open = mirrored_open or []
        self.stamped: list[dict[str, Any]] = []
        self.stamp_sql: list[str] = []

    async def execute(self, stmt: Any):
        sql, params = _compile(stmt)
        if isinstance(stmt, Select):
            if "task_tracking.issue_id IS NULL" in sql:
                return _FakeRowsResult(self._unmirrored)
            return _FakeRowsResult(self._mirrored_open)
        self.stamped.append(params)
        self.stamp_sql.append(sql)
        return _FakeExecResult()


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def harness(monkeypatch):
    def _install(*, unmirrored=None, mirrored_open=None, existing=None):
        issues = _FakeIssueRepo(existing)
        session = _FakeSession(unmirrored=unmirrored, mirrored_open=mirrored_open)

        monkeypatch.setattr(wf, "read_scope", lambda: _ScopeCM(session))
        monkeypatch.setattr(wf, "write_scope", lambda: _ScopeCM(session))
        monkeypatch.setattr(
            "app.repositories.issue_repository.get_issue_repository",
            lambda: issues,
        )
        return issues, session

    return _install


_ROW = {
    "dbos_workflow_id": "wf-1",
    "title": "Publish: Teaser cut",
    "user_id": "u-1",
    "phase": "in_progress",
    "publish_task_id": "9007199254740993",
    "team_id": 42,
}


@pytest.mark.asyncio
async def test_mirrors_a_running_batch_and_stamps_backlink(harness):
    issues, session = harness(unmirrored=[_ROW])
    counts = await wf._mirror_new_batches()
    assert counts["created"] == 1
    payload = issues.created[0]
    assert payload["origin_kind"] == "publish"
    assert payload["origin_id"] == "publish:9007199254740993"
    assert payload["status"] == "in_progress"
    assert payload["team_id"] == 42
    assert "assignee_agent_id" not in payload  # mirror never assigns
    assert "wf-1" in session.stamped[0].values()
    assert 901 in session.stamped[0].values()  # atomic_create's fabricated id


@pytest.mark.asyncio
async def test_existing_origin_reuses_issue_and_still_stamps(harness):
    issues, session = harness(
        unmirrored=[_ROW],
        existing={"publish:9007199254740993": [{"id": 77, "status": "todo"}]},
    )
    counts = await wf._mirror_new_batches()
    assert counts["created"] == 0
    assert issues.created == []
    assert 77 in session.stamped[0].values()  # backlink repaired, no duplicate


@pytest.mark.asyncio
async def test_stamp_update_carries_issue_id_null_overwrite_guard(harness):
    """The stamp UPDATE's WHERE clause must keep ``task_tracking.issue_id
    IS NULL`` — it's the only thing stopping a re-run from clobbering an
    already-stamped row's backlink with a different issue_id. That clause
    contributes no bind param, so a test that only inspects
    ``session.stamped[i]`` (params) can't see it go missing; this asserts
    against the compiled SQL text instead (final review, Minor 2)."""
    _, session = harness(unmirrored=[_ROW])
    await wf._mirror_new_batches()

    assert len(session.stamp_sql) == 1
    sql = session.stamp_sql[0]
    assert sql.startswith("UPDATE public.task_tracking SET issue_id=")
    assert "task_tracking.issue_id IS NULL" in sql
    assert "task_tracking.dbos_workflow_id = " in sql


def _real_future() -> datetime:
    """Relative to the wall clock — the sweep helpers read ``now()`` for real."""
    return datetime.now(timezone.utc) + timedelta(days=1)


def _real_past() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=1)


@pytest.mark.asyncio
async def test_fast_completed_batch_is_mirrored_then_closed_same_sweep(harness):
    """End-to-end of the P1-1 fix: a batch that was already ``completed`` the
    first time the sweeper looked (the 61-second production case) gets an
    issue AND that issue lands on done — via transition_status, so
    completed_at is stamped."""
    completed = {**_ROW, "phase": "completed", "scheduled_at": None}
    issues, _ = harness(
        unmirrored=[completed],
        # What the second query sees once the create above has committed.
        mirrored_open=[
            {
                "dbos_workflow_id": "wf-1",
                "phase": "completed",
                "issue_id": 901,
                "issue_status": "in_progress",
                "scheduled_at": None,
            }
        ],
    )
    created_counts = await wf._mirror_new_batches()
    assert created_counts["created"] == 1
    assert issues.created[0]["status"] == "in_progress"
    assert "description" not in issues.created[0]  # nothing scheduled to say

    synced = await wf._sync_terminal_batches()
    assert synced["synced"] == 1
    assert issues.transitions == [(901, "done")]


@pytest.mark.asyncio
async def test_scheduled_batch_is_mirrored_open_with_the_go_live_time(harness):
    """P1-2 on the create side: the issue exists, is NOT done, and carries the
    planned go-live instant so the to-do can answer "when does this land?"."""
    when = _real_future()
    issues, _ = harness(
        unmirrored=[{**_ROW, "phase": "completed", "scheduled_at": when}]
    )
    counts = await wf._mirror_new_batches()

    assert counts["created"] == 1
    payload = issues.created[0]
    assert payload["status"] == "in_progress"
    assert payload["status"] != "done"
    assert when.isoformat() in payload["description"]


@pytest.mark.asyncio
async def test_scheduled_completed_batch_is_not_closed_before_go_live(harness):
    """The load-bearing guard. Without the schedule gate this row transitions
    straight to done — the to-do would say finished up to three hours before
    anything is live on the platform."""
    issues, _ = harness(
        mirrored_open=[
            {
                "dbos_workflow_id": "wf-1",
                "phase": "completed",
                "issue_id": 5,
                "issue_status": "in_progress",
                "scheduled_at": _real_future(),
            }
        ]
    )
    counts = await wf._sync_terminal_batches()

    assert issues.transitions == []
    assert counts["synced"] == 0
    assert counts["awaiting_schedule"] == 1


@pytest.mark.asyncio
async def test_scheduled_batch_closes_once_go_live_has_passed(harness):
    issues, _ = harness(
        mirrored_open=[
            {
                "dbos_workflow_id": "wf-1",
                "phase": "completed",
                "issue_id": 5,
                "issue_status": "in_progress",
                "scheduled_at": _real_past(),
            }
        ]
    )
    counts = await wf._sync_terminal_batches()

    assert issues.transitions == [(5, "done")]
    assert counts["synced"] == 1
    assert counts["awaiting_schedule"] == 0


@pytest.mark.asyncio
async def test_failed_scheduled_batch_still_blocks_immediately(harness):
    """The gate must not delay incidents: a scheduled batch that failed needs
    a human now, not at its (never-reached) go-live time."""
    issues, _ = harness(
        mirrored_open=[
            {
                "dbos_workflow_id": "wf-1",
                "phase": "failed",
                "issue_id": 6,
                "issue_status": "in_progress",
                "scheduled_at": _real_future(),
            }
        ]
    )
    counts = await wf._sync_terminal_batches()

    assert issues.transitions == [(6, "blocked")]
    assert counts["awaiting_schedule"] == 0


@pytest.mark.asyncio
async def test_terminal_sync_closes_and_blocks(harness):
    issues, _ = harness(
        mirrored_open=[
            {
                "dbos_workflow_id": "wf-1",
                "phase": "completed",
                "issue_id": 1,
                "issue_status": "in_progress",
            },
            {
                "dbos_workflow_id": "wf-2",
                "phase": "failed",
                "issue_id": 2,
                "issue_status": "todo",
            },
        ]
    )
    counts = await wf._sync_terminal_batches()
    assert counts["synced"] == 2
    assert issues.transitions == [(1, "done"), (2, "blocked")]


# ── P1-3: the read-back gate ────────────────────────────────────────────
#
# P1-2 released the issue on a CLOCK. Reaching go-live says nothing about
# whether the platform reviewed it, refused it, dropped the schedule, or the
# user deleted the post — so "done" was still an assumption, just a
# better-timed one. These pin the replacement.


class TestReadbackVerdictAggregation:
    """One batch, N accounts -> one word. Precedence is the design."""

    def test_no_session_rows_means_nothing_to_verify(self):
        assert wf.readback_verdict(None) == wf.READBACK_NOT_REQUIRED
        assert wf.readback_verdict([]) == wf.READBACK_NOT_REQUIRED

    def test_all_verified_lets_the_issue_close(self):
        assert wf.readback_verdict(["verified", "verified"]) == "verified"

    def test_one_not_live_account_outranks_three_good_ones(self):
        """A partial failure a human must see. Averaging it away is exactly how
        it becomes invisible."""
        states = ["verified", "verified", "not_live", "verified"]
        assert wf.readback_verdict(states) == "not_live"

    def test_not_live_outranks_abandoned_and_pending(self):
        assert wf.readback_verdict(["abandoned", "not_live", "pending"]) == "not_live"

    def test_abandoned_outranks_pending(self):
        assert wf.readback_verdict(["pending", "abandoned"]) == "abandoned"

    def test_a_never_attempted_row_reads_as_pending(self):
        """NULL is 'we have not looked yet', not 'nothing to look at'."""
        assert wf.readback_verdict([None, "verified"]) == "pending"

    def test_not_supported_does_not_hold_the_user_hostage(self):
        """A platform without a read-back is OUR coverage gap. Blocking the
        user's work item over it would punish them for something they cannot
        act on — so it falls back to P1-2's time-based release."""
        assert wf.readback_verdict(["not_supported"]) == "verified"


class TestReadbackGate:
    def test_verified_closes_the_issue(self):
        assert (
            wf.terminal_sync_action(
                "completed",
                "in_progress",
                scheduled_at=_real_past(),
                readback="verified",
            )
            == "done"
        )

    def test_not_live_blocks_instead_of_closing(self):
        assert (
            wf.terminal_sync_action(
                "completed",
                "in_progress",
                scheduled_at=_real_past(),
                readback="not_live",
            )
            == "blocked"
        )

    def test_abandoned_blocks_too(self):
        """ "We could not confirm it went live" must never render as done."""
        assert (
            wf.terminal_sync_action(
                "completed",
                "in_progress",
                scheduled_at=_real_past(),
                readback="abandoned",
            )
            == "blocked"
        )

    def test_pending_holds_the_issue_open(self):
        assert (
            wf.terminal_sync_action(
                "completed",
                "in_progress",
                scheduled_at=_real_past(),
                readback="pending",
            )
            is None
        )

    def test_the_clock_is_checked_before_the_readback(self):
        """Ordering guard: a batch that has not reached go-live must never be
        blocked for 'not being live yet'."""
        assert (
            wf.terminal_sync_action(
                "completed",
                "in_progress",
                scheduled_at=_real_future(),
                readback="not_live",
            )
            is None
        )

    def test_default_keeps_the_pre_p1_3_behaviour(self):
        """An OAuth/h5 batch has no session rows and therefore no verdict; it
        must close exactly as it did before."""
        assert wf.terminal_sync_action("completed", "in_progress") == "done"


def _readback_row(**overrides):
    row = {
        "dbos_workflow_id": "wf-1",
        "phase": "completed",
        "issue_id": 5,
        "issue_status": "in_progress",
        "scheduled_at": _real_past(),
        "readback_states": ["verified"],
        "readback_details": [None],
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_scheduled_batch_closes_only_after_a_verified_readback(harness):
    """The happy path of the new gate: go-live passed AND the platform
    confirmed the post. Only then does the work item close."""
    issues, _ = harness(mirrored_open=[_readback_row()])
    counts = await wf._sync_terminal_batches()

    assert issues.transitions == [(5, "done")]
    assert counts["synced"] == 1


@pytest.mark.asyncio
async def test_gone_from_the_platform_blocks_the_issue_with_a_reason(harness):
    """The user's acceptance case: schedule a publish, then delete the post on
    the platform. Go-live passes, the read-back finds nothing, and the work
    item lands in the incident lane carrying WHY."""
    issues, _ = harness(
        mirrored_open=[
            _readback_row(
                readback_states=["not_live"],
                readback_details=["[not_found] read 5 work(s), none matches"],
            )
        ]
    )
    counts = await wf._sync_terminal_batches()

    assert issues.transitions == [(5, "blocked")]
    assert counts["blocked_by_readback"] == 1
    note = issues.descriptions[5]
    assert "NOT live" in note
    assert "[not_found]" in note


@pytest.mark.asyncio
async def test_an_unconfirmable_batch_blocks_rather_than_hanging(harness):
    """Giving up must stay visible. A row that quietly stayed 'in progress'
    forever is the shape CLAUDE.md's typed-failure rule forbids."""
    issues, _ = harness(
        mirrored_open=[
            _readback_row(
                readback_states=["abandoned"],
                readback_details=["[verification_abandoned] gave up after 5 attempts"],
            )
        ]
    )
    counts = await wf._sync_terminal_batches()

    assert issues.transitions == [(5, "blocked")]
    assert counts["blocked_by_readback"] == 1
    assert "could not be confirmed" in issues.descriptions[5]


@pytest.mark.asyncio
async def test_a_batch_awaiting_its_readback_is_counted_separately(harness):
    """'waiting for the platform's clock' and 'waiting for the read-back' are
    different situations with different fixes, so they get different counters
    instead of both hiding in the generic skip bucket."""
    issues, _ = harness(mirrored_open=[_readback_row(readback_states=[None])])
    counts = await wf._sync_terminal_batches()

    assert issues.transitions == []
    assert counts["awaiting_readback"] == 1
    assert counts["awaiting_schedule"] == 0
    assert counts["skipped"] == 0


@pytest.mark.asyncio
async def test_a_failed_batch_blocks_without_waiting_for_any_readback(harness):
    """An incident needs a human now. The read-back gate only ever guards the
    path to done."""
    issues, _ = harness(
        mirrored_open=[_readback_row(phase="failed", readback_states=[None])]
    )
    counts = await wf._sync_terminal_batches()

    assert issues.transitions == [(5, "blocked")]
    # Not attributed to the read-back — the batch failed on its own.
    assert counts["blocked_by_readback"] == 0


class TestReadbackNote:
    def test_typed_reasons_are_carried_through_verbatim(self):
        note = wf.build_readback_note("not_live", ["[rejected] unauthorised music"])
        assert "[rejected] unauthorised music" in note

    def test_repeated_reasons_are_collapsed(self):
        """A broadcast batch hits N accounts with the same outcome; printing it
        four times adds nothing."""
        note = wf.build_readback_note("not_live", ["[rejected] x"] * 4)
        assert note.count("[rejected] x") == 1

    def test_a_verdict_with_no_detail_still_says_something_actionable(self):
        assert wf.build_readback_note("abandoned", []) is not None
        assert wf.build_readback_note("abandoned", [None, ""]) is not None
