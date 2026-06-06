"""Integration test for the 4 repaired cleanup RPCs (mig 258).

All four functions (``find_duplicate_videos`` / ``get_cleanup_suggestions`` /
``get_cleanup_stats`` / ``get_cleanup_data``) were dead end-to-end after the
ownership-model + analysis-rename migrations:
  * mig 083 dropped ``parsed_media.user_id`` (per-user ownership moved to
    ``resources.creator_id``).
  * mig 086 dropped ``parsed_media.cover_url`` — cover URLs now live in the
    ``parsed_media.cover_urls`` **jsonb** array (mig 004 ADD COLUMN cover_urls
    JSONB).
  * mig 076 renamed ``media_analysis`` -> ``resource_analysis`` and its
    ``media_id`` column -> ``resource_id`` (FK -> resources.id).

mig 258 redefines the functions resource-centric. The one runtime-fatal type
bug it shipped with: the RETURNS TABLE declared ``cover_urls text[]`` while
``parsed_media.cover_urls`` is jsonb — plpgsql ``RETURN QUERY`` strictly
type-checks the projection against the declared column types, so the mismatch
raises 42804 ("Returned type jsonb does not match expected type text[]") at
*call* time (not define time). The fix is ``cover_urls jsonb``.

This test drives the *real* mig-258 function bodies against a real PG, seeding
parsed_media -> resources -> resource_analysis, and asserts each function
RETURNS without a type error and exposes the column names the consumer
(app/services/infra/cleanup_service.py) reads:
    media_id (BRACKET access) / title / cover_urls (jsonb array indexed [0]) /
    author / storage_size / created_at / last_viewed_at / view_count / reason /
    similar_to / similarity_score + the stats keys.

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_cleanup_functions_rpc.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

# Bigints well inside int8 range; unique to this test.
_MEDIA_ID_A = 9_258_000_000_000_001  # never-viewed, large
_MEDIA_ID_B = 9_258_000_000_000_002  # old-unused (near-duplicate of A)
_RESOURCE_ID_A = 9_258_000_000_000_011
_RESOURCE_ID_B = 9_258_000_000_000_012

# The mig file whose function bodies we apply verbatim (no copy/paste drift).
_MIG_258 = (
    Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "258_fix_cleanup_functions.sql"
)

# Minimal resource_analysis shape (mig 014 + mig 076 post-rename): the
# content_embedding vector(1536) the duplicate RPC reads, keyed on resource_id
# (FK -> resources.id). Created on-the-fly only when absent (fresh ORM2 test DB)
# and dropped again afterwards.
_CREATE_RESOURCE_ANALYSIS = """
CREATE TABLE resource_analysis (
    resource_id BIGINT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    analysis_level VARCHAR(10) DEFAULT 'none',
    content_embedding vector(1536),
    PRIMARY KEY (resource_id, analysis_level)
);
"""


def _vec_literal(values: list[float]) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def _normalize_cover_urls(value) -> list:
    """asyncpg returns jsonb as str; PostgREST as a list. Accept both."""
    if isinstance(value, str):
        import json

        return json.loads(value)
    return value


@pytest.fixture
async def conn():
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration test")
    connection = await asyncpg.connect(_TEST_DSN)
    created_table = False
    has_table = await connection.fetchval(
        "SELECT to_regclass('public.resource_analysis') IS NOT NULL"
    )
    if not has_table:
        await connection.execute(_CREATE_RESOURCE_ANALYSIS)
        created_table = True
    try:
        yield connection
    finally:
        for rid in (_RESOURCE_ID_A, _RESOURCE_ID_B):
            await connection.execute(
                "DELETE FROM resource_analysis WHERE resource_id = $1", rid
            )
            await connection.execute("DELETE FROM resources WHERE id = $1", rid)
        for mid in (_MEDIA_ID_A, _MEDIA_ID_B):
            await connection.execute("DELETE FROM parsed_media WHERE id = $1", mid)
        if created_table:
            await connection.execute("DROP TABLE IF EXISTS resource_analysis")
        await connection.close()


async def _apply_mig_258(connection: asyncpg.Connection) -> None:
    """Apply the real mig-258 bodies (DROP-first + CREATE) verbatim."""
    sql = _MIG_258.read_text(encoding="utf-8")
    # NOTIFY pgrst would no-op outside PostgREST; harmless, but keep the apply
    # focused on the function DDL.
    sql = sql.replace("NOTIFY pgrst, 'reload schema';", "")
    await connection.execute(sql)


async def _seed(connection: asyncpg.Connection):
    creator_id = await connection.fetchval("SELECT id FROM auth.users LIMIT 1")
    if creator_id is None:
        pytest.skip("no auth.users row to satisfy resources.creator_id FK")

    # A: never viewed, 60 days old, large file.
    await connection.execute(
        """
        INSERT INTO parsed_media
            (id, platform_id, original_url, title, author, cover_urls,
             storage_size, view_count, created_at, last_viewed_at, keep_forever)
        VALUES ($1, $2, $3, 'Cleanup Title A', 'Author A',
                '["https://example.com/a1.jpg","https://example.com/a2.jpg"]'::jsonb,
                500000000, 0, now() - interval '60 days', NULL, false)
        """,
        _MEDIA_ID_A,
        "__test_cleanup_258_a__",
        "https://example.com/a",
    )
    # B: viewed once, last viewed 45 days ago (old_unused), small file.
    await connection.execute(
        """
        INSERT INTO parsed_media
            (id, platform_id, original_url, title, author, cover_urls,
             storage_size, view_count, created_at, last_viewed_at, keep_forever)
        VALUES ($1, $2, $3, 'Cleanup Title B', 'Author B',
                '["https://example.com/b1.jpg"]'::jsonb,
                10000000, 5, now() - interval '90 days',
                now() - interval '45 days', false)
        """,
        _MEDIA_ID_B,
        "__test_cleanup_258_b__",
        "https://example.com/b",
    )
    await connection.execute(
        """
        INSERT INTO resources (id, creator_id, source_type, filename, media_id, is_trashed)
        VALUES ($1, $2, 'web', 'a.mp4', $3, false),
               ($4, $2, 'web', 'b.mp4', $5, false)
        """,
        _RESOURCE_ID_A,
        creator_id,
        _MEDIA_ID_A,
        _RESOURCE_ID_B,
        _MEDIA_ID_B,
    )

    # Two near-identical embeddings -> find_duplicate_videos returns a pair.
    dim = 1536
    emb_a = [0.1] * dim
    emb_b = [0.1001] * dim
    await connection.execute(
        """
        INSERT INTO resource_analysis (resource_id, analysis_level, content_embedding)
        VALUES ($1, 'full', $2::vector), ($3, 'full', $4::vector)
        """,
        _RESOURCE_ID_A,
        _vec_literal(emb_a),
        _RESOURCE_ID_B,
        _vec_literal(emb_b),
    )
    return creator_id


async def test_find_duplicate_videos_returns_consumer_shape(conn):
    await _apply_mig_258(conn)
    creator_id = await _seed(conn)

    rows = await conn.fetch(
        "SELECT * FROM find_duplicate_videos($1::uuid, $2, $3)",
        creator_id,
        0.5,
        20,
    )
    assert rows, "find_duplicate_videos returned no rows for near-identical embeddings"
    row = dict(rows[0])
    # Consumer reads (cleanup_service._get_duplicates_via_rpc loop).
    assert {
        "media_id",
        "title",
        "cover_urls",
        "author",
        "storage_size",
        "created_at",
        "last_viewed_at",
        "view_count",
        "similar_to",
        "similarity_score",
    } <= set(row.keys())
    assert row["media_id"] in (_MEDIA_ID_A, _MEDIA_ID_B)
    assert row["similar_to"] in (_MEDIA_ID_A, _MEDIA_ID_B)
    cover = _normalize_cover_urls(row["cover_urls"])
    assert isinstance(cover, list) and cover  # jsonb array, indexable [0]
    assert row["similarity_score"] >= 0.5


async def test_get_cleanup_suggestions_returns_consumer_shape(conn):
    await _apply_mig_258(conn)
    creator_id = await _seed(conn)

    rows = await conn.fetch(
        "SELECT * FROM get_cleanup_suggestions($1::uuid, $2, $3, $4)",
        creator_id,
        7,
        30,
        50,
    )
    assert rows, "get_cleanup_suggestions returned no rows"
    by_media = {dict(r)["media_id"]: dict(r) for r in rows}
    assert _MEDIA_ID_A in by_media and _MEDIA_ID_B in by_media
    row = by_media[_MEDIA_ID_A]
    assert {
        "media_id",
        "title",
        "cover_urls",
        "author",
        "storage_size",
        "created_at",
        "last_viewed_at",
        "view_count",
        "reason",
        "reason_detail",
    } <= set(row.keys())
    assert by_media[_MEDIA_ID_A]["reason"] == "never_viewed"
    assert by_media[_MEDIA_ID_B]["reason"] == "old_unused"
    cover = _normalize_cover_urls(row["cover_urls"])
    assert isinstance(cover, list) and cover[0] == "https://example.com/a1.jpg"


async def test_get_cleanup_stats_returns_consumer_shape(conn):
    await _apply_mig_258(conn)
    creator_id = await _seed(conn)

    rows = await conn.fetch(
        "SELECT * FROM get_cleanup_stats($1::uuid)",
        creator_id,
    )
    assert rows, "get_cleanup_stats returned no rows"
    row = dict(rows[0])
    assert {
        "total_videos",
        "total_storage_bytes",
        "videos_never_viewed",
        "videos_not_viewed_30_days",
        "videos_marked_keep",
        "reclaimable_bytes",
    } <= set(row.keys())
    assert row["total_videos"] >= 2
    assert row["total_storage_bytes"] >= 510000000
    assert row["videos_never_viewed"] >= 1


async def test_get_cleanup_data_returns_consumer_shape(conn):
    await _apply_mig_258(conn)
    creator_id = await _seed(conn)

    payload = await conn.fetchval(
        "SELECT get_cleanup_data($1::uuid, $2, $3, $4)",
        creator_id,
        7,
        30,
        50,
    )
    assert payload is not None
    if isinstance(payload, str):
        import json

        payload = json.loads(payload)

    assert set(payload.keys()) >= {"suggestions", "stats", "categories"}
    suggestions = payload["suggestions"]
    assert isinstance(suggestions, list) and suggestions
    # cleanup_service.get_cleanup_data reads media_id / title / cover_urls(...)
    s0 = suggestions[0]
    assert {"media_id", "title", "cover_urls", "author", "reason"} <= set(s0.keys())
    cover = _normalize_cover_urls(s0["cover_urls"])
    assert isinstance(cover, list) and cover
    assert payload["stats"]["total_videos"] >= 2
