"""Integration tests for CollectionsRepositoryOrm against a real PG database.

Covers: create → get_all → get_by_id → update → update_cache → delete
round-trip, preset creation, and ASSERTS strategy-C value-type parity:
  - id is native int (bigint snowflake, never a str)
  - user_id is str (uuid coerced at the read boundary)
  - created_at / updated_at / cached_at are ISO strings (never datetime objects)
  - rules is dict, cached_video_ids is list
  - is_preset is bool

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_collections_repository_orm.py -v
"""

from __future__ import annotations

import os

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
async def test_user(integration_db_url):
    """A real auth.users id with smart_collections cleaned up after the test."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
        if not uid:
            pytest.skip(
                "No auth.users rows — cannot satisfy smart_collections.user_id FK"
            )
        await conn.execute(
            "DELETE FROM smart_collections WHERE user_id = $1 AND is_preset = FALSE",
            uid,
        )
        await conn.execute(
            "DELETE FROM smart_collections WHERE user_id = $1 AND is_preset = TRUE",
            uid,
        )
    finally:
        await conn.close()
    yield uid
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute("DELETE FROM smart_collections WHERE user_id = $1", uid)
    finally:
        await conn.close()


def _repo():
    from app.repositories.collections_repository_orm import CollectionsRepositoryOrm

    return CollectionsRepositoryOrm()


# ─── Value-type parity helpers ──────────────────────────────────────────────


def _assert_parity(d: dict) -> None:
    """Assert strategy-C parity on a single collection dict."""
    assert type(d["id"]) is int, f"id should be int, got {type(d['id'])}"
    assert type(d["user_id"]) is str, f"user_id should be str, got {type(d['user_id'])}"
    assert isinstance(d["rules"], dict), f"rules should be dict, got {type(d['rules'])}"
    assert (
        type(d["created_at"]) is str
    ), f"created_at should be ISO str, got {type(d['created_at'])}"
    # updated_at may be None on a fresh insert before any update
    if d.get("updated_at") is not None:
        assert (
            type(d["updated_at"]) is str
        ), f"updated_at should be ISO str, got {type(d['updated_at'])}"
    if d.get("cached_at") is not None:
        assert (
            type(d["cached_at"]) is str
        ), f"cached_at should be ISO str, got {type(d['cached_at'])}"
    assert isinstance(
        d.get("is_preset"), bool
    ), f"is_preset should be bool, got {type(d.get('is_preset'))}"
    if d.get("cached_video_ids") is not None:
        assert isinstance(
            d["cached_video_ids"], list
        ), f"cached_video_ids should be list, got {type(d['cached_video_ids'])}"


# ─── Reads ──────────────────────────────────────────────────────────────────


async def test_get_all_collections_empty(integration_db_url, patched_engine, test_user):
    """No rows for this user → returns empty list."""
    repo = _repo()
    result = await repo.get_all_collections(str(test_user))
    assert isinstance(result, list)
    assert result == []


async def test_get_collection_by_id_missing(
    integration_db_url, patched_engine, test_user
):
    """Non-existent id → returns None."""
    repo = _repo()
    result = await repo.get_collection_by_id("9999999999999999", str(test_user))
    assert result is None


# ─── Create round-trip ──────────────────────────────────────────────────────


async def test_create_and_get_all(integration_db_url, patched_engine, test_user):
    """create_collection → get_all_collections round-trip with parity checks."""
    repo = _repo()
    rules = {
        "match": "all",
        "conditions": [{"field": "date", "operator": "gte", "value": "7_days_ago"}],
    }
    created = await repo.create_collection(
        user_id=str(test_user),
        name="My Test Collection",
        rules=rules,
        icon="🧪",
        description="Integration test collection",
        sort_by="created_at",
        sort_order="desc",
    )

    assert created["name"] == "My Test Collection"
    assert created["icon"] == "🧪"
    assert created["is_preset"] is False
    assert created["rules"] == rules
    _assert_parity(created)

    all_cols = await repo.get_all_collections(str(test_user))
    assert len(all_cols) == 1
    assert all_cols[0]["id"] == created["id"]
    _assert_parity(all_cols[0])


# ─── get_by_id ──────────────────────────────────────────────────────────────


async def test_get_collection_by_id(integration_db_url, patched_engine, test_user):
    """get_collection_by_id returns the row with parity-correct types."""
    repo = _repo()
    created = await repo.create_collection(
        user_id=str(test_user),
        name="By-ID Test",
        rules={"match": "all", "conditions": []},
    )
    fetched = await repo.get_collection_by_id(str(created["id"]), str(test_user))
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert fetched["name"] == "By-ID Test"
    _assert_parity(fetched)


async def test_get_collection_by_id_wrong_user(
    integration_db_url, patched_engine, test_user
):
    """A row belonging to another user returns None (ownership filter)."""
    repo = _repo()
    created = await repo.create_collection(
        user_id=str(test_user),
        name="Ownership Test",
        rules={"match": "all", "conditions": []},
    )
    import uuid

    other_user = str(uuid.uuid4())
    fetched = await repo.get_collection_by_id(str(created["id"]), other_user)
    assert fetched is None


# ─── Update ─────────────────────────────────────────────────────────────────


async def test_update_collection(integration_db_url, patched_engine, test_user):
    """update_collection changes name/description and commits (parity check)."""
    repo = _repo()
    created = await repo.create_collection(
        user_id=str(test_user),
        name="Before Update",
        rules={"match": "all", "conditions": []},
    )
    updated = await repo.update_collection(
        str(created["id"]),
        str(test_user),
        name="After Update",
        description="Updated description",
    )
    assert updated is not None
    assert updated["name"] == "After Update"
    assert updated["description"] == "Updated description"
    _assert_parity(updated)

    # Verify COMMIT via a fresh asyncpg read.
    conn = await asyncpg.connect(integration_db_url)
    try:
        name = await conn.fetchval(
            "SELECT name FROM smart_collections WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert name == "After Update"


async def test_update_collection_no_kwargs_returns_existing(
    integration_db_url, patched_engine, test_user
):
    """update_collection with no effective kwargs returns the existing row."""
    repo = _repo()
    created = await repo.create_collection(
        user_id=str(test_user),
        name="No-op Update",
        rules={"match": "all", "conditions": []},
    )
    result = await repo.update_collection(str(created["id"]), str(test_user))
    assert result is not None
    assert result["name"] == "No-op Update"


# ─── update_cache ───────────────────────────────────────────────────────────


async def test_update_cache(integration_db_url, patched_engine, test_user):
    """update_cache writes cached_video_ids/count/cached_at and commits."""
    repo = _repo()
    created = await repo.create_collection(
        user_id=str(test_user),
        name="Cache Test",
        rules={"match": "all", "conditions": []},
    )
    result = await repo.update_cache(
        str(created["id"]), media_ids=[101, 202, 303], count=3
    )
    assert result is not None
    assert result["cached_count"] == 3
    assert result["cached_video_ids"] == [101, 202, 303]
    # cached_at should now be an ISO string (not None)
    assert type(result["cached_at"]) is str
    _assert_parity(result)

    # Column name parity: must be cached_video_ids, NOT cached_media_ids.
    assert "cached_media_ids" not in result

    # Verify COMMIT via asyncpg.
    conn = await asyncpg.connect(integration_db_url)
    try:
        row = await conn.fetchrow(
            "SELECT cached_count, cached_video_ids FROM smart_collections WHERE id = $1",
            created["id"],
        )
    finally:
        await conn.close()
    assert row["cached_count"] == 3
    assert list(row["cached_video_ids"]) == [101, 202, 303]


# ─── Delete ─────────────────────────────────────────────────────────────────


async def test_delete_collection(integration_db_url, patched_engine, test_user):
    """delete_collection removes a non-preset row and returns True."""
    repo = _repo()
    created = await repo.create_collection(
        user_id=str(test_user),
        name="To Delete",
        rules={"match": "all", "conditions": []},
    )
    deleted = await repo.delete_collection(str(created["id"]), str(test_user))
    assert deleted is True

    # Gone from the DB.
    fetched = await repo.get_collection_by_id(str(created["id"]), str(test_user))
    assert fetched is None

    # Verify COMMIT via asyncpg.
    conn = await asyncpg.connect(integration_db_url)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM smart_collections WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert exists is None


async def test_delete_collection_wrong_user_returns_false(
    integration_db_url, patched_engine, test_user
):
    """delete_collection with wrong user_id does not delete (ownership filter)."""
    repo = _repo()
    created = await repo.create_collection(
        user_id=str(test_user),
        name="Ownership Delete Test",
        rules={"match": "all", "conditions": []},
    )
    import uuid

    other_user = str(uuid.uuid4())
    deleted = await repo.delete_collection(str(created["id"]), other_user)
    assert deleted is False

    # Row still exists.
    fetched = await repo.get_collection_by_id(str(created["id"]), str(test_user))
    assert fetched is not None


# ─── Presets ────────────────────────────────────────────────────────────────


async def test_create_default_presets(integration_db_url, patched_engine, test_user):
    """create_default_presets creates 4 preset rows with correct types."""
    repo = _repo()
    created = await repo.create_default_presets(str(test_user))

    assert len(created) == 4
    names = {c["name"] for c in created}
    assert names == {"Recent Downloads", "Favorites", "Most Viewed", "Untagged"}

    for col in created:
        assert col["is_preset"] is True
        _assert_parity(col)

    presets = await repo.get_preset_collections(str(test_user))
    assert len(presets) == 4
    for p in presets:
        _assert_parity(p)


# ─── Factory flag tests ─────────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    """FLAG=false → plain REST repo."""
    from app.core.config import settings
    from app.repositories import collections_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_COLLECTIONS", False)
    repo = mod.get_collections_repository()
    assert type(repo) is mod.CollectionsRepository
    from app.repositories.collections_repository_orm import CollectionsRepositoryOrm

    assert not isinstance(repo, CollectionsRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    """FLAG=true + configured engine → ORM repo."""
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import collections_repository as mod
    from app.repositories.collections_repository_orm import CollectionsRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_COLLECTIONS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_collections_repository()
    assert isinstance(repo, CollectionsRepositoryOrm)
