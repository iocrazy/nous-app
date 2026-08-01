"""A2 (needs_input first-class design §5.1): zero-output turns must not be
whitewashed into ``in_review``.

A turn that produces 0 characters AND declares no FinishIssue outcome is a
silent provider failure, not "please review" — it must be typed as
EMPTY_OUTPUT and routed to ``needs_followup``, never ``in_review``. Content
present but no outcome still falls back to the legacy ``in_review`` default
(regression line). Also covers the run-row housekeeping ``route_finish_outcome``
performs when given a ``run_id``: ``agent_runs.issue_id`` backfill
(unconditional) and, for the EMPTY_OUTPUT branch, a typed error_code /
error_message (prod evidence: agent_runs 333739667136736 sat at
status=completed/error_code=NULL after producing 0 chars with no
declaration).

Fix round 1 (review): ``mark_empty_output`` must NOT touch ``liveness_state``
— every other writer of ``liveness_state='dead'`` pairs it atomically with
``status='failed'`` (migration 207's contract), and this run's status stays
``'completed'``. See ``AgentRunsRepository.mark_empty_output`` for the full
reasoning; ``test_mark_empty_output_leaves_liveness_state_untouched`` below
pins it at the repository level.

Fix round 2 (final review, I3): the EMPTY_OUTPUT branch writes
``agent_outcome="empty_output"`` (previously ``None``), which is what lets a
reply resume it — the resume gate in ``_run_reply_turns`` only accepts a
pending ``agent_outcome`` from a fixed set, so a ``None`` value parked the
issue at ``needs_followup`` out of reach of any future reply. It stays
distinct from ``"needs_input"`` so the Task Center needs-your-answer feed
(``needs_input_predicate``, exact-match) never lists an EMPTY_OUTPUT stall as
something the agent actually asked about."""
from __future__ import annotations

from contextlib import asynccontextmanager
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
        agent_outcome="empty_output",
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
        agent_outcome="empty_output",
        outcome_reason="Agent produced no output (EMPTY_OUTPUT)",
    )


@pytest.mark.asyncio
async def test_zero_content_marks_run_row_empty_output():
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


class _FakeResult:
    def first(self):
        return None


class _FakeSession:
    def __init__(self, captured: dict) -> None:
        self._captured = captured

    async def execute(self, stmt, params=None):
        self._captured["stmt"] = stmt
        return _FakeResult()


def _write_scope(captured: dict):
    @asynccontextmanager
    async def _scope():
        yield _FakeSession(captured)

    return _scope


@pytest.mark.asyncio
async def test_mark_empty_output_leaves_liveness_state_untouched():
    """Repository-level pin (fix round 1): the EMPTY_OUTPUT UPDATE must set
    error_code/error_message and nothing else — in particular, it must NOT
    set liveness_state. Every other writer of liveness_state='dead'
    (liveness_scanner._mark_dead, liveness/reconcile.reconcile_stranded_runs)
    pairs it atomically with status='failed'; this run's status stays
    'completed', so writing 'dead' here would mint a combo that never
    exists anywhere else and breaks ops triage's dead⟺failed assumption."""
    from app.repositories.agent_runs_repository import get_agent_runs_repository

    captured: dict = {}
    with patch(
        "app.repositories.agent_runs_repository.write_scope", _write_scope(captured)
    ):
        await get_agent_runs_repository().mark_empty_output(
            "900000000000123", error_message="Agent produced no output (EMPTY_OUTPUT)"
        )

    params = captured["stmt"].compile().params
    assert params["error_code"] == "EMPTY_OUTPUT"
    assert params["error_message"] == "Agent produced no output (EMPTY_OUTPUT)"
    assert "liveness_state" not in params
    assert "status" not in params


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
