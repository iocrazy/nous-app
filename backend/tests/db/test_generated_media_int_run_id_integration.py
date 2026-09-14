"""DB-backed proof that ``generated_media.origin_run_id`` only takes TEXT
(and therefore that the registration choke point must normalise an int).

WHY THIS FILE EXISTS
────────────────────
The unit test for this (``tests/test_generated_media_register.py::
test_register_normalises_an_int_run_id_into_the_text_column``) stubs the session
and asserts on the COMPILED bind value. That is the right shape for a unit test
— but a compiled bind value is not Postgres. The whole defect was that nobody
had ever handed this statement an int run_id and let a real driver see it:

    sqlalchemy.exc.DBAPIError: (asyncpg) DataError: invalid input for query
    argument $9: 349441401106307 (expected str, got int)

raised in production on 2026-09-14 (MH-96, run 349441401106307) — a generated
image with zero ``generated_media`` rows and zero lineage. The DBOS shot lane
takes ``run_id`` from ``gateway.ledger_run_id(scope)`` (an int); the agent tool
lane takes it from ``run_context["run_id"]`` (a str). Same column, two shapes.

So this file executes the REAL production statement factory
(``_generated_media_insert_stmt`` — the one ``register_generated_media`` and
``_insert_uploaded_row`` both build) against a real Postgres, twice:

  * with the normalised value ``_as_text(int)`` → the row inserts and reads
    back as TEXT;
  * with the raw int (the negative control) → the driver rejects it, which is
    what makes the first case a claim about Postgres rather than about
    SQLAlchemy's compiler.

Point it at any CI-way Postgres (ci_bootstrap.sql → schema_baseline.sql →
migrations above the watermark):

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/db/test_generated_media_int_run_id_integration.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset (so it does not run on a
dev laptop; the schema-drift lane is where it earns its keep).
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason=(
        "INTEGRATION_DATABASE_URL not set — generated_media int-run_id "
        "integration tests need a DB."
    ),
)

#: 真栈里那次失败的 run（MH-96, 2026-09-14 10:01Z）。
_REAL_INT_RUN_ID = 349441401106307


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


@pytest.fixture
async def pg():
    """A raw asyncpg connection for the readback + teardown."""
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


def _values(**overrides):
    """The NOT NULL columns of ``generated_media`` plus whatever the case
    overrides. ``scope_id`` / ``creator_id`` carry no FK, so a fabricated pair
    is enough — this file is about one column's TYPE."""
    base = dict(
        scope_id=990_000_000_000_001,
        creator_id=str(uuid.uuid4()),
        media_kind="image",
        mime="image/png",
        file_path=f"teams/990/generations/{uuid.uuid4().hex}/media.png",
        origin_kind="shot_generate",
    )
    base.update(overrides)
    return base


@_skip
async def test_the_normalised_int_run_id_inserts_and_reads_back_as_text(orm_dsn, pg):
    from app.db.session import write_scope
    from app.services.library.generated_media_service import (
        _as_text,
        _generated_media_insert_stmt,
    )

    stmt = _generated_media_insert_stmt(
        **_values(origin_run_id=_as_text(_REAL_INT_RUN_ID), node_id=_as_text(1234))
    )
    async with write_scope() as session:
        row = (await session.execute(stmt)).mappings().first()

    gen_id = row["id"]
    try:
        stored = await pg.fetchval(
            "SELECT origin_run_id FROM public.generated_media WHERE id = $1", gen_id
        )
        # asyncpg hands a TEXT column back as str — the point of the exercise.
        assert stored == str(_REAL_INT_RUN_ID)
        assert isinstance(stored, str)
    finally:
        await pg.execute("DELETE FROM public.generated_media WHERE id = $1", gen_id)


@_skip
async def test_a_raw_int_run_id_is_rejected_by_the_driver(orm_dsn):
    """负向对照。没有这一条，上面那条只是「SQLAlchemy 能编译一个 str」。"""
    from sqlalchemy.exc import DBAPIError

    from app.db.session import write_scope
    from app.services.library.generated_media_service import (
        _generated_media_insert_stmt,
    )

    stmt = _generated_media_insert_stmt(**_values(origin_run_id=_REAL_INT_RUN_ID))
    with pytest.raises(DBAPIError) as excinfo:
        async with write_scope() as session:
            await session.execute(stmt)

    assert "expected str" in str(excinfo.value)
