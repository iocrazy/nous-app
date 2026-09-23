"""Migration 494 and its two repositories against real Postgres.

The unit lane stubs the session, so it proves the statements compile, not that
Postgres accepts them. Only a server can answer:

  * the column really is ``halfvec(2048)`` and the HNSW index really uses
    ``halfvec_cosine_ops`` — the whole point of the table (``vector`` cannot
    be HNSW-indexed past 2000 dims);
  * ``match_resource_embeddings`` ranks within ONE space: the same vector in
    another space must not come back;
  * the browser roles cannot execute the RPC (``p_user_id`` is a plain
    argument and NULL means "every user");
  * the repositories' ORM statements (HALFVEC bind, ON CONFLICT target,
    insert-or-ignore on the space key, the anti-join) run as written.

Gated on INTEGRATION_DATABASE_URL — skips cleanly in the unit lane:

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/db/test_migration_494_resource_embeddings_integration.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("asyncpg")

import asyncpg  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — resource_embeddings need a DB.",
)

_RPC = "public.match_resource_embeddings(halfvec,bigint,text,double precision,integer,uuid)"
_DIM = 2048


def _unit(i: int) -> list[float]:
    v = [0.0] * _DIM
    v[i] = 1.0
    return v


def _lit(v: list[float]) -> str:
    return "[" + ",".join(map(str, v)) + "]"


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def orm_dsn():
    """Repoint the ORM engine at the test DSN, then restore + dispose so no
    other test inherits a stray engine."""
    from app.core.config import settings
    from app.db import engine as engine_mod
    from app.db import session as session_mod

    old = settings.SUPAVISOR_DATABASE_URL
    settings.SUPAVISOR_DATABASE_URL = _TEST_DSN
    await engine_mod.dispose_engine()
    session_mod.dispose_sessionmaker()
    try:
        yield _TEST_DSN
    finally:
        await engine_mod.dispose_engine()
        session_mod.dispose_sessionmaker()
        settings.SUPAVISOR_DATABASE_URL = old


async def _mk_resource(conn, user_id: uuid.UUID, title: str) -> int:
    media_id = await conn.fetchval(
        "INSERT INTO parsed_media (platform_id, original_url, title) "
        "VALUES ($1, $2, $3) RETURNING id",
        f"pid_{uuid.uuid4().hex[:12]}",
        f"https://example.test/{uuid.uuid4().hex[:8]}",
        title,
    )
    return await conn.fetchval(
        "INSERT INTO resources (creator_id, source_type, filename, media_id, "
        "is_trashed) VALUES ($1, 'web', $2, $3, false) RETURNING id",
        user_id,
        f"{title}.mp4",
        media_id,
    )


@pytest.fixture
async def fx(pg):
    """One user with two web resources and two spaces; torn down in order."""
    user = uuid.uuid4()
    tag = uuid.uuid4().hex[:8]
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user)
    r1 = await _mk_resource(pg, user, "First Clip")
    r2 = await _mk_resource(pg, user, "Second Clip")
    s1 = await pg.fetchval(
        "INSERT INTO embedding_spaces (actual_model, protocol) VALUES ($1, 'test') "
        "RETURNING id",
        f"test-model-a-{tag}",
    )
    s2 = await pg.fetchval(
        "INSERT INTO embedding_spaces (actual_model, protocol) VALUES ($1, 'test') "
        "RETURNING id",
        f"test-model-b-{tag}",
    )
    try:
        yield {"user": user, "r1": r1, "r2": r2, "s1": s1, "s2": s2, "tag": tag}
    finally:
        await pg.execute(
            "DELETE FROM embedding_spaces WHERE actual_model LIKE $1", f"%-{tag}"
        )
        media = await pg.fetch(
            "SELECT media_id FROM resources WHERE id = ANY($1::bigint[])", [r1, r2]
        )
        await pg.execute("DELETE FROM resources WHERE id = ANY($1::bigint[])", [r1, r2])
        await pg.execute(
            "DELETE FROM parsed_media WHERE id = ANY($1::bigint[])",
            [m["media_id"] for m in media],
        )
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user)


# ── schema ────────────────────────────────────────────────────────────────


@_skip
async def test_embedding_column_is_halfvec_2048(pg):
    typ = await pg.fetchval(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = 'public.resource_embeddings'::regclass "
        "AND attname = 'embedding'"
    )
    assert typ == "halfvec(2048)"
    assert await pg.fetchval("SELECT to_regclass('public.embedding_spaces')")


@_skip
async def test_hnsw_index_uses_halfvec_cosine_ops(pg):
    indexdef = await pg.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
        "AND indexname = 'resource_embeddings_embedding_hnsw'"
    )
    assert indexdef is not None
    assert "USING hnsw" in indexdef and "halfvec_cosine_ops" in indexdef


@_skip
@pytest.mark.parametrize("role", ["anon", "authenticated"])
async def test_browser_roles_cannot_execute_the_rpc(pg, role):
    granted = await pg.fetchval(
        "SELECT has_function_privilege($1, to_regprocedure($2), 'EXECUTE')",
        role,
        _RPC,
    )
    assert granted is False, f"{role} can EXECUTE {_RPC}"


# ── RPC semantics (inside a rolled-back transaction) ─────────────────────


@_skip
async def test_rpc_ranks_within_one_space_only(pg, fx):
    tr = pg.transaction()
    await tr.start()
    try:
        await pg.execute(
            "INSERT INTO resource_embeddings (resource_id, layer, space_id, "
            "embedding, source_hash) VALUES ($1, 'semantic', $2, "
            "CAST($3 AS halfvec), 'h1')",
            fx["r1"],
            fx["s1"],
            _lit(_unit(0)),
        )
        rows = await pg.fetch(
            "SELECT * FROM match_resource_embeddings(CAST($1 AS halfvec), $2, "
            "'semantic', 0.5, 10, $3)",
            _lit(_unit(0)),
            fx["s1"],
            fx["user"],
        )
        assert [r["resource_id"] for r in rows] == [fx["r1"]]
        assert rows[0]["similarity"] == pytest.approx(1.0, abs=1e-3)
        assert rows[0]["title"] == "First Clip"

        other_space = await pg.fetch(
            "SELECT * FROM match_resource_embeddings(CAST($1 AS halfvec), $2, "
            "'semantic', 0.5, 10, $3)",
            _lit(_unit(0)),
            fx["s2"],
            fx["user"],
        )
        assert other_space == [], "a vector leaked across embedding spaces"

        other_user = await pg.fetch(
            "SELECT * FROM match_resource_embeddings(CAST($1 AS halfvec), $2, "
            "'semantic', 0.5, 10, $3)",
            _lit(_unit(0)),
            fx["s1"],
            uuid.uuid4(),
        )
        assert other_user == [], "another user's vector came back"
    finally:
        await tr.rollback()


@_skip
async def test_layer_check_rejects_unknown_layer(pg, fx):
    tr = pg.transaction()
    await tr.start()
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await pg.execute(
                "INSERT INTO resource_embeddings (resource_id, layer, space_id, "
                "embedding, source_hash) VALUES ($1, 'bogus', $2, "
                "CAST($3 AS halfvec), 'h')",
                fx["r1"],
                fx["s1"],
                _lit(_unit(0)),
            )
    finally:
        await tr.rollback()


# ── repositories through the real ORM engine ─────────────────────────────


@_skip
async def test_space_get_or_create_is_idempotent(orm_dsn, pg, fx):
    from app.core.embedding_space import SpaceSpec
    from app.repositories.embedding_space_repository import EmbeddingSpaceRepository

    repo = EmbeddingSpaceRepository()
    spec = SpaceSpec(
        actual_model=f"test-model-c-{fx['tag']}",
        dims=2048,
        protocol="ark-multimodal",
        modalities=("text", "image"),
    )
    first = await repo.get_or_create(spec)
    second = await repo.get_or_create(spec)
    assert first["id"] == second["id"]
    assert first["modalities"] == ["text", "image"]
    assert await repo.get(first["id"]) == first
    count = await pg.fetchval(
        "SELECT count(*) FROM embedding_spaces WHERE actual_model = $1",
        spec.actual_model,
    )
    assert count == 1


@_skip
async def test_repository_roundtrip(orm_dsn, pg, fx):
    from app.db.scope import Scope, request_scope
    from app.repositories.resource_embeddings_repository import (
        BackfillRow,
        ResourceEmbeddingsRepository,
    )

    repo = ResourceEmbeddingsRepository()
    user = str(fx["user"])
    async with request_scope(Scope(user_id=user)):
        assert await repo.coverage(
            user_id=user, space_id=fx["s1"], layer="semantic"
        ) == (
            0,
            2,
        )

        await repo.upsert(
            resource_id=fx["r1"],
            layer="semantic",
            space_id=fx["s1"],
            embedding=_unit(0),
            source_hash="h1",
            source_text="first",
        )
        first = await repo.get(fx["r1"], "semantic", fx["s1"])
        # Replace in place: same PK, new vector + hash, one row.
        await repo.upsert(
            resource_id=fx["r1"],
            layer="semantic",
            space_id=fx["s1"],
            embedding=_unit(1),
            source_hash="h2",
            source_text="second",
        )
        got = await repo.get(fx["r1"], "semantic", fx["s1"])
        assert got["source_hash"] == "h2" and got["source_text"] == "second"
        assert got["embedding"] == _unit(1)
        assert got["created_at"] == first["created_at"]
        assert got["updated_at"] >= first["updated_at"]

        hits = await repo.search(
            embedding=_unit(1),
            space_id=fx["s1"],
            layer="semantic",
            user_id=user,
            limit=5,
            threshold=0.5,
        )
        assert [h["resource_id"] for h in hits] == [fx["r1"]]
        assert isinstance(hits[0]["created_at"], str)

        assert await repo.coverage(
            user_id=user, space_id=fx["s1"], layer="semantic"
        ) == (
            1,
            2,
        )
        assert await repo.coverage(
            user_id=user, space_id=fx["s2"], layer="semantic"
        ) == (
            0,
            2,
        )

        missing, total = await repo.missing_for_user(
            user_id=user, space_id=fx["s1"], layer="semantic", limit=10
        )
    assert total == 1
    assert [m.resource_id for m in missing] == [fx["r2"]]
    assert isinstance(missing[0], BackfillRow)
    assert missing[0].title == "Second Clip" and missing[0].has_analysis is False

    rows = await pg.fetchval(
        "SELECT count(*) FROM resource_embeddings WHERE resource_id = $1", fx["r1"]
    )
    assert rows == 1, "ON CONFLICT did not find the PK — appended instead"
