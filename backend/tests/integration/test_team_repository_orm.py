"""Integration tests for TeamRepositoryOrm (Phase 2 H batch) against real PG.

★ THE CROWN-JEWEL TEAM AUTHZ SURFACE. ★ Proves the REST → ORM swap is invisible
AND that STRATEGY-C value-type parity holds on teams / team_members — with a
dedicated test that ``team_members.user_id`` is returned as STR so the
app-layer authz/identity compare
(``next(m for m in members if m["user_id"] == user_id)`` in
teams_router.update_member_role) keeps matching. A native uuid.UUID there is a
SILENT KILLER: ``uuid.UUID == str`` is False forever → a 404 "Member not found"
after a successful role update (no error/log).

THE 'all-bigint = safe' TRAP: teams.id is BIGINT, but teams.owner_id and
team_members.user_id are UUID. We prove BOTH are str on every return path, plus
the remove_member owner-protection compare (str(owner_id) == str(target)) keeps
the owner un-removable.

Parity surface:
  - team_members.user_id (uuid)  → STR (REQUIRED — authz ==).
  - teams.owner_id (uuid)        → STR (shape parity; remove_member protection).
  - teams.id / team_members.team_id (bigint) → native int (the 5.3 trap).
  - role (VARCHAR, NOT Enum)     → native str.
  - settings_json / enabled_modules (jsonb) → native dict.
  - created_at / joined_at (timestamptz) → ISO str.
  - create_team relies on the teams_invite_code_trigger + RETURNING; the
    add_owner_as_member trigger (mig 009) owns the owner-membership insert (the
    redundant explicit insert was dropped as a co-fixed prod bug); add_member
    returns None on 23505 (duplicate member).

Setup: requires INTEGRATION_DATABASE_URL + >=2 auth.users rows (teams.owner_id /
team_members.user_id FK auth.users). Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_team_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_NAME_PREFIX = "__test_orm_team_"


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
async def two_users(integration_db_url):
    """Yield two REAL auth.users ids (teams.owner_id / team_members.user_id FK
    auth.users). Skips if the DB has fewer than two."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        rows = await conn.fetch("SELECT id FROM auth.users LIMIT 2")
        if len(rows) < 2:
            pytest.skip("need >=2 auth.users rows for owner + member FKs")
        yield rows[0]["id"], rows[1]["id"]
    finally:
        await conn.close()


@pytest.fixture
async def cleanup_teams(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        # team_members cascade on teams delete (FK ON DELETE CASCADE).
        await conn.execute("DELETE FROM teams WHERE name LIKE $1", _NAME_PREFIX + "%")
    finally:
        await conn.close()


def _repo():
    from app.repositories.team_repository_orm import TeamRepositoryOrm

    return TeamRepositoryOrm()


def _name() -> str:
    return f"{_NAME_PREFIX}{uuid.uuid4().hex[:8]}"


async def _seed_team(conn, owner) -> dict:
    """Insert a team directly (the teams_add_owner_trigger auto-inserts the
    owner membership; the teams_invite_code_trigger fills invite_code). We do
    call repo.create_team here to keep the owner-membership setup independent of
    the method under test in the per-method tests below (create_team has its own
    dedicated test). Returns the row dict."""
    row = await conn.fetchrow(
        "INSERT INTO teams (name, owner_id) VALUES ($1, $2) "
        "RETURNING id, owner_id, invite_code",
        _name(),
        owner,
    )
    return {
        "id": row["id"],
        "owner_id": row["owner_id"],
        "invite_code": row["invite_code"],
    }


# ─── create_team — PROD-BUG CO-FIX (succeeds; trigger owns owner member) ─


async def test_create_team_succeeds_and_trigger_adds_owner(
    integration_db_url, patched_engine, cleanup_teams, two_users
):
    """create_team must SUCCEED and return the team. The redundant explicit
    owner-membership insert was DROPPED (prod-bug co-fix 2026-06-06): it used to
    collide with the add_owner_as_member trigger (mig 009) on the team_members PK
    → 23505 → every POST /teams 500'd. Now the trigger is the single source of
    the owner membership.

    This test DB has teams_add_owner_trigger installed (asserted below), so we
    ALSO verify the owner ends up in team_members via the trigger. If a future
    test DB lacked the trigger we'd only assert create_team returns the team
    without raising — the owner membership is trigger-driven (verified live in
    prod) and not reproducible without it; we never manually insert the owner to
    force the assertion, which would defeat the point."""
    owner, _member = two_users

    conn = await asyncpg.connect(integration_db_url)
    try:
        has_trigger = (
            await conn.fetchval(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgname = 'teams_add_owner_trigger'"
            )
        ) > 0
    finally:
        await conn.close()

    created = await _repo().create_team(_name(), str(owner))
    assert created is not None
    assert type(created["id"]) is int  # bigint id stays int (5.3 trap)
    assert isinstance(created["invite_code"], str) and created["invite_code"]
    assert type(created["owner_id"]) is str  # uuid → str (shape parity)
    assert created["owner_id"] == str(owner)
    assert isinstance(created["settings_json"], dict)  # jsonb → native dict
    assert type(created["created_at"]) is str and "T" in created["created_at"]

    conn = await asyncpg.connect(integration_db_url)
    try:
        team_count = await conn.fetchval(
            "SELECT count(*) FROM teams WHERE id = $1", created["id"]
        )
        owner_role = await conn.fetchval(
            "SELECT role FROM team_members WHERE team_id = $1 AND user_id = $2",
            created["id"],
            owner,
        )
    finally:
        await conn.close()
    assert team_count == 1  # committed (no silent rollback)
    if has_trigger:
        # The add_owner_as_member trigger inserted the owner membership.
        assert owner_role == "owner"


# ─── ★ THE SILENT-KILLER PROOF: user_id must be str for authz == ────────


async def test_member_user_id_is_str_for_authz_compare(
    integration_db_url, patched_engine, cleanup_teams, two_users
):
    """The CORE crown-jewel guarantee. teams_router.update_member_role does:

        members = await repo.get_team_members(team_id, auth.user_id)
        member = next((m for m in members if m["user_id"] == user_id), None)
        if not member:
            raise HTTPException(404, "Member not found")

    ``user_id`` is the {user_id} path-param STRING. If the ORM returned a native
    uuid.UUID, that == is ALWAYS False → 404 after a successful role update.
    Prove get_team_members returns user_id as STR so the next() lookup matches."""
    owner, member_user = two_users
    conn = await asyncpg.connect(integration_db_url)
    try:
        team = await _seed_team(conn, owner)
    finally:
        await conn.close()
    team_id = str(team["id"])
    await _repo().add_member(team_id, str(member_user), "member")

    members = await _repo().get_team_members(team_id, str(owner))
    assert len(members) == 2
    for m in members:
        assert type(m["user_id"]) is str  # uuid → str (the authz column)
        assert type(m["team_id"]) is int  # bigint → native int (5.3 trap)

    # The exact silent-killer lookup from update_member_role (str path param).
    member_str = str(member_user)
    found = next((m for m in members if m["user_id"] == member_str), None)
    assert found is not None  # native uuid.UUID would make this None → 404
    assert found["role"] == "member"
    # owner also resolvable.
    owner_str = str(owner)
    assert any(m["user_id"] == owner_str for m in members)


# ─── junction add / remove (COMMIT) + owner-protection ──────────────────


async def test_add_remove_member_and_owner_protection(
    integration_db_url, patched_engine, cleanup_teams, two_users
):
    owner, member_user = two_users
    conn = await asyncpg.connect(integration_db_url)
    try:
        team = await _seed_team(conn, owner)
    finally:
        await conn.close()
    team_id = str(team["id"])

    # Add member.
    added = await _repo().add_member(team_id, str(member_user), "member")
    assert added is not None
    assert type(added["user_id"]) is str and added["user_id"] == str(member_user)
    assert added["role"] == "member"

    # Duplicate add → None (23505 on composite PK), NOT raise.
    dup = await _repo().add_member(team_id, str(member_user), "member")
    assert dup is None

    # update_member_role (requester = owner) succeeds.
    ok = await _repo().update_member_role(
        team_id, str(member_user), "admin", str(owner)
    )
    assert ok is True

    # Owner cannot be removed (owner-protection: str(owner_id)==str(target)).
    protected = await _repo().remove_member(team_id, str(owner), str(owner))
    assert protected is False

    # Member CAN be removed by the owner.
    removed = await _repo().remove_member(team_id, str(member_user), str(owner))
    assert removed is True

    conn = await asyncpg.connect(integration_db_url)
    try:
        still_member = await conn.fetchval(
            "SELECT count(*) FROM team_members WHERE team_id = $1 AND user_id = $2",
            team["id"],
            member_user,
        )
        owner_still = await conn.fetchval(
            "SELECT count(*) FROM team_members WHERE team_id = $1 AND user_id = $2",
            team["id"],
            owner,
        )
    finally:
        await conn.close()
    assert still_member == 0  # removal committed
    assert owner_still == 1  # owner protected, never removed


# ─── permission denial parity ───────────────────────────────────────────


async def test_non_member_cannot_change_roles(
    integration_db_url, patched_engine, cleanup_teams, two_users
):
    owner, outsider = two_users
    conn = await asyncpg.connect(integration_db_url)
    try:
        team = await _seed_team(conn, owner)
    finally:
        await conn.close()
    team_id = str(team["id"])
    # outsider is not a member → not owner/admin → False.
    denied = await _repo().update_member_role(
        team_id, str(owner), "admin", str(outsider)
    )
    assert denied is False
    removed = await _repo().remove_member(team_id, str(owner), str(outsider))
    assert removed is False


# ─── read access gating ─────────────────────────────────────────────────


async def test_get_team_by_id_membership_gated(
    integration_db_url, patched_engine, cleanup_teams, two_users
):
    owner, outsider = two_users
    conn = await asyncpg.connect(integration_db_url)
    try:
        team = await _seed_team(conn, owner)
    finally:
        await conn.close()
    team_id = str(team["id"])

    # Owner (a member) sees the team.
    seen = await _repo().get_team_by_id(team_id, str(owner))
    assert seen is not None
    assert seen["id"] == team["id"]
    assert type(seen["owner_id"]) is str

    # Outsider (no membership) → None.
    hidden = await _repo().get_team_by_id(team_id, str(outsider))
    assert hidden is None

    # get_user_teams returns the team for the owner, ordered + str-shaped.
    user_teams = await _repo().get_user_teams(str(owner))
    assert any(t["id"] == team["id"] for t in user_teams)
    assert all(type(t["owner_id"]) is str for t in user_teams)


async def test_update_and_delete_owner_gated(
    integration_db_url, patched_engine, cleanup_teams, two_users
):
    owner, outsider = two_users
    conn = await asyncpg.connect(integration_db_url)
    try:
        team = await _seed_team(conn, owner)
    finally:
        await conn.close()
    team_id = str(team["id"])

    # Non-owner update → None.
    assert await _repo().update_team(team_id, str(outsider), name=_name()) is None
    # Owner update → returns row.
    new_name = _name()
    updated = await _repo().update_team(team_id, str(owner), name=new_name)
    assert updated is not None and updated["name"] == new_name

    # Non-owner delete → False.
    assert await _repo().delete_team(team_id, str(outsider)) is False
    # Owner delete → True (cascade removes memberships).
    assert await _repo().delete_team(team_id, str(owner)) is True

    conn = await asyncpg.connect(integration_db_url)
    try:
        gone = await conn.fetchval(
            "SELECT count(*) FROM teams WHERE id = $1", team["id"]
        )
    finally:
        await conn.close()
    assert gone == 0


async def test_get_team_by_invite_code_and_join(
    integration_db_url, patched_engine, cleanup_teams, two_users
):
    owner, joiner = two_users
    conn = await asyncpg.connect(integration_db_url)
    try:
        team = await _seed_team(conn, owner)
    finally:
        await conn.close()
    code = team["invite_code"]

    # Lookup by invite code (the repo uppercases the code).
    found = await _repo().get_team_by_invite_code(code.lower())
    assert found is not None and found["id"] == team["id"]

    # join_team_by_code (inherited) composes get_team_by_invite_code + add_member.
    joined = await _repo().join_team_by_code(code, str(joiner))
    assert joined is not None and joined["id"] == team["id"]

    # Joining again → "Already a member" (add_member returns None on 23505).
    with pytest.raises(Exception, match="Already a member"):
        await _repo().join_team_by_code(code, str(joiner))


# ─── factory on/off ─────────────────────────────────────────────────────


def test_factory_off_returns_legacy():
    from unittest.mock import patch

    from app.repositories.team_repository import (
        TeamRepository,
        get_team_repository,
    )

    with patch("app.core.config.settings.USE_ORM_TEAM", False):
        assert type(get_team_repository()) is TeamRepository


def test_factory_on_returns_orm(integration_db_url):
    from unittest.mock import patch

    from app.repositories.team_repository_orm import TeamRepositoryOrm

    with (
        patch("app.core.config.settings.USE_ORM_TEAM", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        from app.repositories.team_repository import get_team_repository

        assert type(get_team_repository()) is TeamRepositoryOrm
