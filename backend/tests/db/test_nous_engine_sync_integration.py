"""nous-engine sync against a real Postgres, through the real repository.

The unit tests use an in-memory repository, so only Postgres can say whether the
ORM INSERTs are accepted: the catalog row (type CHECK, unique ``name``,
snowflake default), the zero price row on the ``(model, provider,
effective_at)`` key, and the verbatim credential copy. The engine is mocked
(respx); everything below ``sync_engine_models`` is real.

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55499/drift \\
    uv run pytest tests/db/test_nous_engine_sync_integration.py -v

Skips cleanly when the DSN is unset. Rows are tagged with a random suffix and
removed afterwards.
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import httpx
import pytest
import respx

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — engine sync needs a real database.",
)

_CIPHER = "enc:v1:integration-test-ciphertext"


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
async def engine_env(pg):
    """One seeded engine row on a unique base_url; cleanup by that base_url."""
    tag = uuid.uuid4().hex[:8]
    base_url = f"http://engine-{tag}.test:8000/v1"
    await pg.execute(
        """
        INSERT INTO public.nous_models
            (name, display_name, type, actual_provider, actual_model, api_key,
             base_url, pricing_type, pricing_value, is_enabled, sort_order)
        VALUES ($1, 'Seed', 'llm', 'nous', $2, $3, $4, 'per_hour', 0, true, 0)
        """,
        f"nous-seed-{tag}",
        f"seed-{tag}",
        _CIPHER,
        base_url,
    )
    try:
        yield tag, base_url
    finally:
        names = await pg.fetch(
            "SELECT name FROM public.nous_models WHERE base_url = $1", base_url
        )
        await pg.execute(
            "DELETE FROM public.ai_model_prices WHERE model = ANY($1::text[])",
            [r["name"] for r in names],
        )
        await pg.execute("DELETE FROM public.nous_models WHERE base_url = $1", base_url)


@_skip
@respx.mock
async def test_sync_inserts_rows_and_prices_idempotently(orm_dsn, pg, engine_env):
    from app.services.ai.nous_engine_sync import sync_engine_models

    tag, base_url = engine_env
    llm_id, emb_id = f"qwen-{tag}-8b", f"wemm-{tag}-2b"
    respx.get(url__startswith=f"{base_url}/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": llm_id, "type": "inference", "context_window": 32768},
                    {"id": emb_id, "type": "embedding"},
                    {"id": f"seed-{tag}", "type": "llm", "context_window": 65536},
                    {"id": f"krea-{tag}", "type": "app"},
                ]
            },
        )
    )

    first = await sync_engine_models(base_url=base_url, api_key="sk-plain")
    second = await sync_engine_models(base_url=base_url, api_key="sk-plain")

    assert first.error is None, first.error
    assert sorted(first.created) == sorted([f"nous-{llm_id}", f"nous-{emb_id}"])
    assert first.updated == (f"nous-seed-{tag}",)
    assert second.created == () and second.updated == ()

    rows = {
        r["name"]: r
        for r in await pg.fetch(
            "SELECT name, type, api_key, actual_provider, context_window_tokens, "
            "is_enabled FROM public.nous_models WHERE base_url = $1",
            base_url,
        )
    }
    assert rows[f"nous-{llm_id}"]["type"] == "llm"
    assert rows[f"nous-{llm_id}"]["context_window_tokens"] == 32768
    assert rows[f"nous-{emb_id}"]["type"] == "embedding"
    assert rows[f"nous-seed-{tag}"]["context_window_tokens"] == 65536
    for name in (f"nous-{llm_id}", f"nous-{emb_id}"):
        assert rows[name]["api_key"] == _CIPHER  # stored value copied verbatim
        assert rows[name]["actual_provider"] == "nous"
        assert rows[name]["is_enabled"] is True

    prices = await pg.fetch(
        "SELECT model, provider, prompt_cents_per_1k, completion_cents_per_1k "
        "FROM public.ai_model_prices WHERE model = ANY($1::text[])",
        [f"nous-{llm_id}", f"nous-{emb_id}"],
    )
    assert sorted(p["model"] for p in prices) == sorted(
        [f"nous-{llm_id}", f"nous-{emb_id}"]
    )
    assert all(p["provider"] == "nous" for p in prices)
    assert all(p["prompt_cents_per_1k"] == 0 for p in prices)
    assert all(p["completion_cents_per_1k"] == 0 for p in prices)


@_skip
@respx.mock
async def test_name_owned_by_another_provider_is_skipped(orm_dsn, pg, engine_env):
    """The unique ``name`` is never violated: a taken name is a skip."""
    from app.services.ai.nous_engine_sync import sync_engine_models

    tag, base_url = engine_env
    svc = f"clash-{tag}"
    await pg.execute(
        """
        INSERT INTO public.nous_models
            (name, display_name, type, actual_provider, actual_model, api_key,
             base_url, pricing_type, pricing_value)
        VALUES ($1, 'Other', 'llm', 'doubao', 'x', 'k', $2, 'per_token', 0)
        """,
        f"nous-{svc}",
        base_url,
    )
    respx.get(url__startswith=f"{base_url}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": svc, "type": "llm"}]})
    )

    report = await sync_engine_models(base_url=base_url, api_key="sk-plain")

    assert report.created == ()
    assert [(s.id, s.reason) for s in report.skipped] == [(svc, "name_taken")]


@_skip
async def test_disable_rows_flips_only_enabled_and_returns_names(
    orm_dsn, pg, engine_env
):
    from app.repositories.nous_engine_sync_repository import (
        NousEngineSyncRepository,
    )

    tag, base_url = engine_env
    off_id = await pg.fetchval(
        """
        INSERT INTO public.nous_models
            (name, display_name, type, actual_provider, actual_model, api_key,
             base_url, pricing_type, pricing_value, is_enabled, sort_order)
        VALUES ($1, 'Off', 'asr', 'nous', $2, $3, $4, 'per_hour', 0, false, 1)
        RETURNING id
        """,
        f"nous-off-{tag}",
        f"off-{tag}",
        _CIPHER,
        base_url,
    )
    seed = await pg.fetchrow(
        "SELECT id, updated_at FROM public.nous_models WHERE name = $1",
        f"nous-seed-{tag}",
    )

    names = await NousEngineSyncRepository().disable_rows([seed["id"], off_id])
    again = await NousEngineSyncRepository().disable_rows([seed["id"]])

    assert names == [f"nous-seed-{tag}"]  # the already-disabled row is not echoed
    assert again == []
    after = await pg.fetchrow(
        "SELECT is_enabled, updated_at FROM public.nous_models WHERE id = $1",
        seed["id"],
    )
    assert after["is_enabled"] is False
    assert after["updated_at"] >= seed["updated_at"]


@_skip
async def test_record_ready_writes_status_without_touching_updated_at(
    orm_dsn, pg, engine_env
):
    from app.repositories.nous_engine_sync_repository import (
        NousEngineSyncRepository,
    )

    tag, _ = engine_env
    row = await pg.fetchrow(
        "SELECT id, updated_at FROM public.nous_models WHERE name = $1",
        f"nous-seed-{tag}",
    )
    repo = NousEngineSyncRepository()

    assert await repo.record_ready(row["id"], False) is True
    idle = await pg.fetchrow(
        "SELECT last_test_status, last_test_detail, last_test_code, "
        "last_tested_at, updated_at FROM public.nous_models WHERE id = $1",
        row["id"],
    )
    assert await repo.record_ready(row["id"], True) is True
    ok = await pg.fetchrow(
        "SELECT last_test_status, last_test_detail, last_test_code, updated_at "
        "FROM public.nous_models WHERE id = $1",
        row["id"],
    )

    assert (idle["last_test_status"], idle["last_test_detail"]) == (
        "idle",
        "authorized, not loaded",
    )
    assert idle["last_test_code"] is None and idle["last_tested_at"] is not None
    assert (ok["last_test_status"], ok["last_test_detail"]) == ("ok", "loaded")
    assert ok["last_test_code"] is None
    assert ok["updated_at"] == row["updated_at"]  # a reading is not an edit
    assert await repo.record_ready(1, True) is False  # no such row


@_skip
@respx.mock
async def test_sync_disables_revoked_rows_and_follows_ready(orm_dsn, pg, engine_env):
    """End-to-end through the real repository: a missing service is disabled,
    a listed one takes its ready status."""
    from app.services.ai.nous_engine_sync import sync_engine_models

    tag, base_url = engine_env
    await pg.execute(
        """
        INSERT INTO public.nous_models
            (name, display_name, type, actual_provider, actual_model, api_key,
             base_url, pricing_type, pricing_value, is_enabled, sort_order)
        VALUES ($1, 'Gone', 'asr', 'nous', $2, $3, $4, 'per_hour', 0, true, 1)
        """,
        f"nous-gone-{tag}",
        f"gone-{tag}",
        _CIPHER,
        base_url,
    )
    route = respx.get(url__startswith=f"{base_url}/models").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": f"seed-{tag}", "type": "llm", "ready": False}]}
        )
    )

    report = await sync_engine_models(base_url=base_url, api_key="sk-plain")

    assert route.calls.last.request.url.params.get("include_unready") == "1"
    assert report.error is None, report.error
    assert report.disabled == (f"nous-gone-{tag}",)
    assert report.ready_changed == 1
    rows = {
        r["name"]: r
        for r in await pg.fetch(
            "SELECT name, is_enabled, last_test_status FROM public.nous_models "
            "WHERE base_url = $1",
            base_url,
        )
    }
    assert rows[f"nous-gone-{tag}"]["is_enabled"] is False
    assert rows[f"nous-seed-{tag}"]["is_enabled"] is True
    assert rows[f"nous-seed-{tag}"]["last_test_status"] == "idle"
