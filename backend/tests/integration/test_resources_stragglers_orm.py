"""Integration tests for the ported REST-straggler methods on
``ResourcesRepository`` against real Postgres.

These methods used to run legacy supabase-py REST bodies; they are now on the
ORM session scopes. This suite proves — against a live DB — that the ORM bodies
COMMIT and that Strategy-C value parity with the retired REST bodies holds:

  - find_by_hashes: per-creator, non-trashed, projected column set.
  - resource_tags trio: add (COMMIT) / get (embedded tag) / remove (COMMIT).
  - smart folders: create (COMMIT) / get (is_smart filter + order).
  - execute_smart_rules: resource-field eq + tag contains against a real join.
  - temp-sweeper: list_resources_in_folder (ISO created_at) / soft_delete_resource.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_resources_stragglers_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_NAME_PREFIX = "__test_orm_strag_"


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
async def conn(integration_db_url):
    c = await asyncpg.connect(integration_db_url)
    try:
        yield c
    finally:
        await c.close()


@pytest.fixture
async def auth_user(conn):
    row = await conn.fetchrow("SELECT id FROM auth.users LIMIT 1")
    return row["id"] if row else uuid.uuid4()


@pytest.fixture
async def team_id(conn):
    row = await conn.fetchrow("SELECT id FROM teams LIMIT 1")
    if row is None:
        pytest.skip("no teams row to satisfy folders/resource_items scope_id FK")
    return row["id"]


@pytest.fixture
async def cleanup(integration_db_url):
    yield
    c = await asyncpg.connect(integration_db_url)
    try:
        await c.execute(
            "DELETE FROM resource_items WHERE resource_id IN "
            "(SELECT id FROM resources WHERE filename LIKE $1)",
            _NAME_PREFIX + "%",
        )
        await c.execute(
            "DELETE FROM resource_tags WHERE tag_id IN "
            "(SELECT id FROM tags WHERE name LIKE $1)",
            _NAME_PREFIX + "%",
        )
        await c.execute("DELETE FROM tags WHERE name LIKE $1", _NAME_PREFIX + "%")
        await c.execute(
            "DELETE FROM resources WHERE filename LIKE $1", _NAME_PREFIX + "%"
        )
        await c.execute("DELETE FROM folders WHERE name LIKE $1", _NAME_PREFIX + "%")
    finally:
        await c.close()


def _repo():
    from app.repositories.resources_repository import ResourcesRepository

    return ResourcesRepository()


def _name() -> str:
    return f"{_NAME_PREFIX}{uuid.uuid4().hex[:8]}"


async def _make_resource(conn, creator_id, *, file_hash=None, is_trashed=False) -> int:
    return await conn.fetchval(
        "INSERT INTO resources (creator_id, source_type, filename, file_hash, "
        "is_trashed) VALUES ($1, 'web', $2, $3, $4) RETURNING id",
        creator_id,
        _name(),
        file_hash,
        is_trashed,
    )


# ─── find_by_hashes ───────────────────────────────────────────────────────────


async def test_find_by_hashes_parity(patched_engine, cleanup, conn):
    creator = uuid.uuid4()
    h1, h2 = "a" * 64, "b" * 64
    rid1 = await _make_resource(conn, creator, file_hash=h1)
    await _make_resource(conn, creator, file_hash=h2, is_trashed=True)  # excluded
    # Another creator with the same hash must NOT leak.
    await _make_resource(conn, uuid.uuid4(), file_hash=h1)

    result = await _repo().find_by_hashes([h1, h2], str(creator))

    assert set(result.keys()) == {h1}  # trashed h2 excluded, cross-creator excluded
    row = result[h1]
    assert row["id"] == rid1
    assert type(row["id"]) is int  # bigint native
    # Exactly the legacy projection.
    from app.repositories import resources_repository as m

    assert set(row.keys()) == set(m._FIND_BY_HASH_COLS)


# ─── resource_tags trio ───────────────────────────────────────────────────────


async def test_resource_tag_add_get_remove_commit(
    patched_engine, cleanup, conn, auth_user
):
    resource_id = await _make_resource(conn, auth_user)
    tag_id = await conn.fetchval(
        "INSERT INTO tags (name, type) VALUES ($1, 'user') RETURNING id", _name()
    )

    # add (COMMIT + returning-row parity).
    created = await _repo().add_resource_tag(
        str(resource_id), str(tag_id), str(auth_user)
    )
    assert type(created["resource_id"]) is int
    assert type(created["tag_id"]) is int
    assert type(created["tagged_by"]) is str
    assert type(created["created_at"]) is str and "T" in created["created_at"]

    # Persisted (COMMIT, not rolled back).
    persisted = await conn.fetchval(
        "SELECT count(*) FROM resource_tags WHERE resource_id=$1 AND tag_id=$2",
        resource_id,
        tag_id,
    )
    assert persisted == 1

    # get_resource_tags embeds the full tag row under "tag".
    rows = await _repo().get_resource_tags(str(resource_id))
    assert len(rows) == 1
    assert rows[0]["tag_id"] == tag_id
    assert rows[0]["tag"]["id"] == tag_id
    assert type(rows[0]["tag"]["id"]) is int

    # remove (COMMIT).
    ok = await _repo().remove_resource_tag(str(resource_id), str(tag_id))
    assert ok is True
    gone = await conn.fetchval(
        "SELECT count(*) FROM resource_tags WHERE resource_id=$1 AND tag_id=$2",
        resource_id,
        tag_id,
    )
    assert gone == 0
    # Idempotent: removing again is still True.
    assert await _repo().remove_resource_tag(str(resource_id), str(tag_id)) is True


# ─── smart folders ────────────────────────────────────────────────────────────


async def test_smart_folder_create_and_get(patched_engine, cleanup, auth_user, team_id):
    name = _name()
    rules = {"operator": "AND", "match": True, "conditions": []}
    created = await _repo().create_smart_folder(
        {
            "name": name,
            "scope_id": team_id,
            "created_by": str(auth_user),
            "is_smart": True,
            "smart_rules": rules,
            "icon": None,
            "color": None,
        }
    )
    assert type(created["id"]) is int
    assert type(created["created_by"]) is str
    assert type(created["created_at"]) is str
    assert created["is_smart"] is True
    assert created["smart_rules"] == rules  # jsonb → native dict

    folders = await _repo().get_smart_folders(None, str(team_id))
    assert any(f["id"] == created["id"] and f["is_smart"] for f in folders)


# ─── execute_smart_rules ──────────────────────────────────────────────────────


async def test_execute_smart_rules_eq_and_tag(
    patched_engine, cleanup, conn, auth_user, team_id
):
    resource_id = await _make_resource(conn, auth_user)
    target_name = await conn.fetchval(
        "SELECT filename FROM resources WHERE id=$1", resource_id
    )
    await conn.execute(
        "INSERT INTO resource_items (scope_id, resource_id) VALUES ($1, $2)",
        team_id,
        resource_id,
    )
    tag_id = await conn.fetchval(
        "INSERT INTO tags (name, type) VALUES ($1, 'user') RETURNING id",
        _NAME_PREFIX + "funnytag",
    )
    await conn.execute(
        "INSERT INTO resource_tags (resource_id, tag_id) VALUES ($1, $2)",
        resource_id,
        tag_id,
    )

    # Resource-field eq: filename matches → the item is returned with its embed.
    rules = {
        "operator": "AND",
        "match": True,
        "conditions": [{"field": "filename", "op": "eq", "value": target_name}],
    }
    items = await _repo().execute_smart_rules(None, str(team_id), rules)
    assert any(it["resource_id"] == resource_id for it in items)
    match = next(it for it in items if it["resource_id"] == resource_id)
    assert match["resource"]["id"] == resource_id  # nested full resource row
    assert type(match["resource"]["creator_id"]) is str  # Strategy-C

    # Tag contains: same resource matches the tag rule.
    tag_rules = {
        "operator": "AND",
        "match": True,
        "conditions": [
            {"field": "tags", "op": "contains", "value": _NAME_PREFIX + "funnytag"}
        ],
    }
    tagged = await _repo().execute_smart_rules(None, str(team_id), tag_rules)
    assert any(it["resource_id"] == resource_id for it in tagged)


# ─── temp-sweeper helpers ─────────────────────────────────────────────────────


async def test_list_and_soft_delete_in_folder(
    patched_engine, cleanup, conn, auth_user, team_id
):
    folder_id = await conn.fetchval(
        "INSERT INTO folders (name, scope_id, created_by) VALUES ($1, $2, $3) "
        "RETURNING id",
        _name(),
        team_id,
        auth_user,
    )
    resource_id = await _make_resource(conn, auth_user)
    await conn.execute(
        "INSERT INTO resource_items (scope_id, resource_id, folder_id) "
        "VALUES ($1, $2, $3)",
        team_id,
        resource_id,
        folder_id,
    )

    listed = await _repo().list_resources_in_folder(str(folder_id))
    assert any(r["id"] == resource_id for r in listed)
    row = next(r for r in listed if r["id"] == resource_id)
    # created_at is an ISO STRING (the sweeper calls .replace() on it).
    assert type(row["created_at"]) is str

    # soft_delete flips is_trashed + sets trashed_at (COMMIT).
    await _repo().soft_delete_resource(str(resource_id))
    is_trashed, trashed_at = await conn.fetchrow(
        "SELECT is_trashed, trashed_at FROM resources WHERE id=$1", resource_id
    )
    assert is_trashed is True
    assert trashed_at is not None

    # Now excluded from the default (non-trashed) listing.
    listed_after = await _repo().list_resources_in_folder(str(folder_id))
    assert all(r["id"] != resource_id for r in listed_after)
    # ...but present with include_trashed.
    listed_all = await _repo().list_resources_in_folder(
        str(folder_id), include_trashed=True
    )
    assert any(r["id"] == resource_id for r in listed_all)
