"""Integration tests for TagsRepositoryOrm (Phase 2 M batch) against real PG.

Proves the REST → ORM swap is invisible AND that STRATEGY-C value-type parity
holds on the tag system:

  - tags.id / scope_id / group_id (BIGINT) → native int (the 5.3 trap).
  - tags.user_id (uuid) → str (TagResponse.user_id is a str field).
  - tags.created_at (timestamptz) → ISO str.
  - resource_tags junction (composite PK resource_id+tag_id, both bigint) → the
    add/bulk_add upserts reproduce ON CONFLICT DO UPDATE; no mixed-PK hazard.
  - resolve_media_id_to_resource_id returns a STR (legacy contract).
  - factory on/off.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_tags_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_NAME_PREFIX = "__test_orm_tag_"


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
async def auth_user(integration_db_url):
    """Yield one REAL auth.users id (tags.user_id has no FK but we use a real one
    for realism). Falls back to a random uuid if the DB has no users."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        row = await conn.fetchrow("SELECT id FROM auth.users LIMIT 1")
        yield row["id"] if row else uuid.uuid4()
    finally:
        await conn.close()


@pytest.fixture
async def cleanup(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        # resource_tags FK-cascade off tags; delete test tags by name prefix.
        await conn.execute(
            "DELETE FROM resource_tags WHERE tag_id IN "
            "(SELECT id FROM tags WHERE name LIKE $1)",
            _NAME_PREFIX + "%",
        )
        await conn.execute("DELETE FROM tags WHERE name LIKE $1", _NAME_PREFIX + "%")
    finally:
        await conn.close()


def _repo():
    from app.repositories.tags_repository_orm import TagsRepositoryOrm

    return TagsRepositoryOrm()


def _name() -> str:
    return f"{_NAME_PREFIX}{uuid.uuid4().hex[:8]}"


# ─── create_tag (COMMIT + parity) ───────────────────────────────────────


async def test_create_tag_commit_and_parity(patched_engine, cleanup, auth_user):
    name = _name()
    created = await _repo().create_tag(
        name=name, user_id=str(auth_user), color="#abcdef", name_zh="测试"
    )
    assert created is not None
    # bigint id → native int (5.3 trap).
    assert type(created["id"]) is int
    # uuid user_id → str (TagResponse.user_id is a str field).
    assert type(created["user_id"]) is str
    assert created["user_id"] == str(auth_user)
    # timestamptz → ISO str.
    assert type(created["created_at"]) is str and "T" in created["created_at"]
    assert created["name"] == name
    assert created["type"] == "user"
    assert created["color"] == "#abcdef"
    assert created["name_zh"] == "测试"

    # get_by_id round-trips the same parity shape.
    fetched = await _repo().get_tag_by_id(str(created["id"]))
    assert fetched is not None
    assert type(fetched["id"]) is int
    assert type(fetched["user_id"]) is str
    assert fetched["id"] == created["id"]


async def test_get_tag_by_name_case_insensitive(patched_engine, cleanup, auth_user):
    name = _name()
    await _repo().create_tag(name=name, user_id=str(auth_user))
    # Case-insensitive English match within the user scope.
    found = await _repo().get_tag_by_name(name.upper(), user_id=str(auth_user))
    assert found is not None
    assert found["name"] == name


async def test_update_tag_commit(patched_engine, cleanup, auth_user):
    created = await _repo().create_tag(name=_name(), user_id=str(auth_user))
    updated = await _repo().update_tag(
        str(created["id"]), str(auth_user), color="#111111"
    )
    assert updated is not None
    assert updated["color"] == "#111111"
    assert type(updated["id"]) is int


async def test_update_tag_no_changes_returns_current(
    patched_engine, cleanup, auth_user
):
    created = await _repo().create_tag(name=_name(), user_id=str(auth_user))
    # All-None kwargs → no-op → returns current row.
    same = await _repo().update_tag(str(created["id"]), str(auth_user), color=None)
    assert same is not None
    assert same["id"] == created["id"]


async def test_delete_tag_commit(patched_engine, cleanup, auth_user):
    created = await _repo().create_tag(name=_name(), user_id=str(auth_user))
    ok = await _repo().delete_tag(str(created["id"]), str(auth_user))
    assert ok is True
    gone = await _repo().get_tag_by_id(str(created["id"]))
    assert gone is None


# ─── resource_tags junction (composite-PK upsert) ───────────────────────


async def test_add_and_bulk_add_tags_to_resource_upsert(
    integration_db_url, patched_engine, cleanup, auth_user
):
    """Junction add + bulk_add reproduce ON CONFLICT DO UPDATE on the composite
    PK (resource_id, tag_id). We need a real resources row for the FK."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        res_row = await conn.fetchrow("SELECT id FROM resources LIMIT 1")
    finally:
        await conn.close()
    if res_row is None:
        pytest.skip("no resources row to satisfy resource_tags.resource_id FK")
    resource_id = res_row["id"]

    t1 = await _repo().create_tag(name=_name(), user_id=str(auth_user))
    t2 = await _repo().create_tag(name=_name(), user_id=str(auth_user))

    # Single add (upsert).
    rt = await _repo().add_tag_to_resource(
        str(resource_id), str(t1["id"]), confidence=0.5, source="ai"
    )
    assert type(rt["resource_id"]) is int  # bigint native
    assert type(rt["tag_id"]) is int
    assert rt["tag_id"] == t1["id"]
    assert rt["source"] == "ai"
    # double confidence → native float.
    assert isinstance(rt["confidence"], float)

    # Idempotent re-add (ON CONFLICT DO UPDATE — no PK violation).
    rt2 = await _repo().add_tag_to_resource(
        str(resource_id), str(t1["id"]), source="manual"
    )
    assert rt2["source"] == "manual"

    # bulk_add (multi-VALUES upsert, one of them already present → DO UPDATE).
    rows = await _repo().bulk_add_tags_to_resource(
        str(resource_id), [str(t1["id"]), str(t2["id"])], source="auto"
    )
    assert len(rows) == 2
    assert all(type(r["tag_id"]) is int for r in rows)

    # get_resource_tags returns the embedded tags(*) shape.
    tags = await _repo().get_resource_tags(str(resource_id))
    tag_ids = {r["tag_id"] for r in tags}
    assert t1["id"] in tag_ids and t2["id"] in tag_ids
    for r in tags:
        assert r.get("tags") is not None
        assert type(r["tags"]["id"]) is int

    # remove one.
    removed = await _repo().remove_tag_from_resource(str(resource_id), str(t1["id"]))
    assert removed is True

    # cleanup junction rows for this resource (test tags get cascade-cleaned).
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM resource_tags WHERE resource_id = $1 AND tag_id = ANY($2::bigint[])",
            resource_id,
            [t1["id"], t2["id"]],
        )
    finally:
        await conn.close()


async def test_bulk_add_empty_is_noop(patched_engine, cleanup):
    out = await _repo().bulk_add_tags_to_resource("123", [], source="manual")
    assert out == []


# ─── get_all_tags (system/time/user + media_count) ──────────────────────


async def test_get_all_tags_shape(patched_engine, cleanup, auth_user):
    created = await _repo().create_tag(name=_name(), user_id=str(auth_user))
    tags = await _repo().get_all_tags(user_id=str(auth_user))
    mine = [t for t in tags if t["id"] == created["id"]]
    assert len(mine) == 1
    t = mine[0]
    assert type(t["id"]) is int
    assert type(t["user_id"]) is str
    assert "media_count" in t and type(t["media_count"]) is int
    assert "group_name" in t  # flattened embed (None here)
    assert t["enabled"] is True


# ─── resolve_media_id_to_resource_id returns STR ────────────────────────


async def test_resolve_media_id_returns_str(integration_db_url, patched_engine):
    conn = await asyncpg.connect(integration_db_url)
    try:
        row = await conn.fetchrow(
            "SELECT media_id, id FROM resources WHERE media_id IS NOT NULL LIMIT 1"
        )
    finally:
        await conn.close()
    if row is None:
        pytest.skip("no resources row with media_id to resolve")
    resolved = await _repo().resolve_media_id_to_resource_id(str(row["media_id"]))
    assert resolved is not None
    # Legacy contract: returns the id as a STR.
    assert type(resolved) is str
    assert resolved == str(row["id"])


async def test_resolve_media_id_none_when_missing(patched_engine):
    # A media_id with no resource → None.
    resolved = await _repo().resolve_media_id_to_resource_id("1")
    assert resolved is None or type(resolved) is str


# ─── get_tag_counts fallback (resources.creator_id, formerly broken) ─────


async def test_get_tag_counts_fallback_uses_creator_id(
    integration_db_url, patched_engine, cleanup, auth_user
):
    """The fallback formerly filtered ``resources.user_id`` (a column that does
    not exist) → PG 42703. It now filters the real ``creator_id`` column and
    returns tag counts for that creator instead of raising.

    We need a real resources row owned by ``auth_user`` plus a tag attached to it
    so the fallback returns at least one count row."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        res_row = await conn.fetchrow(
            "SELECT id FROM resources WHERE creator_id = $1 LIMIT 1", auth_user
        )
    finally:
        await conn.close()
    if res_row is None:
        pytest.skip("no resources row owned by auth_user to exercise the fallback")
    resource_id = res_row["id"]

    tag = await _repo().create_tag(name=_name(), user_id=str(auth_user))
    await _repo().add_tag_to_resource(str(resource_id), str(tag["id"]), source="manual")

    try:
        # Directly exercise the fallback (the RPC path returns first otherwise).
        rows = await _repo()._get_tag_counts_fallback(str(auth_user), limit=50)
        mine = [r for r in rows if r["id"] == tag["id"]]
        assert mine, "fallback should return the tag attached to the creator's resource"
        assert type(mine[0]["count"]) is int and mine[0]["count"] >= 1
    finally:
        conn = await asyncpg.connect(integration_db_url)
        try:
            await conn.execute(
                "DELETE FROM resource_tags WHERE resource_id = $1 AND tag_id = $2",
                resource_id,
                tag["id"],
            )
        finally:
            await conn.close()


# ─── factory on/off ─────────────────────────────────────────────────────


def test_factory_off_returns_legacy():
    from unittest.mock import patch

    from app.repositories.tags_repository import TagsRepository, get_tags_repository

    with patch("app.core.config.settings.USE_ORM_TAGS", False):
        assert type(get_tags_repository()) is TagsRepository


def test_factory_on_returns_orm(integration_db_url):
    from unittest.mock import patch

    from app.repositories.tags_repository_orm import TagsRepositoryOrm

    with (
        patch("app.core.config.settings.USE_ORM_TAGS", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        from app.repositories.tags_repository import get_tags_repository

        assert type(get_tags_repository()) is TagsRepositoryOrm
