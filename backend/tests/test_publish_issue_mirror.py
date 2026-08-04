"""Unit tests for the publish→issue mirror sweeper."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

import app.workflows.publish_issue_mirror as wf


class TestDecisionHelpers:
    def test_initial_status_by_phase(self):
        assert wf.issue_status_for_phase("queued") == "todo"
        assert wf.issue_status_for_phase("in_progress") == "in_progress"
        assert wf.issue_status_for_phase("failed") == "blocked"
        assert wf.issue_status_for_phase("lost") == "blocked"
        # Completed history is not retro-mirrored — nothing left to manage.
        assert wf.issue_status_for_phase("completed") is None
        assert wf.issue_status_for_phase("cancelled") is None
        assert wf.issue_status_for_phase(None) is None

    def test_terminal_sync_targets(self):
        assert wf.terminal_sync_action("completed", "in_progress") == "done"
        assert wf.terminal_sync_action("failed", "in_progress") == "blocked"
        assert wf.terminal_sync_action("lost", "todo") == "blocked"
        assert wf.terminal_sync_action("cancelled", "todo") == "cancelled"
        # Already-settled issues are never touched again.
        assert wf.terminal_sync_action("completed", "done") is None
        assert wf.terminal_sync_action("failed", "blocked") is None
        assert wf.terminal_sync_action("completed", "cancelled") is None

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
