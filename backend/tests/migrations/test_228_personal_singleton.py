"""Verify mig 228: cannot add a second member to a kind='personal' team.

Design notes
------------
* The engine connects as the ``postgres`` role which bypasses RLS but still
  honours FK constraints.  We therefore use two auth.users UUIDs that we
  insert (and clean up) as part of the fixture.
* Inserting into auth.users triggers ``handle_new_user`` which (after mig 229)
  auto-creates a kind='personal' workspace team + user_profile row.  We clean
  those up via cascade (DELETE teams WHERE owner_id = ... cascades to
  team_members).
* ``_insert_personal_team()`` now fetches the auto-created personal team
  (inserted by handle_new_user on auth.users INSERT above) rather than
  inserting a second one — the uq_teams_owner_personal unique partial index
  (mig 227) would reject a duplicate personal team for the same owner.
* The ``teams_add_owner_trigger`` (add_owner_as_member) fires on team INSERT
  and automatically adds the owner as the first team_member.  The mig-228
  trigger must allow that first INSERT and block a subsequent one.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.exc import DBAPIError

from app.db import engine as db_engine

# Two fixed UUIDs used only by this test file — chosen to be clearly
# synthetic and avoid collision with real data.
_OWNER_ID = "a0000228-0000-0000-0000-000000000001"
_OTHER_ID = "a0000228-0000-0000-0000-000000000002"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _seed_and_teardown():
    """Create two auth.users rows before the test; cascade-delete after."""
    # Insert auth.users — handle_new_user trigger fires and creates a
    # collaborative workspace team + user_profile for each user.
    for uid, email in [
        (_OWNER_ID, "mig228-owner@test.local"),
        (_OTHER_ID, "mig228-other@test.local"),
    ]:
        await db_engine.execute(
            "INSERT INTO auth.users (id, email, raw_user_meta_data, created_at, updated_at) "
            "VALUES (:uid, :email, '{}'::jsonb, NOW(), NOW()) "
            "ON CONFLICT (id) DO NOTHING",
            {"uid": uid, "email": email},
        )

    yield  # run the test

    # Teardown: delete teams (cascades to team_members), then auth.users.
    # Use CAST() instead of :: so SQLAlchemy's text() named-param binding
    # doesn't confuse ::cast with a second bind-parameter prefix.
    for uid in (_OWNER_ID, _OTHER_ID):
        await db_engine.execute(
            "DELETE FROM public.teams WHERE owner_id = CAST(:uid AS uuid)",
            {"uid": uid},
        )
    for uid in (_OWNER_ID, _OTHER_ID):
        await db_engine.execute(
            "DELETE FROM auth.users WHERE id = CAST(:uid AS uuid)",
            {"uid": uid},
        )


async def _insert_personal_team() -> int:
    """Return the id of the kind='personal' team auto-created for _OWNER_ID.

    After mig 229, handle_new_user() creates a kind='personal' team when an
    auth.users row is inserted.  The uq_teams_owner_personal unique partial
    index (mig 227) rejects a second personal team for the same owner, so we
    SELECT the already-existing one instead of INSERTing a duplicate.
    """
    row = await db_engine.execute_returning_one(
        "SELECT id FROM public.teams "
        "WHERE owner_id = CAST(:owner AS uuid) AND kind = 'personal' "
        "LIMIT 1",
        {"owner": _OWNER_ID},
    )
    assert row is not None, (
        "Expected a personal team auto-created by handle_new_user for _OWNER_ID"
    )
    return row["id"]


async def _insert_collab_team() -> int:
    """Insert a kind='collaborative' team owned by _OWNER_ID; return id."""
    row = await db_engine.execute_returning_one(
        "INSERT INTO public.teams (name, owner_id, invite_code, kind) "
        "VALUES ('Test Collab Team', :owner, generate_invite_code(), 'collaborative') "
        "RETURNING id",
        {"owner": _OWNER_ID},
    )
    assert row is not None
    return row["id"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personal_team_rejects_second_member():
    """Trigger must block a second INSERT into team_members for a personal team."""
    team_id = await _insert_personal_team()

    # The add_owner_as_member trigger fires on team INSERT and adds _OWNER_ID
    # as the first member automatically.  Verify that happened.
    first_count = await db_engine.fetch_val(
        "SELECT COUNT(*)::int FROM public.team_members WHERE team_id = :tid",
        {"tid": team_id},
    )
    assert first_count == 1, (
        f"Expected 1 auto-added owner member, got {first_count}"
    )

    # Now attempt to add a second member — the mig-228 trigger must raise.
    with pytest.raises((DBAPIError, Exception)) as exc_info:
        await db_engine.execute(
            "INSERT INTO public.team_members (team_id, user_id, role) "
            "VALUES (:tid, :uid, 'member')",
            {"tid": team_id, "uid": _OTHER_ID},
        )

    assert "cannot have more than one member" in str(exc_info.value), (
        f"Expected singleton error, got: {exc_info.value}"
    )

    # Confirm no second row was added.
    final_count = await db_engine.fetch_val(
        "SELECT COUNT(*)::int FROM public.team_members WHERE team_id = :tid",
        {"tid": team_id},
    )
    assert final_count == 1, (
        f"Expected still 1 member after rejected INSERT, got {final_count}"
    )


@pytest.mark.asyncio
async def test_collaborative_team_allows_multiple_members():
    """Trigger must NOT block a second member on a collaborative team."""
    team_id = await _insert_collab_team()

    # Owner auto-added.
    first_count = await db_engine.fetch_val(
        "SELECT COUNT(*)::int FROM public.team_members WHERE team_id = :tid",
        {"tid": team_id},
    )
    assert first_count == 1

    # Add second member — must succeed.
    await db_engine.execute(
        "INSERT INTO public.team_members (team_id, user_id, role) "
        "VALUES (:tid, :uid, 'member')",
        {"tid": team_id, "uid": _OTHER_ID},
    )

    final_count = await db_engine.fetch_val(
        "SELECT COUNT(*)::int FROM public.team_members WHERE team_id = :tid",
        {"tid": team_id},
    )
    assert final_count == 2, (
        f"Expected 2 members in collaborative team, got {final_count}"
    )
