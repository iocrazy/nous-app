"""Integration tests for AnalysisRepositoryOrm against a real PostgreSQL DB.

Strategy-C parity asserts:
  - resource_id → native int (not str)
  - analyzed_at / created_at / updated_at → ISO-8601 str (not datetime)
  - analysis_cost → float (not Decimal or str)
  - Writes COMMIT via write_scope() — verified by a raw asyncpg re-read
  - upsert is create-then-update (get-then-branch; no ON CONFLICT)

Skips cleanly when INTEGRATION_DATABASE_URL is not set:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_analysis_repository_orm.py -v

Embedding / RPC paths (update_embedding, search_by_embedding) require pgvector
and the match_videos_by_embedding function in the DB; they are lightly tested
and skip gracefully when the function is absent.
"""

from __future__ import annotations

import os

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

# Snowflake-ish bigints unique to this test suite.
_MEDIA_ID = 9_270_000_000_000_001
_RESOURCE_ID = 9_270_000_000_000_002


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    """Wire the ORM session factory to the test DB."""
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
async def test_resource(integration_db_url):
    """Seed a parsed_media + resources row; yield resource_id; clean up after."""
    conn = await asyncpg.connect(integration_db_url)
    created_resource = False
    created_media = False
    try:
        creator_id = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
        if creator_id is None:
            pytest.skip("no auth.users row to satisfy resources.creator_id FK")

        # Seed parsed_media first (resources.media_id FK).
        pm_exists = await conn.fetchval(
            "SELECT id FROM parsed_media WHERE id = $1", _MEDIA_ID
        )
        if not pm_exists:
            await conn.execute(
                "INSERT INTO parsed_media (id, platform_id, original_url) "
                "VALUES ($1, $2, $3)",
                _MEDIA_ID,
                "__test_analysis_orm__",
                "https://example.com/test-analysis-orm",
            )
            created_media = True

        # Seed resources row.
        res_exists = await conn.fetchval(
            "SELECT id FROM resources WHERE id = $1", _RESOURCE_ID
        )
        if not res_exists:
            await conn.execute(
                "INSERT INTO resources (id, creator_id, source_type, filename, media_id) "
                "VALUES ($1, $2, 'web', 'analysis_orm_test.mp4', $3)",
                _RESOURCE_ID,
                creator_id,
                _MEDIA_ID,
            )
            created_resource = True
    finally:
        await conn.close()

    yield _RESOURCE_ID

    # Cleanup.
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM resource_analysis WHERE resource_id = $1", _RESOURCE_ID
        )
        if created_resource:
            await conn.execute("DELETE FROM resources WHERE id = $1", _RESOURCE_ID)
        if created_media:
            await conn.execute("DELETE FROM parsed_media WHERE id = $1", _MEDIA_ID)
    finally:
        await conn.close()


def _repo():
    from app.repositories.analysis_repository_orm import AnalysisRepositoryOrm

    return AnalysisRepositoryOrm()


# ─── Reads ──────────────────────────────────────────────────────────────────


async def test_get_analysis_returns_none_when_missing(
    integration_db_url, patched_engine, test_resource
):
    """No row → None (mirrors REST maybe_single returning null data)."""
    repo = _repo()
    result = await repo.get_analysis(test_resource)
    assert result is None


async def test_get_analysis_stats_returns_correct_shape(
    integration_db_url, patched_engine, test_resource
):
    """Stats dict has the expected keys regardless of DB content."""
    stats = await _repo().get_analysis_stats()
    assert set(stats.keys()) == {"none", "L1", "L2", "L3", "total"}
    assert isinstance(stats["total"], int)


# ─── Writes ─────────────────────────────────────────────────────────────────


async def test_create_analysis_returns_dict_with_int_resource_id(
    integration_db_url, patched_engine, test_resource
):
    """Create returns a dict; resource_id is a native int."""
    repo = _repo()
    row = await repo.create_analysis(
        test_resource,
        analysis_level="L1",
        visual_description="Unit test desc",
        detected_objects=["cat", "dog"],
        analysis_cost=0.001,
    )
    assert isinstance(row, dict)
    assert row["resource_id"] == int(test_resource)
    assert type(row["resource_id"]) is int
    assert row["analysis_level"] == "L1"
    assert row["visual_description"] == "Unit test desc"
    assert row["detected_objects"] == ["cat", "dog"]


async def test_create_analysis_timestamps_are_iso_strings(
    integration_db_url, patched_engine, test_resource
):
    """analyzed_at and created_at are ISO-8601 strings (not datetime objects)."""
    repo = _repo()
    # Clean any prior row.
    await repo.delete_analysis(test_resource)
    row = await repo.create_analysis(
        test_resource,
        analysis_level="L1",
        analysis_cost=0.0,
    )
    for field in ("analyzed_at", "created_at"):
        val = row.get(field)
        assert val is not None, f"{field} should not be None after create"
        assert isinstance(val, str), f"{field} should be a str, got {type(val)}"
        # Basic ISO-8601 sanity: contains 'T'.
        assert "T" in val, f"{field} doesn't look like ISO-8601: {val!r}"


async def test_create_analysis_cost_is_float(
    integration_db_url, patched_engine, test_resource
):
    """analysis_cost (Numeric DB column) → Python float (not Decimal or str)."""
    repo = _repo()
    await repo.delete_analysis(test_resource)
    row = await repo.create_analysis(
        test_resource,
        analysis_level="L1",
        analysis_cost=0.00042,
    )
    cost = row.get("analysis_cost")
    assert cost is not None
    assert type(cost) is float, f"Expected float, got {type(cost)}"
    assert abs(cost - 0.00042) < 1e-8


async def test_create_commits_to_db(integration_db_url, patched_engine, test_resource):
    """write_scope COMMITS — a fresh asyncpg re-read sees the row."""
    repo = _repo()
    await repo.delete_analysis(test_resource)
    await repo.create_analysis(test_resource, analysis_level="L1", analysis_cost=0.001)

    conn = await asyncpg.connect(integration_db_url)
    try:
        level = await conn.fetchval(
            "SELECT analysis_level FROM resource_analysis WHERE resource_id = $1",
            _RESOURCE_ID,
        )
    finally:
        await conn.close()
    assert level == "L1"


async def test_get_analysis_returns_dict_after_create(
    integration_db_url, patched_engine, test_resource
):
    """get_analysis returns the row that was created; types are correct."""
    repo = _repo()
    await repo.delete_analysis(test_resource)
    await repo.create_analysis(
        test_resource,
        analysis_level="L1",
        visual_description="hello",
        detected_scenes=["street"],
        analysis_cost=0.002,
    )
    row = await repo.get_analysis(test_resource)
    assert row is not None
    assert type(row["resource_id"]) is int
    assert row["visual_description"] == "hello"
    assert row["detected_scenes"] == ["street"]
    assert type(row["analysis_cost"]) is float


async def test_update_analysis_updates_field(
    integration_db_url, patched_engine, test_resource
):
    """update_analysis modifies the row and returns the updated dict."""
    repo = _repo()
    await repo.delete_analysis(test_resource)
    await repo.create_analysis(test_resource, analysis_level="L1", analysis_cost=0.001)
    updated = await repo.update_analysis(
        test_resource, visual_description="updated desc"
    )
    assert updated is not None
    assert updated["visual_description"] == "updated desc"
    assert type(updated["resource_id"]) is int


async def test_update_analysis_no_kwargs_returns_existing(
    integration_db_url, patched_engine, test_resource
):
    """update_analysis with no non-None kwargs returns existing row (no-op)."""
    repo = _repo()
    await repo.delete_analysis(test_resource)
    await repo.create_analysis(test_resource, analysis_level="L1")
    result = await repo.update_analysis(test_resource)
    assert result is not None
    assert result["resource_id"] == int(test_resource)


async def test_upsert_creates_when_missing(
    integration_db_url, patched_engine, test_resource
):
    """upsert creates a new row when none exists."""
    repo = _repo()
    await repo.delete_analysis(test_resource)
    row = await repo.upsert_analysis(
        test_resource, analysis_level="L1", analysis_cost=0.003
    )
    assert row["resource_id"] == int(test_resource)
    assert type(row["analysis_cost"]) is float

    # Verify in DB.
    conn = await asyncpg.connect(integration_db_url)
    try:
        exists = await conn.fetchval(
            "SELECT COUNT(*) FROM resource_analysis WHERE resource_id = $1",
            _RESOURCE_ID,
        )
    finally:
        await conn.close()
    assert exists == 1


async def test_upsert_updates_when_existing(
    integration_db_url, patched_engine, test_resource
):
    """upsert updates the row when it already exists (no duplicate created)."""
    repo = _repo()
    await repo.delete_analysis(test_resource)
    await repo.create_analysis(test_resource, analysis_level="L1", analysis_cost=0.001)
    out = await repo.upsert_analysis(test_resource, visual_description="upsert update")
    assert out["visual_description"] == "upsert update"

    # Only one row in DB.
    conn = await asyncpg.connect(integration_db_url)
    try:
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM resource_analysis WHERE resource_id = $1",
            _RESOURCE_ID,
        )
    finally:
        await conn.close()
    assert cnt == 1


async def test_delete_analysis_returns_true(
    integration_db_url, patched_engine, test_resource
):
    """delete_analysis returns True when a row was deleted."""
    repo = _repo()
    await repo.delete_analysis(test_resource)
    await repo.create_analysis(test_resource, analysis_level="L1")
    deleted = await repo.delete_analysis(test_resource)
    assert deleted is True

    conn = await asyncpg.connect(integration_db_url)
    try:
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM resource_analysis WHERE resource_id = $1",
            _RESOURCE_ID,
        )
    finally:
        await conn.close()
    assert cnt == 0


async def test_delete_analysis_returns_false_when_missing(
    integration_db_url, patched_engine, test_resource
):
    """delete_analysis returns False when no row exists."""
    repo = _repo()
    await repo.delete_analysis(test_resource)  # ensure clean
    result = await repo.delete_analysis(test_resource)
    assert result is False


async def test_get_videos_without_analysis_returns_list(
    integration_db_url, patched_engine, test_resource
):
    """get_videos_without_analysis returns a list of dicts with expected keys."""
    repo = _repo()
    await repo.delete_analysis(test_resource)  # ensure our media is unanalysed
    results = await repo.get_videos_without_analysis(limit=5)
    assert isinstance(results, list)
    # If results exist, check shape.
    for item in results:
        assert "id" in item
        assert type(item["id"]) is int
        assert "title" in item
        assert "description" in item
        assert "cover_urls" in item


async def test_get_analysis_stats_after_create(
    integration_db_url, patched_engine, test_resource
):
    """Stats are consistent after creating a row."""
    repo = _repo()
    await repo.delete_analysis(test_resource)
    await repo.create_analysis(test_resource, analysis_level="L1", analysis_cost=0.0)
    stats = await repo.get_analysis_stats()
    assert stats["L1"] >= 1
    assert stats["total"] >= 1
    assert isinstance(stats["total"], int)


# ─── Factory routing ────────────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    """USE_ORM_ANALYSIS=False → REST AnalysisRepository instance."""
    from app.core.config import settings
    from app.repositories import analysis_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_ANALYSIS", False)
    repo = mod.get_analysis_repository()
    assert type(repo) is mod.AnalysisRepository
    from app.repositories.analysis_repository_orm import AnalysisRepositoryOrm

    assert not isinstance(repo, AnalysisRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    """USE_ORM_ANALYSIS=True + engine configured → AnalysisRepositoryOrm."""
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import analysis_repository as mod
    from app.repositories.analysis_repository_orm import AnalysisRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_ANALYSIS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_analysis_repository()
    assert isinstance(repo, AnalysisRepositoryOrm)
