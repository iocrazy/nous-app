"""DB-backed integration test for the canvas refs archived-output lookup.

WHY THIS FILE EXISTS
────────────────────
``GeneratedMediaRepository.promoted_resource_ids`` is the query that decides
which archived (promoted) canvas outputs get mirrored into
``canvas_resource_refs``. Its unit tests stub the session — they assert on
compiled SQL and on hand-made row tuples — so without this file the statement
has never been executed by Postgres, and three things it now depends on are
exactly the kind a stub cannot answer:

  * ``resource_items`` really is where a resource's scope lives, spelled with
    the columns the ORM model claims (``resource_id`` / ``scope_id``). A wrong
    column name compiles fine in SQLAlchemy and 42703s on the server.
  * the LEFT JOIN + ``ResourceItems.id IS NOT NULL`` flag must come back as a
    real boolean per generation — one row each, in-scope true, out-of-scope
    false — which is the whole basis for "filter, but report what was
    filtered".
  * ``DISTINCT`` has to collapse a resource filed into TWO folders of the same
    scope. Without it that generation answers twice; with a stubbed session
    nothing notices, because the fake never returns the duplicate.

Transport: asyncpg for fixture setup; the repository goes through
``app.db.session``, which the ``orm_dsn`` fixture repoints at the same DSN.
Same pattern as ``tests/db/test_canvas_asset_refs_integration.py``.

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/db/test_promoted_resource_ids_scope_integration.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset.
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
    reason="INTEGRATION_DATABASE_URL not set — canvas refs scope test needs a DB.",
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
    """Two teams, and in each a promoted generation: same shape, different scope.

    Scope A additionally gets a resource filed into TWO folders, which is what
    makes the DISTINCT load-bearing, and a generation whose resource has NO
    ``resource_items`` row at all — a resource can exist un-filed, and that must
    read as out-of-scope rather than as a missing row.
    """
    owner = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", owner)

    async def _team(name: str) -> int:
        return await pg.fetchval(
            "INSERT INTO teams (name, owner_id, invite_code, kind) "
            "VALUES ($1,$2,$3,'collaborative') RETURNING id",
            name,
            owner,
            uuid.uuid4().hex[:16],
        )

    scope_a = await _team(f"Refs Scope A {uuid.uuid4().hex[:8]}")
    scope_b = await _team(f"Refs Scope B {uuid.uuid4().hex[:8]}")

    async def _resource(label: str) -> int:
        return await pg.fetchval(
            "INSERT INTO resources (creator_id, source_type, filename, "
            "current_version, file_type, mime_type) "
            "VALUES ($1,'generated',$2,1,'image','image/png') RETURNING id",
            owner,
            f"{label}-{uuid.uuid4().hex[:8]}.png",
        )

    async def _file(resource_id: int, scope_id: int, folder_id=None) -> None:
        await pg.execute(
            "INSERT INTO resource_items (resource_id, scope_id, folder_id, "
            "added_by) VALUES ($1,$2,$3,$4)",
            resource_id,
            scope_id,
            folder_id,
            owner,
        )

    async def _folder(scope_id: int, name: str) -> int:
        return await pg.fetchval(
            "INSERT INTO folders (scope_id, name, created_by) "
            "VALUES ($1,$2,$3) RETURNING id",
            scope_id,
            name,
            owner,
        )

    async def _generation(scope_id: int, resource_id: int) -> int:
        return await pg.fetchval(
            "INSERT INTO generated_media (scope_id, creator_id, media_kind, "
            "file_path, origin_kind, params, promoted_resource_id, mime) "
            "VALUES ($1,$2,'image',$3,'canvas','{}'::jsonb,$4,'image/png') "
            "RETURNING id",
            scope_id,
            owner,
            f"teams/{scope_id}/generated/{uuid.uuid4().hex}.png",
            resource_id,
        )

    res_a = await _resource("in-scope")
    await _file(res_a, scope_a)

    res_two_folders = await _resource("two-folders")
    folder_1 = await _folder(scope_a, f"F1 {uuid.uuid4().hex[:6]}")
    folder_2 = await _folder(scope_a, f"F2 {uuid.uuid4().hex[:6]}")
    await _file(res_two_folders, scope_a, folder_1)
    await _file(res_two_folders, scope_a, folder_2)

    res_b = await _resource("other-tenant")
    await _file(res_b, scope_b)

    res_unfiled = await _resource("unfiled")

    gen_a = await _generation(scope_a, res_a)
    gen_two_folders = await _generation(scope_a, res_two_folders)
    gen_b = await _generation(scope_b, res_b)
    gen_unfiled = await _generation(scope_a, res_unfiled)

    try:
        yield {
            "scope_a": int(scope_a),
            "scope_b": int(scope_b),
            "gen_a": int(gen_a),
            "gen_two_folders": int(gen_two_folders),
            "gen_b": int(gen_b),
            "gen_unfiled": int(gen_unfiled),
            "res_a": int(res_a),
            "res_two_folders": int(res_two_folders),
            "res_b": int(res_b),
        }
    finally:
        await pg.execute(
            "DELETE FROM generated_media WHERE id = ANY($1::bigint[])",
            [int(gen_a), int(gen_two_folders), int(gen_b), int(gen_unfiled)],
        )
        # resource_items cascades off resources; folders cascade off teams.
        await pg.execute(
            "DELETE FROM resources WHERE id = ANY($1::bigint[])",
            [int(res_a), int(res_two_folders), int(res_b), int(res_unfiled)],
        )
        await pg.execute(
            "DELETE FROM folders WHERE id = ANY($1::bigint[])",
            [int(folder_1), int(folder_2)],
        )
        await pg.execute(
            "DELETE FROM teams WHERE id = ANY($1::bigint[])",
            [int(scope_a), int(scope_b)],
        )
        await pg.execute("DELETE FROM auth.users WHERE id = $1", owner)


@_skip
async def test_only_resources_filed_in_the_asked_scope_come_back(orm_dsn, fx):
    """The tenant check, executed.

    Both generations are archived and both resources exist; only the one filed
    in scope A may be mirrored into a scope-A canvas's refs. This is the case
    the compiled-SQL pin cannot decide: it proves the join finds real rows in
    the real table, not that the string looked right.
    """
    from app.repositories.generated_media_repository import GeneratedMediaRepository

    got = await GeneratedMediaRepository().promoted_resource_ids(
        [fx["gen_a"], fx["gen_b"]], scope_id=fx["scope_a"]
    )
    assert got == {fx["gen_a"]: fx["res_a"]}, (
        "the other tenant's archived resource must not reach this canvas's "
        f"refs mirror; got {got}"
    )

    # And symmetrically, so the filter is not simply "drop the second id".
    got_b = await GeneratedMediaRepository().promoted_resource_ids(
        [fx["gen_a"], fx["gen_b"]], scope_id=fx["scope_b"]
    )
    assert got_b == {fx["gen_b"]: fx["res_b"]}


@_skip
async def test_a_resource_in_two_folders_of_one_scope_answers_once(orm_dsn, fx):
    """DISTINCT, executed.

    Two ``resource_items`` rows for one resource is an ordinary library state
    (the same file filed into two folders). Without DISTINCT the query returns
    that generation twice; the dict build would hide it here, so the row count
    is asserted directly.
    """
    from app.db.session import read_scope
    from app.repositories.generated_media_repository import (
        GeneratedMediaRepository,
        _promoted_resource_ids_stmt,
    )

    async with read_scope() as session:
        rows = (
            await session.execute(
                _promoted_resource_ids_stmt([fx["gen_two_folders"]], fx["scope_a"])
            )
        ).all()
    assert len(rows) == 1, f"DISTINCT must collapse the two folder rows: {rows}"

    got = await GeneratedMediaRepository().promoted_resource_ids(
        [fx["gen_two_folders"]], scope_id=fx["scope_a"]
    )
    assert got == {fx["gen_two_folders"]: fx["res_two_folders"]}


@_skip
async def test_out_of_scope_rows_arrive_flagged_not_missing(orm_dsn, fx):
    """The LEFT JOIN's flag, executed.

    An INNER JOIN would make these rows vanish, and the caller could not tell a
    foreign-scope resource from a purged one — so it could not report the
    difference either. Both the other tenant's row and an UNFILED resource must
    arrive with ``in_scope`` false rather than not arrive.
    """
    from app.db.session import read_scope
    from app.repositories.generated_media_repository import (
        _promoted_resource_ids_stmt,
    )

    ids = [fx["gen_a"], fx["gen_b"], fx["gen_unfiled"]]
    async with read_scope() as session:
        rows = (
            await session.execute(_promoted_resource_ids_stmt(ids, fx["scope_a"]))
        ).all()

    flags = {int(gen_id): bool(in_scope) for gen_id, _res, in_scope in rows}
    assert flags == {
        fx["gen_a"]: True,
        fx["gen_b"]: False,
        fx["gen_unfiled"]: False,
    }, f"every asked generation must come back, flagged: {flags}"
