"""Integration test for the repaired ``match_videos_by_embedding`` RPC (mig 259).

The RPC was dead end-to-end after schema drift:
  * mig 076 renamed ``media_analysis`` -> ``resource_analysis`` and its
    ``media_id`` column -> ``resource_id`` (FK -> resources.id).
  * mig 086 dropped ``parsed_media.cover_url`` (cover URLs now live in the
    ``parsed_media.cover_urls`` jsonb array, mig 004).
mig 066 still read ``media_analysis``/``ma.media_id``/``pm.cover_url`` so every
call 500'd (analysis_repository.search_by_embedding -> search_service).

This test drives the *real* RPC body shipped in mig 259 against a real PG,
seeding parsed_media -> resources -> resource_analysis, and asserts the
returned dict keys/shape match exactly what the consumer reads
(search_service.semantic_search, ~line 86-94):
    r["media_id"]  (BRACKET access -> column must be ``media_id``)
    r.get("cover_urls")  (PLURAL jsonb array -> column must be ``cover_urls``)
    + platform_id / title / description / author / view_count / created_at /
      similarity.

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_match_videos_by_embedding_rpc.py -v
"""

from __future__ import annotations

import os

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

# Snowflake-ish bigints well inside int8 range; unique to this test.
_MEDIA_ID = 9_259_000_000_000_001
_RESOURCE_ID = 9_259_000_000_000_002
_PLATFORM_ID = "__test_mvbe_259__"

# Function body lifted verbatim from supabase/migrations/259_*.sql so the test
# pins the exact projection that ships (DROP-first because the return shape
# changed: video_id -> media_id, cover_url -> cover_urls).
_DROP_FN = (
    "DROP FUNCTION IF EXISTS "
    "match_videos_by_embedding(vector, double precision, integer);"
)
_CREATE_FN = """
CREATE OR REPLACE FUNCTION match_videos_by_embedding(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.7,
    match_count int DEFAULT 10
)
RETURNS TABLE (
    media_id bigint,
    platform_id text,
    title text,
    description text,
    cover_urls jsonb,
    author text,
    view_count bigint,
    created_at timestamptz,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        pm.id,
        pm.platform_id::text,
        pm.title,
        pm.description,
        pm.cover_urls,
        pm.author::text,
        COALESCE(pm.view_count, 0)::bigint,
        pm.created_at,
        (1 - (ra.content_embedding <=> query_embedding))::float
    FROM resource_analysis ra
    JOIN resources r     ON r.id = ra.resource_id
    JOIN parsed_media pm ON pm.id = r.media_id
    WHERE ra.content_embedding IS NOT NULL
      AND r.media_id IS NOT NULL
      AND (1 - (ra.content_embedding <=> query_embedding)) > match_threshold
    ORDER BY ra.content_embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
"""

_EXPECTED_KEYS = {
    "media_id",
    "platform_id",
    "title",
    "description",
    "cover_urls",
    "author",
    "view_count",
    "created_at",
    "similarity",
}


def _vec_literal(values: list[float]) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


# Minimal resource_analysis shape matching the mig 014 + mig 076 post-rename
# schema (PK = (resource_id, analysis_level), FK -> resources.id, the
# content_embedding vector(1536) the RPC reads). Created on-the-fly only when
# the table is absent (fresh ORM2 test DB), and dropped again afterwards so the
# RPC is genuinely exercised everywhere.
_CREATE_RESOURCE_ANALYSIS = """
CREATE TABLE resource_analysis (
    resource_id BIGINT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    analysis_level VARCHAR(10) DEFAULT 'none',
    content_embedding vector(1536),
    PRIMARY KEY (resource_id, analysis_level)
);
"""


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
        await connection.execute(
            "DELETE FROM resource_analysis WHERE resource_id = $1", _RESOURCE_ID
        )
        await connection.execute("DELETE FROM resources WHERE id = $1", _RESOURCE_ID)
        await connection.execute("DELETE FROM parsed_media WHERE id = $1", _MEDIA_ID)
        if created_table:
            await connection.execute("DROP TABLE IF EXISTS resource_analysis")
        await connection.close()


async def test_match_videos_by_embedding_returns_consumer_shape(conn):
    # Apply the mig-259 function body (idempotent DROP + CREATE OR REPLACE).
    await conn.execute(_DROP_FN)
    await conn.execute(_CREATE_FN)

    # resources.creator_id FKs to auth.users — borrow an existing user rather
    # than fabricate one (auth.users isn't ours to seed).
    creator_id = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if creator_id is None:
        pytest.skip("no auth.users row to satisfy resources.creator_id FK")

    # Seed parsed_media -> resources -> resource_analysis.
    await conn.execute(
        """
        INSERT INTO parsed_media
            (id, platform_id, original_url, title, description, author,
             view_count, cover_urls)
        VALUES ($1, $2, $3, 'MVBE Test Title', 'MVBE test description',
                'MVBE Author', 4242, '["https://example.com/cover.jpg"]'::jsonb)
        """,
        _MEDIA_ID,
        _PLATFORM_ID,
        "https://example.com/mvbe",
    )
    await conn.execute(
        """
        INSERT INTO resources
            (id, creator_id, source_type, filename, media_id)
        VALUES ($1, $2, 'web', 'mvbe.mp4', $3)
        """,
        _RESOURCE_ID,
        creator_id,
        _MEDIA_ID,
    )

    dim = 1536
    embedding = [0.0] * dim
    embedding[0] = 1.0  # unit-ish vector; cosine distance to itself == 0
    await conn.execute(
        """
        INSERT INTO resource_analysis (resource_id, analysis_level, content_embedding)
        VALUES ($1, 'full', $2::vector)
        """,
        _RESOURCE_ID,
        _vec_literal(embedding),
    )

    # Query with the SAME vector -> similarity ~1.0, comfortably > threshold.
    rows = await conn.fetch(
        "SELECT * FROM match_videos_by_embedding($1::vector, $2, $3)",
        _vec_literal(embedding),
        0.7,
        10,
    )

    assert rows, "RPC returned no rows for an exact-match embedding"
    row = dict(rows[0])

    # Return-table column names == PostgREST/result dict keys == consumer reads.
    assert set(row.keys()) == _EXPECTED_KEYS

    # media_id is read via BRACKET access in search_service -> must be present.
    assert row["media_id"] == _MEDIA_ID
    assert row["platform_id"] == _PLATFORM_ID
    assert row["title"] == "MVBE Test Title"
    assert row["description"] == "MVBE test description"
    assert row["author"] == "MVBE Author"
    assert row["view_count"] == 4242

    # cover_urls is a jsonb array consumed as (r.get("cover_urls") or [None])[0].
    cover_urls = row["cover_urls"]
    if isinstance(cover_urls, str):
        import json

        cover_urls = json.loads(cover_urls)
    assert isinstance(cover_urls, list)
    assert cover_urls[0] == "https://example.com/cover.jpg"

    # Exact self-match -> cosine similarity ~ 1.0.
    assert row["similarity"] == pytest.approx(1.0, abs=1e-4)
