"""DB-backed integration tests for ``PromptCatalogRepository`` (spec 2026-09-05).

WHY THIS FILE EXISTS
────────────────────
The repository is pure ORM statements and its only other test
(``tests/services/prompts/test_prompt_catalog_repository_sql.py``) compiles them
against a dialect and greps the SQL text — nothing has ever asked Postgres to
RUN them. That is the same gap ``test_assets_repository_integration.py`` was
written to close for the asset repositories, and four things here can only be
settled by a server:

  * ``has_prompt_expr()`` is a ``btrim(coalesce(...))`` / JSONB clause tree, and
    what it counts as "has a prompt" IS the catalog's membership rule. A
    compiled-SQL pin can see the function name; only Postgres can say whether a
    whitespace-only ``gen_prompt`` is excluded while a ``slide_prompts`` map with
    no ``gen_prompt`` at all is included. Those two rows are the whole point of
    the "image ∪ album" half of the catalog.
  * the ``DISTINCT`` plus the ``JOIN resource_items`` is a fan-out risk: a
    resource filed into a scope twice would return twice, and every consumer
    (the resources tab, the canvas panel) renders one card per row.
  * ``example_file_ids`` filters ``slot = 'examples'``. A wrong or dropped slot
    predicate returns a template's cover and reference files as its thumbnails —
    a wrong answer that looks like data, not like an error.
  * the projection lists eleven columns by name, ``prompt_origin`` among them
    (mig 455). A column that does not exist is a 42703 at execution time and
    invisible to a compiled pin.

Transport: asyncpg on ``INTEGRATION_DATABASE_URL`` for fixture setup; the
repository itself goes through ``app.db.session`` (SQLAlchemy async engine),
which the ``orm_dsn`` fixture repoints at the same DSN. Same pattern as
``tests/db/test_assets_repository_integration.py``.

The calls run inside ``system_request_scope`` because that is how the catalog
service calls them (``services/prompts/catalog_service.py::_pictures``):
``Resources`` is scope-mixed, so under a plain user scope the SELECT would get
``creator_id = me`` injected and a teammate's uploads would silently vanish.

Point it at any CI-way Postgres (ci_bootstrap.sql → schema_baseline.sql →
migrations above the watermark, which includes 455):

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/db/test_prompt_catalog_repository_integration.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset. Every test builds its own
team/resource fixture with fresh ids and tears it down in a ``finally``, so the
cases are independent and the file is re-runnable against the same DB.
"""

from __future__ import annotations

import json
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
    reason="INTEGRATION_DATABASE_URL not set — prompt-catalog integration tests need a DB.",
)


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
    """One team (the catalog scope) and one owner.

    Resources are added per case rather than here: each case is about WHICH
    rows the predicate admits, so the rows are the test data, not the scaffold.
    ``resources`` are filed into the scope through ``resource_items`` — that
    join is what ``scope_id`` filters on.
    """
    user_id = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
    team_id = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) "
        "RETURNING id",
        "Prompt Catalog Test Team",
        user_id,
        uuid.uuid4().hex[:16],
    )
    created: Dict[str, List[int]] = {"resources": [], "assets": []}
    try:
        yield {
            "user_id": str(user_id),
            "team_id": int(team_id),
            "created": created,
        }
    finally:
        # asset_files cascades from assets; resource_items from neither, so it
        # goes first. Reverse FK order throughout.
        await pg.execute(
            "DELETE FROM assets WHERE scope_id = $1 OR created_by = $2",
            int(team_id),
            user_id,
        )
        await pg.execute(
            "DELETE FROM resource_items WHERE resource_id = ANY($1::bigint[])",
            created["resources"],
        )
        await pg.execute(
            "DELETE FROM resources WHERE id = ANY($1::bigint[])", created["resources"]
        )
        await pg.execute("DELETE FROM teams WHERE id = $1", int(team_id))
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)


async def _resource(
    pg,
    fx,
    filename: str,
    *,
    in_scope: bool = True,
    is_trashed: bool = False,
    **cols: Any,
) -> int:
    """One ``resources`` row, optionally filed into the fixture's scope."""
    names = ["creator_id", "source_type", "filename", "is_trashed"]
    values: List[Any] = [
        uuid.UUID(fx["user_id"]),
        "upload",
        filename,
        is_trashed,
    ]
    for k, v in cols.items():
        names.append(k)
        values.append(v)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(values)))
    rid = await pg.fetchval(
        f"INSERT INTO resources ({', '.join(names)}) "
        f"VALUES ({placeholders}) RETURNING id",
        *values,
    )
    fx["created"]["resources"].append(int(rid))
    if in_scope:
        await pg.execute(
            "INSERT INTO resource_items (resource_id, scope_id) VALUES ($1, $2)",
            rid,
            fx["team_id"],
        )
    return int(rid)


async def _asset(pg, fx, name: str) -> int:
    aid = await pg.fetchval(
        "INSERT INTO assets (scope_id, asset_type, name, created_by) "
        "VALUES ($1, 'prompt', $2, $3) RETURNING id",
        fx["team_id"],
        name,
        uuid.UUID(fx["user_id"]),
    )
    fx["created"]["assets"].append(int(aid))
    return int(aid)


def _repo():
    from app.repositories.prompt_catalog_repository import PromptCatalogRepository

    return PromptCatalogRepository()


def _scope():
    """The ambient scope the catalog service opens around these calls."""
    from app.db.scope import system_request_scope

    return system_request_scope("prompt catalog integration test")


# ── 1. membership: what has_prompt_expr() actually admits ───────────────────


@_skip
async def test_list_prompted_resources_admits_text_and_slides_and_excludes_the_rest(
    orm_dsn, pg, fx
):
    """The four rows that decide the catalog's membership rule.

    ``gen_prompt`` text and a ``slide_prompts`` map are the "image" and "album"
    halves of the catalog; a trashed row and a whitespace-only prompt are the
    two exclusions ``has_prompt_expr()`` exists to make. The whitespace case is
    the one no compiled-SQL pin can settle: the prompt editor writes ``''`` (and
    a user can type spaces) when a field is cleared, so a bare NOT NULL test
    would list a resource with nothing to show.
    """
    typed = await _resource(
        pg, fx, "typed.png", gen_prompt="a lighthouse at dusk", prompt_origin="typed"
    )
    album = await _resource(
        pg,
        fx,
        "album",
        slide_prompts=json.dumps({"001.jpg": {"en": "a red door"}}),
        prompt_origin="captioned",
    )
    await _resource(pg, fx, "trashed.png", gen_prompt="in the bin", is_trashed=True)
    await _resource(pg, fx, "blank.png", gen_prompt="   ")

    async with _scope():
        rows = await _repo().list_prompted_resources(fx["team_id"])

    got = {int(r["id"]) for r in rows}
    assert got == {typed, album}, (
        "expected exactly the text row and the slide-map row; "
        "trashed and whitespace-only must not be catalog members"
    )

    by_id = {int(r["id"]): r for r in rows}
    # The projection really carries the eleven columns the entry builder reads —
    # a renamed or dropped one is a 42703 here and invisible to a compiled pin.
    assert by_id[typed]["gen_prompt"] == "a lighthouse at dusk"
    assert by_id[typed]["prompt_origin"] == "typed"
    assert by_id[album]["slide_prompts"] == {"001.jpg": {"en": "a red door"}}
    assert by_id[album]["gen_prompt"] in (None, "")
    for r in rows:
        assert set(r) >= {
            "id",
            "filename",
            "media_id",
            "gen_prompt",
            "gen_prompt_zh",
            "gen_prompt_negative",
            "gen_prompt_negative_zh",
            "gen_params",
            "slide_prompts",
            "prompt_origin",
            "updated_at",
        }


@_skip
async def test_list_prompted_resources_is_scoped_and_deduped(orm_dsn, pg, fx):
    """Three properties the JOIN puts at risk, none of them visible to a stub.

    A prompted resource filed into ANOTHER team's scope must not appear — that
    predicate is the only thing keeping one team's uploads out of another's
    catalog, and this case is written with a real second scope rather than an
    unfiled row: an unfiled row is excluded by the INNER JOIN alone, so it
    would pass even with the scope filter deleted. A row with no
    ``resource_items`` at all is here too, for the join itself. And a resource
    filed TWICE must appear once: the join fans out, every consumer renders one
    card per row, so without ``DISTINCT`` the same prompt shows up twice.
    """
    mine = await _resource(pg, fx, "mine.png", gen_prompt="in my scope")
    await _resource(pg, fx, "unfiled.png", gen_prompt="filed nowhere", in_scope=False)

    other_team = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3) "
        "RETURNING id",
        "Prompt Catalog Other Team",
        uuid.UUID(fx["user_id"]),
        uuid.uuid4().hex[:16],
    )
    try:
        theirs = await _resource(
            pg, fx, "theirs.png", gen_prompt="another team's prompt", in_scope=False
        )
        await pg.execute(
            "INSERT INTO resource_items (resource_id, scope_id) VALUES ($1, $2)",
            theirs,
            int(other_team),
        )
        # File `mine` a second time — a real second scope row for the same id.
        await pg.execute(
            "INSERT INTO resource_items (resource_id, scope_id) VALUES ($1, $2)",
            mine,
            fx["team_id"],
        )
        async with _scope():
            rows = await _repo().list_prompted_resources(fx["team_id"])
        assert [int(r["id"]) for r in rows] == [mine]
    finally:
        # resource_items.scope_id CASCADEs on team delete, so the other team's
        # filing goes with it; the resource row itself is on the fixture list.
        await pg.execute("DELETE FROM teams WHERE id = $1", int(other_team))


# ── 2. example_file_ids: the slot filter ────────────────────────────────────


@_skip
async def test_example_file_ids_returns_only_the_examples_slot(orm_dsn, pg, fx):
    """Thumbnails come from the ``examples`` slot and nowhere else.

    A dropped or wrong slot predicate hands a template's cover and reference
    images back as its examples — data-shaped and wrong, which is exactly the
    failure a stubbed session cannot see. The second asset proves the
    ``asset_id IN (...)`` arm really partitions: its files must not leak into
    the first asset's bucket.
    """
    asset = await _asset(pg, fx, f"Template {uuid.uuid4().hex[:8]}")
    other = await _asset(pg, fx, f"Template {uuid.uuid4().hex[:8]}")
    unasked = await _asset(pg, fx, f"Template {uuid.uuid4().hex[:8]}")

    ex1 = await _resource(pg, fx, "example-1.png")
    ex2 = await _resource(pg, fx, "example-2.png")
    ref = await _resource(pg, fx, "reference.png")
    other_ex = await _resource(pg, fx, "other-example.png")
    unasked_ex = await _resource(pg, fx, "unasked-example.png")

    for aid, rid, slot, order in (
        (asset, ex1, "examples", 0),
        (asset, ex2, "examples", 1),
        (asset, ref, "reference", 0),
        (other, other_ex, "examples", 0),
        (unasked, unasked_ex, "examples", 0),
    ):
        await pg.execute(
            "INSERT INTO asset_files (asset_id, resource_id, slot, sort_order) "
            "VALUES ($1, $2, $3, $4)",
            aid,
            rid,
            slot,
            order,
        )

    async with _scope():
        out = await _repo().example_file_ids([asset, other])

    assert out == {asset: [ex1, ex2], other: [other_ex]}
    assert ref not in out[asset], "the reference slot is not a thumbnail source"
    assert unasked not in out, "an asset that was not asked about must not appear"


@_skip
async def test_example_file_ids_empty_input_and_unknown_asset(orm_dsn, pg, fx):
    """No ids → no query, no rows. An id with no example files → absent, not
    an empty list: the caller reads ``examples.get(id, [])``, so both spellings
    render the same, and this pins which one the DB path actually produces."""
    lonely = await _asset(pg, fx, f"Template {uuid.uuid4().hex[:8]}")

    async with _scope():
        repo = _repo()
        assert await repo.example_file_ids([]) == {}
        assert await repo.example_file_ids([lonely]) == {}
