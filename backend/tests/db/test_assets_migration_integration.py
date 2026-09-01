"""DB-backed integration tests for the project-library → assets migration
workflow (``app/workflows/backfill_assets_from_project_entities.py``).

WHY THIS FILE EXISTS
────────────────────
Until it landed, ``_apply`` and ``_reconcile`` had **never run against a
database** — which is exactly why ``_reject_execution_until_p3()`` blocked
``dry_run=False``. That guard is gone; this file is what replaced it. The
planner is pure and unit-tested; everything below the planner is ORM statements
whose unit tests stub the session, so "compiles" was the only thing proven:

  * ``_reconcile`` matches planned keys with a row-wise
    ``(asset_type, lower(name)) IN ((...),(...))`` — a Postgres construct
    SQLAlchemy will happily compile for a backend that cannot run it.
  * ``_apply``'s adoption lookup and ``_reconcile``'s existence lookup must
    agree about what "the same asset" is. They are written independently, in
    different functions, and the pre-P3 mismatch (``_apply`` adopting ANY
    source, ``_reconcile`` counting only ``source='migrated'``) is invisible to
    both unit suites — case 2 below reproduces the exact under-report.
  * idempotency is a property of the SERVER (the partial unique index and the
    ``(asset_id, project_id)`` PK), not of our Python: case 1 runs apply twice.

Point it at any CI-way Postgres (ci_bootstrap.sql → schema_baseline.sql →
migrations above the watermark, which includes 445/446):

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/db/test_assets_migration_integration.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset. Every test builds its own
team/projects/legacy rows with fresh ids and tears them down in a ``finally``.

The plan under test is built from the fixture's OWN rows, not from
``_load_inputs()`` — that helper reads project_characters/project_lib_entities
GLOBALLY (deliberately: a partial scan would emit wrong merge groups), so a plan
built from it would depend on whatever else lives in the shared drift database.
Case 5 exercises ``_load_inputs`` itself, which is where its ORM projection and
the personal/unknown project split get their real execution.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — migration integration tests need a DB.",
)


def _uniq(prefix: str) -> str:
    return f"{prefix} {uuid.uuid4().hex[:12]}"


@pytest.fixture
async def orm_dsn():
    """Repoint the ORM engine (app.db.session read/write scope) at the test DSN
    for the duration of a test, then restore + dispose so no other test inherits
    a stray engine."""
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
    """One team, one owner, three projects in it, plus one personal project
    (``team_id IS NULL``) so the skip bucket has something real to skip.

    Legacy rows are NOT seeded here — each case seeds the ones it needs, because
    what is in ``project_characters`` / ``project_lib_entities`` is the input
    under test.
    """
    user_id = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
    team_id = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) "
        "RETURNING id",
        "Asset Migration Test Team",
        user_id,
        uuid.uuid4().hex[:16],
    )
    project_ids: List[int] = []
    for n in range(3):
        pid = await pg.fetchval(
            "INSERT INTO projects (name, owner_id, team_id) VALUES ($1, $2, $3) "
            "RETURNING id",
            f"Migration Test Project {n}",
            user_id,
            team_id,
        )
        project_ids.append(int(pid))
    personal_project_id = int(
        await pg.fetchval(
            "INSERT INTO projects (name, owner_id, team_id) VALUES ($1, $2, NULL) "
            "RETURNING id",
            "Migration Test Personal Project",
            user_id,
        )
    )
    all_projects = project_ids + [personal_project_id]

    try:
        yield {
            "user_id": str(user_id),
            "team_id": int(team_id),
            "project_ids": project_ids,
            "personal_project_id": personal_project_id,
            "all_project_ids": all_projects,
        }
    finally:
        await pg.execute(
            "DELETE FROM project_characters WHERE project_id = ANY($1::bigint[])",
            all_projects,
        )
        await pg.execute(
            "DELETE FROM project_lib_entities WHERE project_id = ANY($1::bigint[])",
            all_projects,
        )
        # assets cascades to asset_loadouts / asset_project_refs / asset_files.
        await pg.execute(
            "DELETE FROM assets WHERE scope_id = $1 OR created_by = $2",
            int(team_id),
            user_id,
        )
        await pg.execute(
            "DELETE FROM projects WHERE id = ANY($1::bigint[])", all_projects
        )
        await pg.execute("DELETE FROM teams WHERE id = $1", int(team_id))
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)


async def _seed_character(pg, project_id: int, name: str, **kw) -> int:
    return int(
        await pg.fetchval(
            "INSERT INTO project_characters "
            "(project_id, name, role_tag, description, tags, portrait_url) "
            "VALUES ($1, $2, $3, $4, $5::jsonb, $6) RETURNING id",
            project_id,
            name,
            kw.get("role_tag", ""),
            kw.get("description", ""),
            kw.get("tags", "{}"),
            kw.get("portrait_url"),
        )
    )


async def _seed_entity(pg, project_id: int, entity_type: str, name: str, **kw) -> int:
    return int(
        await pg.fetchval(
            "INSERT INTO project_lib_entities "
            "(project_id, entity_type, name, badge_tag, description, tags, cover_url) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7) RETURNING id",
            project_id,
            entity_type,
            name,
            kw.get("badge_tag", ""),
            kw.get("description", ""),
            kw.get("tags", "{}"),
            kw.get("cover_url"),
        )
    )


async def _rows_for(pg, project_ids: List[int]):
    """The fixture's own legacy rows, shaped exactly like ``_load_inputs`` shapes
    them (all columns, native types)."""
    chars = [
        dict(r)
        for r in await pg.fetch(
            "SELECT * FROM project_characters WHERE project_id = ANY($1::bigint[]) "
            "ORDER BY id",
            project_ids,
        )
    ]
    ents = [
        dict(r)
        for r in await pg.fetch(
            "SELECT * FROM project_lib_entities WHERE project_id = ANY($1::bigint[]) "
            "ORDER BY id",
            project_ids,
        )
    ]
    for r in chars + ents:
        # asyncpg hands JSONB back as text; plan_migration expects the dict the
        # ORM projection gives it.
        if isinstance(r.get("tags"), str):
            import json

            r["tags"] = json.loads(r["tags"])
    return chars, ents


def _plan_for(fx, chars, ents):
    from app.workflows.backfill_assets_from_project_entities import plan_migration

    project_team = {pid: fx["team_id"] for pid in fx["project_ids"]}
    return plan_migration(chars, ents, project_team, {fx["personal_project_id"]})


async def _live_assets(pg, team_id: int):
    return await pg.fetch(
        "SELECT id, name, asset_type, source, attrs FROM assets "
        "WHERE scope_id = $1 AND deleted_at IS NULL ORDER BY name",
        team_id,
    )


# ── 1. apply twice → same rows, reconciliation clean both times ─────────────


@_skip
async def test_apply_is_idempotent_and_reconciles_clean_both_times(orm_dsn, pg, fx):
    """The plan's key demand. Re-running must take the ``existing`` branch for
    every row and add no duplicate project refs — a property of the partial
    unique index and the ``(asset_id, project_id)`` PK, not of our Python.
    """
    from app.workflows.backfill_assets_from_project_entities import _apply, _reconcile

    p0, p1, _p2 = fx["project_ids"]
    name = _uniq("Sang Yao")
    loc = _uniq("Rain Alley")
    # Same character in two projects → ONE asset with TWO project refs.
    await _seed_character(pg, p0, name, role_tag="lead", description="short")
    await _seed_character(pg, p1, name, description="a much longer description")
    await _seed_entity(pg, p0, "location", loc)

    chars, ents = await _rows_for(pg, fx["project_ids"])
    plan = _plan_for(fx, chars, ents)
    assert plan["counts"]["assets"] == 2
    assert plan["counts"]["merges"] == 1

    first = await _apply(plan, fx["user_id"])
    assert first == {"created": 2, "existing": 0, "project_refs_added": 3}
    report = await _reconcile(plan)
    assert report["assets_present"] == report["assets_expected"] == 2
    assert report["project_refs_present"] == report["project_refs_expected"] == 3
    assert report["assets_missing"] == [] and report["project_refs_missing"] == []

    after_first = await _live_assets(pg, fx["team_id"])
    assert len(after_first) == 2
    merged = next(r for r in after_first if r["name"] == name)
    assert merged["source"] == "migrated"
    # The merge really happened on the server: both legacy rows are recorded,
    # and ``merged_from`` is non-empty only for a genuine multi-row group.
    import json as _json

    attrs = (
        _json.loads(merged["attrs"])
        if isinstance(merged["attrs"], str)
        else merged["attrs"]
    )
    assert len(attrs["legacy_ids"]) == 2
    assert len(attrs["merged_from"]) == 2

    second = await _apply(plan, fx["user_id"])
    assert second == {"created": 0, "existing": 2, "project_refs_added": 0}
    report2 = await _reconcile(plan)
    assert report2["assets_present"] == 2
    assert report2["project_refs_present"] == 3
    assert report2["assets_missing"] == [] and report2["project_refs_missing"] == []

    after_second = await _live_assets(pg, fx["team_id"])
    assert [r["id"] for r in after_second] == [r["id"] for r in after_first]
    ref_count = await pg.fetchval(
        "SELECT count(*) FROM asset_project_refs WHERE asset_id = ANY($1::bigint[])",
        [int(r["id"]) for r in after_second],
    )
    assert ref_count == 3
    # Characters get a Default loadout on CREATE; running twice must not add a
    # second one (uq_loadout_default is partial on is_default).
    loadouts = await pg.fetchval(
        "SELECT count(*) FROM asset_loadouts WHERE asset_id = $1", int(merged["id"])
    )
    assert loadouts == 1


# ── 2. adoption: the exact under-report the old source filter produced ──────


@_skip
async def test_reconcile_counts_an_adopted_user_created_asset(orm_dsn, pg, fx):
    """``_apply`` claims a same-name asset WHATEVER its source. Ground truth for
    the bug this task fixes: after a correct run, the number of
    ``source='migrated'`` rows is strictly LESS than the plan's asset count — so
    the pre-P3 ``_reconcile`` (which counted exactly that) raised on a run that
    had done everything right.
    """
    from app.workflows.backfill_assets_from_project_entities import (
        _apply,
        _reconcile,
        reconcile_counts,
    )

    p0, p1, _p2 = fx["project_ids"]
    adopted = _uniq("Lin Mu")
    fresh = _uniq("Ye Qing")
    # A hand-made asset that already exists, differing in case to prove the
    # match is the lower(name) one the unique index uses.
    existing_id = int(
        await pg.fetchval(
            "INSERT INTO assets (scope_id, asset_type, name, source, created_by) "
            "VALUES ($1, 'character', $2, 'manual', $3) RETURNING id",
            fx["team_id"],
            adopted.upper(),
            uuid.UUID(fx["user_id"]),
        )
    )
    await _seed_character(pg, p0, adopted)
    await _seed_character(pg, p1, fresh)

    chars, ents = await _rows_for(pg, fx["project_ids"])
    plan = _plan_for(fx, chars, ents)
    assert plan["counts"]["assets"] == 2

    applied = await _apply(plan, fx["user_id"])
    assert applied == {"created": 1, "existing": 1, "project_refs_added": 2}

    # The adopted row is the SAME row, still 'manual' — not a new migrated copy.
    still_manual = await pg.fetchrow(
        "SELECT id, source FROM assets WHERE id = $1", existing_id
    )
    assert still_manual["source"] == "manual"
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM assets WHERE scope_id = $1 AND deleted_at IS NULL",
            fx["team_id"],
        )
        == 2
    )

    # GROUND TRUTH for the fix: the old semantics' number.
    migrated_only = await pg.fetchval(
        "SELECT count(*) FROM assets WHERE scope_id = $1 AND deleted_at IS NULL "
        "AND source = 'migrated'",
        fx["team_id"],
    )
    assert migrated_only == 1
    assert migrated_only < plan["counts"]["assets"], (
        "this case only proves anything while an adopted asset keeps its own "
        "source — if that changes, the regression it guards is gone"
    )

    report = await _reconcile(plan)
    assert report["assets_present"] == report["assets_expected"] == 2
    assert report["project_refs_present"] == report["project_refs_expected"] == 2
    # And the arithmetic half agrees — i.e. the workflow would NOT have raised.
    assert (
        reconcile_counts(
            report["assets_expected"],
            report["assets_present"],
            report["project_refs_expected"],
            report["project_refs_present"],
        )
        is None
    )


# ── 3. a real miss is named, not just counted ──────────────────────────────


@_skip
async def test_reconcile_names_the_key_that_is_missing(orm_dsn, pg, fx):
    """Negative control for cases 1–2: reconciliation is not a rubber stamp.
    Soft-deleting one asset after apply must make it missing BY NAME (the
    partial unique index's ``WHERE deleted_at IS NULL`` is what makes a
    soft-deleted row invisible to both apply and reconcile)."""
    from app.workflows.backfill_assets_from_project_entities import (
        _apply,
        _reconcile,
        reconcile_counts,
    )

    p0, p1, _p2 = fx["project_ids"]
    kept = _uniq("Kept One")
    gone = _uniq("Gone One")
    await _seed_character(pg, p0, kept)
    await _seed_character(pg, p1, gone)

    chars, ents = await _rows_for(pg, fx["project_ids"])
    plan = _plan_for(fx, chars, ents)
    await _apply(plan, fx["user_id"])
    await pg.execute(
        "UPDATE assets SET deleted_at = now() WHERE scope_id = $1 AND name = $2",
        fx["team_id"],
        gone,
    )

    report = await _reconcile(plan)
    assert report["assets_present"] == 1
    assert report["assets_missing"] == [
        {
            "scope_id": str(fx["team_id"]),
            "asset_type": "character",
            "name": gone,
        }
    ]
    assert report["assets_missing_count"] == 1
    assert report["project_refs_missing"] == [
        {"name": gone, "project_id": str(p1), "asset_id": None}
    ]
    with pytest.raises(RuntimeError) as e:
        reconcile_counts(
            report["assets_expected"],
            report["assets_present"],
            report["project_refs_expected"],
            report["project_refs_present"],
            missing_assets=report["assets_missing"],
            missing_refs=report["project_refs_missing"],
        )
    assert gone in str(e.value)


# ── 4. refs outside the plan must not inflate the actual count ──────────────


@_skip
async def test_refs_outside_the_plan_do_not_fail_reconciliation(orm_dsn, pg, fx):
    """An adopted asset can already be linked to projects this plan never
    mentions. Counting "all refs of these assets" would push actual PAST
    expected — a failure mode that only appears on real data."""
    from app.workflows.backfill_assets_from_project_entities import _apply, _reconcile

    p0, _p1, p2 = fx["project_ids"]
    name = _uniq("Wide Reach")
    await _seed_character(pg, p0, name)

    chars, ents = await _rows_for(pg, fx["project_ids"])
    plan = _plan_for(fx, chars, ents)
    await _apply(plan, fx["user_id"])
    asset_id = int(
        await pg.fetchval(
            "SELECT id FROM assets WHERE scope_id = $1 AND name = $2",
            fx["team_id"],
            name,
        )
    )
    # A pre-existing link the migration never asked for.
    await pg.execute(
        "INSERT INTO asset_project_refs (asset_id, project_id) VALUES ($1, $2)",
        asset_id,
        p2,
    )
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM asset_project_refs WHERE asset_id = $1", asset_id
        )
        == 2
    )

    report = await _reconcile(plan)
    assert report["project_refs_expected"] == 1
    assert report["project_refs_present"] == 1
    assert report["project_refs_missing"] == []


# ── 5. _load_inputs against the real schema ────────────────────────────────


@_skip
async def test_load_inputs_reads_the_legacy_tables_and_splits_projects(orm_dsn, pg, fx):
    """``_load_inputs`` projects every column of two legacy tables and splits
    projects into team-owned vs personal (``team_id IS NULL``). Its assertions
    are scoped to the fixture's own rows so other rows in a shared drift DB
    cannot make it pass or fail."""
    from app.workflows.backfill_assets_from_project_entities import _load_inputs

    p0 = fx["project_ids"][0]
    cname = _uniq("Loaded Char")
    ename = _uniq("Loaded Prop")
    cid = await _seed_character(pg, p0, cname, role_tag="lead", tags='{"k": "v"}')
    eid = await _seed_entity(pg, fx["personal_project_id"], "prop", ename)

    chars, ents, project_team, personal = await _load_inputs()

    mine = next(c for c in chars if int(c["id"]) == cid)
    assert mine["name"] == cname
    assert mine["role_tag"] == "lead"
    assert mine["tags"] == {"k": "v"}  # JSONB really arrives as a dict
    assert next(e for e in ents if int(e["id"]) == eid)["entity_type"] == "prop"

    assert project_team[p0] == fx["team_id"]
    assert fx["personal_project_id"] in personal
    assert fx["personal_project_id"] not in project_team
