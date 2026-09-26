"""Migration 507 (shot index) and its two repositories against real Postgres.

Only a server can answer what this file asks (the unit lane stubs the
session): the vector column really is ``halfvec(2048)`` with an HNSW index
on ``halfvec_cosine_ops``; ``match_video_shot_embeddings`` returns ONE row
per video (its best shot) and ranks within ONE space; the browser roles can
neither read the tables nor execute the RPC; ``replace`` swaps a cut list in
one transaction and takes the old vectors with it (FK cascade); coverage /
stale_count / pending_for_user agree on the same population.

Gated on INTEGRATION_DATABASE_URL — skips cleanly in the unit lane:

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/db/test_migration_507_video_shots_integration.py -v
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
    reason="INTEGRATION_DATABASE_URL not set — video_shots need a DB.",
)

_RPC = (
    "public.match_video_shot_embeddings"
    "(halfvec,bigint,text,double precision,integer,uuid)"
)
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
    """Repoint the ORM engine at the test DSN, then restore + dispose."""
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


async def _mk_video(conn, user_id: uuid.UUID, title: str, *, video: bool = True) -> int:
    media_id = await conn.fetchval(
        "INSERT INTO parsed_media (platform_id, original_url, title) "
        "VALUES ($1, $2, $3) RETURNING id",
        f"pid_{uuid.uuid4().hex[:12]}",
        f"https://example.test/{uuid.uuid4().hex[:8]}",
        title,
    )
    return await conn.fetchval(
        "INSERT INTO resources (creator_id, source_type, filename, media_id, "
        "is_trashed, file_type, mime_type) VALUES ($1, 'web', $2, $3, false, $4, $5) "
        "RETURNING id",
        user_id,
        f"{title}.mp4",
        media_id,
        "video" if video else "image",
        "video/mp4" if video else "image/jpeg",
    )


async def _mk_shot(conn, resource_id: int, index: int, start: int, end: int) -> int:
    return await conn.fetchval(
        "INSERT INTO video_shots (resource_id, shot_index, start_ms, end_ms, "
        "rep_frame_ms) VALUES ($1, $2, $3, $4, $5) RETURNING id",
        resource_id,
        index,
        start,
        end,
        (start + end) // 2,
    )


async def _mk_vec(conn, shot_id: int, space_id: int, vec: list[float]) -> None:
    await conn.execute(
        "INSERT INTO video_shot_embeddings (shot_id, kind, space_id, embedding, "
        "source_hash) VALUES ($1, 'frame', $2, CAST($3 AS halfvec), 'h')",
        shot_id,
        space_id,
        _lit(vec),
    )


@pytest.fixture
async def fx(pg):
    """One user, two videos + one image resource, two spaces; torn down."""
    user = uuid.uuid4()
    tag = uuid.uuid4().hex[:8]
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user)
    v1 = await _mk_video(pg, user, "First Video")
    v2 = await _mk_video(pg, user, "Second Video")
    img = await _mk_video(pg, user, "A Still", video=False)
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
    ids = [v1, v2, img]
    try:
        yield {"user": user, "v1": v1, "v2": v2, "img": img, "s1": s1, "s2": s2}
    finally:
        await pg.execute(
            "DELETE FROM embedding_spaces WHERE actual_model LIKE $1", f"%-{tag}"
        )
        media = await pg.fetch(
            "SELECT media_id FROM resources WHERE id = ANY($1::bigint[])", ids
        )
        await pg.execute("DELETE FROM resources WHERE id = ANY($1::bigint[])", ids)
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
        "WHERE attrelid = 'public.video_shot_embeddings'::regclass "
        "AND attname = 'embedding'"
    )
    assert typ == "halfvec(2048)"
    for table in ("video_shots", "video_shot_indexes"):
        assert await pg.fetchval("SELECT to_regclass($1)", f"public.{table}")


@_skip
async def test_hnsw_index_uses_halfvec_cosine_ops(pg):
    indexdef = await pg.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
        "AND indexname = 'video_shot_embeddings_embedding_hnsw'"
    )
    assert indexdef is not None
    assert "USING hnsw" in indexdef and "halfvec_cosine_ops" in indexdef


@_skip
@pytest.mark.parametrize("role", ["anon", "authenticated"])
async def test_browser_roles_have_no_access(pg, role):
    granted = await pg.fetchval(
        "SELECT has_function_privilege($1, to_regprocedure($2), 'EXECUTE')",
        role,
        _RPC,
    )
    assert granted is False, f"{role} can EXECUTE {_RPC}"
    for table in ("video_shots", "video_shot_embeddings", "video_shot_indexes"):
        can_read = await pg.fetchval(
            "SELECT has_table_privilege($1, $2, 'SELECT')", role, f"public.{table}"
        )
        assert can_read is False, f"{role} can SELECT {table}"


# ── RPC semantics (inside a rolled-back transaction) ─────────────────────


@_skip
async def test_rpc_returns_best_shot_per_video_within_one_space(pg, fx):
    tr = pg.transaction()
    await tr.start()
    try:
        # v1: two shots, the second is the exact query; v2: one weaker shot.
        a = await _mk_shot(pg, fx["v1"], 0, 0, 4000)
        b = await _mk_shot(pg, fx["v1"], 1, 4000, 9000)
        c = await _mk_shot(pg, fx["v2"], 0, 0, 3000)
        await _mk_vec(pg, a, fx["s1"], [0.8] + [0.6] + [0.0] * (_DIM - 2))
        await _mk_vec(pg, b, fx["s1"], _unit(0))
        await _mk_vec(pg, c, fx["s1"], [0.6] + [0.8] + [0.0] * (_DIM - 2))
        # Same exact vector in ANOTHER space must not come back.
        await _mk_vec(pg, c, fx["s2"], _unit(0))
        rows = await pg.fetch(
            "SELECT * FROM match_video_shot_embeddings(CAST($1 AS halfvec), $2, "
            "'frame', 0.5, 10, $3)",
            _lit(_unit(0)),
            fx["s1"],
            fx["user"],
        )
        assert [r["resource_id"] for r in rows] == [fx["v1"], fx["v2"]]
        assert rows[0]["shot_id"] == b and rows[0]["start_ms"] == 4000
        assert rows[0]["similarity"] == pytest.approx(1.0, abs=1e-3)
        assert rows[0]["title"] == "First Video"
        assert rows[1]["shot_id"] == c
        # Another user sees nothing.
        other = await pg.fetch(
            "SELECT * FROM match_video_shot_embeddings(CAST($1 AS halfvec), $2, "
            "'frame', 0.5, 10, $3)",
            _lit(_unit(0)),
            fx["s1"],
            uuid.uuid4(),
        )
        assert other == []
    finally:
        await tr.rollback()


@_skip
async def test_rpc_plan_uses_the_hnsw_index(pg):
    """The HNSW index serves a cosine ORDER BY ... LIMIT (the shape the RPC
    walks). Unfiltered on purpose: on a near-empty table a (space, kind)
    filter is cheaper through the btree, which says nothing about the
    vector index; the RPC's function-level iterative_scan handles the
    filtered case."""
    tr = pg.transaction()
    await tr.start()
    try:
        await pg.execute("SET LOCAL enable_seqscan = off")
        plan = await pg.fetch(
            "EXPLAIN SELECT (se.embedding <=> CAST($1 AS halfvec)) FROM "
            "video_shot_embeddings se "
            "ORDER BY se.embedding <=> CAST($1 AS halfvec) LIMIT 5",
            _lit(_unit(0)),
        )
        text = "\n".join(r[0] for r in plan)
        assert "video_shot_embeddings_embedding_hnsw" in text, text
    finally:
        await tr.rollback()


# ── repositories through the real ORM engine ─────────────────────────────


@_skip
async def test_replace_swaps_the_cut_list_and_cascades_old_vectors(pg, fx, orm_dsn):
    from app.repositories.video_shots_repository import (
        ShotRow,
        VideoShotEmbeddingsRepository,
        VideoShotsRepository,
    )

    shots = VideoShotsRepository()
    vecs = VideoShotEmbeddingsRepository()
    try:
        ids = await shots.replace(
            resource_id=fx["v1"],
            shots=[ShotRow(0, 0, 4000, 2000, 0.9), ShotRow(1, 4000, 9000, 6500, 0.4)],
            algo_version="hist_v1",
            duration_ms=9000,
        )
        assert len(ids) == 2
        await vecs.upsert_many(
            space_id=fx["s1"],
            kind="frame",
            rows=[(ids[0], _unit(0), "hist_v1:a"), (ids[1], _unit(1), "hist_v1:b")],
        )
        assert (
            await pg.fetchval(
                "SELECT count(*) FROM video_shot_embeddings WHERE shot_id = ANY($1::bigint[])",
                ids,
            )
            == 2
        )
        index = await shots.get_index(fx["v1"])
        assert index["shot_count"] == 2 and index["algo_version"] == "hist_v1"
        listed = await shots.list_shots(fx["v1"])
        assert [s["shot_index"] for s in listed] == [0, 1]
        assert listed[1]["rep_frame_ms"] == 6500

        # Re-cut: one shot now. Old rows and their vectors are gone.
        new_ids = await shots.replace(
            resource_id=fx["v1"],
            shots=[ShotRow(0, 0, 9000, 4500)],
            algo_version="hist_v2",
            duration_ms=9000,
        )
        assert len(new_ids) == 1 and new_ids[0] not in ids
        assert (
            await pg.fetchval(
                "SELECT count(*) FROM video_shot_embeddings WHERE shot_id = ANY($1::bigint[])",
                ids,
            )
            == 0
        )
        index = await shots.get_index(fx["v1"])
        assert index["shot_count"] == 1 and index["algo_version"] == "hist_v2"
    finally:
        await pg.execute("DELETE FROM video_shots WHERE resource_id = $1", fx["v1"])
        await pg.execute(
            "DELETE FROM video_shot_indexes WHERE resource_id = $1", fx["v1"]
        )


@_skip
async def test_search_coverage_stale_and_pending_agree(pg, fx, orm_dsn):
    from app.repositories.video_shots_repository import (
        ShotRow,
        VideoShotEmbeddingsRepository,
        VideoShotsRepository,
    )

    shots = VideoShotsRepository()
    vecs = VideoShotEmbeddingsRepository()
    user = str(fx["user"])
    try:
        # Nothing indexed: both videos pending (the image is not a video).
        rows, total = await vecs.pending_for_user(
            user_id=user,
            space_id=fx["s1"],
            kind="frame",
            algo_version="hist_v1",
            limit=10,
        )
        assert total == 2 and {r.resource_id for r in rows} == {fx["v1"], fx["v2"]}
        assert all(r.reason == "missing" for r in rows)
        assert await vecs.coverage(user_id=user, space_id=fx["s1"], kind="frame") == (
            0,
            2,
        )

        # Index v1 with the current algo, v2 with an older one.
        ids1 = await shots.replace(
            resource_id=fx["v1"],
            shots=[ShotRow(0, 0, 4000, 2000)],
            algo_version="hist_v1",
            duration_ms=4000,
        )
        ids2 = await shots.replace(
            resource_id=fx["v2"],
            shots=[ShotRow(0, 0, 3000, 1500)],
            algo_version="hist_v0",
            duration_ms=3000,
        )
        await vecs.upsert_many(
            space_id=fx["s1"],
            kind="frame",
            rows=[(ids1[0], _unit(0), "hist_v1:x"), (ids2[0], _unit(1), "hist_v0:y")],
        )
        assert await vecs.coverage(user_id=user, space_id=fx["s1"], kind="frame") == (
            2,
            2,
        )
        assert (
            await vecs.stale_count(
                user_id=user, space_id=fx["s1"], kind="frame", algo_version="hist_v1"
            )
            == 1
        )
        rows, total = await vecs.pending_for_user(
            user_id=user,
            space_id=fx["s1"],
            kind="frame",
            algo_version="hist_v1",
            limit=10,
        )
        assert (
            total == 1
            and rows[0].resource_id == fx["v2"]
            and rows[0].reason == "stale_algo"
        )
        # The sweeper's cross-user view agrees, carries the owner, and
        # ``limit=0`` only counts.
        all_rows, all_total = await vecs.pending_all(
            space_id=fx["s1"], kind="frame", algo_version="hist_v1", limit=10
        )
        assert all_total >= 1
        mine = [r for r in all_rows if r.resource_id == fx["v2"]]
        assert mine and mine[0].user_id == user and mine[0].reason == "stale_algo"
        none, count_only = await vecs.pending_all(
            space_id=fx["s1"], kind="frame", algo_version="hist_v1", limit=0
        )
        assert none == [] and count_only == all_total
        # In the other space nothing is covered and both are missing again.
        assert await vecs.coverage(user_id=user, space_id=fx["s2"], kind="frame") == (
            0,
            2,
        )
        _, total2 = await vecs.pending_for_user(
            user_id=user,
            space_id=fx["s2"],
            kind="frame",
            algo_version="hist_v1",
            limit=10,
        )
        assert total2 == 2
        assert await vecs.count_in_space(fx["s1"]) == 2

        hits = await vecs.search(
            embedding=_unit(0),
            space_id=fx["s1"],
            kind="frame",
            user_id=user,
            limit=5,
            threshold=0.5,
        )
        assert [h["resource_id"] for h in hits] == [fx["v1"]]
        assert hits[0]["shot_id"] == ids1[0] and hits[0]["end_ms"] == 4000
        with pytest.raises(ValueError):
            await vecs.search(
                embedding=_unit(0),
                space_id=fx["s1"],
                kind="frame",
                user_id="",
                limit=5,
                threshold=0.5,
            )
    finally:
        for rid in (fx["v1"], fx["v2"]):
            await pg.execute("DELETE FROM video_shots WHERE resource_id = $1", rid)
            await pg.execute(
                "DELETE FROM video_shot_indexes WHERE resource_id = $1", rid
            )
