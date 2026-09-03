"""DB-backed integration tests for the asset-library repositories (mig 445/446).

WHY THIS FILE EXISTS
────────────────────
``AssetsRepository`` and ``AssetRelationsRepository`` are pure ORM statements.
The unit tests around them stub the session, so until this file landed **not one
of those statements had ever been executed by Postgres** — the exact shape of
gap CLAUDE.md's "边界 mock 必须用真实 JSON 形状" and "读正常 ≠ 服务正常" warn about.
A statement can compile happily under SQLAlchemy and still be rejected (or,
worse, silently do the wrong thing) on the server:

  * ``create()`` decides "this is a duplicate" by string-matching
    ``uq_assets_scope_type_name`` inside ``IntegrityError.orig`` — whether asyncpg
    puts the *index* name there is a driver fact, not a SQLAlchemy one (case 1).
  * ``update(...).returning(Assets)`` yielding a mapped entity (not a Row) is an
    ORM-dialect behaviour (case 2).
  * the two partial unique indexes only free/hold a name because of their
    ``WHERE`` predicates (cases 3, 7, 9).
  * ``on_conflict_do_update`` / ``on_conflict_do_nothing`` need the real index to
    exist with the right columns (cases 5, 6).
  * ``strip_from_loadouts`` rewrites ``ARRAY(BigInteger)`` columns (case 8).
  * repo writes inside an ambient ``unit_of_work()`` must join that transaction,
    so one raise rolls the whole batch back (case 10 — Task 9 review ruling I-2).
  * the three derived-count GROUP BYs every list page runs (``slot_counts`` /
    ``project_ids`` / ``loadout_counts``) really produce the folded wire row,
    and ``readiness`` is derived from the first of them (case 11).
  * ``duplicate`` writes four tables in one transaction: its loadout copies must
    not trip ``uq_loadout_default``, its file rows must land on the COPY's
    loadouts, and its 409 must stay typed instead of becoming a
    PendingRollbackError on the aborted transaction (cases 14, 15).

Transport: asyncpg on ``INTEGRATION_DATABASE_URL`` for fixture setup and for the
assertions; the repos themselves go through ``app.db.session`` (SQLAlchemy async
engine), which the ``orm_dsn`` fixture repoints at the same DSN. Same pattern as
``tests/db/test_note_tags_integration.py``.

Point it at any CI-way Postgres (ci_bootstrap.sql → schema_baseline.sql →
migrations above the watermark, which includes 445/446):

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/db/test_assets_repository_integration.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset. Every test builds its own
team/resource/project fixture with fresh ids and tears it down in a ``finally``,
so the cases are independent and the file is re-runnable against the same DB.
"""

from __future__ import annotations

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
    reason="INTEGRATION_DATABASE_URL not set — asset-library integration tests need a DB.",
)


def _uniq(prefix: str) -> str:
    """A name no other run (or case) can collide with.

    ``uq_assets_scope_type_name`` keys on ``COALESCE(scope_id, 0)`` — so system
    presets (scope_id NULL) share ONE global namespace across every run of this
    file. Fixture-scoped names are not enough for them; unique ones are.
    """
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
    """A raw asyncpg connection for setup/assertions (plain libpq DSN)."""
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def fx(pg) -> Dict[str, Any]:
    """One team (the asset scope), one owner, two in-scope resources and one
    project belonging to that team.

    ``assets.scope_id`` FK-references ``public.teams``, ``asset_files.resource_id``
    references ``public.resources``, and ``AssetRelationsRepository.resource_in_scope``
    reads ``resource_items(resource_id, scope_id)`` — so a resource is only "in
    scope" once it has a ``resource_items`` row pointing at the team. Only the
    NOT NULL columns each table declares in ``schema_baseline.sql`` are supplied.

    Torn down in reverse FK order in a ``finally``; ``assets`` rows are removed by
    scope AND by ``created_by`` so a scope-less system preset (case 4) is caught too.
    """
    user_id = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
    team_id = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) "
        "RETURNING id",
        "Asset Library Test Team",
        user_id,
        uuid.uuid4().hex[:16],
    )
    project_id = await pg.fetchval(
        "INSERT INTO projects (name, owner_id, team_id) VALUES ($1, $2, $3) "
        "RETURNING id",
        "Asset Library Test Project",
        user_id,
        team_id,
    )
    resource_ids = []
    for n in range(3):
        rid = await pg.fetchval(
            "INSERT INTO resources (creator_id, source_type, filename) "
            "VALUES ($1, 'upload', $2) RETURNING id",
            user_id,
            f"fixture-{n}.png",
        )
        await pg.execute(
            "INSERT INTO resource_items (resource_id, scope_id) VALUES ($1, $2)",
            rid,
            team_id,
        )
        resource_ids.append(int(rid))

    try:
        yield {
            "user_id": str(user_id),
            "team_id": int(team_id),
            "project_id": int(project_id),
            "resource_ids": resource_ids,
        }
    finally:
        # assets → cascades to asset_files / asset_links / asset_loadouts /
        # asset_project_refs. Presets have scope_id NULL, hence the created_by arm.
        await pg.execute(
            "DELETE FROM assets WHERE scope_id = $1 OR created_by = $2",
            int(team_id),
            user_id,
        )
        await pg.execute("DELETE FROM projects WHERE id = $1", int(project_id))
        await pg.execute(
            "DELETE FROM resource_items WHERE resource_id = ANY($1::bigint[])",
            resource_ids,
        )
        await pg.execute(
            "DELETE FROM resources WHERE id = ANY($1::bigint[])", resource_ids
        )
        await pg.execute("DELETE FROM teams WHERE id = $1", int(team_id))
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)


async def _make_asset(repo, fx, asset_type: str, name: str, **extra) -> Dict[str, Any]:
    return await repo.create(
        fx["team_id"], {"asset_type": asset_type, "name": name, **extra}, fx["user_id"]
    )


# ── 1. duplicate name → DuplicateAssetName carrying the existing id ─────────


@_skip
async def test_create_duplicate_name_case_insensitive_raises_with_existing_id(
    orm_dsn, pg, fx
):
    """Second create differing ONLY by letter case must raise DuplicateAssetName
    with the first row's id.

    This is the proof that asyncpg's ``IntegrityError.orig`` text actually carries
    ``uq_assets_scope_type_name`` — ``create()`` re-raises anything whose text does
    NOT contain that literal, so a driver that reported the constraint differently
    would turn every duplicate into a 500 instead of the 409 the router promises.
    It is also the proof that ``lower(name)`` in the index makes the match
    case-insensitive.
    """
    from app.repositories.assets_repository import (
        AssetsRepository,
        DuplicateAssetName,
    )

    repo = AssetsRepository()
    name = _uniq("Aria Vance")
    first = await _make_asset(repo, fx, "character", name)

    with pytest.raises(DuplicateAssetName) as excinfo:
        await _make_asset(repo, fx, "character", name.upper())

    assert excinfo.value.existing_id == int(first["id"])

    rows = await pg.fetch(
        "SELECT id FROM assets WHERE scope_id = $1 AND asset_type = 'character' "
        "AND lower(name) = lower($2) AND deleted_at IS NULL",
        fx["team_id"],
        name,
    )
    assert len(rows) == 1  # the second INSERT left nothing behind


# ── 2. update() returns the entity and bumps updated_at ────────────────────


@_skip
async def test_update_returns_entity_and_bumps_updated_at(orm_dsn, pg, fx):
    """``update(...).returning(Assets)`` must yield a mapped entity (``_row_dict``
    reads ``obj.__table__``, which a plain Row has not got), and ``updated_at``
    must move — ``assets`` deliberately ships NO touch trigger (mig 445), so the
    repo's explicit ``updated_at`` is the only thing keeping it honest.
    """
    from app.repositories.assets_repository import AssetsRepository

    repo = AssetsRepository()
    created = await _make_asset(repo, fx, "location", _uniq("Rooftop"))

    updated = await repo.update(
        int(created["id"]), fx["team_id"], {"description": "Night, neon, wet asphalt"}
    )

    assert updated is not None  # the row came back, not None
    assert int(updated["id"]) == int(created["id"])
    assert updated["description"] == "Night, neon, wet asphalt"
    assert updated["updated_at"] > created["updated_at"]

    live = await pg.fetchrow(
        "SELECT description, updated_at FROM assets WHERE id = $1", int(created["id"])
    )
    assert live["description"] == "Night, neon, wet asphalt"  # really committed
    assert live["updated_at"] > created["updated_at"]


# ── 3. soft delete frees the name (partial unique index) ───────────────────


@_skip
async def test_soft_delete_frees_the_name_for_reuse(orm_dsn, pg, fx):
    """``uq_assets_scope_type_name`` is partial (``WHERE deleted_at IS NULL``), so
    a soft-deleted row must stop squatting its name. Without the predicate this
    second create would raise DuplicateAssetName forever."""
    from app.repositories.assets_repository import AssetsRepository

    repo = AssetsRepository()
    name = _uniq("Reused Name")
    first = await _make_asset(repo, fx, "prop", name)

    assert await repo.soft_delete(int(first["id"]), fx["team_id"]) is True

    second = await _make_asset(repo, fx, "prop", name)
    assert int(second["id"]) != int(first["id"])  # a genuinely new row

    # The soft-deleted one is gone from the reads but still on disk.
    assert await repo.get(int(first["id"]), fx["team_id"]) is None
    assert (
        await pg.fetchval(
            "SELECT deleted_at IS NOT NULL FROM assets WHERE id = $1", int(first["id"])
        )
        is True
    )


# ── 4. system presets (scope_id NULL) are visible from every scope ─────────


@_skip
async def test_system_preset_with_null_scope_is_visible_to_a_team_scope(
    orm_dsn, pg, fx
):
    """A preset has ``scope_id NULL`` (allowed only by the ``assets_scope_or_preset``
    CHECK) and must be returned by BOTH ``get()`` and ``list()`` for an unrelated
    team — that is the ``or_(scope_id == …, is_system_preset)`` arm in the repo.

    ``create()`` cannot express it (it does ``int(scope_id)``), so the row goes in
    through a session directly, exactly as a seeder would.
    """
    from app.db.session import write_scope
    from app.models import Assets
    from app.repositories.assets_repository import AssetsRepository

    repo = AssetsRepository()
    preset_name = _uniq("Preset Cinematic")

    async with write_scope() as session:
        obj = Assets(
            scope_id=None,
            asset_type="prompt",
            name=preset_name,
            is_system_preset=True,
            source="system_preset",
            prompt_positive="cinematic lighting, 35mm",
            created_by=fx["user_id"],
        )
        session.add(obj)
        await session.flush()
        preset_id = int(obj.id)

    assert (
        await pg.fetchval("SELECT scope_id FROM assets WHERE id = $1", preset_id)
    ) is None  # really NULL on disk, not 0

    got = await repo.get(preset_id, fx["team_id"])
    assert got is not None
    assert int(got["id"]) == preset_id
    assert got["is_system_preset"] is True

    listed = await repo.list(fx["team_id"], asset_type="prompt", limit=200)
    assert preset_id in {int(r["id"]) for r in listed}


# ── 5. attach is an upsert on (asset, resource, slot) ──────────────────────


@_skip
async def test_attach_twice_upserts_the_note_and_keeps_one_row(orm_dsn, pg, fx):
    """Second ``attach`` for the same (asset, resource, slot) must UPDATE, not
    insert a second row — ``on_conflict_do_update`` against the composite PK."""
    from app.repositories.asset_relations_repository import AssetRelationsRepository
    from app.repositories.assets_repository import AssetsRepository

    assets = AssetsRepository()
    rel = AssetRelationsRepository()
    asset = await _make_asset(assets, fx, "character", _uniq("Attach Target"))
    rid = fx["resource_ids"][0]

    first = await rel.attach(int(asset["id"]), rid, "sheet", note="first")
    assert first["note"] == "first"

    second = await rel.attach(int(asset["id"]), rid, "sheet", note="second")
    assert second["note"] == "second"  # the conflicting insert updated the note

    rows = await pg.fetch(
        "SELECT note FROM asset_files WHERE asset_id = $1 AND resource_id = $2 "
        "AND slot = 'sheet'",
        int(asset["id"]),
        rid,
    )
    assert len(rows) == 1  # one row, not two
    assert rows[0]["note"] == "second"


# ── 6. add_link is idempotent ──────────────────────────────────────────────


@_skip
async def test_add_link_twice_is_idempotent(orm_dsn, pg, fx):
    """``on_conflict_do_nothing`` + re-select: the second call must return the
    same row rather than raising or duplicating."""
    from app.repositories.asset_relations_repository import AssetRelationsRepository
    from app.repositories.assets_repository import AssetsRepository

    assets = AssetsRepository()
    rel = AssetRelationsRepository()
    character = await _make_asset(assets, fx, "character", _uniq("Linker"))
    costume = await _make_asset(assets, fx, "costume", _uniq("Trench Coat"))

    a = await rel.add_link(int(character["id"]), int(costume["id"]), "wears")
    b = await rel.add_link(int(character["id"]), int(costume["id"]), "wears")

    assert a["created_at"] == b["created_at"]  # same row, not a re-created one

    count = await pg.fetchval(
        "SELECT count(*) FROM asset_links WHERE from_asset_id = $1 "
        "AND to_asset_id = $2 AND relation = 'wears'",
        int(character["id"]),
        int(costume["id"]),
    )
    assert count == 1


# ── 7. set_default refuses a foreign loadout without clearing the default ──


@_skip
async def test_set_default_rejects_foreign_loadout_and_moves_an_owned_one(
    orm_dsn, pg, fx
):
    """The ``FOR UPDATE`` ownership probe runs BEFORE any write, so a loadout
    belonging to another asset must return ``False`` and leave the asset with
    exactly the default it already had.

    Naively clearing the siblings first (the order the partial unique index
    forces on the happy path) would otherwise leave the asset with NO default at
    all — a silent corruption that returns False and looks like a no-op.
    """
    from app.repositories.asset_relations_repository import AssetRelationsRepository
    from app.repositories.assets_repository import AssetsRepository

    assets = AssetsRepository()
    rel = AssetRelationsRepository()
    mine = await _make_asset(assets, fx, "character", _uniq("Owner Asset"))
    other = await _make_asset(assets, fx, "character", _uniq("Other Asset"))

    default = await rel.create_loadout(
        int(mine["id"]), {"name": "Default", "is_default": True}
    )
    alt = await rel.create_loadout(
        int(mine["id"]), {"name": "Alt", "is_default": False}
    )
    foreign = await rel.create_loadout(
        int(other["id"]), {"name": "Foreign", "is_default": True}
    )

    assert await rel.set_default(int(foreign["id"]), int(mine["id"])) is False

    still = await pg.fetch(
        "SELECT id FROM asset_loadouts WHERE asset_id = $1 AND is_default",
        int(mine["id"]),
    )
    assert len(still) == 1  # exactly one default — not zero
    assert int(still[0]["id"]) == int(default["id"])  # and it is the original one

    assert await rel.set_default(int(alt["id"]), int(mine["id"])) is True

    moved = await pg.fetch(
        "SELECT id FROM asset_loadouts WHERE asset_id = $1 AND is_default",
        int(mine["id"]),
    )
    assert len(moved) == 1
    assert int(moved[0]["id"]) == int(alt["id"])  # the default moved


# ── 8. strip_from_loadouts touches only the named asset's loadouts ─────────


@_skip
async def test_strip_from_loadouts_only_touches_the_named_asset(orm_dsn, pg, fx):
    """Removing a costume id must rewrite every loadout of THAT asset and leave
    another asset's loadout holding the same id untouched (the UPDATE is scoped
    by ``asset_id``). Also pins the ARRAY(BigInteger) rewrite round-tripping."""
    from app.repositories.asset_relations_repository import AssetRelationsRepository
    from app.repositories.assets_repository import AssetsRepository

    assets = AssetsRepository()
    rel = AssetRelationsRepository()
    mine = await _make_asset(assets, fx, "character", _uniq("Strip Target"))
    other = await _make_asset(assets, fx, "character", _uniq("Strip Bystander"))
    costume_x = await _make_asset(assets, fx, "costume", _uniq("Costume X"))
    costume_y = await _make_asset(assets, fx, "costume", _uniq("Costume Y"))
    x, y = int(costume_x["id"]), int(costume_y["id"])

    lo_a = await rel.create_loadout(
        int(mine["id"]), {"name": "A", "costume_ids": [x, y]}
    )
    lo_b = await rel.create_loadout(int(mine["id"]), {"name": "B", "costume_ids": [x]})
    lo_other = await rel.create_loadout(
        int(other["id"]), {"name": "Other", "costume_ids": [x, y]}
    )

    touched = await rel.strip_from_loadouts(int(mine["id"]), costume_id=x)
    assert touched == 2  # both of this asset's loadouts held x

    async def costumes(loadout_id: int) -> list[int]:
        return [
            int(c)
            for c in await pg.fetchval(
                "SELECT costume_ids FROM asset_loadouts WHERE id = $1", loadout_id
            )
        ]

    assert await costumes(int(lo_a["id"])) == [y]  # x gone, y kept, order preserved
    assert await costumes(int(lo_b["id"])) == []
    assert await costumes(int(lo_other["id"])) == [x, y]  # bystander untouched


# ── 9. delete_loadout protects the default ────────────────────────────────


@_skip
async def test_delete_loadout_refuses_the_default_and_deletes_a_non_default(
    orm_dsn, pg, fx
):
    """``.where(is_default.is_(False))`` on the DELETE: the default survives and
    the call reports ``False`` (not a silent success), a non-default goes."""
    from app.repositories.asset_relations_repository import AssetRelationsRepository
    from app.repositories.assets_repository import AssetsRepository

    assets = AssetsRepository()
    rel = AssetRelationsRepository()
    asset = await _make_asset(assets, fx, "character", _uniq("Loadout Owner"))
    default = await rel.create_loadout(
        int(asset["id"]), {"name": "Default", "is_default": True}
    )
    spare = await rel.create_loadout(
        int(asset["id"]), {"name": "Spare", "is_default": False}
    )

    assert await rel.delete_loadout(int(default["id"]), int(asset["id"])) is False
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM asset_loadouts WHERE id = $1", int(default["id"])
        )
        == 1
    )  # still there

    assert await rel.delete_loadout(int(spare["id"]), int(asset["id"])) is True
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM asset_loadouts WHERE id = $1", int(spare["id"])
        )
        == 0
    )


# ── 10. a raise inside unit_of_work() rolls back the whole batch ───────────


@_skip
async def test_batch_attach_inside_unit_of_work_rolls_back_on_error(orm_dsn, pg, fx):
    """Task 9 ruling I-2: the router wraps batch attach in ``unit_of_work()``, so
    the repo writes must JOIN that transaction rather than each committing on its
    own. Two valid attaches followed by an invalid slot must leave ZERO rows.

    If ``write_scope()`` ever stopped joining the ambient session, the first two
    attaches would commit independently and this asserts 2 instead of 0 — a
    half-applied batch the caller was told had failed.
    """
    from app.db.session import unit_of_work
    from app.repositories.assets_repository import AssetsRepository
    from app.schemas.assets import AttachFileRequest
    from app.services.assets.assets_service import AssetError, AssetsService

    assets = AssetsRepository()
    service = AssetsService()
    asset = await _make_asset(assets, fx, "character", _uniq("Atomic Batch"))
    aid, uid = int(asset["id"]), fx["user_id"]
    r0, r1, r2 = fx["resource_ids"]

    with pytest.raises(AssetError) as excinfo:
        async with unit_of_work():
            await service.attach_file(
                aid,
                fx["team_id"],
                AttachFileRequest(resource_id=str(r0), slot="sheet"),
                uid,
            )
            await service.attach_file(
                aid,
                fx["team_id"],
                AttachFileRequest(resource_id=str(r1), slot="stills"),
                uid,
            )
            # 'portrait' is not a character slot → AssetError before any write.
            await service.attach_file(
                aid,
                fx["team_id"],
                AttachFileRequest(resource_id=str(r2), slot="portrait"),
                uid,
            )

    assert excinfo.value.code == "invalid_slot"
    assert excinfo.value.status == 422

    remaining = await pg.fetchval(
        "SELECT count(*) FROM asset_files WHERE asset_id = $1", aid
    )
    assert remaining == 0  # the two successful attaches rolled back with the third


# ── 11. the three derived-count GROUP BYs every list page runs ─────────────


@_skip
async def test_list_assets_derives_slot_project_and_loadout_counts(orm_dsn, pg, fx):
    """``slot_counts`` / ``project_ids`` / ``loadout_counts`` run on EVERY list
    page and had never been executed by Postgres (review M9).

    All three are GROUP BY / IN queries whose result is keyed by ``asset_id`` and
    then folded into the wire row by ``with_derived``. A stubbed session cannot
    say whether ``func.count()`` comes back as an int, whether the per-slot
    grouping really splits ``sheet`` from ``stills``, or whether the ids survive
    as BIGINTs — and ``readiness`` is derived from ``slot_counts``, so a wrong
    count here silently mislabels an asset ``draft`` on the shelf.

    Exercised through ``AssetsService.list_assets`` (not the repo methods on
    their own) because the fold is what the client actually receives.
    ``link_project`` / ``project_team_id`` ride along — both previously
    unexecuted too.
    """
    from app.schemas.assets import AssetCreate, AttachFileRequest
    from app.services.assets.assets_service import AssetsService

    service = AssetsService()
    team_id, uid, pid = fx["team_id"], fx["user_id"], fx["project_id"]
    r0, r1, r2 = fx["resource_ids"]

    # create_asset also writes the Default loadout → loadout #1.
    asset = await service.create_asset(
        team_id,
        AssetCreate(asset_type="character", name=_uniq("Derived Counts")),
        uid,
    )
    aid = int(asset["id"])
    # readiness starts draft: the primary slot ('sheet') is empty.
    assert asset["readiness"] == {"state": "draft", "missing": ["sheet"]}
    assert asset["file_counts_by_slot"] == {}
    assert asset["loadout_count"] == 1

    for rid, slot in ((r0, "sheet"), (r1, "sheet"), (r2, "stills")):
        await service.attach_file(
            aid, team_id, AttachFileRequest(resource_id=str(rid), slot=slot), uid
        )
    await service.link_project(aid, team_id, pid, uid)
    await service.relations.create_loadout(aid, {"name": "Night Watch"})

    rows = await service.list_assets(
        team_id, asset_type="character", project_id=None, q=None, limit=200, offset=0
    )
    row = next(r for r in rows if int(r["id"]) == aid)

    assert row["file_counts_by_slot"] == {"sheet": 2, "stills": 1}
    assert row["project_ids"] == [str(pid)]  # serialized, not a JSON number
    assert row["loadout_count"] == 2
    assert row["readiness"] == {"state": "ready", "missing": []}

    # The project_id FILTER shares the subquery those refs feed; prove it selects
    # this asset and that an unrelated project id selects nothing.
    filtered = await service.list_assets(
        team_id, asset_type=None, project_id=pid, q=None, limit=200, offset=0
    )
    assert [int(r["id"]) for r in filtered] == [aid]
    assert (
        await service.list_assets(
            team_id, asset_type=None, project_id=pid + 1, q=None, limit=200, offset=0
        )
        == []
    )

    # Ground truth straight from the server, in case the fold ever lies.
    assert dict(
        (r["slot"], r["n"])
        for r in await pg.fetch(
            "SELECT slot, count(*) AS n FROM asset_files WHERE asset_id = $1 "
            "GROUP BY slot",
            aid,
        )
    ) == {"sheet": 2, "stills": 1}
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM asset_loadouts WHERE asset_id = $1", aid
        )
        == 2
    )


# ── 12. the tag filter's jsonpath is accepted (and matches) by PostgreSQL ───


@_skip
async def test_tag_filter_matches_any_group_and_rejects_a_miss(orm_dsn, pg, fx):
    """``jsonb_path_exists(tags, '$.*[*] ? (@ == $v)', jsonb_build_object('v', …))``
    is the one P2 predicate SQLAlchemy cannot vouch for: the jsonpath is a
    server-parsed literal, so a typo compiles fine here and only fails (or
    silently matches nothing) on the server.

    Also pins the lax-mode claim in ``_tag_match``'s comment — ``[*]`` applied
    to a group whose value is a bare scalar still matches, so a tag written
    ``{"mood": "warm"}`` is findable exactly like ``{"role": ["hero"]}``.
    """
    from app.repositories.assets_repository import AssetsRepository

    repo = AssetsRepository()
    tagged = await _make_asset(
        repo,
        fx,
        "character",
        _uniq("Tagged"),
        tags={"role": ["hero", "lead"], "era": ["ming"]},
    )
    scalar = await _make_asset(
        repo, fx, "location", _uniq("Scalar Tag"), tags={"mood": "warm"}
    )
    await _make_asset(repo, fx, "prop", _uniq("Untagged"))

    async def ids(tag):
        return {
            int(r["id"]) for r in await repo.list(fx["team_id"], tag=tag, limit=200)
        }

    assert int(tagged["id"]) in await ids("hero")  # first member of a group
    assert int(tagged["id"]) in await ids("ming")  # a different group entirely
    assert int(scalar["id"]) in await ids("warm")  # scalar group value
    assert await ids("villain") == set()  # a value nobody carries
    # The group NAME is not a value — matching it would make the filter answer
    # a question nobody asked.
    assert int(tagged["id"]) not in await ids("role")


# ── 13. touch_asset really moves the parent row's clock ────────────────────


@_skip
async def test_relation_write_bumps_the_assets_updated_at(orm_dsn, pg, fx):
    """``assets`` ships no touch trigger (mig 445), so ``updated_at`` moves only
    because ``touch_asset`` issues its own UPDATE — which nothing but a real
    server can confirm (``func.now()`` is resolved by PostgreSQL).
    """
    from app.schemas.assets import AttachFileRequest
    from app.services.assets.assets_service import AssetsService

    service = AssetsService()
    created = await service.assets.create(
        fx["team_id"],
        {"asset_type": "character", "name": _uniq("Touched")},
        fx["user_id"],
    )
    aid = int(created["id"])
    before = await pg.fetchval("SELECT updated_at FROM assets WHERE id = $1", aid)

    await service.attach_file(
        aid,
        fx["team_id"],
        AttachFileRequest(resource_id=str(fx["resource_ids"][0]), slot="sheet"),
        fx["user_id"],
    )

    after = await pg.fetchval("SELECT updated_at FROM assets WHERE id = $1", aid)
    assert after > before, "attaching a file left the asset's clock stale"


# ── 14. duplicate: the whole copy, with the loadout ids remapped ───────────


@_skip
async def test_duplicate_copies_files_links_and_loadouts_with_ids_remapped(
    orm_dsn, pg, fx
):
    """``duplicate`` writes into four tables in one transaction, and two of its
    rules are PostgreSQL facts the stubbed unit suite cannot check:

      * the copied loadouts must not violate ``uq_loadout_default`` — a partial
        unique index the server checks row by row and cannot defer, which is why
        the default is created FIRST;
      * ``asset_files.loadout_id`` must land on the COPY's loadout row. A
        verbatim copy would still satisfy the FK (the source's loadout exists),
        so nothing but reading the row back proves the remap happened.
    """
    from app.schemas.assets import (
        AssetCreate,
        AttachFileRequest,
        DuplicateRequest,
        LinkRequest,
        LoadoutCreate,
        LoadoutUpdate,
    )
    from app.services.assets.assets_service import AssetsService

    service = AssetsService()
    team, uid = fx["team_id"], fx["user_id"]
    r_sheet, r_worn, _unused = fx["resource_ids"]

    src = await service.create_asset(
        team,
        AssetCreate(
            asset_type="character",
            name=_uniq("Duplicable"),
            attrs={"height": "tall"},
            tags={"role": ["hero"]},
        ),
        uid,
    )
    costume = await service.create_asset(
        team, AssetCreate(asset_type="costume", name=_uniq("Night Cloak")), uid
    )
    aid = int(src["id"])
    await service.add_link(
        aid, team, LinkRequest(to_asset_id=costume["id"], relation="wears")
    )
    night = await service.create_loadout(
        aid, team, LoadoutCreate(name="Night raid", costume_ids=[costume["id"]])
    )
    # The default is now the SECOND loadout created — so a copy made in source
    # order would insert a non-default first and the default second.
    await service.update_loadout(
        aid, team, int(night["id"]), LoadoutUpdate(is_default=True)
    )
    await service.attach_file(
        aid, team, AttachFileRequest(resource_id=str(r_sheet), slot="sheet"), uid
    )
    await service.attach_file(
        aid,
        team,
        AttachFileRequest(resource_id=str(r_worn), slot="worn", loadout_id=night["id"]),
        uid,
    )
    await service.link_project(aid, team, fx["project_id"], uid)

    copied = await service.duplicate(aid, team, uid, DuplicateRequest())
    new_id = int(copied["id"])

    assert copied["source"] == "duplicated"
    assert copied["duplicated_from"] == src["id"]
    assert copied["is_system_preset"] is False
    assert copied["attrs"] == {"height": "tall"}
    assert copied["tags"] == {"role": ["hero"]}

    rows = await pg.fetch(
        "SELECT name, is_default FROM asset_loadouts WHERE asset_id = $1 "
        "ORDER BY name",
        new_id,
    )
    assert [(r["name"], r["is_default"]) for r in rows] == [
        ("Default", False),
        ("Night raid", True),
    ], "the copy must carry both loadouts and exactly one default"

    new_night = await pg.fetchval(
        "SELECT id FROM asset_loadouts WHERE asset_id = $1 AND name = 'Night raid'",
        new_id,
    )
    worn = await pg.fetchval(
        "SELECT loadout_id FROM asset_files WHERE asset_id = $1 AND slot = 'worn'",
        new_id,
    )
    assert int(worn) == int(new_night)
    assert int(worn) != int(night["id"]), "the file still points at the SOURCE"
    sheet = await pg.fetchrow(
        "SELECT loadout_id, resource_id FROM asset_files "
        "WHERE asset_id = $1 AND slot = 'sheet'",
        new_id,
    )
    assert sheet["loadout_id"] is None and int(sheet["resource_id"]) == r_sheet

    links = await pg.fetch(
        "SELECT to_asset_id, relation FROM asset_links WHERE from_asset_id = $1",
        new_id,
    )
    assert [(str(r["to_asset_id"]), r["relation"]) for r in links] == [
        (costume["id"], "wears")
    ]
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM asset_project_refs WHERE asset_id = $1", new_id
        )
        == 0
    ), "project refs are the source's usage, not the copy's"


# ── 15. duplicate: a name collision is a typed 409, not an aborted tx ───────


@_skip
async def test_duplicate_name_collision_is_a_typed_409_and_writes_nothing(
    orm_dsn, pg, fx
):
    """``create``'s 409 path resolves the existing id by SELECTing AFTER the
    IntegrityError — which inside ``duplicate``'s transaction would be a
    PendingRollbackError (an untyped 500), because the failed INSERT already
    aborted it. So ``duplicate`` asks ``find_by_name`` BEFORE inserting and
    ``create_raw`` never asks after. Only a real server can tell the two apart:
    against a stub, both answer 409.
    """
    from app.schemas.assets import AssetCreate, DuplicateRequest
    from app.services.assets.assets_service import AssetError, AssetsService

    service = AssetsService()
    team, uid = fx["team_id"], fx["user_id"]
    base = _uniq("Bamboo Grove")

    src = await service.create_asset(
        team, AssetCreate(asset_type="location", name=base), uid
    )
    clash = await service.create_asset(
        team, AssetCreate(asset_type="location", name=f"{base} (copy)"), uid
    )

    with pytest.raises(AssetError) as excinfo:
        await service.duplicate(int(src["id"]), team, uid, DuplicateRequest())

    assert excinfo.value.status == 409 and excinfo.value.code == "asset_exists"
    assert excinfo.value.extra["existing_asset_id"] == clash["id"]
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM assets WHERE scope_id = $1 AND asset_type = "
            "'location' AND deleted_at IS NULL",
            team,
        )
        == 2
    ), "the refused duplicate must not have left a row behind"


# ── 16. count_by_type: scope-only, preset-free, soft-delete-aware ──────────


@_skip
async def test_count_by_type_ignores_presets_other_scopes_and_trashed_rows(
    orm_dsn, pg, fx
):
    """The three exclusions the sidebar badges rest on, against a real server.

    Each one is a way the badge could over-count while every unit test stayed
    green, because a stubbed session replays whatever rows the test author
    thought the query would return:

    * a **system preset** is global — ``list()`` unions it into every scope, so
      counting it would give every team the same non-zero floor no action of
      theirs can move. The preset here carries a ``scope_id`` (the
      ``assets_scope_or_preset`` CHECK is an OR, so that is legal), which is
      precisely the row a scope-only predicate would wrongly count;
    * a **soft-deleted** asset must stop counting the moment it is trashed —
      the badge is how the user sees the delete took effect;
    * another scope's assets must not leak in at all.

    Zero-fill is asserted too: ``audio`` has no rows here and must read 0, not
    be missing.
    """
    from app.db.session import write_scope
    from app.models import Assets
    from app.repositories.assets_repository import AssetsRepository

    repo = AssetsRepository()
    team = fx["team_id"]

    a = await _make_asset(repo, fx, "character", _uniq("Counted Lead"))
    await _make_asset(repo, fx, "character", _uniq("Counted Second"))
    await _make_asset(repo, fx, "location", _uniq("Counted Grove"))
    trashed = await _make_asset(repo, fx, "prop", _uniq("Counted Blade"))

    # A preset that DOES carry this scope's id — the row a predicate that only
    # filtered on scope_id would happily count.
    async with write_scope() as session:
        session.add(
            Assets(
                scope_id=team,
                asset_type="prompt",
                name=_uniq("Preset In Scope"),
                is_system_preset=True,
                source="system_preset",
                prompt_positive="cinematic lighting",
                created_by=fx["user_id"],
            )
        )
        await session.flush()

    # A second team's asset, to prove the scope predicate is doing work.
    other_team = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) "
        "RETURNING id",
        "Asset Counts Other Team",
        fx["user_id"],
        uuid.uuid4().hex[:16],
    )
    try:
        await repo.create(
            int(other_team),
            {"asset_type": "character", "name": _uniq("Not Ours")},
            fx["user_id"],
        )

        before = await repo.count_by_type(team)
        assert before["character"] == 2, before
        assert before["location"] == 1 and before["prop"] == 1
        # The in-scope preset is NOT counted, even though it is in this scope.
        assert before["prompt"] == 0, before
        # A type with no rows reads 0 rather than being absent.
        assert before["audio"] == 0 and "audio" in before

        await repo.soft_delete(int(trashed["id"]), team)
        after = await repo.count_by_type(team)
        assert after["prop"] == 0, "a trashed asset kept counting"
        assert after["character"] == 2, "soft delete moved an unrelated tally"

        # Negative control: the row really is still on disk, so the drop above
        # is the predicate working, not the fixture having vanished.
        assert (
            await pg.fetchval(
                "SELECT count(*) FROM assets WHERE id = $1", int(trashed["id"])
            )
            == 1
        )
        assert int(a["id"]) > 0
    finally:
        await pg.execute("DELETE FROM assets WHERE scope_id = $1", int(other_team))
        await pg.execute("DELETE FROM teams WHERE id = $1", int(other_team))


# ── 17. create_asset: the asset and its Default loadout commit together ─────


@_skip
async def test_create_asset_rolls_the_character_back_when_its_loadout_fails(
    orm_dsn, pg, fx
):
    """The P3/M3 fix. Before it, ``create_asset`` ran two transactions: a
    Default loadout that failed to insert left a COMMITTED character with no
    loadout — the one state ``create_loadout`` exists to make impossible —
    while the caller saw only an error.

    Only a real server can tell the fix from a no-op: with a fake repo the
    write "not happening" is the fake declining to append to a dict, which
    proves nothing about a transaction. Here the assertion is a SELECT on
    another connection AFTER the raise.
    """
    from app.schemas.assets import AssetCreate
    from app.services.assets.assets_service import AssetsService

    service = AssetsService()
    team, uid = fx["team_id"], fx["user_id"]
    name = _uniq("Doomed Hero")

    async def _boom(asset_id, fields):
        raise RuntimeError("loadout insert failed")

    service.relations.create_loadout = _boom

    with pytest.raises(RuntimeError):
        await service.create_asset(
            team, AssetCreate(asset_type="character", name=name), uid
        )

    assert (
        await pg.fetchval(
            "SELECT count(*) FROM assets WHERE scope_id = $1 AND name = $2",
            team,
            name,
        )
        == 0
    ), "the character survived a failed Default loadout — two transactions again"

    # Negative control: the same call WITHOUT the injected failure does commit,
    # so the zero above is the rollback and not a fixture that never inserts.
    ok_name = _uniq("Live Hero")
    service_ok = AssetsService()
    created = await service_ok.create_asset(
        team, AssetCreate(asset_type="character", name=ok_name), uid
    )
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM asset_loadouts WHERE asset_id = $1 AND is_default",
            int(created["id"]),
        )
        == 1
    )


@_skip
async def test_create_asset_duplicate_stays_a_typed_409_inside_its_new_uow(
    orm_dsn, pg, fx
):
    """Case 15's finding now applies to ``create_asset`` too, because it opens a
    transaction: a unique violation aborts it, so the id lookup
    ``AssetsRepository.create`` used to run afterwards would raise
    PendingRollbackError — an untyped 500 where the API contract says 409 with
    the clashing id. The pre-check is what keeps it typed, and only a real
    server distinguishes the two.
    """
    from app.schemas.assets import AssetCreate
    from app.services.assets.assets_service import AssetError, AssetsService

    service = AssetsService()
    team, uid = fx["team_id"], fx["user_id"]
    name = _uniq("Bamboo Grove")

    first = await service.create_asset(
        team, AssetCreate(asset_type="location", name=name), uid
    )
    with pytest.raises(AssetError) as excinfo:
        # Case-insensitive: uq_assets_scope_type_name keys on lower(name).
        await service.create_asset(
            team, AssetCreate(asset_type="location", name=name.upper()), uid
        )

    assert excinfo.value.status == 409 and excinfo.value.code == "asset_exists"
    assert excinfo.value.extra["existing_asset_id"] == first["id"]
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM assets WHERE scope_id = $1 AND asset_type = "
            "'location' AND deleted_at IS NULL",
            team,
        )
        == 1
    ), "the refused create left a row behind"

    # The session is still usable afterwards — a PendingRollbackError would
    # have poisoned it, and this call is what would surface that.
    again = await service.create_asset(
        team, AssetCreate(asset_type="location", name=_uniq("Reed Marsh")), uid
    )
    assert again["id"] != first["id"]


# ── 18. the q filter matches the user's text literally (LIKE escaping) ──────


@_skip
async def test_q_filter_treats_like_metacharacters_as_literal_text(orm_dsn, pg, fx):
    """M2. ``_like_escape`` + ``escape="\\\\"`` is a claim about how PostgreSQL
    reads the pattern, and the compiled-SQL pin can only show the ESCAPE clause
    is emitted — whether the server then matches ``a_b`` against ``axb`` is the
    server's answer, not SQLAlchemy's.

    The failure this prevents is a filter that silently WIDENS: a search for
    ``100%`` returning the whole shelf reads as "search is broken", never as an
    error.
    """
    from app.repositories.assets_repository import AssetsRepository

    repo = AssetsRepository()
    team, uid = fx["team_id"], fx["user_id"]
    tag = uuid.uuid4().hex[:12]

    literal = await repo.create(team, {"asset_type": "prop", "name": f"a_b {tag}"}, uid)
    decoy = await repo.create(team, {"asset_type": "prop", "name": f"axb {tag}"}, uid)
    percent = await repo.create(
        team, {"asset_type": "prop", "name": f"100% {tag}"}, uid
    )

    async def _names(q):
        return {r["name"] for r in await repo.list(team, q=q, limit=200)}

    # `_` is a single-char wildcard unescaped: without the fix `axb` matches.
    hit = await _names(f"a_b {tag}")
    assert literal["name"] in hit
    assert decoy["name"] not in hit, "'_' still matched any character"

    # `%` matches everything unescaped, so this q would return all three.
    pct = await _names("100%")
    assert percent["name"] in pct
    assert literal["name"] not in pct and decoy["name"] not in pct

    # A lone `%` used to be a filter that filters nothing — the widest failure
    # shape. Escaped, it is a search for rows whose name CONTAINS a percent
    # sign, so exactly the one row qualifies (the fixture's other assets are
    # this case's three; the tag keeps every other run's rows out).
    lone = await _names("%")
    assert percent["name"] in lone
    assert (
        literal["name"] not in lone and decoy["name"] not in lone
    ), "'%' was still a match-everything wildcard"

    # Negative control: ordinary text still matches, so the escaping did not
    # simply break search (a pattern of literal backslashes matches nothing).
    assert (await _names(f"axb {tag}")) == {decoy["name"]}
    # And the ILIKE is still case-insensitive.
    assert (await _names(f"AXB {tag}")) == {decoy["name"]}


# ── 19. create_asset joins a caller's transaction instead of nesting ────────


@_skip
async def test_create_asset_inside_a_callers_uow_rolls_back_with_it(orm_dsn, pg, fx):
    """The C-1 regression, reproduced end to end.

    ``_save_as_asset_core`` (generated_inbox_service) calls ``create_asset``
    INSIDE its own ``unit_of_work()``, then attaches the file and flips the
    review state. A nested ``unit_of_work()`` is REQUIRES_NEW — the inner block
    commits independently and the outer rollback does not undo it — so an
    asset created that way would SURVIVE a later failure in the same logical
    operation, as an orphan with no attachment. The user's retry then hits it
    as a 409 asset_exists, with no way forward.

    Only a real server shows this: with a stubbed session, "nested" and
    "joined" both look like the writes happening.
    """
    from app.db.session import unit_of_work
    from app.schemas.assets import AssetCreate
    from app.services.assets.assets_service import AssetsService

    service = AssetsService()
    team, uid = fx["team_id"], fx["user_id"]
    name = _uniq("Orphan Candidate")

    # The id is captured INSIDE the transaction, before the raise, because it
    # is the only way to ask the loadout question directly afterwards.
    doomed: Dict[str, int] = {}
    with pytest.raises(RuntimeError):
        async with unit_of_work():
            row = await service.create_asset(
                team, AssetCreate(asset_type="character", name=name), uid
            )
            doomed["id"] = int(row["id"])
            # Stands in for attach_file / set_review_state failing.
            raise RuntimeError("the rest of the caller's work failed")

    assert "id" in doomed, "create_asset returned no row before the raise"

    assert (
        await pg.fetchval(
            "SELECT count(*) FROM assets WHERE scope_id = $1 AND name = $2",
            team,
            name,
        )
        == 0
    ), "the asset outlived its caller's transaction — nested UoW, orphan row"

    # The Default loadout must not survive either (it would be an orphan of an
    # orphan, invisible to every asset query). Asked by the ROW's id, not by
    # joining back to ``assets``: that join is entailed by the FK once the
    # assert above holds, so it could not have failed and was proving nothing.
    # By id it is a real observation — a loadout that outlived its asset would
    # be a dangling row, and this is what would see it.
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM asset_loadouts WHERE asset_id = $1", doomed["id"]
        )
        == 0
    )

    # Negative control: the same call inside a COMMITTING outer transaction
    # does persist, so the zero above is the rollback reaching it — not
    # create_asset having quietly stopped writing when a caller wraps it.
    kept = _uniq("Kept Hero")
    async with unit_of_work():
        created = await service.create_asset(
            team, AssetCreate(asset_type="character", name=kept), uid
        )
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM asset_loadouts WHERE asset_id = $1 AND is_default",
            int(created["id"]),
        )
        == 1
    ), "joining the outer transaction lost the Default loadout"


# ── 22. mig 449: explicit library membership, against the real server ───────


@_skip
async def test_library_filter_and_counts_split_the_two_membership_states(
    orm_dsn, pg, fx
):
    """The three ``library=`` values and the badge exclusion, on real SQL.

    Every one of these is a WHERE clause, so the unit suite can only prove that
    SQLAlchemy emitted the text — a stubbed session replays whatever rows the
    test author expected. What needs a server is that the predicates PARTITION:
    ``in`` and ``out`` must be complements over the same rows, and ``all`` must
    equal their union. A predicate that quietly matched nothing (or everything)
    passes the compiled-SQL pins and fails here.

    The counts assertion is the one with a user-visible failure mode: the badge
    sits directly above the shelf, and the shelf defaults to ``in``. A badge
    counting project-originated rows would name a number the grid beneath it
    cannot show.
    """
    from app.repositories.assets_repository import AssetsRepository

    repo = AssetsRepository()
    team = fx["team_id"]

    member = await _make_asset(repo, fx, "character", _uniq("Adopted Lead"))
    # ``create`` takes the column dict straight through, which is how the
    # import path lands a row outside the library.
    outsider = await _make_asset(
        repo, fx, "character", _uniq("Imported Extra"), source="script_import"
    )
    await repo.create(
        team,
        {
            "asset_type": "location",
            "name": _uniq("Imported Grove"),
            "source": "script_import",
            "in_library": False,
        },
        fx["user_id"],
    )

    # The bare create defaults to a member — the DEFAULT is what every
    # deliberate path relies on, so it is asserted rather than assumed.
    assert member["in_library"] is True
    # …and the explicit False actually reached the column.
    assert (
        await pg.fetchval(
            "SELECT in_library FROM assets WHERE id = $1", int(outsider["id"])
        )
        is True
    ), "source alone must NOT decide membership — only the explicit value does"

    await repo.update(int(outsider["id"]), team, {"in_library": False})

    def _ids(rows):
        return {int(r["id"]) for r in rows}

    inside = _ids(await repo.list(team, asset_type="character", library="in"))
    outside = _ids(await repo.list(team, asset_type="character", library="out"))
    everything = _ids(await repo.list(team, asset_type="character", library="all"))

    assert int(member["id"]) in inside and int(member["id"]) not in outside
    assert int(outsider["id"]) in outside and int(outsider["id"]) not in inside
    # The partition property — this is what a predicate matching nothing, or
    # everything, cannot satisfy.
    assert inside & outside == set()
    assert inside | outside == everything

    # The default is "in": a caller that says nothing gets the library, not
    # everything. Asserted on the REPO because the router default is a separate
    # value that could drift from it.
    assert _ids(await repo.list(team, asset_type="character")) == inside

    counts = await repo.count_by_type(team)
    assert counts["character"] == 1, "an out-of-library row inflated a badge"
    assert counts["location"] == 0, counts


@_skip
async def test_removing_from_the_library_is_not_a_delete(orm_dsn, pg, fx):
    """``set_library_membership`` moves ONE column. The row, its project refs
    and its loadouts all stay — the whole reason membership is a flag rather
    than a deletion is that the project page keeps showing these."""
    from app.repositories.assets_repository import AssetsRepository
    from app.schemas.assets import AssetCreate
    from app.services.assets.assets_service import AssetsService

    repo = AssetsRepository()
    service = AssetsService(assets_repo=repo)
    team, uid = fx["team_id"], fx["user_id"]

    row = await service.create_asset(
        team, AssetCreate(asset_type="character", name=_uniq("Kept Hero")), uid
    )
    asset_id = int(row["id"])
    await service.link_project(asset_id, team, int(fx["project_id"]), uid)

    out = await service.set_library_membership(asset_id, team, in_library=False)
    assert out["in_library"] is False

    live = await pg.fetchrow(
        "SELECT in_library, deleted_at FROM assets WHERE id = $1", asset_id
    )
    assert live["in_library"] is False and live["deleted_at"] is None
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM asset_project_refs WHERE asset_id = $1", asset_id
        )
        == 1
    ), "removing from the library unlinked the project"
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM asset_loadouts WHERE asset_id = $1", asset_id
        )
        == 1
    ), "removing from the library dropped the Default loadout"

    # Idempotent: asking again for the state it already holds is the outcome,
    # not a conflict.
    again = await service.set_library_membership(asset_id, team, in_library=False)
    assert again["in_library"] is False

    back = await service.set_library_membership(asset_id, team, in_library=True)
    assert back["in_library"] is True


# ── 21. resolve_legacy: JSONB containment over attrs.legacy_ids ────────────


@_skip
async def test_resolve_legacy_matches_the_pair_and_only_the_pair(orm_dsn, pg, fx):
    """``attrs @> '{"legacy_ids": [[table, id]]}'`` is the second predicate in
    this repository that only PostgreSQL can vouch for.

    Three properties SQLAlchemy cannot answer for, all of which would fail
    silently — as an empty result that reads exactly like "that entity was
    never migrated":

    * containment reaches INTO the nested pair arrays (a merged asset carries
      several, and the probe carries one);
    * the two halves match together, so a ``project_characters`` id does not
      answer for a ``project_lib_entities`` id of the same number;
    * the id is a JSON *number*, the way the migration wrote it — ``"12"`` and
      ``12`` are different JSONB scalars.

    Provenance is written here the way ``backfill_assets_from_project_entities``
    writes it, INCLUDING the pre-rename table labels (mig 447 renamed the
    tables; the labels deliberately stayed).
    """
    from app.repositories.assets_repository import AssetsRepository

    repo = AssetsRepository()
    merged = await _make_asset(
        repo,
        fx,
        "character",
        _uniq("Old Zhang"),
        attrs={
            "legacy_ids": [["project_characters", 12], ["project_characters", 44]],
            "merged_from": [["project_characters", 44]],
            "legacy_cover_url": None,
        },
    )
    harbour = await _make_asset(
        repo,
        fx,
        "location",
        _uniq("Harbour"),
        attrs={"legacy_ids": [["project_lib_entities", 12]]},
    )
    plain = await _make_asset(repo, fx, "prop", _uniq("Hand Made"))

    team = fx["team_id"]
    # Every legacy row a merge absorbed resolves to the one asset it became.
    for legacy_id in (12, 44):
        assert await repo.resolve_legacy(team, "project_characters", legacy_id) == int(
            merged["id"]
        )
    # Same id, other table → the other asset. This is the pair matching, and it
    # is the assertion that would fail if the id were compared on its own.
    assert await repo.resolve_legacy(team, "project_lib_entities", 12) == int(
        harbour["id"]
    )
    # A pair nobody carries, and an asset with no provenance at all (the shape
    # ADOPTION leaves behind) → None, not an error.
    assert await repo.resolve_legacy(team, "project_characters", 99) is None
    assert int(plain["id"]) not in {
        await repo.resolve_legacy(team, "project_characters", 12),
        await repo.resolve_legacy(team, "project_lib_entities", 12),
    }

    # ``merged_from`` repeats the same pairs but is not the identity map — a
    # probe keyed on the ``legacy_ids`` KEY must not reach it. 44 is in BOTH
    # here, so the control is an asset carrying it ONLY under merged_from.
    only_merged = await _make_asset(
        repo,
        fx,
        "prop",
        _uniq("Merge Record"),
        attrs={"merged_from": [["project_lib_entities", 77]]},
    )
    assert await repo.resolve_legacy(team, "project_lib_entities", 77) is None
    assert only_merged  # the row exists; it is the KEY that keeps it out

    # A soft-deleted asset stops answering: its name is free again and the
    # shelf no longer lists it, so pointing a canvas card at it would
    # resurrect a row the user deleted.
    assert await repo.soft_delete(int(merged["id"]), team) is True
    assert await repo.resolve_legacy(team, "project_characters", 12) is None

    # Cross-scope: the same provenance in another team is invisible from here.
    other_team = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) "
        "RETURNING id",
        "Other Asset Team",
        uuid.UUID(fx["user_id"]),
        uuid.uuid4().hex[:16],
    )
    try:
        await repo.create(
            int(other_team),
            {
                "asset_type": "character",
                "name": _uniq("Old Zhang Elsewhere"),
                "attrs": {"legacy_ids": [["project_characters", 12]]},
            },
            fx["user_id"],
        )
        assert await repo.resolve_legacy(team, "project_characters", 12) is None
        assert (
            await repo.resolve_legacy(int(other_team), "project_characters", 12)
            is not None
        )
    finally:
        await pg.execute("DELETE FROM assets WHERE scope_id = $1", int(other_team))
        await pg.execute("DELETE FROM teams WHERE id = $1", int(other_team))


# ── the provenance column: WRITE joined to READ (P4 Task 7, final wave) ─────


@_skip
async def test_source_asset_stamp_is_found_by_the_inbox_filter(orm_dsn, pg, fx):
    """`set_source_asset` (the write) and `list_inbox(source_asset_id=...)`
    (the read) are pinned in different files against different fixtures. This
    is the one case that joins them on a real database.

    Two things only Postgres can answer here: whether the BIGINT the write
    binds is the same value the filter compares (the two sides reach the column
    through the same ORM attribute, but a Snowflake past 2^53 is exactly where
    "the same attribute" has stopped being enough in this repo before), and
    whether the partial index's predicate — `WHERE source_asset_id IS NOT NULL`
    — leaves unstamped rows reachable by every OTHER filter.
    """
    from app.repositories.assets_repository import AssetsRepository
    from app.repositories.generated_media_repository import GeneratedMediaRepository

    repo = AssetsRepository()
    asset = await repo.create(
        fx["team_id"],
        {"asset_type": "character", "name": _uniq("Stamped Subject")},
        fx["user_id"],
    )
    other = await repo.create(
        fx["team_id"],
        {"asset_type": "character", "name": _uniq("Unstamped Subject")},
        fx["user_id"],
    )

    async def _gen(name: str) -> int:
        return int(
            await pg.fetchval(
                "INSERT INTO generated_media "
                "(scope_id, creator_id, media_kind, file_path, origin_kind) "
                "VALUES ($1, $2, 'image', $3, 'canvas_generate') RETURNING id",
                fx["team_id"],
                uuid.UUID(fx["user_id"]),
                f"/tmp/{name}.png",
            )
        )

    stamped_id = await _gen("stamped")
    bare_id = await _gen("bare")

    gm = GeneratedMediaRepository()
    written = await gm.set_source_asset(
        stamped_id, int(asset["id"]), scope_id=fx["team_id"]
    )
    assert written is not None
    # The wire shape stringifies ids; the comparison is on the VALUE.
    assert int(written["source_asset_id"]) == int(asset["id"])

    page = await gm.list_inbox(fx["team_id"], source_asset_id=int(asset["id"]))
    got = {int(r["id"]) for r in page["items"]}
    assert got == {stamped_id}, "the write is not reachable by the read it exists for"

    # A different asset does not inherit it, and the unstamped row is still
    # listable without the filter — the partial index hides nothing.
    empty = await gm.list_inbox(fx["team_id"], source_asset_id=int(other["id"]))
    assert empty["items"] == []
    unfiltered = {int(r["id"]) for r in (await gm.list_inbox(fx["team_id"]))["items"]}
    assert {stamped_id, bare_id} <= unfiltered


@_skip
async def test_the_stamp_refuses_a_row_in_another_scope(orm_dsn, pg, fx):
    """`scope_id` is optional on `set_source_asset` and every caller passes it.
    Without the predicate this is an UPDATE by primary key, and a caller handed
    a user-supplied `gen_id` could stamp a row in someone else's tenant."""
    from app.repositories.assets_repository import AssetsRepository
    from app.repositories.generated_media_repository import GeneratedMediaRepository

    repo = AssetsRepository()
    asset = await repo.create(
        fx["team_id"],
        {"asset_type": "character", "name": _uniq("Scoped Subject")},
        fx["user_id"],
    )
    other_team = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) "
        "RETURNING id",
        "Other Stamp Team",
        uuid.UUID(fx["user_id"]),
        uuid.uuid4().hex[:16],
    )
    try:
        foreign_id = int(
            await pg.fetchval(
                "INSERT INTO generated_media "
                "(scope_id, creator_id, media_kind, file_path, origin_kind) "
                "VALUES ($1, $2, 'image', '/tmp/foreign.png', 'canvas_generate') "
                "RETURNING id",
                int(other_team),
                uuid.UUID(fx["user_id"]),
            )
        )

        gm = GeneratedMediaRepository()
        refused = await gm.set_source_asset(
            foreign_id, int(asset["id"]), scope_id=fx["team_id"]
        )

        # `None` is the typed failure, and the row is genuinely untouched —
        # a returning-clause miss with a write behind it would be worse than
        # no check at all.
        assert refused is None
        assert (
            await pg.fetchval(
                "SELECT source_asset_id FROM generated_media WHERE id = $1",
                foreign_id,
            )
            is None
        )
    finally:
        await pg.execute(
            "DELETE FROM generated_media WHERE scope_id = $1", int(other_team)
        )
        await pg.execute("DELETE FROM teams WHERE id = $1", int(other_team))


async def _ensure_member(pg, user_id: str, team_id: int) -> None:
    """Make ``user_id`` a member of ``team_id``, and PROVE the row is there.

    ``teams_add_owner_trigger`` already inserts the owner's membership, so a
    bare INSERT is a unique violation rather than setup. The assertion is the
    point: without a ``team_members`` row the visibility predicate answers
    "you see nothing", which would make every negative assertion below pass
    for the wrong reason.
    """
    await pg.execute(
        "INSERT INTO team_members (user_id, team_id, role) VALUES ($1, $2, 'owner') "
        "ON CONFLICT DO NOTHING",
        uuid.UUID(str(user_id)),
        int(team_id),
    )
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM team_members WHERE user_id = $1 AND team_id = $2",
            uuid.UUID(str(user_id)),
            int(team_id),
        )
    ) == 1


# ── 23. list_accessible: ruling B's predicate, executed by PostgreSQL ────────
#
# ``list_accessible`` (P5 ruling B) is the ONE query behind both the chat's
# asset-ref resolver and ``GET /assets/search``. Everything above it is proved
# without a server: the compiled-SQL pins in
# tests/services/assets/test_assets_repository_sql.py show what is emitted, and
# the clause interpreter in tests/services/ai/chat/test_asset_ref_resolver.py
# and tests/api/test_assets_search_router.py evaluates the same statement in
# memory. None of that executes anything.
#
# Two claims in it are the server's alone. The membership subquery binds a
# ``str`` user id against ``team_members.user_id``, a UUID column — whether the
# driver accepts that is a driver fact, not a SQLAlchemy one, and it is the
# whole authorization arm. And the ``q`` filter's ``ESCAPE '\'`` is a claim
# about how PostgreSQL reads a LIKE pattern: unescaped, a search for ``a_b``
# also matches ``axb`` and a search for ``%`` matches everything — a filter
# that silently WIDENS, which reads as "search is broken" rather than as an
# error.


@_skip
async def test_list_accessible_is_membership_or_preset_and_hides_deleted(
    orm_dsn, pg, fx
):
    """Ruling B, case by case, against the real server.

    An outsider with their own team and their own asset is set up on purpose:
    the negative case must be a row somebody really can see, not an absent one
    — a predicate that matched nothing would pass an "it is not in my results"
    assertion for the wrong reason. So the same query is asked twice, once as
    each user, and each must see exactly their own side plus the preset.
    """
    from app.db.session import write_scope
    from app.models import Assets
    from app.repositories.assets_repository import AssetsRepository

    repo = AssetsRepository()
    team, uid = fx["team_id"], fx["user_id"]

    # The predicate joins through ``team_members``, so the case states that
    # precondition rather than assuming it. ``teams_add_owner_trigger`` already
    # seeds the owner's row, hence ON CONFLICT — the assertion after it is what
    # makes the setup falsifiable if that trigger ever stops firing.
    await _ensure_member(pg, uid, team)

    mine = await _make_asset(repo, fx, "character", _uniq("Mine"))
    erased = await _make_asset(repo, fx, "character", _uniq("Erased"))
    assert await repo.soft_delete(int(erased["id"]), team) is True

    async with write_scope() as session:
        obj = Assets(
            scope_id=None,
            asset_type="prompt",
            name=_uniq("Preset Accessible"),
            is_system_preset=True,
            source="system_preset",
            prompt_positive="cinematic lighting, 35mm",
            created_by=uid,
        )
        session.add(obj)
        await session.flush()
        preset_id = int(obj.id)

    other_uid = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", other_uid)
    other_team = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) "
        "RETURNING id",
        "Outsider Team",
        other_uid,
        uuid.uuid4().hex[:16],
    )
    try:
        await _ensure_member(pg, str(other_uid), int(other_team))
        theirs_id = int(
            (
                await repo.create(
                    int(other_team),
                    {"asset_type": "character", "name": _uniq("Theirs")},
                    str(other_uid),
                )
            )["id"]
        )

        mine_ids = {int(r["id"]) for r in await repo.list_accessible(uid)}
        assert int(mine["id"]) in mine_ids
        assert preset_id in mine_ids, "the is_system_preset arm did not fire"
        assert theirs_id not in mine_ids, "another team's asset leaked"
        assert int(erased["id"]) not in mine_ids

        # The mirror image, so "not in my results" cannot be the whole story.
        their_ids = {int(r["id"]) for r in await repo.list_accessible(str(other_uid))}
        assert theirs_id in their_ids
        assert preset_id in their_ids
        assert int(mine["id"]) not in their_ids

        # include_deleted relaxes ONLY the soft-delete filter. If it also
        # widened membership, this probe would answer about somebody else's
        # asset.
        with_deleted = {
            int(r["id"]) for r in await repo.list_accessible(uid, include_deleted=True)
        }
        assert int(erased["id"]) in with_deleted
        assert theirs_id not in with_deleted

        # asset_ids narrows without relaxing anything: an id the caller cannot
        # see stays invisible even when they name it.
        named = {
            int(r["id"])
            for r in await repo.list_accessible(
                uid, asset_ids=[int(mine["id"]), theirs_id, preset_id]
            )
        }
        assert named == {int(mine["id"]), preset_id}
    finally:
        await pg.execute("DELETE FROM assets WHERE scope_id = $1", int(other_team))
        await pg.execute("DELETE FROM teams WHERE id = $1", int(other_team))
        await pg.execute("DELETE FROM auth.users WHERE id = $1", other_uid)


@_skip
async def test_list_accessible_filters_match_literally_and_partition(orm_dsn, pg, fx):
    """``q`` / ``asset_type`` / ``library`` / ``limit`` on the accessible set.

    ``q`` is the one with a silent failure mode: unescaped, ``a_b`` matches
    ``axb`` and ``%`` matches every row the caller can see — the picker would
    look like it was filtering and would not be. The other three are asserted
    as a PARTITION (``in`` and ``out`` complements, their union reachable
    without a library filter), because a predicate that quietly matched
    nothing passes any single-sided check.
    """
    from app.repositories.assets_repository import AssetsRepository

    repo = AssetsRepository()
    team, uid = fx["team_id"], fx["user_id"]
    tag = uuid.uuid4().hex[:12]

    await _ensure_member(pg, uid, team)

    literal = await _make_asset(repo, fx, "prop", f"a_b {tag}")
    decoy = await _make_asset(repo, fx, "prop", f"axb {tag}")
    percent = await _make_asset(repo, fx, "prop", f"100% {tag}")
    character = await _make_asset(repo, fx, "character", f"Cast a_b {tag}")
    outside = await repo.create(
        team,
        {
            "asset_type": "location",
            "name": f"Imported Grove {tag}",
            "source": "script_import",
            "in_library": False,
        },
        uid,
    )
    ours = {int(r["id"]) for r in (literal, decoy, percent, character, outside)}

    async def _ids(**kw):
        rows = await repo.list_accessible(uid, **kw)
        return {int(r["id"]) for r in rows}

    # ``_`` is a single-character wildcard unless escaped: without the escape
    # ``axb`` (and the character row) come back too.
    hit = await _ids(q=f"a_b {tag}")
    assert int(literal["id"]) in hit
    assert int(decoy["id"]) not in hit, "'_' still matched any character"

    pct = await _ids(q="100%")
    assert int(percent["id"]) in pct
    assert int(literal["id"]) not in pct and int(decoy["id"]) not in pct

    # A lone ``%`` used to be a filter that filters nothing — the widest
    # failure shape. Escaped, it searches for a literal percent sign.
    lone = await _ids(q="%")
    assert int(percent["id"]) in lone
    assert not (ours - {int(percent["id"])}) & lone, "'%' was still match-everything"

    # Ordinary text still matches (a pattern of literal backslashes would match
    # nothing), and the ILIKE is still case-insensitive.
    assert await _ids(q=f"AXB {tag}") & ours == {int(decoy["id"])}

    typed = await _ids(asset_type="character")
    assert int(character["id"]) in typed
    assert not (typed & {int(literal["id"]), int(percent["id"])})

    in_lib = await _ids(library="in") & ours
    out_lib = await _ids(library="out") & ours
    both = await _ids(library="all") & ours
    assert out_lib == {int(outside["id"])}
    assert in_lib | out_lib == both == ours
    assert not (in_lib & out_lib), "in/out are not complements"
    # ``library=None`` (the default) means no predicate at all, which must
    # reach the same rows as an explicit ``all``.
    assert await _ids() & ours == ours

    assert len(await repo.list_accessible(uid, limit=1)) == 1
