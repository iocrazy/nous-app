"""Schema-drift guard: reflect live Postgres and assert every ORM model matches.

Replaces Alembic autogenerate as the "models stay in sync with the DB" guarantee.
Models were generated from PROD via sqlacodegen; against PROD this test is GREEN
by construction.

Five hard gates (all run against the 107 mapped public tables):
  1. test_no_missing_tables         — every mapped table exists in live public schema
  2. test_column_names_match        — set(model cols) == set(live cols) per table
  3. test_nullability_matches       — model nullable matches live is_nullable per col
  4. test_column_types_match        — model SA type maps to expected udt_name per col
  5. test_no_unmapped_inscope_tables — (live tables) - {3 excluded} - {mapped} == empty

Setup: set INTEGRATION_DATABASE_URL to a live Postgres DSN.
  source /tmp/orm2_integration_prod.env
  uv run pytest tests/db/test_schema_drift.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset (CI has no DB).
"""

from __future__ import annotations

import os
from typing import Any

import asyncpg
import pytest

# ── Module-level skip gate ──────────────────────────────────────────────
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

# ── Permanently excluded public tables (non-domain / infra) ─────────────
# These 3 tables exist in public but are deliberately NOT mapped as ORM models.
# They must never be flagged as "unmapped" drift.
_EXCLUDED_TABLES: frozenset[str] = frozenset(
    {
        "_scratch_dbos",
        "_scratch_test",
        "dbos_workflow_routing",
    }
)

# ── SQLAlchemy type → PG udt_name normalization map ─────────────────────
# Maps the SQLAlchemy column type class name (from type(col.type).__name__)
# to the PG information_schema.columns.udt_name token.
# Covers every SA type that appears across the 107 mapped tables.
#
# Rules:
#   • Enums: udt_name == the PG enum type name (col.type.name), NOT "text".
#   • ARRAY(Text) → "_text"; ARRAY(BigInteger) → "_int8"; etc.
#   • Computed columns (persisted) show up with their base column type — no skip.
#   • "Uuid" (SQLAlchemy native) and "UUID" (pg dialect) both map to "uuid".
#   • String/VARCHAR → "varchar" regardless of length.
#   • Double(53) → "float8".
#
# TYPE_CHECK_SKIP: (table_name, column_name) pairs whose type comparison is
# skipped with a documented reason. Keep MINIMAL.
_SA_TYPE_TO_UDT: dict[str, str] = {
    "BigInteger": "int8",
    "Integer": "int4",
    "SmallInteger": "int2",
    "Text": "text",
    "String": "varchar",  # character varying; length irrelevant here
    "Boolean": "bool",
    "JSONB": "jsonb",
    "INET": "inet",
    "Uuid": "uuid",  # sqlalchemy.Uuid (SA 2.0 native)
    "UUID": "uuid",  # sqlalchemy.dialects.postgresql.UUID
    "Numeric": "numeric",
    "Double": "float8",
    "Date": "date",
    # DateTime: timezone-aware → "timestamptz"; naive → "timestamp"
    # Handled dynamically in _expected_udt().
    # ARRAY: item type drives the "_<udt>" token.
    # Handled dynamically in _expected_udt().
    # Enum: maps to the PG enum type name from col.type.name.
    # Handled dynamically in _expected_udt().
    # VECTOR: pgvector extension type.
    "VECTOR": "vector",
}

# Small allowlist for genuinely ambiguous mappings that cannot be normalized
# automatically. Each entry must have a comment explaining WHY.
# Format: (table_name, column_name): "reason"
TYPE_CHECK_SKIP: dict[tuple[str, str], str] = {
    # No skips currently needed — all types map cleanly.
}

# Map for ARRAY item type → PG array udt prefix (e.g., "_text", "_int8")
_ARRAY_ITEM_UDT: dict[str, str] = {
    "Text": "_text",
    "String": "_text",  # varchar[] also uses _text element storage
    "BigInteger": "_int8",
    "Integer": "_int4",
    "SmallInteger": "_int2",
    "Boolean": "_bool",
    "Uuid": "_uuid",
    "UUID": "_uuid",
}


def _expected_udt(col: Any) -> str | None:
    """Return the expected PG udt_name for a SQLAlchemy column.

    Returns None if this column type is in TYPE_CHECK_SKIP (caller skips it).
    Raises ValueError for unmapped types (forces us to extend the map).
    """
    table_name = col.table.name  # stripped of schema prefix by caller
    col_name = col.name
    if (table_name, col_name) in TYPE_CHECK_SKIP:
        return None  # explicitly skipped

    sa_type = col.type
    type_class = type(sa_type).__name__

    # ── Special cases ────────────────────────────────────────────────
    if type_class == "DateTime":
        return "timestamptz" if sa_type.timezone else "timestamp"

    if type_class == "Enum":
        # PG user-defined enum: udt_name is the enum type name, e.g. "download_status"
        if not sa_type.name:
            raise ValueError(
                f"{table_name}.{col_name}: SQLAlchemy Enum must have .name set "
                f"(got name={sa_type.name!r}) — cannot determine expected udt_name"
            )
        return sa_type.name

    if type_class == "ARRAY":
        item_class = type(sa_type.item_type).__name__
        udt = _ARRAY_ITEM_UDT.get(item_class)
        if udt is None:
            raise ValueError(
                f"{table_name}.{col_name}: ARRAY with unmapped item type "
                f"{item_class!r} — extend _ARRAY_ITEM_UDT"
            )
        return udt

    # ── Standard map lookup ──────────────────────────────────────────
    udt = _SA_TYPE_TO_UDT.get(type_class)
    if udt is None:
        raise ValueError(
            f"{table_name}.{col_name}: unmapped SA type {type_class!r} "
            f"— extend _SA_TYPE_TO_UDT in test_schema_drift.py"
        )
    return udt


# ── DSN fixture ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip(
            "INTEGRATION_DATABASE_URL not set — skipping schema-drift integration tests"
        )
    return _TEST_DSN


# ── Live schema fixture ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
async def live_schema(integration_db_url: str) -> dict[str, dict[str, dict[str, str]]]:
    """Return the live public schema as:
      { table_name: { column_name: { 'udt_name': ..., 'is_nullable': 'YES'|'NO' } } }

    Queries information_schema.columns for public BASE TABLEs only.
    Read-only; no writes to the database.
    """
    conn = await asyncpg.connect(integration_db_url)
    try:
        rows = await conn.fetch(
            """
            SELECT c.table_name, c.column_name, c.udt_name, c.is_nullable
            FROM information_schema.columns AS c
            JOIN information_schema.tables AS t
              ON t.table_schema = c.table_schema
             AND t.table_name   = c.table_name
            WHERE c.table_schema = 'public'
              AND t.table_type   = 'BASE TABLE'
            ORDER BY c.table_name, c.ordinal_position
            """
        )
    finally:
        await conn.close()

    schema: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        tbl = row["table_name"]
        col = row["column_name"]
        schema.setdefault(tbl, {})[col] = {
            "udt_name": row["udt_name"],
            "is_nullable": row["is_nullable"],
        }
    return schema


# ── ORM metadata helper ─────────────────────────────────────────────────


def _orm_tables() -> dict[str, Any]:
    """Return { bare_table_name: sqlalchemy.Table } from Base.metadata.

    Strips the 'public.' schema prefix so names match information_schema output.
    Importing here (inside the function) avoids import-time side effects when
    INTEGRATION_DATABASE_URL is unset and the module is loaded for collection.
    """
    import app.models  # noqa: F401 — registers all models onto Base.metadata
    from app.db.orm_base import Base

    result: dict[str, Any] = {}
    for full_name, table in Base.metadata.tables.items():
        # full_name is "public.table_name" for all our models
        bare = full_name.split(".", 1)[-1]
        result[bare] = table
    return result


# ── Tests ───────────────────────────────────────────────────────────────


async def test_no_missing_tables(
    live_schema: dict[str, dict[str, dict[str, str]]],
) -> None:
    """Every ORM-mapped table must exist as a BASE TABLE in live public schema.

    Drift: a migration dropped a table without removing the model.
    """
    orm = _orm_tables()
    missing = sorted(tname for tname in orm if tname not in live_schema)
    assert not missing, (
        f"{len(missing)} ORM-mapped table(s) not found in live 'public' schema "
        f"(BASE TABLE only):\n  "
        + "\n  ".join(missing)
        + "\n\nIf a table was intentionally removed, delete the corresponding model."
    )


async def test_column_names_match(
    live_schema: dict[str, dict[str, dict[str, str]]],
) -> None:
    """Column-name sets must match exactly between model and live table.

    Reports: dropped = in live but not in model; extra = in model but not in live.
    Drift: a migration added/dropped/renamed a column without updating the model.
    """
    orm = _orm_tables()
    failures: list[str] = []

    for tname, table in sorted(orm.items()):
        if tname not in live_schema:
            continue  # already caught by test_no_missing_tables
        live_cols = set(live_schema[tname].keys())
        model_cols = {col.name for col in table.columns}

        dropped = sorted(live_cols - model_cols)  # in live, missing from model
        extra = sorted(model_cols - live_cols)  # in model, missing from live

        if dropped or extra:
            parts: list[str] = [f"  {tname}:"]
            if dropped:
                parts.append(f"    dropped (in live, not in model): {dropped}")
            if extra:
                parts.append(f"    extra   (in model, not in live): {extra}")
            failures.append("\n".join(parts))

    assert (
        not failures
    ), f"Column-name mismatch in {len(failures)} table(s):\n" + "\n".join(failures)


async def test_nullability_matches(
    live_schema: dict[str, dict[str, dict[str, str]]],
) -> None:
    """Model column.nullable must match live is_nullable for every shared column.

    Drift: a migration added/removed NOT NULL without updating the model.
    """
    orm = _orm_tables()
    failures: list[str] = []

    for tname, table in sorted(orm.items()):
        if tname not in live_schema:
            continue
        for col in table.columns:
            if col.name not in live_schema[tname]:
                continue  # column-name mismatches caught elsewhere

            live_is_nullable = live_schema[tname][col.name]["is_nullable"]
            live_nullable: bool = live_is_nullable == "YES"
            model_nullable: bool = bool(col.nullable)

            if model_nullable != live_nullable:
                failures.append(
                    f"  {tname}.{col.name}: "
                    f"model nullable={model_nullable}, "
                    f"live nullable={live_nullable} (is_nullable={live_is_nullable!r})"
                )

    assert (
        not failures
    ), f"Nullability mismatch in {len(failures)} column(s):\n" + "\n".join(failures)


async def test_column_types_match(
    live_schema: dict[str, dict[str, dict[str, str]]],
) -> None:
    """Model column type must match live udt_name for every shared column.

    Uses _expected_udt() to convert each SQLAlchemy type to its PG udt token.
    Columns listed in TYPE_CHECK_SKIP are exempted with a documented reason.

    Drift: a migration changed a column type without updating the model.
    """
    orm = _orm_tables()
    failures: list[str] = []

    for tname, table in sorted(orm.items()):
        if tname not in live_schema:
            continue
        for col in sorted(table.columns, key=lambda c: c.name):
            if col.name not in live_schema[tname]:
                continue  # column-name mismatches caught elsewhere

            expected = _expected_udt(col)
            if expected is None:
                # Explicitly skipped via TYPE_CHECK_SKIP
                continue

            live_udt = live_schema[tname][col.name]["udt_name"]

            if expected != live_udt:
                failures.append(
                    f"  {tname}.{col.name}: "
                    f"model expects udt={expected!r}, "
                    f"live udt={live_udt!r} "
                    f"(SA type: {type(col.type).__name__})"
                )

    assert not failures, (
        f"Column type mismatch in {len(failures)} column(s):\n"
        + "\n".join(failures)
        + "\n\nIf the mapping is genuinely ambiguous, add to TYPE_CHECK_SKIP "
        "with a documented reason."
    )


async def test_no_unmapped_inscope_tables(
    live_schema: dict[str, dict[str, dict[str, str]]],
) -> None:
    """No in-scope public BASE TABLE should be unmapped.

    In-scope = live public BASE TABLEs minus the 3 permanently excluded tables.
    Drift: a migration added a new table but no model was created.

    The live BASE TABLE set is derived from the live_schema fixture
    (frozenset(live_schema.keys())) — that fixture already restricts to
    public BASE TABLEs, so no second DB connection is needed.
    """
    orm = _orm_tables()
    mapped = frozenset(orm.keys())
    live_base_tables = frozenset(live_schema.keys())
    in_scope_live = live_base_tables - _EXCLUDED_TABLES

    unmapped = sorted(in_scope_live - mapped)
    assert not unmapped, (
        f"{len(unmapped)} in-scope live public table(s) have no ORM model:\n  "
        + "\n  ".join(unmapped)
        + "\n\nEither add a model in app/models/, or add the table to "
        "_EXCLUDED_TABLES in this file with a justification."
    )
