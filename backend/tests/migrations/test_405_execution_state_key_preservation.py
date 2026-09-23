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

# ``set_status`` writes as ``SET LOCAL ROLE service_role``. On a Supabase
# instance that role carries platform grants and BYPASSRLS; on the
# schema-drift database (``supabase/ci_bootstrap.sql``) it is a bare NOLOGIN
# role with neither, so every write here is ``permission denied``. Give it the
# prod-shaped rights only where they are missing, and take back exactly what
# this module added.
_SERVICE_ROLE_PRIVS = ("SELECT", "UPDATE", "DELETE")


async def _missing_service_role_rights() -> tuple[list[str], bool]:
    missing = []
    for priv in _SERVICE_ROLE_PRIVS:
        row = await db_engine.fetch_one(
            "SELECT has_table_privilege('service_role', 'public.issues', :p) AS ok",
            {"p": priv},
        )
        if not (row or {}).get("ok"):
            missing.append(priv)
    role = await db_engine.fetch_one(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = 'service_role'"
    )
    return missing, not (role or {}).get("rolbypassrls")


_LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1"})


def _db_host_is_local(dsn: str | None) -> bool:
    """True only for a DSN whose host is this machine. Anything unparsable or
    remote is treated as not local: the fixture below alters a ROLE, which is
    cluster-wide, and must never run against a shared or production cluster."""
    from sqlalchemy.engine import make_url

    if not dsn:
        return False
    try:
        host = make_url(dsn).host
    except Exception:  # noqa: BLE001 — unparsable means "do not touch"
        return False
    return host in _LOCAL_DB_HOSTS


@pytest.fixture(scope="module", autouse=True)
async def _service_role_is_prod_shaped():
    from app.core.config import settings

    # The engine connects with SUPAVISOR_DATABASE_URL; that is the cluster the
    # GRANT / ALTER ROLE below would land on.
    if not _db_host_is_local(settings.SUPAVISOR_DATABASE_URL):
        pytest.skip(
            "test_405 alters the service_role role; refusing on a non-local"
            " database (host must be localhost or 127.0.0.1)"
        )
    missing, needs_bypass = await _missing_service_role_rights()
    if missing:
        await db_engine.execute(
            f"GRANT {', '.join(missing)} ON public.issues TO service_role"
        )
    if needs_bypass:
        await db_engine.execute("ALTER ROLE service_role BYPASSRLS")
    try:
        yield
    finally:
        if needs_bypass:
            await db_engine.execute("ALTER ROLE service_role NOBYPASSRLS")
        if missing:
            await db_engine.execute(
                f"REVOKE {', '.join(missing)} ON public.issues FROM service_role"
            )


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


async def _borrow_creator() -> dict[str, Any]:
    """A valid creator for the FK. Borrow one from an existing issue; on an
    empty schema (the drift DB has users but no issues) fall back to any
    ``auth.users`` row rather than skipping the whole file."""
    owner = await db_engine.fetch_one(
        "SELECT created_by_user_id, created_by_agent_id, team_id, project_id "
        "FROM public.issues WHERE created_by_user_id IS NOT NULL LIMIT 1"
    )
    if owner:
        return dict(owner)
    user = await db_engine.fetch_one("SELECT id FROM auth.users LIMIT 1")
    if not user:
        pytest.skip("no issue or auth.users row to borrow a valid creator from")
    return {"created_by_user_id": user["id"], "team_id": None, "project_id": None}


async def _seed_issue(status: str = "blocked", *, cancelled: bool = False) -> int:
    """Create a throwaway issue carrying every key, return its id."""
    owner = await _borrow_creator()

    suffix = uuid.uuid4().hex[:8]
    # execute_returning_val, not fetch_one: fetch_* run on engine.connect()
    # and never commit, so the INSERT would silently roll back and every
    # assertion below would read a row that does not exist.
    issue_id = await db_engine.execute_returning_val(
        """
        INSERT INTO public.issues
            (issue_number, identifier, title, status, execution_state,
             created_by_user_id, team_id, project_id, cancelled_at)
        VALUES
            (:num, :ident, :title, :status, CAST(:state AS jsonb),
             :uid, :team, :project,
             CASE WHEN :cancelled THEN now() ELSE NULL END)
        RETURNING id
        """,
        {
            "num": 900000 + (int(suffix, 16) % 90000),
            "ident": f"TEST-{suffix.upper()}",
            "title": "Execution state key preservation probe",
            "status": status,
            "state": json.dumps(SEED_STATE),
            "uid": owner["created_by_user_id"],
            "team": owner.get("team_id"),
            "project": owner.get("project_id"),
            "cancelled": cancelled,
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


async def _row(issue_id: int) -> dict[str, Any]:
    row = await db_engine.fetch_one(
        "SELECT status, cancelled_at, execution_state FROM public.issues "
        "WHERE id = :id",
        {"id": issue_id},
    )
    return dict(row)


@pytest.fixture
async def cancelled_issue_id():
    iid = await _seed_issue("cancelled", cancelled=True)
    try:
        yield iid
    finally:
        await _drop_issue(iid)


@pytest.fixture
async def running_issue_id():
    iid = await _seed_issue("in_progress")
    try:
        yield iid
    finally:
        await _drop_issue(iid)


async def test_finish_does_not_overwrite_a_cancelled_issue(cancelled_issue_id):
    """Defect C (S4): the run's own finish landed ``in_review`` over the
    user's cancel. A lifecycle write never replaces a terminal status."""
    from app.workflows.issue_lifecycle import set_status

    before = await _row(cancelled_issue_id)
    wrote = await set_status(cancelled_issue_id, "in_review", agent_outcome="completed")

    after = await _row(cancelled_issue_id)
    assert after["status"] == "cancelled"
    assert after["cancelled_at"] == before["cancelled_at"]
    assert wrote is False
    assert "agent_outcome" not in (
        json.loads(after["execution_state"])
        if isinstance(after["execution_state"], str)
        else after["execution_state"]
    )


async def test_finish_still_lands_on_a_running_issue(running_issue_id):
    """Positive control: the guard only refuses terminal rows."""
    from app.workflows.issue_lifecycle import set_status

    wrote = await set_status(running_issue_id, "in_review", agent_outcome="completed")

    assert (await _row(running_issue_id))["status"] == "in_review"
    assert wrote is True
