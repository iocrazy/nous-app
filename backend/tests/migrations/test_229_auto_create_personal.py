"""Verify mig 229: new auth user gets a personal team auto-created.

Design notes
------------
* Inserting into auth.users triggers handle_new_user() which (after mig 229)
  must create exactly one kind='personal' team for the new user.
* The teams_add_owner_trigger (add_owner_as_member, mig 009) fires on team
  INSERT and adds the owner as the first team_member automatically.
* A second auth.users INSERT for the same owner_id must not create a duplicate
  personal team (idempotent via ON CONFLICT DO NOTHING + uq_teams_owner_personal).
* Teardown cascades: DELETE teams WHERE owner_id cascades to team_members;
  then auth.users is deleted separately.
"""

from __future__ import annotations

import os

import pytest

from app.db import engine as db_engine

# Mark these as integration tests — they need a live PG with migrations
# 227-230 applied. CI's pytest run skips them via the env-var guard below
# (CI doesn't set SUPAVISOR_DATABASE_URL).
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("SUPAVISOR_DATABASE_URL")
        and not os.environ.get("INTEGRATION_DATABASE_URL"),
        reason="integration DB URL not set",
    ),
]


_USER_ID = "a0000229-0000-0000-0000-000000000001"
_EMAIL = "mig229-test@test.local"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _cleanup():
    """Guarantee cleanup even if the INSERT inside the test raises."""
    yield
    await db_engine.execute(
        "DELETE FROM public.teams WHERE owner_id = CAST(:uid AS uuid)",
        {"uid": _USER_ID},
    )
    await db_engine.execute(
        "DELETE FROM auth.users WHERE id = CAST(:uid AS uuid)",
        {"uid": _USER_ID},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personal_team_auto_created_on_auth_user_insert():
    """Inserting an auth.users row must yield exactly one kind='personal' team."""
    await db_engine.execute(
        "INSERT INTO auth.users "
        "(id, email, raw_user_meta_data, created_at, updated_at) "
        "VALUES (CAST(:uid AS uuid), :email, '{}'::jsonb, NOW(), NOW())",
        {"uid": _USER_ID, "email": _EMAIL},
    )

    rows = await db_engine.fetch_all(
        "SELECT id, kind, name FROM public.teams "
        "WHERE owner_id = CAST(:uid AS uuid) AND kind = 'personal'",
        {"uid": _USER_ID},
    )
    assert len(rows) == 1, f"expected 1 personal team, got {len(rows)}"
    team = rows[0]
    assert team["kind"] == "personal"

    # Owner must be present in team_members (via teams_add_owner_trigger).
    members = await db_engine.fetch_all(
        "SELECT user_id::text AS u FROM public.team_members WHERE team_id = :tid",
        {"tid": team["id"]},
    )
    assert any(
        m["u"] == _USER_ID for m in members
    ), "owner should be auto-added as a team member"


@pytest.mark.asyncio
async def test_no_duplicate_personal_team_on_repeated_call():
    """Idempotency: a second auth.users INSERT for the same id is a no-op.

    handle_new_user uses ON CONFLICT DO NOTHING on both user_profiles and
    teams, and uq_teams_owner_personal (mig 227) rejects duplicate personal
    teams.  We simulate the idempotency path by issuing the same teams INSERT
    that handle_new_user would issue (ON CONFLICT DO NOTHING) a second time
    after the trigger has already fired once.

    Note: handle_new_user() has RETURNS TRIGGER and can only be called in a
    trigger context; we therefore test idempotency via the INSERT it executes
    rather than by calling the function directly.
    """
    # First insert via auth.users trigger.
    await db_engine.execute(
        "INSERT INTO auth.users "
        "(id, email, raw_user_meta_data, created_at, updated_at) "
        "VALUES (CAST(:uid AS uuid), :email, '{}'::jsonb, NOW(), NOW())",
        {"uid": _USER_ID, "email": _EMAIL},
    )

    # Simulate the body of handle_new_user firing a second time for the same
    # user — the ON CONFLICT DO NOTHING clause must make it a no-op.
    await db_engine.execute(
        "INSERT INTO public.teams (name, owner_id, kind) "
        "VALUES ('mig229-test''s Workspace', CAST(:uid AS uuid), 'personal') "
        "ON CONFLICT DO NOTHING",
        {"uid": _USER_ID},
    )

    count = await db_engine.fetch_val(
        "SELECT COUNT(*)::int FROM public.teams "
        "WHERE owner_id = CAST(:uid AS uuid) AND kind = 'personal'",
        {"uid": _USER_ID},
    )
    assert count == 1, f"expected exactly 1 personal team, got {count}"


@pytest.mark.asyncio
async def test_no_collaborative_team_created_for_new_user():
    """handle_new_user must not create a kind='collaborative' team."""
    await db_engine.execute(
        "INSERT INTO auth.users "
        "(id, email, raw_user_meta_data, created_at, updated_at) "
        "VALUES (CAST(:uid AS uuid), :email, '{}'::jsonb, NOW(), NOW())",
        {"uid": _USER_ID, "email": _EMAIL},
    )

    collab_count = await db_engine.fetch_val(
        "SELECT COUNT(*)::int FROM public.teams "
        "WHERE owner_id = CAST(:uid AS uuid) AND kind = 'collaborative'",
        {"uid": _USER_ID},
    )
    assert (
        collab_count == 0
    ), f"handle_new_user must not auto-create collaborative teams, got {collab_count}"
