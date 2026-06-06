"""Integration tests for PermissionRepositoryOrm (Phase 2 M batch) vs real PG.

Proves the REST → ORM swap is invisible on the ReBAC read surface
(access_overrides / folders / libraries / team_members / resource_items) AND
that STRATEGY-C value-type parity holds:

  - folders.id/parent_id/scope_id, resource_items.scope_id/folder_id,
    libraries.id (all BIGINT) → STAY native int (the 5.3 trap).
  - access_overrides.* uuids (id / user_id / granted_by) → STR (shape parity);
    object_id is text; created_at → ISO str.
  - libraries.scope_id is TEXT → native str; team_members.role → bare str.
  - get_access_override str()-coerces its object_id param so a native-int
    folder id binds against the TEXT object_id column (REST int→text cast
    parity) — pinned by test_access_override_int_object_id_binds.

READ-ONLY repo → no writes; rows are seeded directly via asyncpg and cleaned up
by id.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_permission_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_perm_"


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


async def _real_user_id(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy created_by / user_id FKs")
    return uid


@pytest.fixture
async def seed(integration_db_url):
    """Seed a team + folders + library + team_member + resource_item +
    access_overrides; tear everything down by id afterward."""
    conn = await asyncpg.connect(integration_db_url)
    created: dict = {}
    try:
        user_id = await _real_user_id(conn)
        created["user_id"] = user_id

        team_id = await conn.fetchval(
            "INSERT INTO teams (name, owner_id, invite_code) "
            "VALUES ($1, $2, $3) RETURNING id",
            f"{_PREFIX}team",
            user_id,
            uuid.uuid4().hex[:10],
        )
        created["team_id"] = team_id

        # A DB trigger may already insert the team owner into team_members on
        # team creation — upsert to guarantee role='owner' either way.
        await conn.execute(
            "INSERT INTO team_members (user_id, team_id, role) VALUES ($1, $2, $3) "
            "ON CONFLICT (team_id, user_id) DO UPDATE SET role = EXCLUDED.role",
            user_id,
            team_id,
            "owner",
        )

        parent_folder = await conn.fetchval(
            "INSERT INTO folders (name, scope_id, created_by) "
            "VALUES ($1, $2, $3) RETURNING id",
            f"{_PREFIX}parent",
            team_id,
            user_id,
        )
        created["parent_folder"] = parent_folder
        child_folder = await conn.fetchval(
            "INSERT INTO folders (name, scope_id, created_by, parent_id) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            f"{_PREFIX}child",
            team_id,
            user_id,
            parent_folder,
        )
        created["child_folder"] = child_folder

        library_id = await conn.fetchval(
            "INSERT INTO libraries (name, scope_type, scope_id, created_by, "
            "visibility) VALUES ($1, 'team', $2, $3, 'restricted') RETURNING id",
            f"{_PREFIX}lib",
            str(team_id),
            user_id,
        )
        created["library_id"] = library_id

        resource_id = await conn.fetchval(
            "INSERT INTO resources (creator_id, source_type, filename) "
            "VALUES ($1, 'upload', $2) RETURNING id",
            user_id,
            f"{_PREFIX}res.mp4",
        )
        created["resource_id"] = resource_id
        await conn.execute(
            "INSERT INTO resource_items (scope_id, resource_id, folder_id) "
            "VALUES ($1, $2, $3)",
            team_id,
            resource_id,
            parent_folder,
        )

        # An access override keyed by the PARENT FOLDER id (a bigint) stored as
        # text in object_id — pins the int→text bind parity.
        await conn.execute(
            "INSERT INTO access_overrides (object_type, object_id, user_id, role) "
            "VALUES ('folder', $1, $2, 'editor')",
            str(parent_folder),
            user_id,
        )
    finally:
        await conn.close()

    yield created

    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM access_overrides WHERE object_id = $1",
            str(created.get("parent_folder")),
        )
        await conn.execute(
            "DELETE FROM resource_items WHERE resource_id = $1",
            created.get("resource_id"),
        )
        await conn.execute(
            "DELETE FROM resources WHERE id = $1", created.get("resource_id")
        )
        await conn.execute(
            "DELETE FROM folders WHERE id = ANY($1::bigint[])",
            [created.get("child_folder"), created.get("parent_folder")],
        )
        await conn.execute(
            "DELETE FROM libraries WHERE id = $1", created.get("library_id")
        )
        await conn.execute("DELETE FROM teams WHERE id = $1", created.get("team_id"))
    finally:
        await conn.close()


def _repo():
    from app.repositories.permission_repository_orm import PermissionRepositoryOrm

    return PermissionRepositoryOrm()


# ─── Reads + strategy-C parity ──────────────────────────────────────────


async def test_get_folder_by_id_parity(integration_db_url, patched_engine, seed):
    """folders.id/parent_id/scope_id stay native int; visibility text."""
    got = await _repo().get_folder_by_id(str(seed["child_folder"]))
    assert got is not None
    assert type(got["id"]) is int
    assert got["id"] == seed["child_folder"]
    assert type(got["parent_id"]) is int  # bigint FK stays int (5.3 trap)
    assert got["parent_id"] == seed["parent_folder"]
    assert type(got["scope_id"]) is int
    assert got["visibility"] in {"inherited", "restricted"}


async def test_get_folder_missing_returns_none(
    integration_db_url, patched_engine, seed
):
    assert await _repo().get_folder_by_id("999999999999999999") is None


async def test_get_library_by_id_parity(integration_db_url, patched_engine, seed):
    """libraries.id bigint → int; scope_id TEXT → str; visibility consumed."""
    got = await _repo().get_library_by_id(str(seed["library_id"]))
    assert got is not None
    assert type(got["id"]) is int
    assert got["scope_type"] == "team"
    assert type(got["scope_id"]) is str  # libraries.scope_id is TEXT
    assert got["scope_id"] == str(seed["team_id"])
    assert got["visibility"] == "restricted"


async def test_get_team_member_role(integration_db_url, patched_engine, seed):
    role = await _repo().get_team_member_role(
        str(seed["user_id"]), str(seed["team_id"])
    )
    assert role == "owner"
    # Non-member → None.
    none_role = await _repo().get_team_member_role(
        str(uuid.uuid4()), str(seed["team_id"])
    )
    assert none_role is None


async def test_get_resource_item_scope_parity(integration_db_url, patched_engine, seed):
    """resource_items.scope_id / folder_id stay native int (5.3 trap)."""
    got = await _repo().get_resource_item_scope(str(seed["resource_id"]))
    assert got is not None
    assert type(got["scope_id"]) is int
    assert got["scope_id"] == seed["team_id"]
    assert type(got["folder_id"]) is int
    assert got["folder_id"] == seed["parent_folder"]


async def test_get_access_override_uuid_parity(
    integration_db_url, patched_engine, seed
):
    """get_access_override (string object_id) returns role + str'd uuids."""
    got = await _repo().get_access_override(
        "folder", str(seed["parent_folder"]), str(seed["user_id"])
    )
    assert got is not None
    assert got["role"] == "editor"
    assert type(got["user_id"]) is str  # uuid → str (shape parity)
    assert got["user_id"] == str(seed["user_id"])
    assert type(got["id"]) is str  # uuid PK → str
    assert type(got["created_at"]) is str and "T" in got["created_at"]


async def test_access_override_int_object_id_binds(
    integration_db_url, patched_engine, seed
):
    """THE M-BATCH BIND HAZARD: passing a NATIVE int object_id (a bigint folder
    id, as get_folder_by_id returns it) must bind against the TEXT object_id
    column (str()-coerced) and match — reproducing PostgREST's int→text cast.
    Without the coercion asyncpg raises; with it the override is found."""
    got = await _repo().get_access_override(
        "folder", seed["parent_folder"], str(seed["user_id"])
    )
    assert got is not None
    assert got["role"] == "editor"


async def test_access_override_missing_returns_none(
    integration_db_url, patched_engine, seed
):
    got = await _repo().get_access_override(
        "folder", str(seed["child_folder"]), str(seed["user_id"])
    )
    assert got is None  # no override on the child folder


# ─── Factory flag wiring ────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories import permission_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_PERMISSION", False)
    repo = mod.get_permission_repository()
    assert type(repo) is mod.PermissionRepository
    from app.repositories.permission_repository_orm import PermissionRepositoryOrm

    assert not isinstance(repo, PermissionRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import permission_repository as mod
    from app.repositories.permission_repository_orm import PermissionRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_PERMISSION", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_permission_repository()
    assert isinstance(repo, PermissionRepositoryOrm)
