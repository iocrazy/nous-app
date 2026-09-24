"""mig 502 against a real Postgres: nous-qwen3-llm → nous-qwen3-8-27b + two variants.

Why this has to run on a live database
======================================
The guarantees are Postgres decisions a stubbed session cannot make: whether
the twin guard really keeps both rows, whether ``INSERT … SELECT`` copies the
encrypted key off the renamed row, which jsonb paths ``jsonb_set`` rewrites,
whether the ``text[]`` rebuild keeps order, and whether the price rekey dodges
the ``(model, provider, effective_at)`` unique key.

The migration body is replayed **from the file** inside a rolled-back
transaction on production-shaped rows (the base row and price row are copied
from a 2026-09-24 read-only probe of production). The file's own ``BEGIN;`` /
``COMMIT;`` / ``NOTIFY`` lines are dropped so the replay stays inside the
test's transaction; everything else runs verbatim, twice where idempotency is
the claim.

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55499/drift \\
    uv run pytest tests/db/test_migration_502_rename_and_variants_integration.py -v

Skips cleanly when the DSN is unset.
"""

from __future__ import annotations

import json
import os
import pathlib
import uuid
from datetime import datetime

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — mig 502 needs a real database.",
)

_MIGRATION_SQL = (
    pathlib.Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "502_rename_nous_qwen3_llm_and_add_variants.sql"
)

_TXN_LINES = {"BEGIN;", "COMMIT;", "NOTIFY pgrst, 'reload schema';"}

OLD, NEW = "nous-qwen3-llm", "nous-qwen3-8-27b"
ORCA, HUIHUI = "nous-qwen3-8-27b-orcarouter", "nous-qwen3-8-27b-huihui"
KEY = "enc:v1:t502-ciphertext"
BASE_URL = "http://host.docker.internal:8000/v1"
PRICE_AT = datetime.fromisoformat("2026-09-06T00:00:00+00:00")


def _body() -> str:
    """The migration minus its own transaction control (simple protocol, so
    asyncpg accepts the multi-statement file)."""
    return "\n".join(
        line
        for line in _MIGRATION_SQL.read_text().splitlines()
        if line.strip() not in _TXN_LINES
    )


@pytest.fixture
async def conn():
    c = await asyncpg.connect(_TEST_DSN)
    tx = c.transaction()
    await tx.start()
    try:
        yield c
    finally:
        await tx.rollback()
        await c.close()


async def _insert_base_row(conn, name: str) -> None:
    """The production row, as probed (api_key shortened to a fake ciphertext)."""
    await conn.execute(
        "INSERT INTO public.nous_models "
        "  (name, display_name, type, actual_provider, actual_model, api_key, "
        "   base_url, pricing_type, pricing_value, is_enabled, sort_order, description) "
        "VALUES ($1, 'Nous Qwen3 35B (LLM)', 'llm', 'nous', 'qwen3-8-27b', $2, "
        "        $3, 'per_hour', 0, TRUE, 0, "
        "        'Self-hosted qwen3-6-35b via nous-engine (host.docker.internal:8000)')",
        name,
        KEY,
        BASE_URL,
    )


async def _seed(conn) -> dict:
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    for u in (user_a, user_b):
        await conn.execute(
            "INSERT INTO auth.users (id, email) VALUES ($1, $2)",
            u,
            f"mig502-{u.hex[:8]}@test.dev",
        )
    await _insert_base_row(conn, OLD)

    settings = {
        "graph_extractor_model": OLD,
        "maintenance_llm_model": f"nous:{OLD}",
        "ai_module.summarization.model": OLD,
        "ai_module.summarization.api_key": OLD,  # not a model key → stays
        "site.t502_banner": OLD,  # unrelated key → stays
        "ai_module.topic_scorer.model": "nous-qwen3-llm-x",  # not whole-value → stays
    }
    for key, value in settings.items():
        await conn.execute(
            "INSERT INTO public.system_settings (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key,
            json.dumps(value),
        )

    settings_a = {
        "theme": "dark",
        "ai_settings": {
            "task_assignment": {
                "translation": f"nous:{OLD}",
                "summarization": OLD,  # bare → stays (488's billing rule)
                "chat": "script_ai",
            },
            "ai_providers": {
                "nous": {
                    "enabled": True,
                    "disabled_models": ["gpt-4o", OLD, f"nous:{OLD}"],
                },
            },
        },
    }
    for u, s in ((user_a, settings_a), (user_b, {"theme": "light"})):
        await conn.execute(
            "INSERT INTO public.user_settings (user_id, settings_json) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (user_id) DO UPDATE SET settings_json = EXCLUDED.settings_json",
            u,
            json.dumps(s),
        )

    agent_a = await conn.fetchval(
        "INSERT INTO public.ai_agents (name, model, fallback_models) "
        "VALUES ('T502 Agent A', $1, $2) RETURNING id",
        OLD,
        ["qwen-max", f"nous:{OLD}", "nous-qwen3-llm-x"],
    )
    agent_b = await conn.fetchval(
        "INSERT INTO public.ai_agents (name, model) VALUES ('T502 Agent B', 'qwen-max') RETURNING id"
    )
    await conn.execute(
        "INSERT INTO public.agent_overrides (agent_id, user_id, model, fallback_models) "
        "VALUES ($1, $3, $4, NULL), ($2, $3, NULL, ARRAY[$5])",
        agent_a,
        agent_b,
        user_a,
        f"nous:{OLD}",
        OLD,
    )
    await conn.execute(
        "INSERT INTO public.ai_model_prices "
        "  (model, provider, prompt_cents_per_1k, completion_cents_per_1k, effective_at) "
        "VALUES ($1, 'nous', 0, 0, $2) "
        # mig 454 already seeds exactly this row on a replayed schema.
        "ON CONFLICT (model, provider, effective_at) DO NOTHING",
        OLD,
        PRICE_AT,
    )
    return {"user_a": user_a, "user_b": user_b, "agent_a": agent_a, "agent_b": agent_b}


async def _setting(conn, key: str):
    return json.loads(
        await conn.fetchval(
            "SELECT value FROM public.system_settings WHERE key = $1", key
        )
    )


async def _snapshot(conn) -> dict:
    """Everything the migration may touch (whole tables, so a stray write shows)."""

    async def rows(sql: str) -> list:
        return [tuple(r) for r in await conn.fetch(sql)]

    return {
        "catalog": await rows(
            "SELECT name, display_name, type, actual_provider, actual_model, api_key, "
            "app_id, base_url, pricing_type, pricing_value, is_enabled, sort_order, "
            "description, owner_user_id, context_window_tokens FROM public.nous_models ORDER BY name"
        ),
        "settings": await rows(
            "SELECT key, value FROM public.system_settings ORDER BY key"
        ),
        "user_settings": await rows(
            "SELECT user_id, settings_json, updated_at FROM public.user_settings ORDER BY user_id"
        ),
        "agents": await rows(
            "SELECT id, model, fallback_models FROM public.ai_agents ORDER BY id"
        ),
        "overrides": await rows(
            "SELECT agent_id, user_id, model, fallback_models FROM public.agent_overrides "
            "ORDER BY agent_id, user_id"
        ),
        "prices": await rows(
            "SELECT model, provider, prompt_cents_per_1k, completion_cents_per_1k, "
            "effective_at, supports_vision FROM public.ai_model_prices "
            "ORDER BY model, provider, effective_at"
        ),
    }


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@_skip
async def test_base_row_renamed_and_relabelled(conn):
    await _seed(conn)
    await conn.execute(_body())

    names = {r["name"] for r in await conn.fetch("SELECT name FROM public.nous_models")}
    assert OLD not in names
    row = await conn.fetchrow(
        "SELECT display_name, description, actual_model, api_key, context_window_tokens "
        "FROM public.nous_models WHERE name = $1",
        NEW,
    )
    assert row["display_name"] == "Nous Qwen3 8 27B"
    assert (
        row["description"] == "Self-hosted qwen3-8-27b (abliterated AWQ) on nous-engine"
    )
    assert row["actual_model"] == "qwen3-8-27b"
    assert row["api_key"] == KEY
    # The real max_model_len is not known yet — the window stays unset.
    assert row["context_window_tokens"] is None


@_skip
async def test_variants_copy_credential_and_carry_their_window(conn):
    await _seed(conn)
    await conn.execute(_body())

    rows = {
        r["name"]: r
        for r in await conn.fetch(
            "SELECT name, display_name, type, actual_provider, actual_model, api_key, app_id, "
            "base_url, pricing_type, pricing_value, is_enabled, owner_user_id, "
            "context_window_tokens FROM public.nous_models WHERE name = ANY($1::text[])",
            [ORCA, HUIHUI],
        )
    }
    expected = {
        ORCA: ("Nous Qwen3 8 27B OrcaRouter (256K)", "qwen3-8-27b-orcarouter", 262144),
        HUIHUI: ("Nous Qwen3 8 27B Huihui (32K)", "qwen3-8-27b-huihui", 32768),
    }
    assert set(rows) == set(expected)
    for name, (display, actual, window) in expected.items():
        r = rows[name]
        assert (r["display_name"], r["actual_model"], r["context_window_tokens"]) == (
            display,
            actual,
            window,
        )
        assert (r["type"], r["actual_provider"]) == ("llm", "nous")
        assert (r["api_key"], r["base_url"], r["app_id"]) == (KEY, BASE_URL, None)
        assert (r["pricing_type"], float(r["pricing_value"]), r["is_enabled"]) == (
            "per_hour",
            0.0,
            True,
        )
        assert r["owner_user_id"] is None


@_skip
async def test_twin_pair_is_left_alone(conn):
    """Both spellings already exist as distinct rows: neither moves, and old
    references keep pointing at the old row (exact-name wins in the alias layer)."""
    await _insert_base_row(conn, OLD)
    await _insert_base_row(conn, NEW)
    await conn.execute(
        "INSERT INTO public.system_settings (key, value) VALUES ('graph_extractor_model', $1::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        json.dumps(OLD),
    )
    await conn.execute(_body())

    names = {
        r["name"]: r["display_name"]
        for r in await conn.fetch(
            "SELECT name, display_name FROM public.nous_models WHERE name = ANY($1::text[])",
            [OLD, NEW],
        )
    }
    assert set(names) == {OLD, NEW}
    assert names[OLD] == "Nous Qwen3 35B (LLM)"
    assert await _setting(conn, "graph_extractor_model") == OLD


@_skip
async def test_admin_edited_display_name_is_kept_on_an_already_renamed_row(conn):
    """If someone renamed by hand first, only the stale label is corrected."""
    await _insert_base_row(conn, NEW)
    await conn.execute(
        "UPDATE public.nous_models SET display_name = 'My Qwen' WHERE name = $1", NEW
    )
    await conn.execute(_body())
    assert (
        await conn.fetchval(
            "SELECT display_name FROM public.nous_models WHERE name = $1", NEW
        )
        == "My Qwen"
    )


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


@_skip
async def test_system_settings_rewritten_by_exact_value_only(conn):
    await _seed(conn)
    await conn.execute(_body())

    assert await _setting(conn, "graph_extractor_model") == NEW
    assert await _setting(conn, "maintenance_llm_model") == f"nous:{NEW}"
    assert await _setting(conn, "ai_module.summarization.model") == NEW
    assert await _setting(conn, "ai_module.summarization.api_key") == OLD
    assert await _setting(conn, "site.t502_banner") == OLD
    assert await _setting(conn, "ai_module.topic_scorer.model") == "nous-qwen3-llm-x"


@_skip
async def test_user_settings_colon_task_assignment_and_bare_disabled_models(conn):
    ids = await _seed(conn)
    await conn.execute(_body())

    s = json.loads(
        await conn.fetchval(
            "SELECT settings_json FROM public.user_settings WHERE user_id = $1",
            ids["user_a"],
        )
    )
    assert s["ai_settings"]["task_assignment"] == {
        "translation": f"nous:{NEW}",
        "summarization": OLD,
        "chat": "script_ai",
    }
    assert s["ai_settings"]["ai_providers"]["nous"] == {
        "enabled": True,
        "disabled_models": ["gpt-4o", NEW, f"nous:{OLD}"],
    }
    assert s["theme"] == "dark"
    raw_b = await conn.fetchval(
        "SELECT settings_json FROM public.user_settings WHERE user_id = $1",
        ids["user_b"],
    )
    assert json.loads(raw_b) == {"theme": "light"}


@_skip
async def test_agent_models_rewritten_in_both_forms_order_kept(conn):
    ids = await _seed(conn)
    await conn.execute(_body())

    a = await conn.fetchrow(
        "SELECT model, fallback_models FROM public.ai_agents WHERE id = $1",
        ids["agent_a"],
    )
    assert a["model"] == NEW
    assert a["fallback_models"] == ["qwen-max", f"nous:{NEW}", "nous-qwen3-llm-x"]
    b = await conn.fetchrow(
        "SELECT model, fallback_models FROM public.ai_agents WHERE id = $1",
        ids["agent_b"],
    )
    assert (b["model"], b["fallback_models"]) == ("qwen-max", [])

    overrides = {
        r["agent_id"]: (r["model"], r["fallback_models"])
        for r in await conn.fetch(
            "SELECT agent_id, model, fallback_models FROM public.agent_overrides WHERE user_id = $1",
            ids["user_a"],
        )
    }
    assert overrides[ids["agent_a"]] == (f"nous:{NEW}", None)
    assert overrides[ids["agent_b"]] == (None, [NEW])


@_skip
async def test_price_rekeyed_and_variant_prices_added(conn):
    await _seed(conn)
    await conn.execute(_body())

    prices = {
        r["model"]: r
        for r in await conn.fetch(
            "SELECT model, provider, prompt_cents_per_1k, completion_cents_per_1k, "
            "supports_vision, effective_at FROM public.ai_model_prices "
            "WHERE model = ANY($1::text[])",
            [OLD, NEW, ORCA, HUIHUI],
        )
    }
    assert set(prices) == {NEW, ORCA, HUIHUI}
    assert prices[NEW]["effective_at"] == PRICE_AT
    for name in (NEW, ORCA, HUIHUI):
        p = prices[name]
        assert p["provider"] == "nous"
        assert (
            float(p["prompt_cents_per_1k"]),
            float(p["completion_cents_per_1k"]),
        ) == (0, 0)
        assert p["supports_vision"] is False


@_skip
async def test_price_rekey_never_collides_with_an_existing_new_key(conn):
    """A price row already on the new name at the same effective_at: the old
    row stays (no unique-key violation), the new one is untouched."""
    await _seed(conn)
    await conn.execute(
        "INSERT INTO public.ai_model_prices "
        "  (model, provider, prompt_cents_per_1k, completion_cents_per_1k, effective_at) "
        "VALUES ($1, 'nous', 1, 1, $2)",
        NEW,
        PRICE_AT,
    )
    await conn.execute(_body())
    rows = {
        r["model"]: float(r["prompt_cents_per_1k"])
        for r in await conn.fetch(
            "SELECT model, prompt_cents_per_1k FROM public.ai_model_prices "
            "WHERE model = ANY($1::text[])",
            [OLD, NEW],
        )
    }
    assert rows == {OLD: 0.0, NEW: 1.0}


# ---------------------------------------------------------------------------
# Idempotency and the empty database
# ---------------------------------------------------------------------------


@_skip
async def test_second_run_changes_nothing(conn):
    await _seed(conn)
    await conn.execute(_body())
    first = await _snapshot(conn)
    await conn.execute(_body())
    assert await _snapshot(conn) == first


@_skip
async def test_database_without_the_row_is_a_no_op(conn):
    """schema-drift replays this on a database with no catalog data."""
    await conn.execute(
        "DELETE FROM public.nous_models WHERE name = ANY($1::text[])",
        [OLD, NEW, ORCA, HUIHUI],
    )
    before = await _snapshot(conn)
    await conn.execute(_body())
    assert await _snapshot(conn) == before
