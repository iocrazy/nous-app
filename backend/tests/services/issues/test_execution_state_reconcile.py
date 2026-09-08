"""MH-1 reconciliation: a stale turn marker with no live run gets agent_outcome."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.unit
NOW = dt.datetime.now(dt.timezone.utc)


def test_merge_stmt_merges_instead_of_assigning():
    from app.services.issues.execution_state import merge_stmt

    sql = str(
        merge_stmt(7, {"agent_outcome": "interrupted"}).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "coalesce(public.issues.execution_state" in sql and "||" in sql
    assert "UPDATE public.issues SET execution_state=" in sql


def _wire(monkeypatch, issues, latest_runs):
    import app.repositories.agent_runs_repository as runs_mod
    import app.repositories.issue_repository as issues_mod
    import app.services.issues.execution_state as es

    monkeypatch.setattr(
        issues_mod.issue_repository,
        "list_in_progress_without_live_run",
        AsyncMock(return_value=issues),
        raising=False,
    )
    runs_repo = SimpleNamespace(
        list_for_issue=AsyncMock(
            side_effect=lambda **kw: latest_runs.get(kw["issue_id"], [])
        )
    )
    monkeypatch.setattr(runs_mod, "get_agent_runs_repository", lambda: runs_repo)
    merge = AsyncMock()
    monkeypatch.setattr(es, "merge_execution_state", merge)
    return merge


@pytest.mark.asyncio
async def test_stale_ended_run_gets_stamped_recent_or_missing_does_not(monkeypatch):
    from app.workflows import agent_runs_sweeper as sw

    issues = [
        {"id": 1, "ai_session_id": 100},  # run ended an hour ago → stamp
        {"id": 2, "ai_session_id": None},  # run ended 1 min ago → grace, skip
        {
            "id": 3,
            "ai_session_id": None,
        },  # no run at all → skip (stranded monitor's job)
    ]
    runs = {
        1: [{"id": 501, "status": "failed", "ended_at": NOW - dt.timedelta(hours=1)}],
        2: [
            {
                "id": 502,
                "status": "completed",
                "ended_at": NOW - dt.timedelta(minutes=1),
            }
        ],
    }
    merge = _wire(monkeypatch, issues, runs)
    assert await sw.reconcile_issue_execution_state_step() == 1
    merge.assert_awaited_once()
    issue_id, patch = merge.await_args.args
    assert issue_id == 1
    assert patch["agent_outcome"] == "interrupted"
    assert "501" in patch["outcome_reason"] and "failed" in patch["outcome_reason"]
    assert patch["reconciled_at"].startswith("20")


def test_candidate_query_excludes_live_awaiting_and_already_stamped():
    """The repo query is the filter; pin its shape."""
    import inspect

    from app.repositories.issue_repository import IssueRepository

    src = inspect.getsource(IssueRepository.list_in_progress_without_live_run)
    assert 'has_key("turn")' in src
    assert '~Issues.execution_state.has_key("awaiting_input")' in src
    assert '~Issues.execution_state.has_key("agent_outcome")' in src
    assert 'AgentRuns.status == "running"' in src and "~live" in src


def test_tick_calls_the_reconcile_step():
    import inspect

    from app.workflows import agent_runs_sweeper as sw

    assert "reconcile_issue_execution_state_step()" in inspect.getsource(
        sw.agent_runs_sweeper_workflow
    )


def test_claim_budget_wrap_up_stmt_is_a_conditional_update():
    """Phase 2a Task 6 (review F5): one winner among racing runs, idempotent
    for the same run — the WHERE carries both."""
    from sqlalchemy.dialects import postgresql

    from app.services.issues.execution_state import claim_budget_wrap_up_stmt

    compiled = claim_budget_wrap_up_stmt(7, 42).compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()
    assert "update public.issues" in sql
    assert "execution_state ? " in sql  # the flag must exist
    assert "->>" in sql and " is null" in sql and " or " in sql
    assert "jsonb_build_object" in sql and sql.count("||") == 2
    assert "42" in map(str, compiled.params.values())
