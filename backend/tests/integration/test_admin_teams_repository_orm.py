"""Integration tests for AdminTeamsRepositoryOrm (Phase 2 admin wave) vs real PG.

teams / team_members / team_quotas / collections admin reads + writes.

Proves REST → ORM swap invisibility + strategy-C parity:
  - owner_id / user_id (uuid) → STR (the owner/member email-enrichment DICT-KEY trap)
  - id / team_id (bigint) → native int (5.3 trap); batch_points_balances keys str(id)
  - created_at / joined_at (timestamptz) → ISO STR; jsonb → native dict
  - kind plain str (NOT Enum)
  - update / delete / update_member_role / delete_member WRITE + COMMIT
  - get() / get_member() return None on absent (router None-guard → 404)
  - factory on/off

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_admin_teams_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_admteam_"


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
    """Insert a team (bigint id, snowflake) + one member + a quota row. Borrows two
    real auth.users ids for owner_id / member user_id. Cleans up after."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        users = await conn.fetch("SELECT id FROM auth.users LIMIT 2")
        if not users:
            pytest.skip("no auth.users rows to use as owner/member ids")
        owner_id = users[0]["id"]
        member_id = users[1]["id"] if len(users) > 1 else users[0]["id"]
        tid = await conn.fetchval(
            "INSERT INTO teams (name, owner_id, invite_code, kind) "
            "VALUES ($1, $2, $3, 'collaborative') RETURNING id",
            f"{_PREFIX}{uuid.uuid4().hex[:8]}",
            owner_id,
            f"inv{uuid.uuid4().hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO team_members (team_id, user_id, role) VALUES ($1, $2, 'member')",
            tid,
            member_id,
        )
        await conn.execute(
            "INSERT INTO team_quotas (team_id, points_balance) VALUES ($1, 1234) "
            "ON CONFLICT (team_id) DO UPDATE SET points_balance = 1234",
            tid,
        )
        yield {
            "id": int(tid),
            "owner_id": str(owner_id),
            "member_id": str(member_id),
        }
    finally:
        await conn.execute("DELETE FROM team_quotas WHERE team_id = $1", tid)
        await conn.execute("DELETE FROM team_members WHERE team_id = $1", tid)
        await conn.execute("DELETE FROM teams WHERE id = $1", tid)
        await conn.close()


def _repo():
    from app.repositories.admin.teams_repository_orm import AdminTeamsRepositoryOrm

    return AdminTeamsRepositoryOrm()


async def test_list_teams_shape_and_parity(
    integration_db_url, patched_engine, seed_team
):
    rows, total = await _repo().list_teams(search=_PREFIX, offset=0, limit=200)
    ours = [r for r in rows if int(r["id"]) == seed_team["id"]]
    assert ours and total >= 1
    r = ours[0]
    assert type(r["id"]) is int  # bigint → native int (5.3 trap)
    assert type(r["owner_id"]) is str  # uuid → str (enrichment dict-key trap)
    assert r["owner_id"] == seed_team["owner_id"]
    assert type(r["created_at"]) is str
    assert (
        r["kind"] == "collaborative" and type(r["kind"]) is str
    )  # plain str (not Enum)
    # jsonb columns → native dict (or None)
    assert r["settings_json"] is None or isinstance(r["settings_json"], dict)


async def test_owner_id_enrichment_set_membership(
    integration_db_url, patched_engine, seed_team
):
    """Replay list_teams' set(owner_ids) + owner_info.get(oid). With owner_id str'd,
    the set carries strs and the lookup hits — a native UUID would mismatch the str
    keys the batch helper builds against the REST path."""
    rows, _ = await _repo().list_teams(search=_PREFIX, offset=0, limit=200)
    owner_ids = list({t["owner_id"] for t in rows})
    assert all(type(x) is str for x in owner_ids)
    owner_info = {oid: ("e@x.com", "u") for oid in owner_ids}
    ours = [r for r in rows if int(r["id"]) == seed_team["id"]][0]
    assert owner_info.get(ours["owner_id"]) == ("e@x.com", "u")


async def test_get_team_and_points(integration_db_url, patched_engine, seed_team):
    team = await _repo().get(seed_team["id"])
    assert team is not None
    assert type(team["id"]) is int and team["id"] == seed_team["id"]
    assert type(team["owner_id"]) is str

    bal = await _repo().get_points_balance(seed_team["id"])
    assert type(bal) is int and bal == 1234

    batch = await _repo().batch_points_balances([seed_team["id"]])
    # keyed by str(team_id) — matches the router's points_balances.get(str(tid))
    assert batch.get(str(seed_team["id"])) == 1234


async def test_list_members_dict_key(integration_db_url, patched_engine, seed_team):
    members = await _repo().list_members(seed_team["id"])
    assert members
    m = members[0]
    assert type(m["user_id"]) is str  # uuid → str (member enrichment dict-key)
    assert type(m["joined_at"]) is str or m["joined_at"] is None
    user_ids = [mm["user_id"] for mm in members]
    user_info = {uid: ("e", "u") for uid in user_ids}
    assert user_info.get(m["user_id"]) == ("e", "u")


async def test_get_absent_returns_none(integration_db_url, patched_engine):
    """get() returns None on a 0-row result (one_or_none) so the router's
    None-guard fires and returns 404 instead of 500."""
    assert await _repo().get(999999999999999999) is None


async def test_update_and_delete_member_commit(
    integration_db_url, patched_engine, seed_team
):
    await _repo().update_member_role(seed_team["id"], seed_team["member_id"], "admin")
    conn = await asyncpg.connect(integration_db_url)
    try:
        role = await conn.fetchval(
            "SELECT role FROM team_members WHERE team_id = $1 AND user_id = $2",
            seed_team["id"],
            seed_team["member_id"],
        )
        assert role == "admin"  # committed
    finally:
        await conn.close()

    await _repo().delete_member(seed_team["id"], seed_team["member_id"])
    conn = await asyncpg.connect(integration_db_url)
    try:
        gone = await conn.fetchval(
            "SELECT count(*) FROM team_members WHERE team_id = $1 AND user_id = $2",
            seed_team["id"],
            seed_team["member_id"],
        )
        assert gone == 0
    finally:
        await conn.close()


async def test_update_team_commit(integration_db_url, patched_engine, seed_team):
    await _repo().update(seed_team["id"], {"name": f"{_PREFIX}renamed"})
    conn = await asyncpg.connect(integration_db_url)
    try:
        name = await conn.fetchval(
            "SELECT name FROM teams WHERE id = $1", seed_team["id"]
        )
        assert name == f"{_PREFIX}renamed"
    finally:
        await conn.close()


# ─── factory parity ─────────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories.admin import teams_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_TEAMS", False)
    assert type(mod.get_admin_teams_repository()) is mod.AdminTeamsRepository


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories.admin import teams_repository as mod
    from app.repositories.admin.teams_repository_orm import AdminTeamsRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_TEAMS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    assert isinstance(mod.get_admin_teams_repository(), AdminTeamsRepositoryOrm)
