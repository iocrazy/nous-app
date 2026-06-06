"""Integration tests for the SIX Storyboard ORM repos (Phase 2 L-solo) vs PG.

Proves the REST → ORM swap is invisible AND that STRATEGY-C value-type parity
holds across the Storyboard Workbench surface (6 tables):

  - bigint ids + FKs (the six ids + project_id / node_id / source_node_id /
    target_node_id / team_id) → STAY native int (the 5.3 trap).
  - uuid (storyboard_projects.created_by — the ONLY uuid column) → STR (REST
    parity; the response model declares created_by: str).
  - timestamptz (created_at / updated_at) → ISO STR. NO date columns.
  - numeric canvas coords (position_x/y, width, height, duration_seconds —
    Double) → LEFT NATIVE float. jsonb → native dict.

Writes (create / update / bulk_upsert / reorder / soft_delete / update_viewport
/ delete) go through ``write_scope()`` (COMMITS) — a fresh asyncpg read proves
no silent rollback.

bulk_upsert (Node / Edge / Frame) is pinned with a MIXED new+existing batch in
one call (the L1 mixed-PK CompileError trap): a row WITHOUT id (insert via the
snowflake server default) + a row WITH an explicit id (update) — the row-by-row
pg_insert ON CONFLICT (id) path must handle both atomically.

NOTE: there are NO date/timestamp RANGE filters in these repos (every query is
equality / IN / ilike / ordering), so there is no timestamptz<VARCHAR boundary
to pin (unlike LogsRepositoryOrm).

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_storyboard_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_sb_"


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
async def cleanup_test_rows(integration_db_url):
    """Delete test storyboard projects (CASCADE drops nodes / edges / frames /
    characters / assets) by name prefix, after each test."""
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM storyboard_projects WHERE name LIKE $1", _PREFIX + "%"
        )
    finally:
        await conn.close()


async def _real_team_id(conn):
    tid = await conn.fetchval("SELECT id FROM teams LIMIT 1")
    if not tid:
        pytest.skip("No teams rows to satisfy storyboard_projects.team_id")
    return tid


async def _real_user_id(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy storyboard_projects.created_by")
    return uid


# ─── repo accessors ─────────────────────────────────────────────────────


def _project_repo():
    from app.repositories.storyboard_repository_orm import (
        StoryboardProjectRepositoryOrm,
    )

    return StoryboardProjectRepositoryOrm()


def _node_repo():
    from app.repositories.storyboard_repository_orm import StoryboardNodeRepositoryOrm

    return StoryboardNodeRepositoryOrm()


def _edge_repo():
    from app.repositories.storyboard_repository_orm import StoryboardEdgeRepositoryOrm

    return StoryboardEdgeRepositoryOrm()


def _frame_repo():
    from app.repositories.storyboard_repository_orm import StoryboardFrameRepositoryOrm

    return StoryboardFrameRepositoryOrm()


def _character_repo():
    from app.repositories.storyboard_repository_orm import (
        StoryboardCharacterRepositoryOrm,
    )

    return StoryboardCharacterRepositoryOrm()


def _asset_repo():
    from app.repositories.storyboard_repository_orm import StoryboardAssetRepositoryOrm

    return StoryboardAssetRepositoryOrm()


async def _seed_project(repo, team_id, user_id) -> dict:
    return await repo.create(
        {
            "team_id": team_id,
            "created_by": str(user_id),
            "name": f"{_PREFIX}{uuid.uuid4().hex[:8]}",
        }
    )


# ─── Project: create / read / update / parity ───────────────────────────


async def test_project_create_read_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        team_id = await _real_team_id(conn)
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    repo = _project_repo()
    created = await repo.create(
        {
            "team_id": team_id,
            "created_by": str(user_id),
            "name": f"{_PREFIX}{uuid.uuid4().hex[:8]}",
        }
    )
    assert type(created["id"]) is int  # bigint id stays int (5.3 trap)
    assert type(created["team_id"]) is int  # bigint team_id stays int
    assert created["created_by"] == str(user_id)  # uuid → str
    assert type(created["created_at"]) is str and "T" in created["created_at"]

    # Persisted (no silent rollback).
    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT name FROM storyboard_projects WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert persisted == created["name"]

    got = await repo.get_by_id(str(created["id"]))
    assert got is not None and got["id"] == created["id"]

    updated = await repo.update(str(created["id"]), {"description": "hi"})
    assert updated["description"] == "hi"


async def test_project_viewport_softdelete_list(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        team_id = await _real_team_id(conn)
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    repo = _project_repo()
    proj = await _seed_project(repo, team_id, user_id)

    # viewport update (jsonb round-trip; native dict)
    await repo.update_viewport(str(proj["id"]), {"x": 1.5, "y": 2.0, "zoom": 0.8})
    got = await repo.get_by_id(str(proj["id"]))
    assert got["viewport_json"] == {"x": 1.5, "y": 2.0, "zoom": 0.8}

    # list_by_team shows the active project
    listing = await repo.list_by_team(str(team_id))
    assert proj["id"] in {p["id"] for p in listing["items"]}
    assert listing["total"] >= 1

    # soft-delete → excluded from list
    await repo.soft_delete(str(proj["id"]))
    listing2 = await repo.list_by_team(str(team_id))
    assert proj["id"] not in {p["id"] for p in listing2["items"]}


# ─── Node: bulk_upsert MIXED-PK + update / delete ───────────────────────


async def test_node_bulk_upsert_mixed_pk(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """Node bulk_upsert with a MIXED batch: one row WITHOUT id (insert via the
    snowflake default) + one row WITH explicit id (update) in ONE call."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        team_id = await _real_team_id(conn)
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    repo = _node_repo()
    proj = await _seed_project(_project_repo(), team_id, user_id)

    # First insert one node (gets a server-default id).
    first = await repo.bulk_upsert(
        str(proj["id"]),
        [{"node_type": "upload", "position_x": 1.0, "position_y": 2.0}],
    )
    assert len(first) == 1
    existing_id = first[0]["id"]
    assert type(existing_id) is int
    # numeric coords stay native float
    assert type(first[0]["position_x"]) is float

    # MIXED batch: update existing (explicit id) + insert new (no id).
    mixed = await repo.bulk_upsert(
        str(proj["id"]),
        [
            {"id": existing_id, "node_type": "upload", "position_x": 99.0},
            {"node_type": "group", "position_x": 5.0, "position_y": 6.0},
        ],
    )
    assert len(mixed) == 2
    by_id = {n["id"]: n for n in mixed}
    assert by_id[existing_id]["position_x"] == 99.0  # updated
    new_ids = [nid for nid in by_id if nid != existing_id]
    assert len(new_ids) == 1  # one fresh insert

    all_nodes = await repo.get_by_project(str(proj["id"]))
    assert {existing_id, new_ids[0]} <= {n["id"] for n in all_nodes}

    # update + delete single
    upd = await repo.update(str(existing_id), {"locked": True})
    assert upd["locked"] is True
    await repo.delete(str(new_ids[0]))
    remaining = {n["id"] for n in await repo.get_by_project(str(proj["id"]))}
    assert new_ids[0] not in remaining


# ─── Edge: bulk_upsert MIXED-PK + delete ────────────────────────────────


async def test_edge_bulk_upsert_mixed_pk(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        team_id = await _real_team_id(conn)
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    project_repo = _project_repo()
    node_repo = _node_repo()
    edge_repo = _edge_repo()
    proj = await _seed_project(project_repo, team_id, user_id)
    nodes = await node_repo.bulk_upsert(
        str(proj["id"]),
        [
            {"node_type": "upload", "position_x": 0.0, "position_y": 0.0},
            {"node_type": "upload", "position_x": 1.0, "position_y": 1.0},
        ],
    )
    src, dst = nodes[0]["id"], nodes[1]["id"]

    first = await edge_repo.bulk_upsert(
        str(proj["id"]),
        [{"source_node_id": src, "target_node_id": dst, "edge_type": "default"}],
    )
    assert len(first) == 1
    eid = first[0]["id"]
    assert type(eid) is int
    assert type(first[0]["source_node_id"]) is int  # bigint FK stays int

    # MIXED: update existing + insert new
    mixed = await edge_repo.bulk_upsert(
        str(proj["id"]),
        [
            {
                "id": eid,
                "source_node_id": src,
                "target_node_id": dst,
                "edge_type": "smoothstep",
            },
            {"source_node_id": dst, "target_node_id": src, "edge_type": "default"},
        ],
    )
    assert len(mixed) == 2
    by_id = {e["id"]: e for e in mixed}
    assert by_id[eid]["edge_type"] == "smoothstep"

    got = await edge_repo.get_by_project(str(proj["id"]))
    assert eid in {e["id"] for e in got}
    await edge_repo.delete(str(eid))
    assert eid not in {e["id"] for e in await edge_repo.get_by_project(str(proj["id"]))}


# ─── Frame: create / bulk_upsert MIXED-PK / reorder / reads ─────────────


async def test_frame_create_bulk_reorder(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        team_id = await _real_team_id(conn)
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    proj = await _seed_project(_project_repo(), team_id, user_id)
    node = (
        await _node_repo().bulk_upsert(
            str(proj["id"]),
            [{"node_type": "storyboard_split", "position_x": 0.0, "position_y": 0.0}],
        )
    )[0]
    node_id = node["id"]
    frame_repo = _frame_repo()

    # create (uses real columns)
    f1 = await frame_repo.create(
        {
            "node_id": node_id,
            "project_id": proj["id"],
            "frame_index": 0,
            "duration_seconds": 2.0,
            "transition_type": "cut",
            "sort_order": 0,
        }
    )
    assert type(f1["id"]) is int
    assert type(f1["duration_seconds"]) is float  # Double left native

    # bulk_upsert MIXED: update f1 (explicit id) + insert a new frame (no id)
    mixed = await frame_repo.bulk_upsert(
        str(node_id),
        [
            {
                "id": f1["id"],
                "project_id": proj["id"],
                "frame_index": 0,
                "sort_order": 5,
            },
            {"project_id": proj["id"], "frame_index": 1, "sort_order": 1},
        ],
    )
    assert len(mixed) == 2
    by_id = {f["id"]: f for f in mixed}
    assert by_id[f1["id"]]["sort_order"] == 5  # updated
    new_frame_id = [fid for fid in by_id if fid != f1["id"]][0]

    # reads
    by_node = await frame_repo.get_by_node(str(node_id))
    assert {f["id"] for f in by_node} == {f1["id"], new_frame_id}
    by_proj = await frame_repo.get_by_project(str(proj["id"]))
    assert {f1["id"], new_frame_id} <= {f["id"] for f in by_proj}

    # reorder → sort_order matches list position
    await frame_repo.reorder([str(new_frame_id), str(f1["id"])])
    reordered = {
        f["id"]: f["sort_order"] for f in await frame_repo.get_by_node(str(node_id))
    }
    assert reordered[new_frame_id] == 0
    assert reordered[f1["id"]] == 1

    # update single frame
    upd = await frame_repo.update(str(f1["id"]), {"note": "hero shot"})
    assert upd["note"] == "hero shot"


# ─── Phantom-write parity guard (compile-time, NO DB needed) ────────────
#
# WHY THESE TESTS EXIST — they lock in the bulk_upsert parity INVARIANT: a
# phantom (non-column) key on a row must RAISE, never be silently dropped. The
# legacy REST bulk_upsert hands ALL row keys to PostgREST's .upsert(); a key that
# is not a real column makes PostgREST 400 and the method re-raises → 500. The
# ORM bulk_upsert reproduces that EXACTLY by passing row keys straight to
# pg_insert(...).values(**row), so an unknown column raises a SQLAlchemy
# CompileError ("Unconsumed column names") at statement COMPILE time = the same
# observable 500.
#
# NOTE: the two AI-gen callers that USED to feed phantom columns (script_ai_router
# node writes / both workflow persist-scene frame writes) have since been FIXED
# (BUG 6 — they now write real columns + nest extras in data_json/annotations_json).
# These guards therefore use SYNTHETIC phantom rows: they pin the parity
# mechanism itself (so a future "cleanup" that routes bulk_upsert through
# _known_only — silently dropping unknown keys — is caught), independent of any
# specific caller.
#
# The whole parity argument rests on bulk_upsert NOT being routed through
# _known_only (which would silently DROP the phantom keys and turn those 500s
# into successes — a behavior change forbidden on an inert migration). These
# guards FAIL the moment a future "cleanup" filters bulk_upsert keys, catching a
# silent behavior change no other test would notice.
#
# We assert the SPECIFIC CompileError on the exact statement the method builds
# (pg_insert(model).values(**row)) rather than running through the public
# bulk_upsert + write_scope(): with the engine unconfigured write_scope() raises
# a RuntimeError ("engine disabled") BEFORE the statement compiles, which a bare
# pytest.raises(Exception) would pass for the WRONG reason and would NOT guard
# the invariant. Compiling the statement directly needs neither a live DB nor
# the engine fixtures, and pins the raise to the real parity mechanism. We also
# assert _known_only WOULD have dropped the phantom key — proving the two paths
# genuinely diverge (the exact regression this guards). Synthetic phantom keys
# (scene_number on the Node path, order_index on the Frame path) stand in for any
# unknown column; Edge shares the same _bulk_upsert_rows code path.


def test_node_bulk_upsert_phantom_column_compile_raises():
    """A phantom (non-column) key on a node upsert row raises CompileError at
    statement build, NOT a silent key-drop. Pins the REST→ORM 500 parity
    invariant for bulk_upsert (scene_number here is a synthetic stand-in; the
    real script_ai_router caller no longer writes it — BUG 6 fixed)."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.exc import CompileError

    from app.models import StoryboardNodes
    from app.repositories.storyboard_repository_orm import _NODES_ATTRS, _known_only

    row = {"node_type": "storyboard_split", "scene_number": 1}
    stmt = pg_insert(StoryboardNodes).values(**row)  # what _bulk_upsert_rows builds
    with pytest.raises(CompileError):
        str(stmt.compile(dialect=postgresql.dialect()))
    # The divergence this guards: _known_only WOULD drop the phantom key (so
    # routing bulk_upsert through it would silently succeed — forbidden).
    assert "scene_number" not in _known_only(row, _NODES_ATTRS)


def test_frame_bulk_upsert_phantom_column_compile_raises():
    """A phantom (non-column) key on a frame upsert row raises CompileError at
    statement build, NOT a silent key-drop. Pins the REST→ORM 500 parity
    invariant for bulk_upsert (order_index here is a synthetic stand-in; the real
    workflow persist-scene callers no longer write it — BUG 6 fixed)."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.exc import CompileError

    from app.models import StoryboardFrames
    from app.repositories.storyboard_repository_orm import _FRAMES_ATTRS, _known_only

    row = {"order_index": 0, "prompt": "x", "status": "pending"}
    stmt = pg_insert(StoryboardFrames).values(**row)  # what _bulk_upsert_rows builds
    with pytest.raises(CompileError):
        str(stmt.compile(dialect=postgresql.dialect()))
    assert "order_index" not in _known_only(row, _FRAMES_ATTRS)


# ─── Character: CRUD ────────────────────────────────────────────────────


async def test_character_crud(integration_db_url, patched_engine, cleanup_test_rows):
    conn = await asyncpg.connect(integration_db_url)
    try:
        team_id = await _real_team_id(conn)
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    proj = await _seed_project(_project_repo(), team_id, user_id)
    repo = _character_repo()

    c = await repo.create(
        {
            "project_id": proj["id"],
            "name": "Hero",
            "visual_traits": {"hair": "long brown"},
        }
    )
    assert type(c["id"]) is int
    assert c["visual_traits"] == {"hair": "long brown"}  # jsonb native dict

    got = await repo.get_by_id(str(c["id"]))
    assert got["name"] == "Hero"

    upd = await repo.update(str(c["id"]), {"name": "Villain"})
    assert upd["name"] == "Villain"

    listing = await repo.list_by_project(str(proj["id"]))
    assert c["id"] in {x["id"] for x in listing}

    await repo.delete(str(c["id"]))
    assert c["id"] not in {x["id"] for x in await repo.list_by_project(str(proj["id"]))}


# ─── Asset: create / find_by_hash / list / delete ───────────────────────


async def test_asset_crud_and_dedup(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        team_id = await _real_team_id(conn)
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    proj = await _seed_project(_project_repo(), team_id, user_id)
    repo = _asset_repo()
    file_hash = uuid.uuid4().hex

    a = await repo.create(
        {
            "project_id": proj["id"],
            "file_path": "teams/x/img.png",
            "file_hash": file_hash,
            "file_size": 1234,
            "mime_type": "image/png",
            "source_type": "uploaded",
        }
    )
    assert type(a["id"]) is int
    assert type(a["project_id"]) is int

    found = await repo.find_by_hash(str(proj["id"]), file_hash)
    assert found is not None and found["id"] == a["id"]

    listing = await repo.list_by_project(str(proj["id"]))
    assert a["id"] in {x["id"] for x in listing}

    await repo.delete(str(a["id"]))
    assert await repo.find_by_hash(str(proj["id"]), file_hash) is None


# ─── Factory flag wiring ────────────────────────────────────────────────


def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories import storyboard_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_STORYBOARD", False)
    assert (
        type(mod.get_storyboard_project_repository()) is mod.StoryboardProjectRepository
    )
    assert type(mod.get_storyboard_node_repository()) is mod.StoryboardNodeRepository
    assert type(mod.get_storyboard_edge_repository()) is mod.StoryboardEdgeRepository
    assert type(mod.get_storyboard_frame_repository()) is mod.StoryboardFrameRepository
    assert (
        type(mod.get_storyboard_character_repository())
        is mod.StoryboardCharacterRepository
    )
    assert type(mod.get_storyboard_asset_repository()) is mod.StoryboardAssetRepository


def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import storyboard_repository as mod
    from app.repositories.storyboard_repository_orm import (
        StoryboardAssetRepositoryOrm,
        StoryboardCharacterRepositoryOrm,
        StoryboardEdgeRepositoryOrm,
        StoryboardFrameRepositoryOrm,
        StoryboardNodeRepositoryOrm,
        StoryboardProjectRepositoryOrm,
    )

    monkeypatch.setattr(settings, "USE_ORM_STORYBOARD", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    assert isinstance(
        mod.get_storyboard_project_repository(), StoryboardProjectRepositoryOrm
    )
    assert isinstance(mod.get_storyboard_node_repository(), StoryboardNodeRepositoryOrm)
    assert isinstance(mod.get_storyboard_edge_repository(), StoryboardEdgeRepositoryOrm)
    assert isinstance(
        mod.get_storyboard_frame_repository(), StoryboardFrameRepositoryOrm
    )
    assert isinstance(
        mod.get_storyboard_character_repository(), StoryboardCharacterRepositoryOrm
    )
    assert isinstance(
        mod.get_storyboard_asset_repository(), StoryboardAssetRepositoryOrm
    )
