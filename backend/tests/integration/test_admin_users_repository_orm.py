"""Integration tests for AdminUsersRepositoryOrm (Phase 2 admin wave) vs real PG.

user_profiles admin user-management reads + update/ban writes.

Proves REST → ORM swap invisibility + strategy-C parity:
  - id (uuid) → STR (the DICT-KEY enrichment trap — replays the email-map lookup)
  - role Enum(UserRole) → bare .value str (str(role) parity)
  - created_at / updated_at (timestamptz) → ISO STR
  - display_id (bigint) → native int
  - update() / set_banned() WRITE + COMMIT, return the full row
  - factory on/off

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_admin_users_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


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
async def seed_user(integration_db_url):
    """Insert a user_profiles row backed by a real auth.users id (FK), with a
    unique username + role='admin'. Yields the id (str). Cleans up after."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
        if uid is None:
            pytest.skip("no auth.users row to satisfy user_profiles.id FK")
        # Remove any pre-existing profile for this id, then insert ours.
        await conn.execute("DELETE FROM user_profiles WHERE id = $1", uid)
        uname = f"__test_orm_admuser_{uuid.uuid4().hex[:12]}"
        await conn.execute(
            "INSERT INTO user_profiles (id, username, role, is_banned) "
            "VALUES ($1, $2, 'admin', false)",
            uid,
            uname,
        )
        yield {"id": str(uid), "username": uname}
    finally:
        await conn.execute("DELETE FROM user_profiles WHERE id = $1", uid)
        await conn.close()


def _repo():
    from app.repositories.admin.users_repository_orm import AdminUsersRepositoryOrm

    return AdminUsersRepositoryOrm()


async def test_list_shape_and_parity(integration_db_url, patched_engine, seed_user):
    rows, total = await _repo().list_with_filters(
        page=1, page_size=200, search=seed_user["username"]
    )
    ours = [r for r in rows if str(r["id"]) == seed_user["id"]]
    assert ours and total >= 1
    r = ours[0]
    assert type(r["id"]) is str  # uuid → str (dict-key enrichment trap)
    assert r["id"] == seed_user["id"]
    # role Enum → bare str (str(role) parity: 'admin', NOT 'UserRole.ADMIN')
    assert type(r["role"]) is str
    assert str(r["role"]) == "admin"
    assert type(r["created_at"]) is str
    assert type(r["display_id"]) is int
    assert r["is_banned"] is False


async def test_dict_key_enrichment_lookup(
    integration_db_url, patched_engine, seed_user
):
    """Replay the router's email-map lookup: the id from a row, used as a dict key
    built from the SAME id list, must hit. A native UUID id would still hit the
    passthrough dict but would diverge from the str the REST path produced; here we
    assert the id is a str so set membership / str() / Supabase .eq all match REST."""
    row = await _repo().get_by_id(seed_user["id"])
    assert row is not None
    uid = row["id"]
    assert type(uid) is str
    # Simulate the batch helper keying its result by the passed id, then the
    # router's email_map.get(uid) lookup with the row's id.
    user_ids = [uid]
    email_map = {x: f"{x}@example.com" for x in user_ids}
    assert email_map.get(row["id"]) == f"{uid}@example.com"  # no silent miss


async def test_exists(integration_db_url, patched_engine, seed_user):
    assert await _repo().exists(seed_user["id"]) is True
    assert await _repo().exists(str(uuid.uuid4())) is False


async def test_update_round_trip_and_returns_full_row(
    integration_db_url, patched_engine, seed_user
):
    updated = await _repo().update(seed_user["id"], {"role": "user"})
    assert updated is not None
    assert str(updated["role"]) == "user"  # Enum unwrapped on the returned row too
    assert updated["id"] == seed_user["id"]

    conn = await asyncpg.connect(integration_db_url)
    try:
        role = await conn.fetchval(
            "SELECT role FROM user_profiles WHERE id = $1", seed_user["id"]
        )
    finally:
        await conn.close()
    assert str(role) == "user"  # committed


async def test_set_banned(integration_db_url, patched_engine, seed_user):
    updated = await _repo().set_banned(seed_user["id"], True)
    assert updated is not None and updated["is_banned"] is True

    conn = await asyncpg.connect(integration_db_url)
    try:
        banned = await conn.fetchval(
            "SELECT is_banned FROM user_profiles WHERE id = $1", seed_user["id"]
        )
    finally:
        await conn.close()
    assert banned is True


async def test_update_absent_returns_none(integration_db_url, patched_engine):
    assert await _repo().update(str(uuid.uuid4()), {"is_banned": True}) is None


# ─── factory parity ─────────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories.admin import users_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_USERS", False)
    assert type(mod.get_admin_users_repository()) is mod.AdminUsersRepository


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories.admin import users_repository as mod
    from app.repositories.admin.users_repository_orm import AdminUsersRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_USERS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    assert isinstance(mod.get_admin_users_repository(), AdminUsersRepositoryOrm)
