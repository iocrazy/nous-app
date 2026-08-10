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

    async def list_by_origin(self, origin_kind, origin_id, *, include_hidden=False):
        return list(self._existing.get(origin_id, []))

    async def atomic_create(self, payload):
        self.created.append(payload)
        return {**payload, "id": 900 + len(self.created)}

    async def transition_status(self, issue_id, new_status, *, dbos_workflow_id=None):
        self.transitions.append((issue_id, new_status))
        return {"id": issue_id, "status": new_status}


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
