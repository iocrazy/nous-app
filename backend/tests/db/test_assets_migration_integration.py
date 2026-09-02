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
``_load_inputs()`` — that helper reads ``_legacy_project_characters`` /
``_legacy_project_lib_entities`` (renamed by mig 447; the ORM models follow)
GLOBALLY (deliberately: a partial scan would emit wrong merge groups), so a
plan built from it would depend on whatever else lives in the shared drift
database.
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
    """One team, one owner, three projects in it, plus a SECOND team that owns
    nothing — it exists so cross-scope cases have a real other tenant to be
    dropped in favour of, rather than a made-up id that any lookup would miss
    for the wrong reason.

    Plus the two shapes of team-less project P3 has to tell apart:

      * ``personal_project_id`` / ``personal_project_id_2`` — owned by a user
        who HAS a personal team (``personal_team_id``, ``kind='personal'``).
        These MAP: their rows migrate into that team, and two of them exist so
        the cross-project merge can be shown to work the same way it does for
        two projects inside one collaborative team.
      * ``orphan_project_id`` — owned by a DIFFERENT user with no personal team
        row at all. Nothing to map it to, so it is the one that still skips.

    The owner is deliberately given a real `kind='personal'` team here rather
    than letting the mapping fall through: ``uq_teams_owner_personal`` makes
    that at most one row, which is what lets ``_load_inputs`` batch the lookup
    instead of calling the one-user-per-query helper.

    Legacy rows are NOT seeded here — each case seeds the ones it needs,
    because what is in ``_legacy_project_characters`` /
    ``_legacy_project_lib_entities`` is the input under test.
    """
    user_id = uuid.uuid4()
    orphan_user_id = uuid.uuid4()
    await pg.execute(
        "INSERT INTO auth.users (id) VALUES ($1), ($2)", user_id, orphan_user_id
    )
    team_id = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) "
        "RETURNING id",
        "Asset Migration Test Team",
        user_id,
        uuid.uuid4().hex[:16],
    )
    other_team_id = int(
        await pg.fetchval(
            "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) "
            "RETURNING id",
            "Asset Migration Other Team",
            user_id,
            uuid.uuid4().hex[:16],
        )
    )
    personal_team_id = int(
        await pg.fetchval(
            "INSERT INTO teams (name, owner_id, invite_code, kind) "
            "VALUES ($1, $2, $3, 'personal') RETURNING id",
            "Asset Migration Personal Team",
            user_id,
            uuid.uuid4().hex[:16],
        )
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
    personal_project_ids: List[int] = []
    for n in range(2):
        personal_project_ids.append(
            int(
                await pg.fetchval(
                    "INSERT INTO projects (name, owner_id, team_id) "
                    "VALUES ($1, $2, NULL) RETURNING id",
                    f"Migration Test Personal Project {n}",
                    user_id,
                )
            )
        )
    orphan_project_id = int(
        await pg.fetchval(
            "INSERT INTO projects (name, owner_id, team_id) VALUES ($1, $2, NULL) "
            "RETURNING id",
            "Migration Test Ownerless Project",
            orphan_user_id,
        )
    )
    all_projects = project_ids + personal_project_ids + [orphan_project_id]
    all_teams = [int(team_id), other_team_id, personal_team_id]

    try:
        yield {
            "user_id": str(user_id),
            "orphan_user_id": str(orphan_user_id),
            "team_id": int(team_id),
            "other_team_id": other_team_id,
            "personal_team_id": personal_team_id,
            "project_ids": project_ids,
            "personal_project_id": personal_project_ids[0],
            "personal_project_id_2": personal_project_ids[1],
            "orphan_project_id": orphan_project_id,
            "all_project_ids": all_projects,
            "all_team_ids": all_teams,
        }
    finally:
        await pg.execute(
            "DELETE FROM _legacy_project_characters "
            "WHERE project_id = ANY($1::bigint[])",
            all_projects,
        )
        await pg.execute(
            "DELETE FROM _legacy_project_lib_entities "
            "WHERE project_id = ANY($1::bigint[])",
            all_projects,
        )
        # generated_media has no FK to teams, so it is deleted by hand and
        # BEFORE the assets it points at (source_asset_id is ON DELETE SET NULL
        # — harmless, but leaving rows behind would let one case's global scan
        # counts leak into the next).
        await pg.execute(
            "DELETE FROM generated_media WHERE scope_id = ANY($1::bigint[])",
            all_teams,
        )
        # assets cascades to asset_loadouts / asset_project_refs / asset_files.
        await pg.execute(
            "DELETE FROM assets WHERE scope_id = ANY($1::bigint[]) OR created_by = $2",
            all_teams,
            user_id,
        )
        # canvases cascade with their project; projects/resource_items cascade
        # with the team, but resources themselves do not (they are owned by the
        # user, not the scope), so drop the items first and then the rows.
        await pg.execute(
            "DELETE FROM resource_items WHERE scope_id = ANY($1::bigint[])",
            all_teams,
        )
        await pg.execute("DELETE FROM resources WHERE creator_id = $1", user_id)
        await pg.execute(
            "DELETE FROM projects WHERE id = ANY($1::bigint[])", all_projects
        )
        await pg.execute("DELETE FROM teams WHERE id = ANY($1::bigint[])", all_teams)
        await pg.execute(
            "DELETE FROM auth.users WHERE id = ANY($1::uuid[])",
            [user_id, orphan_user_id],
        )


async def _seed_character(pg, project_id: int, name: str, **kw) -> int:
    return int(
        await pg.fetchval(
            "INSERT INTO _legacy_project_characters "
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
            "INSERT INTO _legacy_project_lib_entities "
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
            "SELECT * FROM _legacy_project_characters "
            "WHERE project_id = ANY($1::bigint[]) "
            "ORDER BY id",
            project_ids,
        )
    ]
    ents = [
        dict(r)
        for r in await pg.fetch(
            "SELECT * FROM _legacy_project_lib_entities "
            "WHERE project_id = ANY($1::bigint[]) "
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


def _project_team(fx):
    """The project → SCOPE map ``_load_inputs`` would build for this fixture:
    team projects to their team, personal projects to their OWNER's personal
    team (the P3 mapping), and the ownerless one absent — it has no scope."""
    return {
        **{pid: fx["team_id"] for pid in fx["project_ids"]},
        fx["personal_project_id"]: fx["personal_team_id"],
        fx["personal_project_id_2"]: fx["personal_team_id"],
    }


def _plan_for(fx, chars, ents):
    from app.workflows.backfill_assets_from_project_entities import plan_migration

    return plan_migration(chars, ents, _project_team(fx), {fx["orphan_project_id"]})


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
    assert first["counts"] == {"created": 2, "existing": 0, "project_refs_added": 3}
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
    assert second["counts"] == {"created": 0, "existing": 2, "project_refs_added": 0}
    # The legacy→asset indexes must survive a re-run, where NOTHING is created.
    # They are built from the plan (not from ``attrs.legacy_ids``) precisely so
    # the "existing" branch still populates them — step 5 maps generations on a
    # second run exactly as it would have on the first.
    assert second["asset_id_by_ref"] == first["asset_id_by_ref"]
    assert second["asset_id_by_key"] == first["asset_id_by_key"]
    assert len(second["asset_id_by_ref"]) == 3  # two merged chars + one location
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
    assert applied["counts"] == {"created": 1, "existing": 1, "project_refs_added": 2}
    # ADOPTION never writes ``attrs``, so the hand-made asset has no
    # ``legacy_ids`` to re-derive from — the index has to come from the plan,
    # or every generation stamped with that character's legacy id would look
    # unmatched forever.
    assert (
        applied["asset_id_by_ref"][
            ("project_characters", next(c["id"] for c in chars if c["name"] == adopted))
        ]
        == existing_id
    )

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
async def test_load_inputs_reads_the_legacy_tables_and_resolves_every_scope(
    orm_dsn, pg, fx
):
    """``_load_inputs`` projects every column of two legacy tables and resolves
    each project to its ASSET SCOPE: a team project to its team, a personal
    project to its OWNER's personal team (the P3 mapping, running here against
    the real ``uq_teams_owner_personal`` index rather than a stub), and an
    ownerless one to nothing at all.

    The batched ``teams`` lookup is the mirror of
    ``resources_service._resolve_personal_team_id``; this is the only place it
    executes against Postgres, so a UUID-vs-text comparison that SQLAlchemy
    compiles but PG rejects would surface here and nowhere else.

    Assertions are scoped to the fixture's own rows so other rows in a shared
    drift DB cannot make it pass or fail.
    """
    from app.workflows.backfill_assets_from_project_entities import _load_inputs

    p0 = fx["project_ids"][0]
    cname = _uniq("Loaded Char")
    ename = _uniq("Loaded Prop")
    cid = await _seed_character(pg, p0, cname, role_tag="lead", tags='{"k": "v"}')
    eid = await _seed_entity(pg, fx["personal_project_id"], "prop", ename)

    chars, ents, project_team, unmappable = await _load_inputs()

    mine = next(c for c in chars if int(c["id"]) == cid)
    assert mine["name"] == cname
    assert mine["role_tag"] == "lead"
    assert mine["tags"] == {"k": "v"}  # JSONB really arrives as a dict
    assert next(e for e in ents if int(e["id"]) == eid)["entity_type"] == "prop"

    assert project_team[p0] == fx["team_id"]
    # Both personal projects of the same owner resolve to the SAME scope —
    # which is what makes the cross-project merge below possible at all.
    assert project_team[fx["personal_project_id"]] == fx["personal_team_id"]
    assert project_team[fx["personal_project_id_2"]] == fx["personal_team_id"]
    assert fx["personal_project_id"] not in unmappable

    # The residue: no personal team for that owner, so no scope, so no guess.
    assert fx["orphan_project_id"] in unmappable
    assert fx["orphan_project_id"] not in project_team


@_skip
async def test_the_scope_load_inputs_picks_is_the_one_the_read_route_resolves(
    orm_dsn, pg, fx
):
    """The mapping is only correct if it agrees with the route the workspace
    pages actually call. ``assets_router._project_scope_id`` is that route's
    resolver; this asserts the two answers are the same row, so a future edit
    to either one cannot drift the migration away from the UI's read.
    """
    from app.api.assets_router import _project_scope_id
    from app.workflows.backfill_assets_from_project_entities import _load_inputs

    _chars, _ents, project_team, _unmappable = await _load_inputs()
    for key in ("personal_project_id", "personal_project_id_2"):
        assert project_team[fx[key]] == int(await _project_scope_id(fx[key])), key
    # And for a team project, where the two agree trivially — stated so the
    # assertion above is not the only thing keeping them aligned.
    p0 = fx["project_ids"][0]
    assert project_team[p0] == int(await _project_scope_id(p0))


# ── seeds for steps 1/2-tail, 4 and 5 ───────────────────────────────────────


async def _seed_resource(pg, user_id, scope_id: int, filename: str) -> int:
    """A resources row plus the resource_items row that puts it in a scope.

    Scope membership lives on ``resource_items``, not on ``resources`` — which
    is exactly why the cover step joins the two: a resource id alone says
    nothing about which team may see the file.
    """
    rid = int(
        await pg.fetchval(
            "INSERT INTO resources (creator_id, source_type, filename, file_path) "
            "VALUES ($1, 'upload', $2, $3) RETURNING id",
            uuid.UUID(str(user_id)),
            filename,
            f"/data/{filename}",
        )
    )
    await pg.execute(
        "INSERT INTO resource_items (resource_id, scope_id, added_by) "
        "VALUES ($1, $2, $3)",
        rid,
        scope_id,
        uuid.UUID(str(user_id)),
    )
    return rid


async def _seed_canvas(pg, project_id: int, kind: str, name: str) -> int:
    return int(
        await pg.fetchval(
            "INSERT INTO canvases (project_id, kind, name) VALUES ($1, $2, $3) "
            "RETURNING id",
            project_id,
            kind,
            name,
        )
    )


async def _seed_genmedia(pg, user_id, scope_id: int, **kw) -> int:
    return int(
        await pg.fetchval(
            "INSERT INTO generated_media "
            "(scope_id, creator_id, media_kind, file_path, origin_kind, params, "
            " promoted_resource_id, review_state) "
            "VALUES ($1, $2, 'image', $3, 'canvas_generation', $4::jsonb, $5, $6) "
            "RETURNING id",
            scope_id,
            uuid.UUID(str(user_id)),
            kw.get("file_path", "/data/gen.png"),
            kw.get("params", "{}"),
            kw.get("promoted_resource_id"),
            kw.get("review_state", "unreviewed"),
        )
    )


# ── 6. covers: id-bearing URLs resolve, everything else stays legacy ────────


@_skip
async def test_cover_urls_resolve_in_scope_and_attach_to_the_unsorted_slot(
    orm_dsn, pg, fx
):
    """Spec §4 step 1/2 tail. Four legacy covers, one per outcome:

      * an ``/api/v1/resources/{id}/cover`` URL for a file in the team's own
        scope → resolved: ``cover_file_id`` set AND attached to ``unsorted``;
      * a ``/media/{id}`` URL, the other shape this app builds → resolved;
      * a URL naming a real resource that belongs to ANOTHER team → dropped.
        This is the case a "close enough" matcher gets wrong, and getting it
        wrong publishes another tenant's file on a character sheet;
      * a bare filename → dropped, because guessing at a path tail can attach
        the wrong image and produce no error anywhere.

    Then the whole thing runs a second time: nothing may change.
    """
    from app.workflows.backfill_assets_from_project_entities import (
        _apply,
        _run_extra_steps,
    )

    p0 = fx["project_ids"][0]
    own_a = await _seed_resource(pg, fx["user_id"], fx["team_id"], "sheet-a.png")
    own_b = await _seed_resource(pg, fx["user_id"], fx["team_id"], "sheet-b.png")
    foreign = await _seed_resource(pg, fx["user_id"], fx["other_team_id"], "theirs.png")

    n_resources = _uniq("Cover Resources")
    n_media = _uniq("Cover Media")
    n_cross = _uniq("Cover Cross Scope")
    n_bare = _uniq("Cover Bare Name")
    await _seed_character(
        pg,
        p0,
        n_resources,
        portrait_url=f"https://api.x/api/v1/resources/{own_a}/cover",
    )
    await _seed_character(
        pg, p0, n_media, portrait_url=f"https://api.x/media/{own_b}?token=abc"
    )
    await _seed_character(
        pg, p0, n_cross, portrait_url=f"https://api.x/api/v1/resources/{foreign}/cover"
    )
    await _seed_character(pg, p0, n_bare, portrait_url="portrait.jpg")

    chars, ents = await _rows_for(pg, fx["project_ids"])
    plan = _plan_for(fx, chars, ents)
    applied = await _apply(plan, fx["user_id"])
    out = await _run_extra_steps(
        plan,
        {pid: fx["team_id"] for pid in fx["project_ids"]},
        dry_run=False,
        run_user_id=fx["user_id"],
        asset_id_by_key=applied["asset_id_by_key"],
        asset_id_by_ref=applied["asset_id_by_ref"],
    )
    assert out["covers"] == {
        "covers_with_url": 4,
        "covers_resolved": 2,
        "covers_unresolved": 2,
        "covers_attached": 2,
        "covers_cover_set": 2,
    }

    async def _cover_of(name):
        return await pg.fetchval(
            "SELECT cover_file_id FROM assets WHERE scope_id = $1 AND name = $2",
            fx["team_id"],
            name,
        )

    assert await _cover_of(n_resources) == own_a
    assert await _cover_of(n_media) == own_b
    # The two that did not resolve keep a NULL cover and their legacy URL, so a
    # human can still see what the row pointed at.
    assert await _cover_of(n_cross) is None
    assert await _cover_of(n_bare) is None
    for name, url_fragment in ((n_cross, str(foreign)), (n_bare, "portrait.jpg")):
        attrs = await pg.fetchval(
            "SELECT attrs::text FROM assets WHERE scope_id = $1 AND name = $2",
            fx["team_id"],
            name,
        )
        assert url_fragment in attrs

    slots = await pg.fetch(
        "SELECT af.resource_id, af.slot FROM asset_files af JOIN assets a "
        "ON a.id = af.asset_id WHERE a.scope_id = $1 ORDER BY af.resource_id",
        fx["team_id"],
    )
    assert sorted((int(r["resource_id"]), r["slot"]) for r in slots) == sorted(
        [(own_a, "unsorted"), (own_b, "unsorted")]
    )

    # Both write counts are ROWCOUNTS, so a re-run reports ZERO — the same way
    # the canvas and genmedia buckets already do. Counting loop iterations (the
    # pre-fix-wave form) reported 2 forever, which reads as "this run attached
    # two covers" on a run that wrote nothing at all. The read-side buckets are
    # unchanged, because what the plan resolves has not changed.
    again = await _run_extra_steps(
        plan,
        {pid: fx["team_id"] for pid in fx["project_ids"]},
        dry_run=False,
        run_user_id=fx["user_id"],
        asset_id_by_key=applied["asset_id_by_key"],
        asset_id_by_ref=applied["asset_id_by_ref"],
    )
    assert again["covers"] == {
        "covers_with_url": 4,
        "covers_resolved": 2,
        "covers_unresolved": 2,
        "covers_attached": 0,
        "covers_cover_set": 0,
    }
    # The foreign file was never attached to anything of ours — the assertion
    # that makes "dropped" mean dropped rather than "counted but attached".
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM asset_files WHERE resource_id = $1", foreign
        )
        == 0
    )

    # No second attachment row either — the zero above is the write really not
    # happening, not the counter having stopped counting.
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM asset_files af JOIN assets a ON a.id = af.asset_id "
            "WHERE a.scope_id = $1",
            fx["team_id"],
        )
        == 2
    )


@_skip
async def test_a_cover_the_user_changed_later_is_not_overwritten(orm_dsn, pg, fx):
    """``cover_file_id`` is set only while it is still NULL. A user who picked a
    different cover after the first run outranks a URL from the legacy row —
    otherwise every re-run of an idempotent backfill silently reverts them."""
    from app.workflows.backfill_assets_from_project_entities import (
        _apply,
        _run_extra_steps,
    )

    p0 = fx["project_ids"][0]
    legacy_file = await _seed_resource(pg, fx["user_id"], fx["team_id"], "legacy.png")
    chosen_file = await _seed_resource(pg, fx["user_id"], fx["team_id"], "chosen.png")
    name = _uniq("Kept Choice")
    await _seed_character(
        pg, p0, name, portrait_url=f"/api/v1/resources/{legacy_file}/cover"
    )

    chars, ents = await _rows_for(pg, fx["project_ids"])
    plan = _plan_for(fx, chars, ents)
    applied = await _apply(plan, fx["user_id"])
    project_team = {pid: fx["team_id"] for pid in fx["project_ids"]}
    await _run_extra_steps(
        plan,
        project_team,
        dry_run=False,
        run_user_id=fx["user_id"],
        asset_id_by_key=applied["asset_id_by_key"],
        asset_id_by_ref=applied["asset_id_by_ref"],
    )
    asset_id = int(
        await pg.fetchval(
            "SELECT id FROM assets WHERE scope_id = $1 AND name = $2",
            fx["team_id"],
            name,
        )
    )
    assert (
        await pg.fetchval("SELECT cover_file_id FROM assets WHERE id = $1", asset_id)
        == legacy_file
    )

    await pg.execute(
        "UPDATE assets SET cover_file_id = $1 WHERE id = $2", chosen_file, asset_id
    )
    await _run_extra_steps(
        plan,
        project_team,
        dry_run=False,
        run_user_id=fx["user_id"],
        asset_id_by_key=applied["asset_id_by_key"],
        asset_id_by_ref=applied["asset_id_by_ref"],
    )
    assert (
        await pg.fetchval("SELECT cover_file_id FROM assets WHERE id = $1", asset_id)
        == chosen_file
    )


# ── 7. step 4: entity canvases reverse-parsed onto their asset ──────────────


@_skip
async def test_entity_canvases_link_by_name_and_leave_the_rest_alone(orm_dsn, pg, fx):
    """Spec §4 step 4. Five canvases, one per outcome — linked, unparseable
    title, title/kind disagreement, no such asset, and a canvas in a personal
    project (no team → no scope to match in).

    Counters that a foreign row in this shared database could inflate
    (``canvases_scanned``, ``canvases_no_scope``) are asserted with ``>=``; the
    ones only THIS fixture's projects can produce are asserted exactly, and
    every outcome is additionally checked on the canvas row itself.
    """
    from app.workflows.backfill_assets_from_project_entities import (
        _apply,
        _run_extra_steps,
    )

    p0, p1, _p2 = fx["project_ids"]
    char_name = _uniq("Canvas Char")
    prop_name = _uniq("Canvas Prop")
    await _seed_character(pg, p0, char_name)
    await _seed_entity(pg, p1, "prop", prop_name)

    linked = await _seed_canvas(pg, p0, "character", f"{char_name} · Character")
    # Same entity, different case — the match is lower(name), like the index.
    linked_prop = await _seed_canvas(pg, p1, "prop", f"{prop_name.upper()} · Prop")
    renamed = await _seed_canvas(pg, p0, "character", char_name)  # suffix dropped
    mismatched = await _seed_canvas(pg, p0, "character", f"{char_name} · Prop")
    no_asset = await _seed_canvas(pg, p0, "character", f"{_uniq('Nobody')} · Character")
    personal = await _seed_canvas(
        pg, fx["personal_project_id"], "character", f"{char_name} · Character"
    )

    chars, ents = await _rows_for(pg, fx["project_ids"])
    plan = _plan_for(fx, chars, ents)
    applied = await _apply(plan, fx["user_id"])
    project_team = {pid: fx["team_id"] for pid in fx["project_ids"]}
    out = await _run_extra_steps(
        plan,
        project_team,
        dry_run=False,
        run_user_id=fx["user_id"],
        asset_id_by_key=applied["asset_id_by_key"],
        asset_id_by_ref=applied["asset_id_by_ref"],
    )
    cnv = out["canvases"]
    assert cnv["canvases_linked"] == 2
    assert cnv["skipped_unparsed_canvas"] == 2  # renamed + kind/suffix mismatch
    assert cnv["canvases_no_asset"] == 1
    assert cnv["canvases_no_scope"] >= 1
    assert cnv["canvases_scanned"] >= 6

    async def _asset_of(canvas_id):
        return await pg.fetchval(
            "SELECT asset_id FROM canvases WHERE id = $1", canvas_id
        )

    char_asset = int(
        await pg.fetchval(
            "SELECT id FROM assets WHERE scope_id = $1 AND name = $2",
            fx["team_id"],
            char_name,
        )
    )
    prop_asset = int(
        await pg.fetchval(
            "SELECT id FROM assets WHERE scope_id = $1 AND name = $2 "
            "AND asset_type = 'prop'",
            fx["team_id"],
            prop_name,
        )
    )
    assert await _asset_of(linked) == char_asset
    assert await _asset_of(linked_prop) == prop_asset
    for cid in (renamed, mismatched, no_asset, personal):
        assert await _asset_of(cid) is None

    # Re-run: the linked ones are no longer even scanned (asset_id IS NULL is
    # in the scan), so nothing is linked twice and nothing is re-pointed.
    again = await _run_extra_steps(
        plan,
        project_team,
        dry_run=False,
        run_user_id=fx["user_id"],
        asset_id_by_key=applied["asset_id_by_key"],
        asset_id_by_ref=applied["asset_id_by_ref"],
    )
    assert again["canvases"]["canvases_linked"] == 0
    assert await _asset_of(linked) == char_asset


@_skip
async def test_a_hand_linked_canvas_is_never_repointed(orm_dsn, pg, fx):
    """Negative control for the scan filter: a canvas someone already pointed
    at a DIFFERENT asset must survive a run whose name rule would have chosen
    another one."""
    from app.workflows.backfill_assets_from_project_entities import (
        _apply,
        _run_extra_steps,
    )

    p0 = fx["project_ids"][0]
    name = _uniq("Hand Linked")
    other = _uniq("Someone Else")
    await _seed_character(pg, p0, name)
    await _seed_character(pg, p0, other)
    canvas_id = await _seed_canvas(pg, p0, "character", f"{name} · Character")

    chars, ents = await _rows_for(pg, fx["project_ids"])
    plan = _plan_for(fx, chars, ents)
    applied = await _apply(plan, fx["user_id"])
    other_asset = int(
        await pg.fetchval(
            "SELECT id FROM assets WHERE scope_id = $1 AND name = $2",
            fx["team_id"],
            other,
        )
    )
    await pg.execute(
        "UPDATE canvases SET asset_id = $1 WHERE id = $2", other_asset, canvas_id
    )

    out = await _run_extra_steps(
        plan,
        {pid: fx["team_id"] for pid in fx["project_ids"]},
        dry_run=False,
        run_user_id=fx["user_id"],
        asset_id_by_key=applied["asset_id_by_key"],
        asset_id_by_ref=applied["asset_id_by_ref"],
    )
    assert out["canvases"]["canvases_linked"] == 0
    assert (
        await pg.fetchval("SELECT asset_id FROM canvases WHERE id = $1", canvas_id)
        == other_asset
    )


# ── 8. step 5: generated_media mapping + the two state transitions ──────────


@_skip
async def test_generated_media_maps_to_its_asset_and_moves_inbox_state(orm_dsn, pg, fx):
    """Spec §4 step 5, all three facts at once.

    The legacy ids in ``params`` are what tie a generation to a card, and they
    arrive as JSON STRINGS (entityRef.ts stamps ``String(raw)``) — the shape a
    hand-written fixture would "tidy" into a number, and then never exercise.

    ``genmedia_saved`` / ``genmedia_in_assets`` scan globally, so they are
    asserted with ``>=`` and every row's final state is checked directly.
    """
    from app.workflows.backfill_assets_from_project_entities import (
        _apply,
        _run_extra_steps,
    )

    p0, p1, _p2 = fx["project_ids"]
    char_name = _uniq("Gen Char")
    prop_name = _uniq("Gen Prop")
    char_legacy = await _seed_character(pg, p0, char_name)
    prop_legacy = await _seed_entity(pg, p1, "prop", prop_name)

    promoted = await _seed_resource(pg, fx["user_id"], fx["team_id"], "promoted.png")
    attached = await _seed_resource(pg, fx["user_id"], fx["team_id"], "attached.png")

    g_char = await _seed_genmedia(
        pg,
        fx["user_id"],
        fx["team_id"],
        params=f'{{"entity_kind": "character", "entity_id": "{char_legacy}"}}',
    )
    g_prop = await _seed_genmedia(
        pg,
        fx["user_id"],
        fx["team_id"],
        params=f'{{"entity_kind": "prop", "entity_id": "{prop_legacy}"}}',
    )
    # A legacy id that never existed: unmatched, never mapped to "some asset".
    g_unknown = await _seed_genmedia(
        pg,
        fx["user_id"],
        fx["team_id"],
        params='{"entity_kind": "character", "entity_id": "999999999"}',
    )
    # Right legacy row, wrong tenant — a generation must never be attributed to
    # an asset outside its own scope.
    g_cross = await _seed_genmedia(
        pg,
        fx["user_id"],
        fx["other_team_id"],
        params=f'{{"entity_kind": "character", "entity_id": "{char_legacy}"}}',
    )
    g_saved = await _seed_genmedia(
        pg, fx["user_id"], fx["team_id"], promoted_resource_id=promoted
    )
    g_in_assets = await _seed_genmedia(
        pg, fx["user_id"], fx["team_id"], promoted_resource_id=attached
    )
    # A row the user dismissed: 'deleted' is their decision about the card and
    # nothing here may un-dismiss it.
    g_deleted = await _seed_genmedia(
        pg,
        fx["user_id"],
        fx["team_id"],
        promoted_resource_id=attached,
        review_state="deleted",
    )

    chars, ents = await _rows_for(pg, fx["project_ids"])
    plan = _plan_for(fx, chars, ents)
    applied = await _apply(plan, fx["user_id"])
    char_asset = applied["asset_id_by_ref"][("project_characters", char_legacy)]
    prop_asset = applied["asset_id_by_ref"][("project_lib_entities", prop_legacy)]
    # ``attached`` is in the asset library; ``promoted`` is only promoted.
    await pg.execute(
        "INSERT INTO asset_files (asset_id, resource_id, slot) VALUES ($1, $2, 'stills')",
        char_asset,
        attached,
    )

    project_team = {pid: fx["team_id"] for pid in fx["project_ids"]}
    out = await _run_extra_steps(
        plan,
        project_team,
        dry_run=False,
        run_user_id=fx["user_id"],
        asset_id_by_key=applied["asset_id_by_key"],
        asset_id_by_ref=applied["asset_id_by_ref"],
    )
    gen = out["generated_media"]
    assert gen["genmedia_mapped"] == 2
    assert gen["genmedia_scope_mismatch"] == 1
    assert gen["genmedia_unmatched"] >= 1
    assert gen["genmedia_saved"] >= 2
    assert gen["genmedia_in_assets"] >= 1

    async def _row(gid):
        return await pg.fetchrow(
            "SELECT source_asset_id, review_state FROM generated_media WHERE id = $1",
            gid,
        )

    assert (await _row(g_char))["source_asset_id"] == char_asset
    assert (await _row(g_prop))["source_asset_id"] == prop_asset
    assert (await _row(g_unknown))["source_asset_id"] is None
    assert (await _row(g_cross))["source_asset_id"] is None
    assert (await _row(g_saved))["review_state"] == "saved"
    # Promoted AND attached: 'saved' runs first, then 'in_assets' claims it —
    # the stronger statement is the one that survives.
    assert (await _row(g_in_assets))["review_state"] == "in_assets"
    assert (await _row(g_deleted))["review_state"] == "deleted"

    again = await _run_extra_steps(
        plan,
        project_team,
        dry_run=False,
        run_user_id=fx["user_id"],
        asset_id_by_key=applied["asset_id_by_key"],
        asset_id_by_ref=applied["asset_id_by_ref"],
    )
    # Everything already done: nothing left to map, nothing left to save.
    assert again["generated_media"]["genmedia_mapped"] == 0
    assert (await _row(g_char))["source_asset_id"] == char_asset
    assert (await _row(g_saved))["review_state"] == "saved"
    assert (await _row(g_in_assets))["review_state"] == "in_assets"


# ── 9. the dry-run preview is the live run's numbers, not a guess ───────────


@_skip
async def test_dry_run_previews_the_same_numbers_the_live_run_produces(orm_dsn, pg, fx):
    """The claim "dry_run reports what would happen" is only worth making if it
    is measured. This runs the preview against a database where NOTHING has
    been migrated yet — no assets, so no canvas can find one and no generation
    has an asset id to point at — then applies for real and compares.

    The two places that make the preview exact rather than a lower bound are
    both exercised here: a canvas whose asset the plan is about to create, and
    a generation whose ``in_assets`` verdict depends on the cover this run is
    about to attach.
    """
    from app.workflows.backfill_assets_from_project_entities import (
        _apply,
        _run_extra_steps,
    )

    p0 = fx["project_ids"][0]
    name = _uniq("Preview Char")
    cover = await _seed_resource(pg, fx["user_id"], fx["team_id"], "preview.png")
    legacy_id = await _seed_character(
        pg, p0, name, portrait_url=f"/api/v1/resources/{cover}/cover"
    )
    await _seed_canvas(pg, p0, "character", f"{name} · Character")
    await _seed_genmedia(
        pg,
        fx["user_id"],
        fx["team_id"],
        params=f'{{"entity_kind": "character", "entity_id": "{legacy_id}"}}',
    )
    # Promoted from the very file the cover step is about to attach: without
    # the cover step handing its resolved ids to step 5, the preview would say
    # 0 in_assets and the live run would say 1.
    await _seed_genmedia(pg, fx["user_id"], fx["team_id"], promoted_resource_id=cover)

    chars, ents = await _rows_for(pg, fx["project_ids"])
    plan = _plan_for(fx, chars, ents)
    project_team = {pid: fx["team_id"] for pid in fx["project_ids"]}

    preview = await _run_extra_steps(
        plan, project_team, dry_run=True, run_user_id=fx["user_id"]
    )
    # Nothing was written by the preview.
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM assets WHERE scope_id = $1", fx["team_id"]
        )
        == 0
    )
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM canvases WHERE project_id = $1 "
            "AND asset_id IS NOT NULL",
            p0,
        )
        == 0
    )
    assert preview["covers"]["covers_resolved"] == 1
    assert preview["canvases"]["canvases_linked"] == 1
    assert preview["generated_media"]["genmedia_mapped"] == 1

    applied = await _apply(plan, fx["user_id"])
    live = await _run_extra_steps(
        plan,
        project_team,
        dry_run=False,
        run_user_id=fx["user_id"],
        asset_id_by_key=applied["asset_id_by_key"],
        asset_id_by_ref=applied["asset_id_by_ref"],
    )
    for step in ("covers", "canvases", "generated_media"):
        for bucket, previewed in preview[step].items():
            if bucket in ("covers_attached", "covers_cover_set"):
                continue  # a write count; the preview writes nothing by design
            assert live[step][bucket] == previewed, (
                f"{step}.{bucket}: preview said {previewed}, "
                f"the live run did {live[step][bucket]}"
            )


# ── 10. the SYSTEM scope wrap is load-bearing, not decorative ──────────────


@_skip
async def test_the_cover_step_survives_production_scope_enforcement(orm_dsn, pg, fx):
    """``Resources`` is ``UserScoped`` and PRODUCTION runs with
    ``SCOPE_ENFORCE_RESOURCES=True`` (set in ``secrets/backend.env``, which
    overrides config.yml), while the repo's own default is False. So every
    other test in this file exercises the cover step with the choke point
    INERT — which means none of them can tell whether the
    ``system_request_scope`` wrap exists.

    This one flips the flag to what production actually runs and asserts the
    step still resolves. Without the wrap the ``do_orm_execute`` choke point
    fail-closed raises ``UnscopedQueryError`` and the whole backfill dies on
    its first cover — in production only, on the first live run.
    """
    from unittest.mock import patch

    from app.db import scope as scope_mod
    from app.workflows.backfill_assets_from_project_entities import (
        _apply,
        _run_extra_steps,
    )

    p0 = fx["project_ids"][0]
    rid = await _seed_resource(pg, fx["user_id"], fx["team_id"], "enforced.png")
    name = _uniq("Enforced Cover")
    await _seed_character(pg, p0, name, portrait_url=f"/api/v1/resources/{rid}/cover")

    chars, ents = await _rows_for(pg, fx["project_ids"])
    plan = _plan_for(fx, chars, ents)
    applied = await _apply(plan, fx["user_id"])

    with patch.object(scope_mod.settings, "SCOPE_ENFORCE_RESOURCES", True):
        assert scope_mod.is_enforced("resources") is True  # the flag really bit
        out = await _run_extra_steps(
            plan,
            {pid: fx["team_id"] for pid in fx["project_ids"]},
            dry_run=False,
            run_user_id=fx["user_id"],
            asset_id_by_key=applied["asset_id_by_key"],
            asset_id_by_ref=applied["asset_id_by_ref"],
        )
    assert out["covers"]["covers_resolved"] == 1
    assert (
        await pg.fetchval(
            "SELECT cover_file_id FROM assets WHERE scope_id = $1 AND name = $2",
            fx["team_id"],
            name,
        )
        == rid
    )


# ── 11. the P3 ruling: personal projects migrate to the owner's personal team ─


@_skip
async def test_a_personal_projects_rows_land_in_the_owners_personal_team(
    orm_dsn, pg, fx
):
    """C-1, end to end.

    Before P3 these rows were counted into a skip bucket and left in
    ``_legacy_project_characters`` / ``_legacy_project_lib_entities`` — while
    the workspace pages had already been switched to read ``assets`` and the
    old readers deleted, which made them unreachable. This is the test that
    says they move.

    Four properties in one run, because they are only true together:
      * the rows land in the OWNER's personal team, not anywhere else;
      * a same-name asset already in that team is ADOPTED, not duplicated —
        the merge is symmetric with what a collaborative team gets;
      * two personal projects of one owner share one asset and each keep their
        own ``asset_project_refs`` row;
      * the ownerless project's rows still skip, with no asset created for them.
    """
    from app.workflows.backfill_assets_from_project_entities import (
        _apply,
        _load_inputs,
        _reconcile,
        plan_migration,
    )

    personal_a = fx["personal_project_id"]
    personal_b = fx["personal_project_id_2"]
    scope = fx["personal_team_id"]

    shared = _uniq("Solo Lead")
    prop = _uniq("Jade Seal")
    ownerless = _uniq("Nowhere Man")

    # A hand-made asset already in the personal team, differing in case so the
    # adoption is proven to match on lower(name) like the unique index does.
    adopted_id = int(
        await pg.fetchval(
            "INSERT INTO assets (scope_id, asset_type, name, created_by, source) "
            "VALUES ($1, 'character', $2, $3, 'manual') RETURNING id",
            scope,
            shared.upper(),
            uuid.UUID(fx["user_id"]),
        )
    )

    await _seed_character(pg, personal_a, shared, role_tag="lead")
    await _seed_character(pg, personal_b, shared, description="from the second")
    await _seed_entity(pg, personal_a, "prop", prop)
    # The residue, seeded so "nothing was created for it" is an observation
    # rather than the absence of an input.
    await _seed_character(pg, fx["orphan_project_id"], ownerless)

    # The map comes from the REAL resolver, not a hand-built one — that is the
    # half under test. The ROWS are still the fixture's own, so a shared drift
    # database cannot change this plan's merge groups.
    chars, ents = await _rows_for(pg, fx["all_project_ids"])
    _c, _e, project_team, unmappable = await _load_inputs()
    plan = plan_migration(chars, ents, project_team, unmappable)
    assert plan["counts"]["assets"] == 2  # the merged character + the prop
    assert plan["counts"]["merges"] == 1
    assert plan["counts"]["skipped_unmappable_personal"] == 1
    assert {a["scope_id"] for a in plan["assets"]} == {scope}

    first = await _apply(plan, fx["user_id"])
    # One created (the prop), one adopted (the hand-made character), three refs
    # — two for the merged character, one for the prop.
    assert first["counts"] == {"created": 1, "existing": 1, "project_refs_added": 3}
    report = await _reconcile(plan)
    assert report["assets_present"] == report["assets_expected"] == 2
    assert report["project_refs_present"] == report["project_refs_expected"] == 3

    live = await _live_assets(pg, scope)
    assert len(live) == 2
    merged = next(r for r in live if r["name"].lower() == shared.lower())
    assert int(merged["id"]) == adopted_id, "the existing asset was duplicated"
    refs = sorted(
        int(r["project_id"])
        for r in await pg.fetch(
            "SELECT project_id FROM asset_project_refs WHERE asset_id = $1",
            adopted_id,
        )
    )
    assert refs == sorted([personal_a, personal_b])

    # Nothing was created for the ownerless project, in ANY scope.
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM assets WHERE name = $1 AND deleted_at IS NULL",
            ownerless,
        )
        == 0
    )

    # Idempotent: a second run adopts everything and adds no ref.
    second = await _apply(plan, fx["user_id"])
    assert second["counts"] == {"created": 0, "existing": 2, "project_refs_added": 0}
    assert second["asset_id_by_key"] == first["asset_id_by_key"]
    assert [int(r["id"]) for r in await _live_assets(pg, scope)] == [
        int(r["id"]) for r in live
    ]


@_skip
async def test_a_personal_projects_entity_canvas_links_after_the_mapping(
    orm_dsn, pg, fx
):
    """Step 4 inherits the mapping instead of re-deriving it. Before P3 a
    personal project's canvases all landed in ``canvases_no_scope`` — the
    canvas and its asset existed and stayed unconnected."""
    from app.workflows.backfill_assets_from_project_entities import (
        _apply,
        _link_entity_canvases,
        _load_inputs,
        plan_migration,
    )

    personal_a = fx["personal_project_id"]
    name = _uniq("Lantern Court")
    await _seed_entity(pg, personal_a, "location", name)
    canvas_id = await _seed_canvas(pg, personal_a, "location", f"{name} · Location")
    # The ownerless project's canvas is the control, and it carries the SAME
    # entity name on purpose. A different name would make it unlinkable for
    # two reasons at once (no scope, and no asset by that name), so the test
    # could not tell them apart: a bug that resolved this project to some
    # arbitrary scope would still leave it unlinked, and the control would
    # report success. Sharing the name means the ONLY thing keeping it
    # unlinked is having no scope — which is exactly the property under test.
    orphan_canvas_id = await _seed_canvas(
        pg, fx["orphan_project_id"], "location", f"{name} · Location"
    )
    # …and a same-name asset in the OTHER scope this fixture owns, so that
    # every scope a borrowing bug could reach has something for that canvas to
    # link to. Without this, "still unlinked" could just mean the arbitrary
    # scope it grabbed happened to be empty, and the control would pass on a
    # build that had stopped skipping. Seeded directly (no legacy row), so the
    # plan is unchanged.
    await pg.fetchval(
        "INSERT INTO assets (scope_id, asset_type, name, created_by, source) "
        "VALUES ($1, 'location', $2, $3, 'manual') RETURNING id",
        fx["team_id"],
        name,
        uuid.UUID(fx["user_id"]),
    )

    chars, ents = await _rows_for(pg, fx["all_project_ids"])
    _c, _e, project_team, unmappable = await _load_inputs()
    plan = plan_migration(chars, ents, project_team, unmappable)
    await _apply(plan, fx["user_id"])

    counts = await _link_entity_canvases(plan, project_team, dry_run=False)
    assert counts["canvases_linked"] >= 1
    linked_to = await pg.fetchval(
        "SELECT asset_id FROM canvases WHERE id = $1", canvas_id
    )
    assert linked_to is not None
    assert (
        await pg.fetchval("SELECT scope_id FROM assets WHERE id = $1", int(linked_to))
        == fx["personal_team_id"]
    )

    # The control, EXECUTED. Both halves, because either alone is weak: the
    # bucket must have counted it (otherwise it was silently dropped somewhere
    # else, or parsed into `skipped_unparsed_canvas` — the name IS parseable,
    # so a scope-less canvas landing there would mean the two reasons had been
    # folded together), and the row must still be unlinked (a count is a claim
    # about a decision; this is the decision's effect).
    assert counts["canvases_no_scope"] >= 1
    assert (
        await pg.fetchval(
            "SELECT asset_id FROM canvases WHERE id = $1", orphan_canvas_id
        )
        is None
    ), "the ownerless project's canvas was linked despite having no scope"

    # Re-running links nothing further (the scan itself filters asset_id IS NULL).
    again = await _link_entity_canvases(plan, project_team, dry_run=False)
    assert again["canvases_linked"] == 0
    # …and the control is still a control on the re-run: it is unlinked, so the
    # scan still sees it, so it must still be counted rather than quietly
    # disappearing from the report.
    assert again["canvases_no_scope"] >= 1
