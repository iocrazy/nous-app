"""Verify mig 445 creates the asset-library core tables with the exact shape
the ORM models in app/models/assets.py mirror (schema-drift gates 1-6)."""

from __future__ import annotations

import os

import pytest

from app.db import engine as db_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("SUPAVISOR_DATABASE_URL")
        and not os.environ.get("INTEGRATION_DATABASE_URL"),
        reason="integration DB URL not set",
    ),
]

_TABLES = (
    "assets",
    "asset_loadouts",
    "asset_files",
    "asset_links",
    "asset_project_refs",
    "canvas_asset_refs",
)


async def _columns(table: str) -> dict[str, dict]:
    rows = await db_engine.fetch_all(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t",
        {"t": table},
    )
    return {r["column_name"]: r for r in rows}


@pytest.mark.parametrize("table", _TABLES)
async def test_table_exists(table):
    cols = await _columns(table)
    assert cols, f"{table} missing"


async def test_assets_columns():
    cols = await _columns("assets")
    assert cols["asset_type"]["is_nullable"] == "NO"
    assert cols["prompt_positive"]["is_nullable"] == "YES"
    assert cols["attrs"]["data_type"] == "jsonb"
    assert cols["deleted_at"]["is_nullable"] == "YES"


async def test_assets_unique_name_per_scope_type_is_partial():
    rows = await db_engine.fetch_all(
        "SELECT indexdef FROM pg_indexes WHERE tablename='assets' "
        "AND indexname='uq_assets_scope_type_name'",
        {},
    )
    assert rows and "WHERE (deleted_at IS NULL)" in rows[0]["indexdef"]


async def test_asset_loadouts_single_default_per_asset():
    rows = await db_engine.fetch_all(
        "SELECT indexdef FROM pg_indexes WHERE tablename='asset_loadouts' "
        "AND indexname='uq_loadout_default'",
        {},
    )
    assert rows and "WHERE is_default" in rows[0]["indexdef"]


async def test_assets_scope_nullable_only_for_presets():
    cols = await _columns("assets")
    assert cols["scope_id"]["is_nullable"] == "YES"
    rows = await db_engine.fetch_all(
        "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid='assets'::regclass AND conname='assets_scope_or_preset'",
        {},
    )
    assert rows and "is_system_preset" in rows[0]["def"]


@pytest.mark.parametrize("table", _TABLES)
async def test_rls_enabled_service_role_only(table):
    rows = await db_engine.fetch_all(
        "SELECT relrowsecurity FROM pg_class WHERE relname=:t", {"t": table}
    )
    assert rows and rows[0]["relrowsecurity"] is True
    pols = await db_engine.fetch_all(
        "SELECT policyname, roles::text AS roles FROM pg_policies WHERE tablename=:t",
        {"t": table},
    )
    assert len(pols) == 1 and "service_role" in pols[0]["roles"]


async def test_asset_links_relation_check():
    rows = await db_engine.fetch_all(
        "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid='asset_links'::regclass AND conname='asset_links_relation_check'",
        {},
    )
    assert rows and "wears" in rows[0]["def"] and "voice_of" in rows[0]["def"]
