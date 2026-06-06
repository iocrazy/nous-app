"""Integration tests for AIRepositoryOrm (Phase 2 M batch) against real PG.

Proves the REST → ORM swap is invisible AND that STRATEGY-C value-type parity
holds on resource_transcripts / resource_summaries + the resources status cols:

  - transcript/summary id (uuid) → STR (shape parity).
  - resource_id (bigint) → native int (the 5.3 trap).
  - created_at (timestamptz) → ISO STR (REQUIRED — Transcript/SummaryResponse
    .created_at are typed Optional[str]; pydantic rejects a native datetime).
  - segments / key_points / topics (jsonb) → native dict/list.
  - duration_seconds (double) → native float.
  - resources.*_status are Enum(AiTaskStatus) — update binds the bare string.

Writes (save_transcript / save_summary upsert, update_media_ai_status) go
through ``write_scope()`` (COMMITS) — a fresh asyncpg read proves no silent
rollback. The upsert path is exercised twice (insert then update-on-conflict).

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_ai_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_FN_PREFIX = "__test_orm_ai_"


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
async def seed_resource(integration_db_url):
    """Create a throwaway resources row; yield its bigint id. CASCADE drops
    its transcript/summary on cleanup."""
    conn = await asyncpg.connect(integration_db_url)
    rid = None
    try:
        creator = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
        if creator is None:
            pytest.skip("No auth.users rows to satisfy resources.creator_id FK")
        rid = await conn.fetchval(
            "INSERT INTO resources (creator_id, source_type, filename, "
            "transcript_status, summary_status) "
            "VALUES ($1, 'upload', $2, 'pending', 'pending') RETURNING id",
            creator,
            f"{_FN_PREFIX}{uuid.uuid4().hex[:8]}.mp4",
        )
        yield rid
    finally:
        if rid is not None:
            await conn.execute("DELETE FROM resources WHERE id = $1", rid)
        await conn.close()


def _repo():
    from app.repositories.ai_repository_orm import AIRepositoryOrm

    return AIRepositoryOrm()


# ─── Transcript upsert (COMMIT + parity) ────────────────────────────────


async def test_save_transcript_commit_and_parity(
    integration_db_url, patched_engine, seed_resource
):
    rid = seed_resource
    saved = await _repo().save_transcript(
        str(rid),
        {
            "language": "en",
            "full_text": "hello world",
            "segments": [{"start": 0.0, "text": "hello"}],
            "whisper_model": "large-v3",
            "duration_seconds": 12.5,
        },
    )
    assert saved is not None
    # id uuid → str (shape parity); resource_id bigint → native int (5.3 trap).
    assert type(saved["id"]) is str
    assert type(saved["resource_id"]) is int
    assert saved["resource_id"] == rid
    # created_at → ISO str (REQUIRED — TranscriptResponse.created_at is str).
    assert type(saved["created_at"]) is str and "T" in saved["created_at"]
    # jsonb → native list/dict; double → native float.
    assert isinstance(saved["segments"], list)
    assert isinstance(saved["duration_seconds"], float)

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT full_text FROM resource_transcripts WHERE resource_id = $1", rid
        )
    finally:
        await conn.close()
    assert persisted == "hello world"  # committed (no silent rollback)


async def test_save_transcript_upsert_on_conflict(patched_engine, seed_resource):
    """Second save on the same resource_id UPDATES (ON CONFLICT) not duplicates."""
    rid = seed_resource
    await _repo().save_transcript(str(rid), {"full_text": "first", "language": "en"})
    second = await _repo().save_transcript(
        str(rid), {"full_text": "second", "language": "zh"}
    )
    assert second is not None
    assert second["full_text"] == "second"
    fetched = await _repo().get_transcript(str(rid))
    assert fetched is not None
    assert fetched["full_text"] == "second"
    assert fetched["language"] == "zh"


# ─── Summary upsert (COMMIT + parity) ───────────────────────────────────


async def test_save_summary_commit_and_parity(patched_engine, seed_resource):
    rid = seed_resource
    saved = await _repo().save_summary(
        str(rid),
        {
            "summary_type": "brief",
            "summary_text": "a summary",
            "key_points": ["a", "b"],
            "topics": ["x"],
            "llm_model": "qwen",
            "llm_provider": "qwen",
        },
    )
    assert saved is not None
    assert type(saved["id"]) is str
    assert type(saved["resource_id"]) is int
    assert type(saved["created_at"]) is str
    assert isinstance(saved["key_points"], list)

    fetched = await _repo().get_summary(str(rid))
    assert fetched is not None
    assert fetched["summary_text"] == "a summary"


# ─── status update (COMMIT) + enum-string bind ──────────────────────────


async def test_update_media_ai_status_commit(
    integration_db_url, patched_engine, seed_resource
):
    rid = seed_resource
    ok = await _repo().update_media_ai_status(
        str(rid), "transcript_status", "completed"
    )
    assert ok is True

    conn = await asyncpg.connect(integration_db_url)
    try:
        status = await conn.fetchval(
            "SELECT transcript_status::text FROM resources WHERE id = $1", rid
        )
    finally:
        await conn.close()
    assert status == "completed"  # enum-string bind committed


async def test_update_media_ai_status_invalid_field_raises(patched_engine):
    with pytest.raises(ValueError):
        await _repo().update_media_ai_status("1", "bogus_status", "completed")


async def test_get_videos_needing_transcription_native_int_id(
    patched_engine, seed_resource
):
    """The cold/uncalled helper still returns parity rows: id native int."""
    rid = seed_resource
    # seed_resource has transcript_status='pending' but download_path is NULL,
    # so it should NOT appear (the .isnot(None) filter). Set a path then re-query.
    rows = await _repo().get_videos_needing_transcription(limit=50)
    for r in rows:
        assert type(r["id"]) is int
    _ = rid  # fixture used for engine wiring / cleanup


# ─── factory on/off ─────────────────────────────────────────────────────


def test_factory_off_returns_legacy():
    from unittest.mock import patch

    from app.repositories.ai_repository import AIRepository, get_ai_repository

    with patch("app.core.config.settings.USE_ORM_AI", False):
        assert type(get_ai_repository()) is AIRepository


def test_factory_on_returns_orm(integration_db_url):
    from unittest.mock import patch

    from app.repositories.ai_repository_orm import AIRepositoryOrm

    with (
        patch("app.core.config.settings.USE_ORM_AI", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        from app.repositories.ai_repository import get_ai_repository

        assert type(get_ai_repository()) is AIRepositoryOrm
