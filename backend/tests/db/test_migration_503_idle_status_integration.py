"""mig 503 against a real Postgres: ``last_test_status`` accepts ``idle``.

The hourly probe now writes ``idle`` for a local nous-engine model that is
authorized but not loaded. Only Postgres can say whether the CHECK accepts it,
so the write goes through the real ``NousModelRepository.record_test_result``
(``app.db.session`` repointed at the DSN), with a negative control proving the
CHECK is still a closed set. The 32K window backfill is replayed from the file
inside a rolled-back transaction.

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55499/drift \\
    uv run pytest tests/db/test_migration_503_idle_status_integration.py -v

Skips cleanly when the DSN is unset.
"""

from __future__ import annotations

import os
import pathlib
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — mig 503 needs a real database.",
)

_MIGRATION_SQL = (
    pathlib.Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "503_nous_models_last_test_status_idle.sql"
)
_TXN_LINES = {"BEGIN;", "COMMIT;", "NOTIFY pgrst, 'reload schema';"}
_BASE_URL = "http://host.docker.internal:8000/v1"


def _body() -> str:
    return "\n".join(
        line
        for line in _MIGRATION_SQL.read_text().splitlines()
        if line.strip() not in _TXN_LINES
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
async def model_id(pg):
    name = f"test-503-{uuid.uuid4().hex[:8]}"
    mid = await pg.fetchval(
        """
        INSERT INTO public.nous_models
            (name, display_name, type, actual_provider, actual_model, api_key,
             base_url, is_enabled, sort_order)
        VALUES ($1, 'T503', 'llm', 'nous', 'qwen3-8-27b-huihui', '', $2, FALSE, 999)
        RETURNING id
        """,
        name,
        _BASE_URL,
    )
    try:
        yield mid
    finally:
        await pg.execute("DELETE FROM public.nous_models WHERE id = $1", mid)


@_skip
async def test_record_test_result_persists_idle(orm_dsn, pg, model_id) -> None:
    from app.repositories.nous_model_repository import get_nous_model_repository

    repo = get_nous_model_repository()
    detail = "authorized, not loaded (loads on first request)"
    saved = await repo.record_test_result(str(model_id), "idle", detail, None)
    assert saved is not None, "record_test_result swallowed a CHECK violation"
    row = await pg.fetchrow(
        "SELECT last_test_status, last_test_detail, last_test_code, last_tested_at"
        " FROM public.nous_models WHERE id = $1",
        model_id,
    )
    assert row["last_test_status"] == "idle"
    assert row["last_test_detail"] == detail
    assert row["last_test_code"] is None
    assert row["last_tested_at"] is not None


@_skip
async def test_check_is_still_a_closed_set(pg, model_id) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await pg.execute(
            "UPDATE public.nous_models SET last_test_status = 'loading' WHERE id = $1",
            model_id,
        )


@_skip
async def test_base_row_window_backfill_is_guarded_and_idempotent(pg) -> None:
    tx = pg.transaction()
    await tx.start()
    try:
        await pg.execute(
            "DELETE FROM public.nous_models WHERE name LIKE 'nous-qwen3-8-27b%'"
        )
        for name, window in (
            ("nous-qwen3-8-27b", None),
            ("nous-qwen3-8-27b-orcarouter", 262144),
        ):
            await pg.execute(
                """
                INSERT INTO public.nous_models
                    (name, display_name, type, actual_provider, actual_model, api_key,
                     base_url, is_enabled, sort_order, context_window_tokens)
                VALUES ($1, $1, 'llm', 'nous', $1, '', $2, TRUE, 10, $3)
                """,
                name,
                _BASE_URL,
                window,
            )
        await pg.execute(_body())
        await pg.execute(_body())  # idempotent replay
        got = {
            r["name"]: r["context_window_tokens"]
            for r in await pg.fetch(
                "SELECT name, context_window_tokens FROM public.nous_models"
                " WHERE name LIKE 'nous-qwen3-8-27b%'"
            )
        }
        assert got == {"nous-qwen3-8-27b": 32768, "nous-qwen3-8-27b-orcarouter": 262144}

        # An admin-set value survives a replay.
        await pg.execute(
            "UPDATE public.nous_models SET context_window_tokens = 65536"
            " WHERE name = 'nous-qwen3-8-27b'"
        )
        await pg.execute(_body())
        assert (
            await pg.fetchval(
                "SELECT context_window_tokens FROM public.nous_models"
                " WHERE name = 'nous-qwen3-8-27b'"
            )
            == 65536
        )
    finally:
        await tx.rollback()
