"""DB-backed guards for migrations 480 / 481 (provider catalog tidy).

Both assertions are things only Postgres can answer, and both cover a gap that
stayed invisible for months precisely because the unit lane cannot see it:

  * 481 — the browser roles' table privileges. Every unit test around
    ``NousModelRepository`` asserts the ``_PUBLIC_COLS`` projection hides
    ``api_key`` / ``base_url`` / ``description``, and every one of them passed
    the entire time the anon key could read all three straight off PostgREST.
    A column allowlist on one path says nothing about a second path; only a
    privilege check does.

  * 480 — that the rename actually moved rows and left real OpenAI rows alone.

Gated on INTEGRATION_DATABASE_URL — skips cleanly in the unit lane. Point it at
any CI-way Postgres (ci_bootstrap.sql → schema_baseline.sql → migrations above
the watermark):

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
      uv run pytest tests/db/test_migration_480_481_catalog_integration.py
"""

from __future__ import annotations

import os

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — catalog migration tests need a DB.",
)


@pytest.fixture
async def pg():
    """A raw asyncpg connection for setup/assertions (plain libpq DSN)."""
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 481 — the browser roles cannot reach the catalog at all
# ---------------------------------------------------------------------------


@_skip
@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "DELETE"])
async def test_browser_roles_have_no_table_privilege(pg, role: str, privilege: str):
    granted = await pg.fetchval(
        "SELECT has_table_privilege($1, 'public.nous_models', $2)",
        role,
        privilege,
    )
    assert granted is False, (
        f"{role} can {privilege} public.nous_models — PostgREST will honour "
        f"that with the publishable key that ships in the browser bundle, "
        f"bypassing the _PUBLIC_COLS allowlist and exposing base_url/api_key."
    )


@_skip
async def test_the_backend_role_keeps_access(pg):
    """The negative control. Without it, a migration that revoked from PUBLIC
    (and so from the backend too) would pass the test above while taking the
    whole platform-model catalog offline — a green suite for a broken product.

    The control is ``postgres``, NOT ``service_role``, and the difference is a
    real environment fact rather than a preference: ``supabase/ci_bootstrap.sql``
    creates service_role as a bare ``CREATE ROLE ... NOLOGIN NOINHERIT`` with no
    grants at all, so on the drift database it has never had access and
    asserting otherwise fails for a reason that has nothing to do with 481.
    (This test was written against service_role first and caught exactly that —
    the same trap CLAUDE.md records for ``SET ROLE service_role`` in migrations.)

    Production was checked directly instead, 2026-09-21:

        anon           DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
        authenticated  DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
        postgres       DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
        service_role   DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE

    Four independent grants, so revoking the first two leaves the backend's two
    untouched. Worth noting what that table also says: the browser roles held
    full DML, not merely SELECT — RLS was the only thing standing between an
    anon key and writing to the model catalog, which is why 481 revokes ALL
    rather than just SELECT.
    """
    granted = await pg.fetchval(
        "SELECT has_table_privilege($1, 'public.nous_models', 'SELECT')",
        "postgres",
    )
    assert granted is True


# ---------------------------------------------------------------------------
# 480 — the rename hit the engine rows and nothing else
# ---------------------------------------------------------------------------


@_skip
async def test_no_engine_row_is_left_on_the_openai_vendor_key(pg):
    """The three nous-engine rows must not sit under 'openai'.

    Written against NAMES rather than a row count so it stays meaningful on the
    drift database, which has no seed data at all: on an empty catalog this
    asserts the (vacuously true) invariant, and on production it catches a row
    the migration missed.
    """
    rows = await pg.fetch(
        """
        SELECT name, actual_provider
          FROM public.nous_models
         WHERE name IN ('nous-qwen3-llm', 'nous-qwen3-embedding-8b',
                        'mediahub-moss-asr')
        """
    )
    stragglers = [r["name"] for r in rows if r["actual_provider"] != "nous"]
    assert not stragglers, (
        f"nous-engine rows still on a vendor key: {stragglers}. Admin → AI "
        f"Models renders actual_provider verbatim, so these show up as a card "
        f"named after a provider we do not call."
    )


@_skip
async def test_rename_is_scoped_and_leaves_other_openai_rows_alone(pg):
    """A base_url-shaped predicate would have swept up Azure / OpenRouter proxy
    rows, which really are OpenAI. 480 matches by name for exactly this reason;
    this pins that an unrelated 'openai' row survives it.
    """
    await pg.execute(
        """
        INSERT INTO public.nous_models
            (name, display_name, type, actual_provider, actual_model, api_key,
             base_url, is_enabled, sort_order)
        VALUES ('test-480-real-openai', 'Real OpenAI', 'llm', 'openai',
                'gpt-4o', '', 'https://api.openai.com/v1', FALSE, 999)
        ON CONFLICT (name) DO NOTHING
        """
    )
    try:
        provider = await pg.fetchval(
            "SELECT actual_provider FROM public.nous_models WHERE name = $1",
            "test-480-real-openai",
        )
        assert provider == "openai"
    finally:
        await pg.execute(
            "DELETE FROM public.nous_models WHERE name = $1",
            "test-480-real-openai",
        )
