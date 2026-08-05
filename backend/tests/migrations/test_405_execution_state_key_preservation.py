"""``set_status`` must never drop another writer's ``execution_state`` keys.

The unit tests in ``test_issue_lifecycle_sql.py`` assert the SHAPE of the
statement (merge onto the column, error keys subtracted). This file asserts
the SEMANTICS against a real Postgres: seed one issue row with every key the
other writers own, drive it through the real transitions, and check what
actually survives.

Why it needs a real DB: the whole contract lives in jsonb operator behaviour
(``||`` merge and ``- 'key'`` subtraction). No amount of SQL-string matching
proves a key survived — only the database can answer that.

Skipped in CI (no integration DB URL). Run locally against nous-db with:

    SUPAVISOR_DATABASE_URL=postgresql://postgres:<pw>@127.0.0.1:55436/postgres \\
      uv run pytest tests/migrations/test_405_execution_state_key_preservation.py
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import pytest

from app.db import engine as db_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("SUPAVISOR_DATABASE_URL")
        and not os.environ.get("INTEGRATION_DATABASE_URL"),
        reason="integration DB URL not set",
    ),
]

# Every key the OTHER writers own. If any of these goes missing after a
# set_status call, the merge contract is broken.
#   awaiting_input          → input_gate.mark_awaiting_input
#   turn / turn_started_at  → issue_lifecycle.mark_turn_progress
#   stranded_*              → stranded_issue_monitor._prepare_redispatch
FOREIGN_KEYS = {
    "awaiting_input": {
        "prompt": "Monday or Wednesday?",
        "since": "2026-08-04T00:00:00Z",
    },
    "turn": 3,
    "turn_started_at": "2026-08-04T00:00:00Z",
    "stranded_redispatch_count": 1,
    "stranded_last_dispatched_at": "2026-08-04T00:00:00Z",
}

SEED_STATE = {
    **FOREIGN_KEYS,
    # The stale error a previous blocked run left behind.
    "error_code": "execute_issue_failed",
    "error_message": "primary + 0 fallback(s) exhausted",
}


async def _seed_issue() -> int:
    """Create a throwaway issue carrying every key, return its id."""
    owner = await db_engine.fetch_one(
        "SELECT created_by_user_id, created_by_agent_id, team_id, project_id "
        "FROM public.issues WHERE created_by_user_id IS NOT NULL LIMIT 1"
    )
    if not owner:
        pytest.skip("no existing issue to borrow a valid creator from")

    suffix = uuid.uuid4().hex[:8]
    # execute_returning_val, not fetch_one: fetch_* run on engine.connect()
    # and never commit, so the INSERT would silently roll back and every
    # assertion below would read a row that does not exist.
    issue_id = await db_engine.execute_returning_val(
        """
        INSERT INTO public.issues
            (issue_number, identifier, title, status, execution_state,
             created_by_user_id, team_id, project_id)
        VALUES
            (:num, :ident, :title, 'blocked', CAST(:state AS jsonb),
             :uid, :team, :project)
        RETURNING id
        """,
        {
            "num": 900000 + (int(suffix, 16) % 90000),
            "ident": f"TEST-{suffix.upper()}",
            "title": "Execution state key preservation probe",
            "state": json.dumps(SEED_STATE),
            "uid": owner["created_by_user_id"],
            "team": owner.get("team_id"),
            "project": owner.get("project_id"),
        },
    )
    return int(issue_id)


async def _state(issue_id: int) -> dict[str, Any]:
    row = await db_engine.fetch_one(
        "SELECT execution_state FROM public.issues WHERE id = :id", {"id": issue_id}
    )
    state = row["execution_state"]
    return json.loads(state) if isinstance(state, str) else (state or {})


async def _drop_issue(issue_id: int) -> None:
    await db_engine.execute_as_service_role(
        "DELETE FROM public.issues WHERE id = :id", {"id": issue_id}
    )


def _assert_foreign_keys_intact(state: dict[str, Any], when: str) -> None:
    for key, expected in FOREIGN_KEYS.items():
        assert key in state, f"{when}: {key} was dropped by set_status"
        assert state[key] == expected, f"{when}: {key} was corrupted"


@pytest.fixture
async def issue_id():
    iid = await _seed_issue()
    try:
        yield iid
    finally:
        await _drop_issue(iid)


async def test_resume_clears_error_and_keeps_everything_else(issue_id):
    """blocked → in_progress: the stale error goes, nothing else moves."""
    from app.workflows.issue_lifecycle import set_status

    await set_status(issue_id, "in_progress")

    state = await _state(issue_id)
    assert "error_code" not in state
    assert "error_message" not in state
    _assert_foreign_keys_intact(state, "after resume")


async def test_error_write_preserves_other_writers_keys(issue_id):
    """The one the old whole-column assignment got wrong: writing an error
    must not take awaiting_input / turn / stranded_* down with it."""
    from app.workflows.issue_lifecycle import set_status

    await set_status(
        issue_id, "blocked", error_code="TIMEOUT", error_message="took too long"
    )

    state = await _state(issue_id)
    assert state["error_code"] == "TIMEOUT"
    assert state["error_message"] == "took too long"
    _assert_foreign_keys_intact(state, "after error write")


async def test_outcome_write_preserves_keys_and_clears_stale_error(issue_id):
    """An outcome transition merges its own keys, drops the stale error, and
    still leaves the other writers alone."""
    from app.workflows.issue_lifecycle import set_status

    await set_status(
        issue_id, "in_review", agent_outcome="completed", outcome_reason="all done"
    )

    state = await _state(issue_id)
    assert state["agent_outcome"] == "completed"
    assert state["outcome_reason"] == "all done"
    assert "error_code" not in state
    assert "error_message" not in state
    _assert_foreign_keys_intact(state, "after outcome write")


async def test_needs_input_marker_survives_the_full_park_and_resume_cycle(issue_id):
    """End-to-end ordering proof. The input_gate docstring used to warn that
    clear_awaiting_input had to run BEFORE set_status(in_progress) or the
    marker would be lost to the whole-column assignment. Ordering is no
    longer load-bearing — assert the marker survives a park → resume cycle
    without any clear at all."""
    from app.workflows.issue_lifecycle import set_status

    await set_status(
        issue_id,
        "needs_followup",
        agent_outcome="needs_input",
        outcome_reason="Monday or Wednesday?",
    )
    assert (await _state(issue_id))["awaiting_input"] == FOREIGN_KEYS["awaiting_input"]

    await set_status(issue_id, "in_progress")

    state = await _state(issue_id)
    assert state["awaiting_input"] == FOREIGN_KEYS["awaiting_input"]
    assert state["agent_outcome"] == "needs_input"
