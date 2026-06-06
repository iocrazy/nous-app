"""Integration tests for InviteRepositoryOrm (Phase 2 M batch) against real PG.

Proves the REST → ORM swap is invisible AND that STRATEGY-C value-type parity
holds on team_invites:

  - id / team_id (BIGINT snowflake) → STAY native int (the 5.3 trap).
  - created_by (uuid) → STR (REQUIRED — InviteResponse.created_by is a str
    field; pydantic v2 rejects a native UUID).
  - expires_at / created_at (timestamptz) → ISO STR (REQUIRED — the inherited
    accept_invite expiry check calls .replace()/fromisoformat() on expires_at).
  - get_invite_by_code reproduces the PostgREST teams(id, name) embed.

Writes (create_invite / accept_invite / delete_invite) go through
``write_scope()`` (COMMITS) — a fresh asyncpg read proves no silent rollback.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_invite_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_TEAM_PREFIX = "__test_orm_invite_team_"


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    from unittest.mock import patch

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


@pytest.fixture
async def seed_team(integration_db_url):
    """Create a throwaway team owned by a REAL auth.users row (teams.owner_id /
    team_members.user_id FK auth.users). Yields (team_id, owner_id, other_id)
    where other_id is a second real user (the accept-invite joiner). Cleans up
    the team (CASCADE drops invites + members) afterwards."""
    conn = await asyncpg.connect(integration_db_url)
    team_id = None
    try:
        users = await conn.fetch("SELECT id FROM auth.users LIMIT 2")
        if len(users) < 2:
            pytest.skip("need >=2 auth.users rows to satisfy owner/joiner FKs")
        owner_id = users[0]["id"]
        other_id = users[1]["id"]
        # teams_invite_code_trigger fills invite_code; teams_add_owner_trigger
        # auto-inserts the owner into team_members — so we do NOT insert either.
        team_id = await conn.fetchval(
            "INSERT INTO teams (name, owner_id, invite_code) "
            "VALUES ($1, $2, $3) RETURNING id",
            f"{_TEAM_PREFIX}{uuid.uuid4().hex[:8]}",
            owner_id,
            uuid.uuid4().hex[:10],
        )
        yield team_id, owner_id, other_id
    finally:
        if team_id is not None:
            await conn.execute("DELETE FROM teams WHERE id = $1", team_id)
        await conn.close()


def _repo():
    from app.repositories.invite_repository_orm import InviteRepositoryOrm

    return InviteRepositoryOrm()


# ─── Create (COMMIT + parity) ───────────────────────────────────────────


async def test_create_commit_and_parity(integration_db_url, patched_engine, seed_team):
    """create_invite() PERSISTS and returns a parity dict: id/team_id native
    int, created_by STR, expires_at/created_at ISO str."""
    team_id, owner_id, other_id = seed_team
    created = await _repo().create_invite(
        team_id=str(team_id),
        created_by=str(owner_id),
        expires_in="1h",
        max_uses=5,
    )
    assert created is not None
    assert type(created["id"]) is int  # bigint id stays int (5.3 trap)
    assert type(created["team_id"]) is int  # bigint team_id stays int
    assert created["team_id"] == team_id
    # created_by → str (REQUIRED for the str InviteResponse field).
    assert type(created["created_by"]) is str
    assert created["created_by"] == str(owner_id)
    # timestamptz → ISO str (REQUIRED for the accept_invite .replace() path).
    assert type(created["expires_at"]) is str and "T" in created["expires_at"]
    assert type(created["created_at"]) is str
    assert created["max_uses"] == 5
    assert created["use_count"] == 0
    # The code trigger fired (NOT NULL column populated server-side).
    assert created["code"]

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT count(*) FROM team_invites WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert persisted == 1


async def test_get_by_id_and_by_team_parity(patched_engine, seed_team):
    team_id, owner_id, other_id = seed_team
    created = await _repo().create_invite(
        team_id=str(team_id), created_by=str(owner_id)
    )

    by_id = await _repo().get_invite_by_id(str(created["id"]))
    assert by_id is not None
    assert type(by_id["id"]) is int
    assert type(by_id["created_by"]) is str

    by_team = await _repo().get_invites_by_team(str(team_id))
    assert len(by_team) == 1
    assert by_team[0]["id"] == created["id"]


async def test_get_by_code_embeds_team(patched_engine, seed_team):
    """get_invite_by_code reproduces the PostgREST teams(id, name) embed."""
    team_id, owner_id, other_id = seed_team
    created = await _repo().create_invite(
        team_id=str(team_id), created_by=str(owner_id)
    )
    fetched = await _repo().get_invite_by_code(created["code"])
    assert fetched is not None
    assert fetched["teams"] is not None
    assert fetched["teams"]["id"] == team_id  # embed: bigint → native int
    assert isinstance(fetched["teams"]["name"], str)


# ─── accept_invite (COMMIT + the str-uuid / iso-expires contract) ───────


async def test_accept_invite_commits_membership(
    integration_db_url, patched_engine, seed_team
):
    """accept_invite inserts a team_member, bumps use_count, and returns a
    str team_id (AcceptInviteResponse.team_id is a str field). Exercises the
    inherited expiry check on an ISO-str expires_at without AttributeError."""
    team_id, owner_id, other_id = seed_team
    new_user = other_id
    created = await _repo().create_invite(
        team_id=str(team_id), created_by=str(owner_id), expires_in="1d", max_uses=3
    )
    result = await _repo().accept_invite(created["code"], str(new_user))
    assert result is not None
    # AcceptInviteResponse.team_id is a STR field → must be str, not int.
    assert type(result["team_id"]) is str
    assert result["team_id"] == str(team_id)
    assert isinstance(result["team_name"], str)

    conn = await asyncpg.connect(integration_db_url)
    try:
        member = await conn.fetchval(
            "SELECT count(*) FROM team_members WHERE team_id = $1 AND user_id = $2",
            team_id,
            new_user,
        )
        use_count = await conn.fetchval(
            "SELECT use_count FROM team_invites WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert member == 1  # membership committed (no silent rollback)
    assert use_count == 1  # use_count bumped + committed


async def test_accept_invite_duplicate_member_raises(patched_engine, seed_team):
    """A second accept by the owner (already a member) → 'Already a member'."""
    team_id, owner_id, other_id = seed_team
    created = await _repo().create_invite(
        team_id=str(team_id), created_by=str(owner_id)
    )
    with pytest.raises(Exception) as exc:
        await _repo().accept_invite(created["code"], str(owner_id))
    assert "Already a member" in str(exc.value)


# ─── delete + permission ────────────────────────────────────────────────


async def test_delete_invite_commits(integration_db_url, patched_engine, seed_team):
    team_id, owner_id, other_id = seed_team
    created = await _repo().create_invite(
        team_id=str(team_id), created_by=str(owner_id)
    )
    assert await _repo().delete_invite(str(created["id"]), str(owner_id)) is True

    conn = await asyncpg.connect(integration_db_url)
    try:
        gone = await conn.fetchval(
            "SELECT count(*) FROM team_invites WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert gone == 0


async def test_delete_invite_denied_for_non_admin(patched_engine, seed_team):
    team_id, owner_id, other_id = seed_team
    created = await _repo().create_invite(
        team_id=str(team_id), created_by=str(owner_id)
    )
    # A random user with no membership cannot delete.
    assert await _repo().delete_invite(str(created["id"]), str(uuid.uuid4())) is False


async def test_check_user_can_manage(patched_engine, seed_team):
    team_id, owner_id, other_id = seed_team
    assert (
        await _repo().check_user_can_manage_invites(str(team_id), str(owner_id)) is True
    )
    assert (
        await _repo().check_user_can_manage_invites(str(team_id), str(uuid.uuid4()))
        is False
    )


# ─── factory on/off ─────────────────────────────────────────────────────


def test_factory_off_returns_legacy():
    from unittest.mock import patch

    from app.repositories.invite_repository import (
        InviteRepository,
        get_invite_repository,
    )

    with patch("app.core.config.settings.USE_ORM_INVITE", False):
        assert type(get_invite_repository()) is InviteRepository


def test_factory_on_returns_orm(integration_db_url):
    from unittest.mock import patch

    from app.repositories.invite_repository_orm import InviteRepositoryOrm

    with (
        patch("app.core.config.settings.USE_ORM_INVITE", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        from app.repositories.invite_repository import get_invite_repository

        assert type(get_invite_repository()) is InviteRepositoryOrm
