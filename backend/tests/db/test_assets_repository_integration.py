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
