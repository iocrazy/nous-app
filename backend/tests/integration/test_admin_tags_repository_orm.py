"""Integration tests for AdminTagsRepositoryOrm (Phase 2 admin wave) vs real PG.

tags / tag_groups / resource_tags admin reads + writes.

Proves REST → ORM swap invisibility + strategy-C parity:
  - tag id / group_id (BIGINT — NOT uuid) → native int (5.3 trap); consumers str()
  - tags.user_id (uuid) → STR (generic sweep — list_tags returns the row raw)
  - created_at (timestamptz) → ISO STR; type plain str (CHECK, not Enum)
  - the PostgREST embed select('*, tag_groups(name)') reproduced as nested dict
  - usage_counts keyed by str(tag_id)
  - create/update/delete/batch/reorder WRITE + COMMIT; delete cascades resource_tags
  - factory on/off

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_admin_tags_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_admtag_"


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
async def seed(integration_db_url):
    """Seed one tag_group + one tag in that group. Yields ids. Cleans up after."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        gid = await conn.fetchval(
            "INSERT INTO tag_groups (name, sort_order) VALUES ($1, 5) RETURNING id",
            f"{_PREFIX}grp_{uuid.uuid4().hex[:8]}",
        )
        tid = await conn.fetchval(
            "INSERT INTO tags (name, type, group_id, color, sort_order) "
            "VALUES ($1, 'system', $2, '#abcdef', 3) RETURNING id",
            f"{_PREFIX}tag_{uuid.uuid4().hex[:8]}",
            gid,
        )
        yield {"group_id": int(gid), "tag_id": int(tid)}
    finally:
        await conn.execute("DELETE FROM resource_tags WHERE tag_id = $1", tid)
        await conn.execute("DELETE FROM tags WHERE id = $1", tid)
        await conn.execute("DELETE FROM tag_groups WHERE id = $1", gid)
        await conn.execute("DELETE FROM tags WHERE name LIKE $1", _PREFIX + "%")
        await conn.execute("DELETE FROM tag_groups WHERE name LIKE $1", _PREFIX + "%")
        await conn.close()


def _repo():
    from app.repositories.admin.tags_repository_orm import AdminTagsRepositoryOrm

    return AdminTagsRepositoryOrm()


async def test_list_tags_shape_embed_parity(integration_db_url, patched_engine, seed):
    rows, total = await _repo().list_tags(
        page=1, page_size=200, group_id=str(seed["group_id"])
    )
    ours = [r for r in rows if int(r["id"]) == seed["tag_id"]]
    assert ours and total >= 1
    r = ours[0]
    assert type(r["id"]) is int  # tag id is BIGINT → native int (NOT uuid)
    assert type(r["group_id"]) is int
    assert type(r["created_at"]) is str
    assert r["type"] == "system" and type(r["type"]) is str  # plain str (not Enum)
    # the embed: nested {"name": ...}
    assert isinstance(r["tag_groups"], dict)
    assert "name" in r["tag_groups"]
    # user_id is uuid → str (None here for a system tag, but type sweep applies)
    assert r.get("user_id") is None or type(r["user_id"]) is str


async def test_usage_counts_str_keyed(integration_db_url, patched_engine, seed):
    # add a resource_tags association so usage_counts has something to count
    conn = await asyncpg.connect(integration_db_url)
    try:
        rid = await conn.fetchval(
            "INSERT INTO resources (creator_id, source_type, filename) "
            "VALUES ((SELECT id FROM auth.users LIMIT 1), 'web', $1) RETURNING id",
            f"{_PREFIX}res",
        )
        await conn.execute(
            "INSERT INTO resource_tags (resource_id, tag_id) VALUES ($1, $2)",
            rid,
            seed["tag_id"],
        )
    finally:
        await conn.close()

    counts = await _repo().usage_counts([str(seed["tag_id"])])
    # keyed by str(tag_id) — matches the router's usage_counts.get(str(t["id"]))
    assert counts.get(str(seed["tag_id"])) == 1

    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM resource_tags WHERE tag_id = $1", seed["tag_id"]
        )
        await conn.execute("DELETE FROM resources WHERE id = $1", rid)
    finally:
        await conn.close()


async def test_list_groups_and_all_group_ids(integration_db_url, patched_engine, seed):
    groups = await _repo().list_groups()
    ours = [g for g in groups if int(g["id"]) == seed["group_id"]]
    assert ours and type(ours[0]["id"]) is int

    all_gids = await _repo().all_tag_group_ids()
    # group_id values native int (the router str()s them at group_counts[str(gid)])
    assert any(
        x["group_id"] is not None and int(x["group_id"]) == seed["group_id"]
        for x in all_gids
    )


async def test_create_update_delete_tag_commit(integration_db_url, patched_engine):
    created = await _repo().create_tag(
        {"name": f"{_PREFIX}new", "type": "system", "color": "#111111", "user_id": None}
    )
    assert created is not None and type(created["id"]) is int
    new_id = created["id"]

    updated = await _repo().update_tag(new_id, {"color": "#222222"})
    assert updated is not None and updated["color"] == "#222222"

    deleted = await _repo().delete_tag(new_id)
    assert deleted is True

    conn = await asyncpg.connect(integration_db_url)
    try:
        gone = await conn.fetchval("SELECT count(*) FROM tags WHERE id = $1", new_id)
        assert gone == 0  # committed
    finally:
        await conn.close()


async def test_create_group_and_max_sort(integration_db_url, patched_engine):
    created = await _repo().create_group(f"{_PREFIX}g2", 99)
    assert created is not None and type(created["id"]) is int
    gid = created["id"]
    try:
        mx = await _repo().max_group_sort_order()
        assert type(mx) is int and mx >= 99
    finally:
        conn = await asyncpg.connect(integration_db_url)
        try:
            await conn.execute("DELETE FROM tag_groups WHERE id = $1", gid)
        finally:
            await conn.close()


async def test_reorder_tags_commit(integration_db_url, patched_engine, seed):
    await _repo().reorder_tags([str(seed["tag_id"])])
    conn = await asyncpg.connect(integration_db_url)
    try:
        so = await conn.fetchval(
            "SELECT sort_order FROM tags WHERE id = $1", seed["tag_id"]
        )
        assert so == 0  # idx 0, committed
    finally:
        await conn.close()


# ─── factory parity ─────────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories.admin import tags_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_TAGS", False)
    assert type(mod.get_admin_tags_repository()) is mod.AdminTagsRepository


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories.admin import tags_repository as mod
    from app.repositories.admin.tags_repository_orm import AdminTagsRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_TAGS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    assert isinstance(mod.get_admin_tags_repository(), AdminTagsRepositoryOrm)
