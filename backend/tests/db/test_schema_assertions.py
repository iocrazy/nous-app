"""Unit tests for the startup fail-fast schema-assertion gate.

These tests mock the live ``information_schema.columns`` query so they need
NO real database. They prove the four behaviours the boot gate guarantees:

  1. all critical columns present            → no raise
  2. a critical column missing               → SchemaDriftError naming table + col
  3. a critical table missing entirely       → SchemaDriftError naming the table
  4. engine not configured / connection err  → WARN + return (transient path)

Plus the boot-wiring gate (``SCHEMA_ASSERT_ON_BOOT=false`` skips the check).

The fatal-vs-transient distinction is the crux: a CONFIRMED mismatch
(connected, column missing) crashes boot; a transient DB blip / DB-less
local env must NEVER crash (the app would fail on the first real query
anyway, and we don't want a hiccup to hard-fail every boot).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.db import schema_assertions as sa
from app.db.schema_assertions import (
    CRITICAL_MODELS,
    SchemaDriftError,
    _expected_columns_by_table,
    assert_critical_schema,
)

pytestmark = pytest.mark.asyncio


# ── Helpers ─────────────────────────────────────────────────────────────


def _all_columns_rows() -> list[dict]:
    """Build a fake information_schema.columns result that contains EVERY
    column every critical model maps — i.e. a fully-in-sync live schema."""
    rows: list[dict] = []
    for table, cols in _expected_columns_by_table().items():
        for col in cols:
            rows.append({"table_name": table, "column_name": col})
    return rows


# ── 1. Happy path: all critical columns present → no raise ───────────────


async def test_all_columns_present_no_raise() -> None:
    rows = _all_columns_rows()
    with (
        patch.object(sa.db_engine, "is_configured", return_value=True),
        patch.object(sa.db_engine, "fetch_all", new=AsyncMock(return_value=rows)),
    ):
        # Must not raise.
        await assert_critical_schema()


async def test_single_batched_query() -> None:
    """Latency guarantee: exactly ONE information_schema query for all tables."""
    rows = _all_columns_rows()
    fetch_all = AsyncMock(return_value=rows)
    with (
        patch.object(sa.db_engine, "is_configured", return_value=True),
        patch.object(sa.db_engine, "fetch_all", new=fetch_all),
    ):
        await assert_critical_schema()
    assert fetch_all.await_count == 1


# ── 2. A critical column missing → raises naming table + column ──────────


async def test_missing_column_raises() -> None:
    rows = _all_columns_rows()
    # Drop exactly one column from one critical table.
    target_table = CRITICAL_MODELS[0].__tablename__
    target_col = next(r["column_name"] for r in rows if r["table_name"] == target_table)
    rows = [
        r
        for r in rows
        if not (r["table_name"] == target_table and r["column_name"] == target_col)
    ]

    with (
        patch.object(sa.db_engine, "is_configured", return_value=True),
        patch.object(sa.db_engine, "fetch_all", new=AsyncMock(return_value=rows)),
    ):
        with pytest.raises(SchemaDriftError) as exc:
            await assert_critical_schema()

    msg = str(exc.value)
    assert target_table in msg
    assert target_col in msg


# ── 3. A critical table missing entirely → raises naming the table ───────


async def test_missing_table_raises() -> None:
    rows = _all_columns_rows()
    target_table = CRITICAL_MODELS[0].__tablename__
    # Remove the whole table from the live schema.
    rows = [r for r in rows if r["table_name"] != target_table]

    with (
        patch.object(sa.db_engine, "is_configured", return_value=True),
        patch.object(sa.db_engine, "fetch_all", new=AsyncMock(return_value=rows)),
    ):
        with pytest.raises(SchemaDriftError) as exc:
            await assert_critical_schema()

    assert target_table in str(exc.value)


# ── 4a. Engine not configured → WARN + return (no raise) ─────────────────


async def test_engine_not_configured_no_raise() -> None:
    fetch_all = AsyncMock()
    with (
        patch.object(sa.db_engine, "is_configured", return_value=False),
        patch.object(sa.db_engine, "fetch_all", new=fetch_all),
    ):
        # Must NOT raise; must NOT even query.
        await assert_critical_schema()
    fetch_all.assert_not_awaited()


# ── 4b. Connection error → WARN + return (no raise) ──────────────────────


async def test_connection_error_no_raise() -> None:
    fetch_all = AsyncMock(side_effect=OSError("connection refused"))
    with (
        patch.object(sa.db_engine, "is_configured", return_value=True),
        patch.object(sa.db_engine, "fetch_all", new=fetch_all),
    ):
        # A transient DB blip must NOT crash boot.
        await assert_critical_schema()


async def test_schema_drift_error_not_swallowed_as_transient() -> None:
    """A SchemaDriftError raised mid-check must propagate, never be caught by
    the transient handler (that would defeat the whole gate)."""
    rows = [
        r
        for r in _all_columns_rows()
        if r["table_name"] != CRITICAL_MODELS[0].__tablename__
    ]
    with (
        patch.object(sa.db_engine, "is_configured", return_value=True),
        patch.object(sa.db_engine, "fetch_all", new=AsyncMock(return_value=rows)),
    ):
        with pytest.raises(SchemaDriftError):
            await assert_critical_schema()


# ── 5. Boot gate: SCHEMA_ASSERT_ON_BOOT=false skips the check ────────────


async def test_boot_gate_disabled_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEMA_ASSERT_ON_BOOT", "false")
    inner = AsyncMock()
    with patch.object(sa, "assert_critical_schema", new=inner):
        await sa.assert_critical_schema_on_boot()
    inner.assert_not_awaited()


async def test_boot_gate_enabled_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCHEMA_ASSERT_ON_BOOT", raising=False)
    inner = AsyncMock()
    with patch.object(sa, "assert_critical_schema", new=inner):
        await sa.assert_critical_schema_on_boot()
    inner.assert_awaited_once()


# ── Sanity: the curated set is small + explicit ──────────────────────────


def test_critical_models_are_curated_subset() -> None:
    names = {m.__tablename__ for m in CRITICAL_MODELS}
    assert names == {
        "parsed_media",
        "resources",
        "resource_items",
        "resource_versions",
        "folders",
        "agent_runs",
        "user_settings",
    }


def test_expected_columns_handles_metadata_rename() -> None:
    """parsed_media maps ``metadata_`` → DB column ``metadata`` (SQLAlchemy
    reserves ``metadata`` on declarative classes). The expected-column set
    must use the DB NAME, not the Python attribute."""
    by_table = _expected_columns_by_table()
    assert "metadata" in by_table["parsed_media"]
    assert "metadata_" not in by_table["parsed_media"]


# ── Integration: happy path against a REAL information_schema ────────────
# Proves assert_critical_schema() passes against a live dev schema (which has
# every mapped column). Skips cleanly when no integration DSN is set.
#   source /tmp/orm2_integration.env && uv run pytest tests/db/test_schema_assertions.py -v


@pytest.mark.integration
async def test_assert_passes_against_real_schema() -> None:
    import os as _os

    dsn = _os.environ.get("SUPAVISOR_DATABASE_URL") or _os.environ.get(
        "INTEGRATION_DATABASE_URL", ""
    )
    if not dsn.strip():
        pytest.skip(
            "no integration DSN (SUPAVISOR_DATABASE_URL / INTEGRATION_DATABASE_URL) "
            "— skipping real-schema integration"
        )
    # Point the engine at the dev DSN, reset the singleton, run the real
    # information_schema query end-to-end. The dev schema has every mapped
    # column, so this must NOT raise.
    sa.db_engine.settings.SUPAVISOR_DATABASE_URL = dsn
    await sa.db_engine.dispose_engine()
    try:
        await assert_critical_schema()
    finally:
        await sa.db_engine.dispose_engine()
