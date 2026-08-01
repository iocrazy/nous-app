"""A2 (needs_input first-class design §5.1): zero-output turns must not be
whitewashed into ``in_review``.

A turn that produces 0 characters AND declares no FinishIssue outcome is a
silent provider failure, not "please review" — it must be typed as
EMPTY_OUTPUT and routed to ``needs_followup``, never ``in_review``. Content
present but no outcome still falls back to the legacy ``in_review`` default
(regression line). Also covers the run-row housekeeping ``route_finish_outcome``
performs when given a ``run_id``: ``agent_runs.issue_id`` backfill
(unconditional) and, for the EMPTY_OUTPUT branch, a typed error_code +
liveness finalize (prod evidence: agent_runs 333739667136736 sat at
status=completed/error_code=NULL/liveness_state='running' after producing 0
chars with no declaration)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.workflows.issue_lifecycle import route_finish_outcome


@pytest.mark.asyncio
async def test_zero_content_no_outcome_goes_needs_followup_empty_output():
    set_status = AsyncMock()

    await route_finish_outcome(
        1, None, None, auto_close=False, set_status=set_status, content_len=0
    )

    set_status.assert_awaited_once_with(
        1,
        "needs_followup",
        agent_outcome=None,
        outcome_reason="Agent produced no output (EMPTY_OUTPUT)",
    )
    # Never in_review — the whole point of the fix.
    for call in set_status.await_args_list:
        assert call.args[1] != "in_review"


@pytest.mark.asyncio
async def test_content_present_no_outcome_still_goes_in_review():
    """Regression line: has content but no FinishIssue outcome → the
    original default routing (unchanged legacy behavior)."""
    set_status = AsyncMock()

    await route_finish_outcome(
        1, None, None, auto_close=False, set_status=set_status, content_len=5
    )

    set_status.assert_awaited_once_with(1, "in_review")


@pytest.mark.asyncio
async def test_zero_content_defaults_content_len_and_still_routes_empty_output():
    """content_len defaults to 0 — an existing caller that never passes it
    (there are none left after this task, but the default itself must not
    silently fall through to in_review for a bare None/None call)."""
    set_status = AsyncMock()

    await route_finish_outcome(1, None, None, auto_close=False, set_status=set_status)

    set_status.assert_awaited_once_with(
        1,
        "needs_followup",
        agent_outcome=None,
        outcome_reason="Agent produced no output (EMPTY_OUTPUT)",
    )


@pytest.mark.asyncio
async def test_zero_content_marks_run_row_empty_output_and_finalizes_liveness():
    set_status = AsyncMock()
    repo = AsyncMock()

    with patch(
        "app.repositories.agent_runs_repository.get_agent_runs_repository",
        return_value=repo,
    ):
        await route_finish_outcome(
            1,
            None,
            None,
            auto_close=False,
            set_status=set_status,
            content_len=0,
            run_id="900000000000123",
        )

    repo.mark_empty_output.assert_awaited_once_with(
        "900000000000123", error_message="Agent produced no output (EMPTY_OUTPUT)"
    )


@pytest.mark.asyncio
async def test_run_finalize_backfills_issue_id_regardless_of_outcome():
    """issue_id backfill is unconditional — it must fire for a normal
    completed turn too, not just the EMPTY_OUTPUT branch."""
    set_status = AsyncMock()
    repo = AsyncMock()

    with patch(
        "app.repositories.agent_runs_repository.get_agent_runs_repository",
        return_value=repo,
    ):
        await route_finish_outcome(
            1,
            "completed",
            "done",
            auto_close=False,
            set_status=set_status,
            content_len=10,
            run_id="900000000000123",
        )

    repo.backfill_issue_id.assert_awaited_once_with("900000000000123", 1)
    repo.mark_empty_output.assert_not_awaited()
    set_status.assert_awaited_once_with(
        1, "in_review", agent_outcome="completed", outcome_reason="done"
    )


@pytest.mark.asyncio
async def test_no_run_id_skips_repository_calls_entirely():
    """Existing dispatch/reply call sites that can't resolve a run_id (e.g.
    a fake/test run_turn without one) must not touch the repository at all —
    the housekeeping is opt-in via ``run_id``, never a hard dependency."""
    set_status = AsyncMock()

    with patch(
        "app.repositories.agent_runs_repository.get_agent_runs_repository"
    ) as get_repo:
        await route_finish_outcome(
            1, "completed", "done", auto_close=False, set_status=set_status
        )
        get_repo.assert_not_called()
