"""mig 488 against a real Postgres: the mediahub → nous data renames.

Why this has to run on a live database
======================================
Every guarantee here is Postgres deciding something a stubbed session cannot:
which jsonb paths ``jsonb_set`` rewrites, whether ``left(name, 9)`` really
excludes a twin, whether the ``text[]`` rebuild keeps order, whether
``SET LOCAL session_replication_role`` really keeps ``updated_at`` still and
really resets afterwards, and whether the relkind guard leaves a real table
alone.

Two kinds of case:

* ``test_applied_state_has_no_compat_view`` reads the database as it stands —
  RED on a database that has not had 488 applied (mig 485's view is still
  there), GREEN after.
* The behaviour cases replay the migration body **from the file** inside a
  rolled-back transaction, on production-shaped fixture rows. The file's own
  ``BEGIN;`` / ``COMMIT;`` / ``NOTIFY`` lines are dropped so the replay stays
  inside the test's transaction; everything else runs verbatim, twice where
  idempotency is the claim.

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_migration_488_rename_data_integration.py -v

Skips cleanly when the DSN is unset. The schema-drift workflow runs it through
pytest-no-full-skip.sh, so a full skip there is RED, not green.
"""

from __future__ import annotations

import json
import os
import pathlib
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — mig 488 needs a real database.",
)

#: backend/tests/db/ → repo root → the migration under test.
_MIGRATION_SQL = (
    pathlib.Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "488_rename_data_mediahub_to_nous.sql"
)

_TXN_LINES = {"BEGIN;", "COMMIT;", "NOTIFY pgrst, 'reload schema';"}

OLD_MIME = "application/x-mediahub-gallery"
NEW_MIME = "application/x-nous-gallery"

#: The seven legacy rows production carries (names read off Admin → AI Models).
LEGACY = (
    "mediahub-moss-asr",
    "mediahub-doubao-seed-2-0-lite",
    "mediahub-deepseek-v4-pro",
    "mediahub-deepseek-v4-flash",
    "mediahub-doubao-embedding-vision",
    "mediahub-qwen3-embedding-8b",
    "mediahub-doubao-seed-2-0-pro",
)
TWIN_OLD, TWIN_NEW = "mediahub-t488-twin", "nous-t488-twin"


def _body() -> str:
    """The migration minus its own transaction control, executed argument-free
    so asyncpg uses the simple protocol and accepts the multi-statement file."""
    return "\n".join(
        line
        for line in _MIGRATION_SQL.read_text().splitlines()
        if line.strip() not in _TXN_LINES
    )


@pytest.fixture
async def conn():
    """A connection whose writes — including the replayed migration — are
    always rolled back."""
    c = await asyncpg.connect(_TEST_DSN)
    tx = c.transaction()
    await tx.start()
    try:
        yield c
    finally:
        await tx.rollback()
        await c.close()


async def _seed(conn) -> dict:
    """Production-shaped rows. Returns the ids the assertions need."""
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    for u in (user_a, user_b):
        await conn.execute(
            "INSERT INTO auth.users (id, email) VALUES ($1, $2)",
            u,
            f"mig488-{u.hex[:8]}@test.dev",
        )

    for name in (*LEGACY, "nous-qwen3-llm", TWIN_OLD, TWIN_NEW):
        await conn.execute(
            "INSERT INTO public.nous_models "
            "  (name, display_name, type, actual_provider, actual_model, api_key) "
            "VALUES ($1, $1, 'llm', 'nous', $1, '')",
            name,
        )

    settings = {
        "maintenance_llm_model": "mediahub-doubao-seed-2-0-lite",
        "ai_module.transcription.model": "mediahub-moss-asr",
        "ai_module.embedding.model": "mediahub-doubao-embedding-vision",
        "ai_module.summarization.model": TWIN_OLD,  # twin → stays
        "ai_module.topic_scorer.model": "deepseek-v4-flash",  # raw id → stays
        "ai_module.transcription.api_key": "mediahub-moss-asr",  # not a model key
        "graph_embedder_model": "mediahub-qwen3-embedding-8b",
        "site.t488_banner": "mediahub-moss-asr",  # unrelated key → stays
    }
    for key, value in settings.items():
        await conn.execute(
            "INSERT INTO public.system_settings (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key,
            json.dumps(value),
        )
    # A model key whose value is not a jsonb string must be left alone.
    await conn.execute(
        "INSERT INTO public.system_settings (key, value) "
        "VALUES ('ai_module.caption.model', $1::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        json.dumps({"name": "mediahub-moss-asr"}),
    )

    settings_a = {
        "theme": "dark",
        "ai_settings": {
            "task_assignment": {
                "transcription": "nous:mediahub-moss-asr",
                "summarization": "mediahub-doubao-seed-2-0-lite",  # bare → stays
                "translation": "nous:mediahub-qwen3-llm",
                "caption": f"nous:{TWIN_OLD}",
                "chat": "script_ai",
            },
            "ai_providers": {
                "nous": {
                    "enabled": True,
                    "disabled_models": [
                        "mediahub-deepseek-v4-pro",
                        "nous-qwen3-llm",
                        TWIN_OLD,
                        "gpt-4o",
                    ],
                },
                "doubao": {"api_key": "sk-x"},
            },
        },
    }
    for u, s in ((user_a, settings_a), (user_b, {"theme": "light"})):
        await conn.execute(
            "INSERT INTO public.user_settings (user_id, settings_json) "
            "VALUES ($1, $2::jsonb) "
            "ON CONFLICT (user_id) DO UPDATE SET settings_json = EXCLUDED.settings_json",
            u,
            json.dumps(s),
        )

    agent_a = await conn.fetchval(
        "INSERT INTO public.ai_agents (name, model, fallback_models) "
        "VALUES ('T488 Agent A', 'mediahub-deepseek-v4-pro', $1) RETURNING id",
        ["mediahub-doubao-seed-2-0-lite", "qwen-max", TWIN_OLD],
    )
    agent_b = await conn.fetchval(
        "INSERT INTO public.ai_agents (name, model) "
        "VALUES ('T488 Agent B', 'qwen-max') RETURNING id"
    )
    await conn.execute(
        "INSERT INTO public.agent_overrides (agent_id, user_id, model, fallback_models) "
        "VALUES ($1, $3, 'nous:mediahub-deepseek-v4-flash', NULL), "
        "       ($2, $3, NULL, ARRAY['mediahub-doubao-seed-2-0-pro'])",
        agent_a,
        agent_b,
        user_a,
    )
    await conn.execute(
        "INSERT INTO public.ai_model_prices "
        "  (model, provider, prompt_cents_per_1k, completion_cents_per_1k) "
        "VALUES ('mediahub-qwen3-embedding-8b', '', 0, 0)"
    )

    res_ids = []
    for filename, mime in (("My Gallery", OLD_MIME), ("clip.mp4", "video/mp4")):
        res_ids.append(
            await conn.fetchval(
                "INSERT INTO public.resources "
                "  (creator_id, source_type, filename, mime_type, updated_at) "
                "VALUES ($1, 'upload', $2, $3, '2026-01-01T00:00:00Z') RETURNING id",
                user_a,
                filename,
                mime,
            )
        )
    gallery, video = res_ids
    return {
        "user_a": user_a,
        "user_b": user_b,
        "agent_a": agent_a,
        "agent_b": agent_b,
        "gallery": gallery,
        "video": video,
    }


async def _snapshot(conn, ids: dict) -> dict:
    """Everything the migration may touch, for the idempotency comparison."""
    return {
        "catalog": sorted(
            r["name"] for r in await conn.fetch("SELECT name FROM public.nous_models")
        ),
        "settings": {
            r["key"]: r["value"]
            for r in await conn.fetch("SELECT key, value FROM public.system_settings")
        },
        "user_settings": {
            str(r["user_id"]): (r["settings_json"], r["updated_at"])
            for r in await conn.fetch(
                "SELECT user_id, settings_json, updated_at FROM public.user_settings "
                "WHERE user_id = ANY($1::uuid[])",
                [ids["user_a"], ids["user_b"]],
            )
        },
        "agents": [
            tuple(r)
            for r in await conn.fetch(
                "SELECT name, model, fallback_models FROM public.ai_agents "
                "WHERE id = ANY($1::uuid[]) ORDER BY name",
                [ids["agent_a"], ids["agent_b"]],
            )
        ],
        "overrides": [
            tuple(r)
            for r in await conn.fetch(
                "SELECT agent_id, model, fallback_models FROM public.agent_overrides "
                "WHERE user_id = $1 ORDER BY agent_id",
                ids["user_a"],
            )
        ],
        "prices": sorted(
            r["model"]
            for r in await conn.fetch("SELECT model FROM public.ai_model_prices")
        ),
        "resources": [
            tuple(r)
            for r in await conn.fetch(
                "SELECT id, mime_type, updated_at FROM public.resources "
                "WHERE id = ANY($1::bigint[]) ORDER BY id",
                [ids["gallery"], ids["video"]],
            )
        ],
    }


async def _setting(conn, key: str):
    raw = await conn.fetchval(
        "SELECT value FROM public.system_settings WHERE key = $1", key
    )
    return json.loads(raw)


# ---------------------------------------------------------------------------
# The database as it stands
# ---------------------------------------------------------------------------


@_skip
async def test_applied_state_has_no_compat_view(conn):
    """No replay: 488 has actually run, so mig 485's shim is gone."""
    kind = await conn.fetchval(
        "SELECT relkind::text FROM pg_class "
        "WHERE relname = 'mediahub_models' AND relnamespace = 'public'::regnamespace"
    )
    assert kind is None, f"public.mediahub_models still exists (relkind={kind!r})"


# ---------------------------------------------------------------------------
# Replay on production-shaped rows
# ---------------------------------------------------------------------------


@_skip
async def test_catalog_rows_renamed_except_the_twin(conn):
    await _seed(conn)
    await conn.execute(_body())

    names = {r["name"] for r in await conn.fetch("SELECT name FROM public.nous_models")}
    for old in LEGACY:
        assert old not in names
        assert "nous-" + old[len("mediahub-") :] in names
    assert "nous-qwen3-llm" in names
    # Both halves of the twin pair keep their own name.
    assert {TWIN_OLD, TWIN_NEW} <= names


@_skip
async def test_system_settings_rewritten_by_exact_value_only(conn):
    await _seed(conn)
    await conn.execute(_body())

    assert await _setting(conn, "maintenance_llm_model") == "nous-doubao-seed-2-0-lite"
    assert await _setting(conn, "ai_module.transcription.model") == "nous-moss-asr"
    assert (
        await _setting(conn, "ai_module.embedding.model")
        == "nous-doubao-embedding-vision"
    )
    assert await _setting(conn, "graph_embedder_model") == "nous-qwen3-embedding-8b"
    # Left alone: twin, raw provider id, non-model key, unrelated key, non-string.
    assert await _setting(conn, "ai_module.summarization.model") == TWIN_OLD
    assert await _setting(conn, "ai_module.topic_scorer.model") == "deepseek-v4-flash"
    assert (
        await _setting(conn, "ai_module.transcription.api_key") == "mediahub-moss-asr"
    )
    assert await _setting(conn, "site.t488_banner") == "mediahub-moss-asr"
    assert await _setting(conn, "ai_module.caption.model") == {
        "name": "mediahub-moss-asr"
    }


@_skip
async def test_user_settings_colon_form_rewritten_bare_form_left(conn):
    ids = await _seed(conn)
    await conn.execute(_body())

    raw = await conn.fetchval(
        "SELECT settings_json FROM public.user_settings WHERE user_id = $1",
        ids["user_a"],
    )
    s = json.loads(raw)
    ta = s["ai_settings"]["task_assignment"]
    assert ta == {
        "transcription": "nous:nous-moss-asr",
        # The bare value must NOT move: ai_router bills a bare value that
        # startswith("nous-"), so rewriting it would start charging.
        "summarization": "mediahub-doubao-seed-2-0-lite",
        "translation": "nous:nous-qwen3-llm",
        "caption": f"nous:{TWIN_OLD}",
        "chat": "script_ai",
    }
    assert s["ai_settings"]["ai_providers"]["nous"] == {
        "enabled": True,
        "disabled_models": [
            "nous-deepseek-v4-pro",
            "nous-qwen3-llm",
            TWIN_OLD,
            "gpt-4o",
        ],
    }
    # Siblings untouched.
    assert s["theme"] == "dark"
    assert s["ai_settings"]["ai_providers"]["doubao"] == {"api_key": "sk-x"}

    raw_b = await conn.fetchval(
        "SELECT settings_json FROM public.user_settings WHERE user_id = $1",
        ids["user_b"],
    )
    assert json.loads(raw_b) == {"theme": "light"}


@_skip
async def test_agent_models_and_prices_rewritten_in_order(conn):
    ids = await _seed(conn)
    await conn.execute(_body())

    a = await conn.fetchrow(
        "SELECT model, fallback_models FROM public.ai_agents WHERE id = $1",
        ids["agent_a"],
    )
    assert a["model"] == "nous-deepseek-v4-pro"
    assert a["fallback_models"] == ["nous-doubao-seed-2-0-lite", "qwen-max", TWIN_OLD]

    b = await conn.fetchrow(
        "SELECT model, fallback_models FROM public.ai_agents WHERE id = $1",
        ids["agent_b"],
    )
    assert (b["model"], b["fallback_models"]) == ("qwen-max", [])

    overrides = {
        r["agent_id"]: (r["model"], r["fallback_models"])
        for r in await conn.fetch(
            "SELECT agent_id, model, fallback_models FROM public.agent_overrides "
            "WHERE user_id = $1",
            ids["user_a"],
        )
    }
    assert overrides[ids["agent_a"]] == ("nous:nous-deepseek-v4-flash", None)
    assert overrides[ids["agent_b"]] == (None, ["nous-doubao-seed-2-0-pro"])

    prices = {
        r["model"] for r in await conn.fetch("SELECT model FROM public.ai_model_prices")
    }
    assert "nous-qwen3-embedding-8b" in prices
    assert "mediahub-qwen3-embedding-8b" not in prices


@_skip
async def test_gallery_mime_moves_without_bumping_updated_at(conn):
    ids = await _seed(conn)
    await conn.execute(_body())

    rows = {
        r["id"]: (r["mime_type"], r["updated_at"].isoformat())
        for r in await conn.fetch(
            "SELECT id, mime_type, updated_at FROM public.resources WHERE id = ANY($1::bigint[])",
            [ids["gallery"], ids["video"]],
        )
    }
    assert rows[ids["gallery"]] == (NEW_MIME, "2026-01-01T00:00:00+00:00")
    assert rows[ids["video"]] == ("video/mp4", "2026-01-01T00:00:00+00:00")

    # The suppression ended with that one statement: the role is back to
    # origin and an ordinary UPDATE bumps updated_at again (positive control).
    assert await conn.fetchval("SHOW session_replication_role") == "origin"
    bumped = await conn.fetchval(
        "UPDATE public.resources SET filename = 'Renamed' WHERE id = $1 "
        "RETURNING updated_at > '2026-01-02'::timestamptz",
        ids["gallery"],
    )
    assert bumped is True


@_skip
async def test_second_run_changes_nothing(conn):
    ids = await _seed(conn)
    await conn.execute(_body())
    first = await _snapshot(conn, ids)
    await conn.execute(_body())
    assert await _snapshot(conn, ids) == first


@_skip
async def test_a_real_table_named_mediahub_models_is_left_alone(conn):
    """The relkind guard: only a VIEW is ever dropped."""
    await conn.execute("DROP VIEW IF EXISTS public.mediahub_models")
    await conn.execute("CREATE TABLE public.mediahub_models (x int)")
    await conn.execute(_body())
    kind = await conn.fetchval(
        "SELECT relkind::text FROM pg_class "
        "WHERE relname = 'mediahub_models' AND relnamespace = 'public'::regnamespace"
    )
    assert kind == "r"


@_skip
async def test_the_compat_view_is_dropped(conn):
    await conn.execute(
        "CREATE OR REPLACE VIEW public.mediahub_models AS SELECT * FROM public.nous_models"
    )
    await conn.execute(_body())
    assert await conn.fetchval("SELECT to_regclass('public.mediahub_models')") is None
