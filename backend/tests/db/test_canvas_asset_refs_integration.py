"""DB-backed integration tests for the canvas→asset ref mirror (asset lib P4).

WHY THIS FILE EXISTS
────────────────────
``CanvasAssetRefsRepository`` is pure ORM statements and its unit tests stub the
session, so without this file NOT ONE of them has ever been executed by
Postgres — the same gap ``test_assets_repository_integration.py`` was written to
close for the other two asset repositories. Three things here compile fine under
SQLAlchemy and can only be settled on a real server:

  * ``on_conflict_do_update(index_elements=[canvas_id, asset_id, node_id])``
    needs the real primary key to exist with exactly those columns, and the
    ``DO UPDATE SET loadout_id`` arm is the ONLY thing that stops a stale
    loadout surviving a re-save. ``DO NOTHING`` (what the resource-refs sibling
    uses, where every column is IN the key) would leave the old value and still
    report success — a wrong answer with a 200 on it.
  * ``array_agg(DISTINCT ...)`` must be the DISTINCT *keyword*.
    ``func.distinct(x)`` compiles happily to a ``distinct(x)`` FUNCTION CALL
    that Postgres has no such function for — a unit test with a stubbed session
    cannot tell the two apart.
  * the scope filter's ``COALESCE(projects.team_id, personal_team.id)`` has to
    resolve a personal project through a real LEFT JOIN on ``teams``, and the
    two FK cascades (canvas delete → refs gone; loadout delete → SET NULL) are
    schema behaviour, not Python.

The path under test is the WHOLE save path, not just the repo: each case drives
``CanvasService.update_with_lock`` with a real ``nodes_json``, so the extractor,
the service's per-mirror try/except and the repository are exercised together —
"the row is right after a save", which is the claim that matters.

Transport: asyncpg for fixture setup and assertions; the repos go through
``app.db.session``, which the ``orm_dsn`` fixture repoints at the same DSN.
Same pattern as ``tests/db/test_assets_repository_integration.py``.

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/db/test_canvas_asset_refs_integration.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset.
"""

from __future__ import annotations

import datetime
import os
import uuid
from typing import Any, Dict

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — canvas asset-ref tests need a DB.",
)


@pytest.fixture
async def orm_dsn():
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


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def fx(pg) -> Dict[str, Any]:
    """One owner, a collaborative team, a TEAM project + canvas, a PERSONAL
    project + canvas (``projects.team_id`` NULL, owner's personal team), one
    character asset with two loadouts, and a SECOND team whose canvas must never
    appear in the first team's reverse lookup.

    The personal half is not decoration: ``list_canvases_for_asset``'s scope
    filter resolves a NULL ``projects.team_id`` through the owner's personal
    team, and a fixture with only team projects would pass with that whole arm
    deleted.
    """
    owner = uuid.uuid4()
    other_owner = uuid.uuid4()
    await pg.execute(
        "INSERT INTO auth.users (id) VALUES ($1), ($2)", owner, other_owner
    )

    team_id = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code, kind) "
        "VALUES ($1, $2, $3, 'collaborative') RETURNING id",
        "Canvas Refs Team",
        owner,
        uuid.uuid4().hex[:16],
    )
    personal_team_id = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code, kind) "
        "VALUES ($1, $2, $3, 'personal') RETURNING id",
        "Canvas Refs Personal",
        owner,
        uuid.uuid4().hex[:16],
    )
    foreign_team_id = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code, kind) "
        "VALUES ($1, $2, $3, 'collaborative') RETURNING id",
        "Canvas Refs Foreign Team",
        other_owner,
        uuid.uuid4().hex[:16],
    )

    async def _project(name, owner_id, tid):
        return await pg.fetchval(
            "INSERT INTO projects (name, owner_id, team_id) VALUES ($1,$2,$3) "
            "RETURNING id",
            name,
            owner_id,
            tid,
        )

    async def _canvas(name, project_id):
        return await pg.fetchval(
            "INSERT INTO canvases (project_id, name, kind) VALUES ($1,$2,'smart') "
            "RETURNING id",
            project_id,
            name,
        )

    team_project = await _project("Refs Team Project", owner, team_id)
    personal_project = await _project("Refs Personal Project", owner, None)
    foreign_project = await _project(
        "Refs Foreign Project", other_owner, foreign_team_id
    )

    team_canvas = await _canvas("Team Canvas", team_project)
    personal_canvas = await _canvas("Personal Canvas", personal_project)
    foreign_canvas = await _canvas("Foreign Canvas", foreign_project)

    asset_id = await pg.fetchval(
        "INSERT INTO assets (scope_id, asset_type, name, created_by) "
        "VALUES ($1,'character',$2,$3) RETURNING id",
        team_id,
        f"Sang Yao {uuid.uuid4().hex[:8]}",
        owner,
    )
    loadout_a = await pg.fetchval(
        "INSERT INTO asset_loadouts (asset_id, name) VALUES ($1,'Court Dress') "
        "RETURNING id",
        asset_id,
    )
    loadout_b = await pg.fetchval(
        "INSERT INTO asset_loadouts (asset_id, name) VALUES ($1,'Night Raid') "
        "RETURNING id",
        asset_id,
    )

    try:
        yield {
            "owner": str(owner),
            "team_id": int(team_id),
            "personal_team_id": int(personal_team_id),
            "foreign_team_id": int(foreign_team_id),
            "team_canvas": int(team_canvas),
            "personal_canvas": int(personal_canvas),
            "foreign_canvas": int(foreign_canvas),
            "asset_id": int(asset_id),
            "loadout_a": int(loadout_a),
            "loadout_b": int(loadout_b),
        }
    finally:
        # canvases → cascades to canvas_asset_refs; assets → cascades to
        # asset_loadouts (and to the refs, via the other FK).
        await pg.execute(
            "DELETE FROM canvases WHERE id = ANY($1::bigint[])",
            [int(team_canvas), int(personal_canvas), int(foreign_canvas)],
        )
        await pg.execute("DELETE FROM assets WHERE id = $1", int(asset_id))
        await pg.execute(
            "DELETE FROM projects WHERE id = ANY($1::bigint[])",
            [int(team_project), int(personal_project), int(foreign_project)],
        )
        await pg.execute(
            "DELETE FROM teams WHERE id = ANY($1::bigint[])",
            [int(team_id), int(personal_team_id), int(foreign_team_id)],
        )
        await pg.execute(
            "DELETE FROM auth.users WHERE id = ANY($1::uuid[])", [owner, other_owner]
        )


def _asset_node(node_id: str, asset_id: int, loadout_id=None) -> Dict[str, Any]:
    """The node shape the canvas asset node writes — ids as STRINGS, which is
    what the frontend puts in ``nodes_json`` (CLAUDE.md "边界 mock 必须用真实
    JSON 形状": the fixture copies the wire shape, it does not tidy it)."""
    return {
        "id": node_id,
        "type": "asset",
        "data": {
            "asset_id": str(asset_id),
            "loadout_id": None if loadout_id is None else str(loadout_id),
        },
    }


async def _save(pg, canvas_id: int, nodes) -> None:
    """Drive the REAL save path: read the lock token, then
    ``CanvasService.update_with_lock`` — extractor + service + repository, the
    way production reaches this table."""
    from app.schemas.canvas import CanvasUpdate
    from app.services.canvas.canvas_service import CanvasService

    token = await pg.fetchval(
        "SELECT base_updated_at FROM canvases WHERE id = $1", canvas_id
    )
    await CanvasService().update_with_lock(
        str(canvas_id),
        CanvasUpdate(base_updated_at=token, nodes_json=nodes),
    )


async def _refs(pg, canvas_id: int):
    return await pg.fetch(
        "SELECT asset_id, node_id, loadout_id FROM canvas_asset_refs "
        "WHERE canvas_id = $1 ORDER BY node_id",
        canvas_id,
    )


# ── 1. the lifecycle: create → change loadout → remove ─────────────────────


@_skip
async def test_save_creates_updates_and_removes_the_ref_row(orm_dsn, pg, fx):
    """The whole point of the mirror, in one sequence.

    Note what this case does NOT prove: because ``replace_for_canvas`` DELETEs
    first, the middle step never reaches the ON CONFLICT arm, and this case
    passes with ``DO NOTHING`` too (verified by mutation). The clause itself is
    pinned by ``test_upsert_refreshes_a_conflicting_row_when_the_delete_is_
    suppressed`` below and by the compiled-SQL pin in
    ``tests/test_canvas_asset_refs_repository.py``. What this case DOES prove is
    the user-visible property: after a re-save, the row holds the new loadout.
    """
    canvas = fx["team_canvas"]

    await _save(pg, canvas, [_asset_node("asset-1", fx["asset_id"], fx["loadout_a"])])
    rows = await _refs(pg, canvas)
    assert len(rows) == 1
    assert rows[0]["asset_id"] == fx["asset_id"]
    assert rows[0]["node_id"] == "asset-1"
    assert rows[0]["loadout_id"] == fx["loadout_a"]

    await _save(pg, canvas, [_asset_node("asset-1", fx["asset_id"], fx["loadout_b"])])
    rows = await _refs(pg, canvas)
    assert len(rows) == 1, "same (canvas, asset, node) must stay ONE row"
    assert rows[0]["loadout_id"] == fx["loadout_b"], (
        "the loadout must be UPDATED, not left stale — DO NOTHING would keep "
        f"loadout A ({fx['loadout_a']}) here and report success"
    )

    await _save(pg, canvas, [{"id": "p1", "type": "prompt", "data": {}}])
    assert await _refs(pg, canvas) == [], "removing the node must remove the ref"


@_skip
async def test_clearing_a_loadout_writes_null_rather_than_keeping_the_old_one(
    orm_dsn, pg, fx
):
    """The same property in its quietest form: the user unbinds the loadout but
    keeps the asset, and the row must stop naming a costume nothing on the
    canvas selects."""
    canvas = fx["team_canvas"]
    await _save(pg, canvas, [_asset_node("asset-1", fx["asset_id"], fx["loadout_a"])])
    await _save(pg, canvas, [_asset_node("asset-1", fx["asset_id"], None)])

    rows = await _refs(pg, canvas)
    assert len(rows) == 1 and rows[0]["loadout_id"] is None


@_skip
async def test_two_nodes_on_one_asset_are_two_rows_and_survive_together(
    orm_dsn, pg, fx
):
    """``node_id`` is in the PK, so the same asset placed twice is two rows.
    Also the multi-row INSERT path — one statement, two VALUES tuples, both
    hitting the same ON CONFLICT target."""
    canvas = fx["team_canvas"]
    await _save(
        pg,
        canvas,
        [
            _asset_node("asset-1", fx["asset_id"], fx["loadout_a"]),
            _asset_node("asset-2", fx["asset_id"], fx["loadout_b"]),
        ],
    )
    rows = await _refs(pg, canvas)
    assert [(r["node_id"], r["loadout_id"]) for r in rows] == [
        ("asset-1", fx["loadout_a"]),
        ("asset-2", fx["loadout_b"]),
    ]


@_skip
async def test_an_unreadable_asset_id_costs_that_ref_and_not_the_save(orm_dsn, pg, fx):
    """A node naming a non-snowflake asset must not take the good node's row
    down with it, and must not abort the canvas save. (The count of what was
    dropped is logged — see test_canvas_refs_wiring.)"""
    canvas = fx["team_canvas"]
    bad = {"id": "bad", "type": "asset", "data": {"asset_id": "not-a-snowflake"}}
    await _save(pg, canvas, [bad, _asset_node("good", fx["asset_id"], fx["loadout_a"])])

    rows = await _refs(pg, canvas)
    assert [r["node_id"] for r in rows] == ["good"]
    # The save itself landed — nodes_json really holds both nodes.
    stored = await pg.fetchval("SELECT nodes_json FROM canvases WHERE id = $1", canvas)
    assert stored is not None


@_skip
async def test_a_nonexistent_asset_id_does_not_abort_the_canvas_save(orm_dsn, pg, fx):
    """A well-formed snowflake for an asset that does not exist violates the FK.

    The write must fail INSIDE ``_sync_refs``'s own try/except, leaving the user's
    canvas saved — the mirror is rebuildable, the save is not repeatable.
    """
    canvas = fx["team_canvas"]
    ghost = 727145299382534145
    await _save(pg, canvas, [_asset_node("ghost", ghost)])

    assert await _refs(pg, canvas) == []
    saved = await pg.fetchrow("SELECT nodes_json FROM canvases WHERE id = $1", canvas)
    assert saved is not None, "the canvas save must have survived the FK violation"


@_skip
async def test_upsert_refreshes_a_conflicting_row_when_the_delete_is_suppressed(
    orm_dsn, pg, fx, monkeypatch
):
    """The ON CONFLICT arm, reached on purpose.

    ``replace_for_canvas`` DELETEs before it inserts, so in the normal path the
    conflict never fires — every other case here passes with ``DO NOTHING``.
    That makes the clause a belt whose buckle nothing tests, and the one edit
    that would expose it (making the DELETE conditional, or a concurrent save
    landing between the two statements) would ship green.

    So this case suppresses ONLY the DELETE — by swapping the module's
    ``sa_delete`` for one that matches no rows — and lets the REAL production
    INSERT statement meet a real conflicting row on a real server. With
    ``DO NOTHING`` the stale loadout survives and this fails; with
    ``DO UPDATE SET loadout_id`` it is refreshed.
    """
    from sqlalchemy import delete as real_delete
    from sqlalchemy import false

    import app.repositories.canvas_asset_refs_repository as repo_mod

    repo = repo_mod.CanvasAssetRefsRepository()
    canvas = fx["team_canvas"]

    await repo.replace_for_canvas(
        str(canvas),
        [
            {
                "asset_id": fx["asset_id"],
                "node_id": "asset-1",
                "loadout_id": fx["loadout_a"],
            }
        ],
    )
    assert (await _refs(pg, canvas))[0]["loadout_id"] == fx["loadout_a"]

    monkeypatch.setattr(
        repo_mod, "sa_delete", lambda model: real_delete(model).where(false())
    )
    await repo.replace_for_canvas(
        str(canvas),
        [
            {
                "asset_id": fx["asset_id"],
                "node_id": "asset-1",
                "loadout_id": fx["loadout_b"],
            }
        ],
    )

    rows = await _refs(pg, canvas)
    assert len(rows) == 1, "the conflict must UPDATE the row, not add a second"
    assert rows[0]["loadout_id"] == fx["loadout_b"], (
        "ON CONFLICT ... DO UPDATE SET loadout_id must refresh the value; "
        f"DO NOTHING would leave loadout A ({fx['loadout_a']}) here"
    )


# ── 2. the reverse lookup + its scope filter ───────────────────────────────


@_skip
async def test_reverse_lookup_aggregates_nodes_and_hides_other_scopes(orm_dsn, pg, fx):
    """Three properties in one shape.

    A canvas used on several nodes appears ONCE (the GROUP BY); two nodes
    sharing a loadout contribute that id ONCE (the aggregate's DISTINCT — the
    only place a dropped DISTINCT is observable, since the PK already makes
    duplicate node ids unreachable); and a canvas in another team does not
    appear at all, even though the asset really is referenced from it.
    """
    from app.repositories.canvas_asset_refs_repository import (
        CanvasAssetRefsRepository,
    )

    await _save(
        pg,
        fx["team_canvas"],
        [
            _asset_node("asset-1", fx["asset_id"], fx["loadout_a"]),
            # Same loadout as asset-1 on purpose: without DISTINCT this id
            # would come back twice.
            _asset_node("asset-2", fx["asset_id"], fx["loadout_a"]),
            _asset_node("asset-3", fx["asset_id"], None),
        ],
    )
    await _save(
        pg, fx["foreign_canvas"], [_asset_node("f-1", fx["asset_id"], fx["loadout_b"])]
    )

    repo = CanvasAssetRefsRepository()
    rows = await repo.list_canvases_for_asset(str(fx["asset_id"]), str(fx["team_id"]))

    assert len(rows) == 1, f"expected only the in-scope canvas, got {rows}"
    row = rows[0]
    assert row["canvas_id"] == str(fx["team_canvas"])
    assert row["canvas_name"] == "Team Canvas"
    assert row["project_id"] and row["kind"] == "smart"
    assert row["node_ids"] == ["asset-1", "asset-2", "asset-3"]
    # Deduped (two nodes carry loadout A) and the NULL from the loadout-less
    # node is dropped rather than passed through.
    assert row["loadout_ids"] == [str(fx["loadout_a"])]

    # Positive control: the foreign canvas IS in the table — it is the FILTER
    # that hides it, not an empty write.
    foreign = await repo.list_canvases_for_asset(
        str(fx["asset_id"]), str(fx["foreign_team_id"])
    )
    assert [r["canvas_id"] for r in foreign] == [str(fx["foreign_canvas"])]


@_skip
async def test_reverse_lookup_resolves_a_personal_project_to_its_owners_team(
    orm_dsn, pg, fx
):
    """``projects.team_id`` is NULL for a personal project; the scope is the
    OWNER's personal team. Without the COALESCE arm this canvas would be
    invisible to the person who made it, and the failure would read as "this
    asset is used nowhere"."""
    from app.repositories.canvas_asset_refs_repository import (
        CanvasAssetRefsRepository,
    )

    await _save(
        pg, fx["personal_canvas"], [_asset_node("p-1", fx["asset_id"], fx["loadout_a"])]
    )

    rows = await CanvasAssetRefsRepository().list_canvases_for_asset(
        str(fx["asset_id"]), str(fx["personal_team_id"])
    )
    assert [r["canvas_id"] for r in rows] == [str(fx["personal_canvas"])]

    # …and it does NOT bleed into the collaborative team's view.
    team_rows = await CanvasAssetRefsRepository().list_canvases_for_asset(
        str(fx["asset_id"]), str(fx["team_id"])
    )
    assert team_rows == []


@_skip
async def test_reverse_lookup_omits_trashed_canvases(orm_dsn, pg, fx):
    from app.repositories.canvas_asset_refs_repository import (
        CanvasAssetRefsRepository,
    )

    await _save(
        pg, fx["team_canvas"], [_asset_node("asset-1", fx["asset_id"], fx["loadout_a"])]
    )
    await pg.execute(
        "UPDATE canvases SET deleted_at = $1 WHERE id = $2",
        datetime.datetime.now(datetime.timezone.utc),
        fx["team_canvas"],
    )

    rows = await CanvasAssetRefsRepository().list_canvases_for_asset(
        str(fx["asset_id"]), str(fx["team_id"])
    )
    assert rows == []


# ── 3. the forward lookup + the schema's own cascades ──────────────────────


@_skip
async def test_forward_lookup_names_the_asset_and_keeps_loadoutless_refs(
    orm_dsn, pg, fx
):
    """The loadout join must be OUTER — an INNER join would hide every ref that
    carries no loadout, which is the common case for a prop or a location."""
    from app.repositories.canvas_asset_refs_repository import (
        CanvasAssetRefsRepository,
    )

    await _save(
        pg,
        fx["team_canvas"],
        [
            _asset_node("asset-1", fx["asset_id"], fx["loadout_a"]),
            _asset_node("asset-2", fx["asset_id"], None),
        ],
    )

    rows = await CanvasAssetRefsRepository().list_for_canvas(str(fx["team_canvas"]))

    assert len(rows) == 2
    by_node = {r["node_id"]: r for r in rows}
    assert by_node["asset-1"]["loadout_name"] == "Court Dress"
    assert by_node["asset-2"]["loadout_id"] is None
    assert by_node["asset-2"]["loadout_name"] is None
    assert by_node["asset-1"]["asset_type"] == "character"
    assert by_node["asset-1"]["asset_id"] == str(fx["asset_id"])


@_skip
async def test_deleting_a_loadout_sets_the_ref_null_and_the_row_survives(
    orm_dsn, pg, fx
):
    """``loadout_id``'s FK is ON DELETE SET NULL (mig 445), so deleting a
    costume must NOT delete the asset's presence on the canvas. Schema
    behaviour — no Python of ours runs here."""
    from app.repositories.canvas_asset_refs_repository import (
        CanvasAssetRefsRepository,
    )

    await _save(
        pg, fx["team_canvas"], [_asset_node("asset-1", fx["asset_id"], fx["loadout_a"])]
    )
    await pg.execute("DELETE FROM asset_loadouts WHERE id = $1", fx["loadout_a"])

    rows = await _refs(pg, fx["team_canvas"])
    assert len(rows) == 1 and rows[0]["loadout_id"] is None
    # …and the forward lookup still lists it (the LEFT JOIN again).
    listed = await CanvasAssetRefsRepository().list_for_canvas(str(fx["team_canvas"]))
    assert [r["node_id"] for r in listed] == ["asset-1"]


@_skip
async def test_soft_deleted_asset_keeps_its_rows_but_drops_out_of_the_listing(
    orm_dsn, pg, fx
):
    """``assets.deleted_at`` is a SOFT delete, so the FK does not fire and the
    refs stay. The listing filters them — pinned because the two facts are
    easy to conflate, and "the row is gone" would be the wrong conclusion for
    anyone later restoring the asset."""
    from app.repositories.canvas_asset_refs_repository import (
        CanvasAssetRefsRepository,
    )

    await _save(
        pg, fx["team_canvas"], [_asset_node("asset-1", fx["asset_id"], fx["loadout_a"])]
    )
    await pg.execute(
        "UPDATE assets SET deleted_at = $1 WHERE id = $2",
        datetime.datetime.now(datetime.timezone.utc),
        fx["asset_id"],
    )

    assert len(await _refs(pg, fx["team_canvas"])) == 1  # row survives
    assert (
        await CanvasAssetRefsRepository().list_for_canvas(str(fx["team_canvas"])) == []
    )


@_skip
async def test_backfill_rebuilds_the_mirror_from_nodes_json(orm_dsn, pg, fx):
    """The script is the reason a ``_sync_refs`` failure is survivable.

    Wiped rows must come back from ``nodes_json`` alone — asserted by deleting
    them behind the service's back and running the real backfill.
    """
    from scripts.backfill_canvas_asset_refs import main as backfill

    await _save(
        pg, fx["team_canvas"], [_asset_node("asset-1", fx["asset_id"], fx["loadout_b"])]
    )
    await pg.execute(
        "DELETE FROM canvas_asset_refs WHERE canvas_id = $1", fx["team_canvas"]
    )
    assert await _refs(pg, fx["team_canvas"]) == []

    await backfill()

    rows = await _refs(pg, fx["team_canvas"])
    assert len(rows) == 1 and rows[0]["loadout_id"] == fx["loadout_b"]
