"""Migration 499 and its two repositories against real Postgres.

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
    uv run pytest tests/db/test_migration_499_resource_embeddings_integration.py -v
"""

from __future__ import annotations

import os
import random
import uuid
from datetime import timedelta

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
_HOUR = timedelta(hours=1)


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


def _near(seed: int, noise: float) -> list[float]:
    """Axis 0 plus seeded random noise of amplitude ``noise``. Real-looking
    spread matters: near-duplicates (axis 0 plus one tiny orthogonal nudge)
    make HNSW's neighbour pruning leave the graph disconnected, and then no
    ef_search / iterative scan setting can reach the rows."""
    rng = random.Random(seed)
    v = [(rng.random() - 0.5) * noise for _ in range(_DIM)]
    v[0] += 1.0
    return v


_BODY_SQL = (
    "SELECT re.resource_id FROM resource_embeddings re "
    "JOIN resources r ON r.id = re.resource_id "
    "JOIN parsed_media pm ON pm.id = r.media_id "
    "WHERE re.space_id = $2 AND re.layer = 'semantic' "
    "AND r.media_id IS NOT NULL AND r.is_trashed = false "
    "AND r.source_type = 'web' AND r.creator_id = $3 "
    "AND (1 - (re.embedding <=> CAST($1 AS halfvec))) > 0.5 "
    "ORDER BY re.embedding <=> CAST($1 AS halfvec) LIMIT 5"
)


def _index_names(plan) -> list[str]:
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("Node Type") == "Index Scan":
                found.append(node.get("Index Name"))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(plan)
    return found


@_skip
async def test_rpc_fills_the_page_when_other_users_are_nearer(pg, fx):
    """HNSW hands back only ``hnsw.ef_search`` (default 40) global neighbours
    and the space / user / threshold filters run afterwards. With 60 closer
    vectors belonging to someone else, a plain scan returns nothing for the
    caller while vector_leg still reads ok. The function must scan on
    (iterative_scan) until the caller's page is full."""
    import json

    # Rolled-back runs (this test and the ones above) leave dead entries in
    # the HNSW graph; on a long-lived DB enough of them make live rows
    # unreachable and the test flaky. VACUUM repairs the graph (outside any
    # transaction, so before the one below).
    await pg.execute("VACUUM resource_embeddings")
    tr = pg.transaction()
    await tr.start()
    try:
        other = uuid.uuid4()
        await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", other)
        for i in range(60):
            rid = await _mk_resource(pg, other, f"Other Clip {i}")
            await pg.execute(
                "INSERT INTO resource_embeddings (resource_id, layer, space_id, "
                "embedding, source_hash) VALUES ($1, 'semantic', $2, "
                "CAST($3 AS halfvec), 'o')",
                rid,
                fx["s1"],
                _lit(_near(i, 0.01)),
            )
        mine = [fx["r1"], fx["r2"]]
        for i in range(3):
            mine.append(await _mk_resource(pg, fx["user"], f"Mine {i}"))
        for i, rid in enumerate(mine):
            await pg.execute(
                "INSERT INTO resource_embeddings (resource_id, layer, space_id, "
                "embedding, source_hash) VALUES ($1, 'semantic', $2, "
                "CAST($3 AS halfvec), 'm')",
                rid,
                fx["s1"],
                _lit(_near(1000 + i, 0.03)),
            )
        # A 65-row table gives the planner cheap alternatives production does
        # not have: start from the user's resources, then sort by distance.
        # In production one space holds every row and a user owns thousands
        # of resources, so it walks HNSW in distance order instead. Force that
        # plan shape for this transaction only: no seq scan, no explicit sort.
        await pg.execute("SET LOCAL enable_seqscan = off")
        await pg.execute("SET LOCAL enable_sort = off")
        plan = await pg.fetchval(
            "EXPLAIN (FORMAT JSON) " + _BODY_SQL,
            _lit(_unit(0)),
            fx["s1"],
            fx["user"],
        )
        names = _index_names(json.loads(plan) if isinstance(plan, str) else plan)
        assert "resource_embeddings_embedding_hnsw" in names, names

        rows = await pg.fetch(
            "SELECT * FROM match_resource_embeddings(CAST($1 AS halfvec), $2, "
            "'semantic', 0.5, 5, $3)",
            _lit(_unit(0)),
            fx["s1"],
            fx["user"],
        )
        assert sorted(r["resource_id"] for r in rows) == sorted(mine)
        sims = [r["similarity"] for r in rows]
        assert sims == sorted(sims, reverse=True), "rows not ranked"
    finally:
        await tr.rollback()


@_skip
async def test_rpc_pins_iterative_scan_settings(pg):
    config = await pg.fetchval(
        "SELECT proconfig FROM pg_proc WHERE oid = to_regprocedure($1)", _RPC
    )
    assert "hnsw.iterative_scan=relaxed_order" in config
    assert "hnsw.ef_search=100" in config


@_skip
@pytest.mark.parametrize(
    "table", ["public.embedding_spaces", "public.resource_embeddings"]
)
@pytest.mark.parametrize("role", ["anon", "authenticated"])
async def test_browser_roles_have_no_table_privileges(pg, table, role):
    for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        granted = await pg.fetchval(
            "SELECT has_table_privilege($1, $2, $3)", role, table, priv
        )
        assert granted is False, f"{role} has {priv} on {table}"


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
            source_hash="v:h2",
            source_text="second",
        )
        got = await repo.get(fx["r1"], "semantic", fx["s1"])
        assert got["source_hash"] == "v:h2" and got["source_text"] == "second"
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

        missing, total = await repo.pending_for_user(
            user_id=user,
            space_id=fx["s1"],
            layer="semantic",
            limit=10,
            doc_version="v",  # r1's hash is current: only r2 is pending
        )
    assert total == 1
    assert [m.resource_id for m in missing] == [fx["r2"]]
    assert isinstance(missing[0], BackfillRow)
    assert missing[0].title == "Second Clip" and missing[0].has_analysis is False
    assert missing[0].reason == "missing"

    rows = await pg.fetchval(
        "SELECT count(*) FROM resource_embeddings WHERE resource_id = $1", fx["r1"]
    )
    assert rows == 1, "ON CONFLICT did not find the PK — appended instead"


# ── pending_for_user: stale rows (re-embed on a changed document) ─────────


async def _insert_vector(pg, rid: int, space_id: int, source_hash: str, age):
    await pg.execute(
        "INSERT INTO resource_embeddings (resource_id, layer, space_id, "
        "embedding, source_hash, created_at, updated_at) VALUES ($1, 'semantic', "
        "$2, CAST($3 AS halfvec), $4, now() - CAST($5 AS interval), now() - CAST($5 AS interval))",
        rid,
        space_id,
        _lit(_unit(0)),
        source_hash,
        age,
    )


@_skip
async def test_pending_selects_an_old_version_hash_as_stale_version(orm_dsn, pg, fx):
    """A hash written by another DOC_VERSION (or the pre-prefix bare sha1)
    is stale; missing rows still come first. The ``_`` in the version is a
    LIKE wildcard unless escaped: "semanticXv2:" must NOT count as current."""
    from app.db.scope import Scope, request_scope
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    r3 = await _mk_resource(pg, fx["user"], "Third Clip")
    try:
        await _insert_vector(pg, fx["r1"], fx["s1"], "da39a3ee5e6b4b0d", _HOUR)
        await _insert_vector(pg, r3, fx["s1"], "semanticXv2:abc", _HOUR)
        repo = ResourceEmbeddingsRepository()
        user = str(fx["user"])
        async with request_scope(Scope(user_id=user)):
            rows, total = await repo.pending_for_user(
                user_id=user,
                space_id=fx["s1"],
                layer="semantic",
                limit=10,
                doc_version="semantic_v2",
            )
            stale = await repo.stale_count(
                user_id=user,
                space_id=fx["s1"],
                layer="semantic",
                doc_version="semantic_v2",
            )
            await pg.execute(
                "UPDATE resource_embeddings SET source_hash = 'semantic_v2:abc' "
                "WHERE resource_id = ANY($1::bigint[])",
                [fx["r1"], r3],
            )
            _, total_after = await repo.pending_for_user(
                user_id=user,
                space_id=fx["s1"],
                layer="semantic",
                limit=10,
                doc_version="semantic_v2",
            )
        assert total == 3 and stale == 2
        assert rows[0].resource_id == fx["r2"] and rows[0].reason == "missing"
        assert {(r.resource_id, r.reason) for r in rows[1:]} == {
            (fx["r1"], "stale_version"),
            (r3, "stale_version"),
        }
        assert total_after == 1, "current-version hashes are not pending"
    finally:
        media = await pg.fetchval("SELECT media_id FROM resources WHERE id = $1", r3)
        await pg.execute("DELETE FROM resources WHERE id = $1", r3)
        await pg.execute("DELETE FROM parsed_media WHERE id = $1", media)


@_skip
async def test_pending_selects_a_later_summary_as_stale_source(orm_dsn, pg, fx):
    """A summary created after the vector means the document changed: the
    row is ``stale_source`` until it is re-embedded or touched."""
    from app.db.scope import Scope, request_scope
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    await _insert_vector(pg, fx["r1"], fx["s1"], "semantic_v2:abc", _HOUR)
    await _insert_vector(pg, fx["r2"], fx["s1"], "semantic_v2:def", _HOUR)
    # An OLDER summary on r2 must not make it stale.
    await pg.execute(
        "INSERT INTO resource_summaries (resource_id, summary_text, created_at) "
        "VALUES ($1, 'new summary', now()), ($2, 'old summary', "
        "now() - interval '2 hours')",
        fx["r1"],
        fx["r2"],
    )
    repo = ResourceEmbeddingsRepository()
    user = str(fx["user"])
    async with request_scope(Scope(user_id=user)):
        rows, total = await repo.pending_for_user(
            user_id=user,
            space_id=fx["s1"],
            layer="semantic",
            limit=10,
            doc_version="semantic_v2",
        )
        stale = await repo.stale_count(
            user_id=user,
            space_id=fx["s1"],
            layer="semantic",
            doc_version="semantic_v2",
        )
        await repo.touch(fx["r1"], "semantic", fx["s1"])
        _, total_after = await repo.pending_for_user(
            user_id=user,
            space_id=fx["s1"],
            layer="semantic",
            limit=10,
            doc_version="semantic_v2",
        )
    assert total == 1 and stale == 1
    assert [(r.resource_id, r.reason) for r in rows] == [(fx["r1"], "stale_source")]
    assert total_after == 0, "touch moved updated_at past the summary"
