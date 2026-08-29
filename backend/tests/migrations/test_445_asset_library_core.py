"""Guard for migrations 445 + 446 — the asset-library schema.

WHAT THIS FILE IS *NOT*
───────────────────────
It is not a column-by-column mirror of the ORM models. That is
``tests/db/test_schema_drift.py``'s job, and it does it exhaustively for every
table in the schema. Duplicating it here would only produce a second diff to
refresh whenever a column moves.

What this file checks is the part the drift test cannot see, because it is not
expressible in a SQLAlchemy model: RLS being enabled and its policies naming
``service_role`` only, the ``WHERE`` predicates that make the unique indexes
partial (drop the predicate and the drift test still passes while a
soft-deleted row permanently squats its name), and the CHECK constraint bodies.

Runs against the CI-built ephemeral schema (ci_bootstrap.sql →
schema_baseline.sql → migrations above the watermark), same as
``test_schema_drift.py`` and ``tests/db/test_social_accounts_realtime_rls.py``.
Skips cleanly when INTEGRATION_DATABASE_URL is unset.
"""

from __future__ import annotations

import os
import re

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytestmark.append(
    pytest.mark.skipif(
        not _TEST_DSN,
        reason="INTEGRATION_DATABASE_URL unset — needs a CI-built Postgres",
    )
)

_TABLES = (
    "assets",
    "asset_loadouts",
    "asset_files",
    "asset_links",
    "asset_project_refs",
    "canvas_asset_refs",
)

# Every value canvases.kind may hold after mig 446. Spelled out rather than
# checked one value at a time: this constraint is rewritten by DROP + ADD, and
# has been six times now (280, 357, 358, 362, 421, 446). Each rewrite restates
# the whole list from scratch, so its real failure mode is a value silently
# going missing — which an "is 'costume' in there?" assertion cannot see.
_EXPECTED_CANVAS_KINDS = frozenset(
    {
        "smart",
        "lite",
        "classic",
        "character",
        "location",
        "prop",
        "storyboard",  # mig 421
        "costume",  # mig 446
    }
)


@pytest.fixture
async def conn():
    c = await asyncpg.connect(_TEST_DSN)
    try:
        yield c
    finally:
        await c.close()


async def _columns(conn, table: str) -> dict[str, asyncpg.Record]:
    rows = await conn.fetch(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = $1",
        table,
    )
    return {r["column_name"]: r for r in rows}


# ── mig 445: the six core tables ────────────────────────────────────────


@pytest.mark.parametrize("table", _TABLES)
async def test_table_exists(conn, table):
    cols = await _columns(conn, table)
    assert cols, f"{table} missing"


async def test_assets_columns(conn):
    cols = await _columns(conn, "assets")
    assert cols["asset_type"]["is_nullable"] == "NO"
    assert cols["prompt_positive"]["is_nullable"] == "YES"
    assert cols["attrs"]["data_type"] == "jsonb"
    assert cols["deleted_at"]["is_nullable"] == "YES"


async def test_assets_unique_name_per_scope_type_is_partial(conn):
    rows = await conn.fetch(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public'"
        " AND tablename = 'assets' AND indexname = 'uq_assets_scope_type_name'"
    )
    assert rows and "WHERE (deleted_at IS NULL)" in rows[0]["indexdef"]


async def test_asset_loadouts_single_default_per_asset(conn):
    rows = await conn.fetch(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public'"
        " AND tablename = 'asset_loadouts' AND indexname = 'uq_loadout_default'"
    )
    assert rows and "WHERE is_default" in rows[0]["indexdef"]


async def test_assets_scope_nullable_only_for_presets(conn):
    cols = await _columns(conn, "assets")
    assert cols["scope_id"]["is_nullable"] == "YES"
    rows = await conn.fetch(
        "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint"
        " WHERE conrelid = 'public.assets'::regclass"
        " AND conname = 'assets_scope_or_preset'"
    )
    assert rows and "is_system_preset" in rows[0]["def"]


@pytest.mark.parametrize("table", _TABLES)
async def test_rls_enabled_service_role_only(conn, table):
    enabled = await conn.fetchval(
        "SELECT relrowsecurity FROM pg_class"
        " WHERE relnamespace = 'public'::regnamespace AND relname = $1",
        table,
    )
    assert enabled is True, f"{table} has RLS disabled"
    pols = await conn.fetch(
        "SELECT policyname, roles::text AS roles FROM pg_policies"
        " WHERE schemaname = 'public' AND tablename = $1",
        table,
    )
    assert len(pols) == 1 and "service_role" in pols[0]["roles"]


async def test_asset_links_relation_check(conn):
    rows = await conn.fetch(
        "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint"
        " WHERE conrelid = 'public.asset_links'::regclass"
        " AND conname = 'asset_links_relation_check'"
    )
    assert rows and "wears" in rows[0]["def"] and "voice_of" in rows[0]["def"]


# ── mig 446: generated_media + canvases extensions ──────────────────────


async def test_generated_media_review_state_default(conn):
    cols = await _columns(conn, "generated_media")
    assert cols["review_state"]["is_nullable"] == "NO"
    assert cols["source_asset_id"]["is_nullable"] == "YES"
    default = await conn.fetchval(
        "SELECT column_default FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = 'generated_media'"
        " AND column_name = 'review_state'"
    )
    assert "'unreviewed'" in (default or "")


async def test_canvases_asset_id_and_costume_kind(conn):
    cols = await _columns(conn, "canvases")
    assert cols["asset_id"]["is_nullable"] == "YES"
    definition = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conrelid = 'public.canvases'::regclass"
        " AND conname = 'canvases_kind_check'"
    )
    assert definition, "canvases_kind_check is gone"
    # The only single-quoted literals in this constraint are the kind values.
    kinds = set(re.findall(r"'([^']*)'", definition))
    assert kinds == _EXPECTED_CANVAS_KINDS, (
        f"dropped: {sorted(_EXPECTED_CANVAS_KINDS - kinds)}, "
        f"unexpected: {sorted(kinds - _EXPECTED_CANVAS_KINDS)}"
    )


@pytest.mark.parametrize(
    ("table", "constraint"),
    [
        ("generated_media", "generated_media_source_asset_id_fkey"),
        ("canvases", "canvases_asset_id_fkey"),
    ],
)
async def test_new_asset_fks_null_out_on_delete(conn, table, constraint):
    """Deleting an asset must blank the pointer, never cascade.

    ON DELETE CASCADE here would make deleting one asset destroy the
    generations it was dispatched from and the canvases that referenced it —
    and both are independently owned rows that outlive the asset.
    """
    # ::text because pg_constraint.confdeltype is `"char"`, which asyncpg hands
    # back as bytes — comparing that to 'n' fails for the wrong reason.
    action = await conn.fetchval(
        "SELECT c.confdeltype::text FROM pg_constraint c"
        " JOIN pg_class t ON t.oid = c.conrelid"
        " WHERE t.relnamespace = 'public'::regnamespace AND t.relname = $1"
        " AND c.conname = $2 AND c.contype = 'f'",
        table,
        constraint,
    )
    assert (
        action == "n"
    ), f"{constraint} has ON DELETE {action!r}, expected 'n' (SET NULL)"
