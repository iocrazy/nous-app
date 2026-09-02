"""Schema-drift guard: reflect live Postgres and assert every ORM model matches.

Replaces Alembic autogenerate as the "models stay in sync with the DB" guarantee.

The models were originally generated from prod via sqlacodegen, but "green by
construction" stopped being true the moment the first migration shipped without
a regen — which is exactly what happened for months while this gate silently
no-op'd. Nothing here is green by construction; it is green because it is
checked. In CI the schema under test is built from supabase/schema_baseline.sql
plus the migrations above its watermark, so this runs on every PR with no secret
and no live database.

Six hard gates (all run against the mapped public tables):
  1. test_no_missing_tables         — every mapped table exists in live public schema
  2. test_column_names_match        — set(model cols) == set(live cols) per table
  3. test_nullability_matches       — model nullable matches live is_nullable per col
  4. test_column_types_match        — model SA type maps to expected udt_name per col
  5. test_no_unmapped_inscope_tables — (live tables) - {3 excluded} - {mapped} == empty
  6. test_foreign_keys_match        — model FK column-pairs == live FK column-pairs
                                      (public targets only; auth.users targets are a
                                      convention-omission, not drift)

Setup: point INTEGRATION_DATABASE_URL at any Postgres built the CI way
(ci_bootstrap.sql → schema_baseline.sql → migrations above the watermark):
  INTEGRATION_DATABASE_URL=postgresql://... uv run pytest tests/db/test_schema_drift.py -v

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
# These 2 tables exist in public but are deliberately NOT mapped as ORM models.
# They must never be flagged as "unmapped" drift.
# ``dbos_workflow_routing`` used to be a 3rd entry here; it is now mapped
# (``DbosWorkflowRouting``, Phase C task 1) so it is removed from this
# exclusion set — gates 1-4 now check it like every other table.
_EXCLUDED_TABLES: frozenset[str] = frozenset(
    {
        "_scratch_dbos",
        "_scratch_test",
    }
)

# ── RATCHET ALLOWLISTS (known drift — shrink only, never grow) ──────────
# Same contract as tests/db/test_no_new_run_async_bridges.py: every entry is a
# DOCUMENTED, pre-existing mismatch. Known drift stays green; any NEW drift goes
# red immediately.
#
# The ratchet is enforced two ways:
#   1. test_allowlists_are_still_accurate — an entry that is no longer drifting
#      FAILS, forcing its removal. Exemptions cannot go stale.
#   2. test_allowlists_only_shrink — the ceilings below are asserted. Lowering
#      one as drift is fixed locks the gain in; raising one is a deliberate,
#      reviewable edit rather than a silent slide.
#
# These exist because the models were generated from prod at ORM 2.0 Phase 0 and
# the gate that should have caught subsequent drift was silently no-op'ing (it
# skipped when PROD_DB_PASSWORD was absent — and that secret never existed). The
# follow-up (sqlacodegen full regen + deleting the dead storyboard models) empties
# all three lists; nothing here is intended to be permanent.

# gate 1 — models whose table does not exist live.
# EMPTY, and it should stay that way: a model pointing at a table that does not
# exist is a 42P01 waiting to happen. The eight storyboard models that used to
# live here now map their real post-migration-348 names
# (zzz_deprecated_storyboard_*), so they are checked like everything else rather
# than exempted. They disappear entirely when those tombstone tables are DROPped.
#
# ⚠️ THIS REPO HAS TWO PREFIXES FOR "RENAMED, AWAITING DROP", and a grep for
# retired tables has to try both: ``zzz_deprecated_*`` (migration 348, the
# storyboard tree) and ``_legacy_*`` (migration 447, _legacy_project_characters
# / _legacy_project_lib_entities — the prefix spec §3.8 prescribes and the one
# new retirements should use). Neither family is exempted here: their models
# map the post-rename names, so every gate checks them like any other table.
_ALLOWED_MISSING_TABLES: frozenset[str] = frozenset()

# gate 2 — columns that exist LIVE but are missing from the model.
# Direction matters: this list only ever exempts live-has/model-lacks. The
# reverse (model-has/live-lacks) is NOT exemptible and always fails — that is the
# direction that makes ORM code raise 42703 at runtime. There are zero such cases
# today and there must stay zero.
#
# Consequence of the drift below is bounded: `select(Model)` silently omits these
# columns and new code must reach them via column()/text(). Nothing crashes.
# Each was added by a migration whose feature shipped without regenerating models.
_ALLOWED_MISSING_COLUMNS: dict[str, frozenset[str]] = {
    # EMPTY — every column below is now on its model. Keep it that way: a
    # migration that adds a column must add it to the model in the same PR.
}

# gate 5 — live tables with no ORM model. Each shipped after the models were
# generated; the ORM simply never learned about them. Remove an entry by ADDING
# the model.
# Empty by construction. The one entry this ever held, `project_tasks`, was a
# zombie left by migration 176's half-landed drop (guard passed, task_assets
# dropped, project_tasks did not — it was owned by postgres while 176 ran as
# supabase_admin). Migration 365 re-attempted the drop without SET ROLE and it
# is gone from prod (verified 2026-07-16) and from this gate's ephemeral schema
# (365 > baseline watermark 364, so it applies here too). Entry removed per the
# accuracy ratchet: an allowlist line that no longer describes real drift must
# not linger.
_ALLOWED_UNMAPPED_TABLES: frozenset[str] = frozenset()

# Ratchet ceilings — measured against prod 2026-07-15. These may only ever be
# LOWERED. See test_allowlists_only_shrink.
#
# All three are now ZERO: no allowlist can take a single new entry without a
# visible, reviewed edit to these lines. The models match prod exactly. Any
# future drift — a migration that changes the schema without regenerating a
# model — turns this gate red on the PR that introduces it.
_MAX_ALLOWED_MISSING_TABLES = 0
_MAX_ALLOWED_MISSING_COLUMNS = 0
_MAX_ALLOWED_UNMAPPED_TABLES = 0

# gate 6 — foreign-key drift. Each entry is a documented, pre-existing FK
# mismatch keyed as "src_table.src_col->tgt_table.tgt_col" (schema is always
# public on both sides — see below). A pair can only ever drift in ONE direction
# at a time (either the model declares an FK the live DB lacks, or the live DB
# has an FK the model does not declare), so a single un-tagged key is unambiguous.
#
# Direction of each drift is reported at failure time:
#   • model-has / live-lacks — the DANGEROUS one. The model points a FK at a
#     table/column the live DB does not have that relationship for — usually a
#     target that was renamed or dropped (this is exactly how the two
#     script_storyboard_links FKs drifted to their pre-mig-348 target names and
#     escaped the other five gates).
#   • live-has / model-lacks — the model is reference-only and simply never
#     declared the FK. Harmless at runtime, but still drift, so it is registered.
#
# ⚠️ auth.users targets are NOT in scope here and must never appear: the flat-model
# convention permanently omits FKs to auth.users (GoTrue owns that table), so the
# live→model direction is filtered on target schema = 'auth' before comparison.
# Only public→public FK column-pairs are compared.
#
# EMPTY, ceiling 0: #1406 regenerated the models against prod so model FKs match
# prod exactly. Any migration that renames/drops an FK target (or adds/removes an
# FK) without regenerating the model turns this gate red on that PR.
_ALLOWED_FK_DRIFT: frozenset[str] = frozenset()

# Ratchet ceiling for gate 6 — may only ever be LOWERED. See
# test_allowlists_only_shrink.
_MAX_ALLOWED_FK_DRIFT = 0

# ── SQLAlchemy type → PG udt_name normalization map ─────────────────────
# Maps the SQLAlchemy column type class name (from type(col.type).__name__)
# to the PG information_schema.columns.udt_name token.
# Covers every SA type that appears across the 105 mapped tables.
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
    "REAL": "float4",  # sqlalchemy.REAL — single precision
    "CHAR": "bpchar",  # blank-padded fixed-width char(n)
    "Date": "date",
    # Float: precision drives float4 vs float8 — handled dynamically in
    # _expected_udt() (SA renders bare Float as PG "float8"/double precision).
    # DateTime: timezone-aware → "timestamptz"; naive → "timestamp"
    # Handled dynamically in _expected_udt().
    # ARRAY: item type drives the "_<udt>" token.
    # Handled dynamically in _expected_udt().
    # Enum: maps to the PG enum type name from col.type.name.
    # Handled dynamically in _expected_udt().
    # VECTOR: pgvector extension type.
    "VECTOR": "vector",
    # TSVECTOR: full-text search vector (agent_memory.search_tsv, a STORED
    # generated column — Computed() does not change the reported udt).
    "TSVECTOR": "tsvector",
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

    if type_class == "Float":
        # SQLAlchemy Float renders as PG "double precision" (float8) unless a
        # precision <= 24 asks for single precision (float4). The only Float
        # column today is project_file_comments.timestamp_seconds, whose live
        # udt_name is float8 (verified against prod 2026-07-15).
        precision = getattr(sa_type, "precision", None)
        return "float4" if precision is not None and precision <= 24 else "float8"

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


# ── Live foreign-key fixture ────────────────────────────────────────────


@pytest.fixture(scope="module")
async def live_fks(integration_db_url: str) -> list[dict[str, str]]:
    """Return every foreign key whose SOURCE table is in the public schema.

    One row per (source column, target column) pair — composite FKs are
    positionally unnested via WITH ORDINALITY so each column pair is its own
    row. This matches SQLAlchemy's model side, where a multi-column
    ForeignKeyConstraint yields one ForeignKey object per column.

    Each dict carries the target schema so the caller can filter auth.users
    targets (a convention-omission, not drift). Read-only; no writes.
    """
    conn = await asyncpg.connect(integration_db_url)
    try:
        rows = await conn.fetch(
            """
            SELECT
                src.relname       AS src_table,
                src_att.attname   AS src_col,
                tgt_ns.nspname    AS tgt_schema,
                tgt.relname       AS tgt_table,
                tgt_att.attname   AS tgt_col
            FROM pg_constraint con
            JOIN pg_class     src    ON src.oid = con.conrelid
            JOIN pg_namespace src_ns ON src_ns.oid = src.relnamespace
            JOIN pg_class     tgt    ON tgt.oid = con.confrelid
            JOIN pg_namespace tgt_ns ON tgt_ns.oid = tgt.relnamespace
            JOIN LATERAL unnest(con.conkey)  WITH ORDINALITY AS sk(attnum, ord)
              ON true
            JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS tk(attnum, ord)
              ON sk.ord = tk.ord
            JOIN pg_attribute src_att
              ON src_att.attrelid = con.conrelid AND src_att.attnum = sk.attnum
            JOIN pg_attribute tgt_att
              ON tgt_att.attrelid = con.confrelid AND tgt_att.attnum = tk.attnum
            WHERE con.contype = 'f'
              AND src_ns.nspname = 'public'
            ORDER BY src.relname, src_att.attname
            """
        )
    finally:
        await conn.close()

    return [
        {
            "src_table": r["src_table"],
            "src_col": r["src_col"],
            "tgt_schema": r["tgt_schema"],
            "tgt_table": r["tgt_table"],
            "tgt_col": r["tgt_col"],
        }
        for r in rows
    ]


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


# FK comparison key: schema is public on both sides (models never declare
# non-public FKs; the live side is filtered to public targets before comparison),
# so the key omits it for readability. Format matches _ALLOWED_FK_DRIFT entries.
def _fk_key(src_table: str, src_col: str, tgt_table: str, tgt_col: str) -> str:
    return f"{src_table}.{src_col}->{tgt_table}.{tgt_col}"


def _orm_fks() -> set[str]:
    """Return every model-declared FK as a set of _fk_key strings.

    Uses ForeignKey.target_fullname (the raw 'schema.table.col' colspec) rather
    than .column, so a model FK pointing at a table absent from Base.metadata
    does NOT raise here — that dangling target is exactly the drift this gate
    must report, not crash on. A composite ForeignKeyConstraint contributes one
    ForeignKey per column, so keys are column-pair granular like the live side.
    """
    keys: set[str] = set()
    for table in _orm_tables().values():
        for fk in table.foreign_keys:
            # target_fullname is "schema.table.col"; models always write public.
            parts = fk.target_fullname.split(".")
            if len(parts) == 3:
                tgt_schema, tgt_table, tgt_col = parts
            elif len(parts) == 2:
                # Defensive: an unqualified colspec means the public schema.
                tgt_schema, (tgt_table, tgt_col) = "public", parts
            else:  # pragma: no cover — colspec is always 2- or 3-part
                raise ValueError(
                    f"{table.name}.{fk.parent.name}: cannot parse FK target "
                    f"{fk.target_fullname!r}"
                )
            # Only public-targeting FKs participate; a model auth FK would be a
            # convention violation and (correctly) surface as model-only drift,
            # but in practice none exist.
            if tgt_schema == "public":
                keys.add(_fk_key(table.name, fk.parent.name, tgt_table, tgt_col))
    return keys


# ── Tests ───────────────────────────────────────────────────────────────


async def test_no_missing_tables(
    live_schema: dict[str, dict[str, dict[str, str]]],
) -> None:
    """Every ORM-mapped table must exist as a BASE TABLE in live public schema.

    Drift: a migration dropped a table without removing the model.
    """
    orm = _orm_tables()
    missing = sorted(
        tname
        for tname in orm
        if tname not in live_schema and tname not in _ALLOWED_MISSING_TABLES
    )
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

        # Ratchet: exempt only the documented live-has/model-lacks columns.
        # `extra` is never exempted — see _ALLOWED_MISSING_COLUMNS.
        dropped = sorted(
            live_cols - model_cols - _ALLOWED_MISSING_COLUMNS.get(tname, frozenset())
        )
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

    In-scope = live public BASE TABLEs minus the 2 permanently excluded tables.
    Drift: a migration added a new table but no model was created.

    The live BASE TABLE set is derived from the live_schema fixture
    (frozenset(live_schema.keys())) — that fixture already restricts to
    public BASE TABLEs, so no second DB connection is needed.
    """
    orm = _orm_tables()
    mapped = frozenset(orm.keys())
    live_base_tables = frozenset(live_schema.keys())
    in_scope_live = live_base_tables - _EXCLUDED_TABLES

    unmapped = sorted(in_scope_live - mapped - _ALLOWED_UNMAPPED_TABLES)
    assert not unmapped, (
        f"{len(unmapped)} in-scope live public table(s) have no ORM model:\n  "
        + "\n  ".join(unmapped)
        + "\n\nEither add a model in app/models/, or add the table to "
        "_EXCLUDED_TABLES in this file with a justification."
    )


def _live_public_fk_keys(live_fks: list[dict[str, str]]) -> set[str]:
    """The set of live FK column-pairs eligible for comparison with the models.

    Restricted to:
      • target schema == 'public' — auth.users targets are dropped here (the
        convention-omission carve-out), and any other schema is handled by
        _live_nonpublic_nonauth_fks() so it is surfaced rather than silently
        compared against nothing.
      • source table is ORM-mapped — an FK on an unmapped table is a symptom of
        the table itself being unmapped (gate 5), so it is not double-reported
        here. Gate 5's ceiling is 0, so in practice every public table is mapped.
    """
    mapped = frozenset(_orm_tables().keys())
    return {
        _fk_key(fk["src_table"], fk["src_col"], fk["tgt_table"], fk["tgt_col"])
        for fk in live_fks
        if fk["tgt_schema"] == "public" and fk["src_table"] in mapped
    }


async def test_foreign_keys_match(live_fks: list[dict[str, str]]) -> None:
    """Model-declared FK column-pairs must match live public FK column-pairs.

    Two drift directions, both reported:
      • model-has / live-lacks — model declares an FK the live DB does not have.
        Usually a target renamed/dropped by a migration without regenerating the
        model (the script_storyboard_links failure mode).
      • live-has / model-lacks — live DB has an FK the model never declared.

    auth.users targets are filtered out before comparison (flat-model convention
    omits them). Any live FK targeting a schema OTHER than public or auth is not
    exemptible and always fails — the models can only ever target public, so such
    an FK is by definition undeclared drift and must be looked at, never assumed.
    """
    orm_keys = _orm_fks()
    live_keys = _live_public_fk_keys(live_fks)

    # Any non-public, non-auth target is unexpected and never exempt.
    mapped = frozenset(_orm_tables().keys())
    unexpected_targets = sorted(
        f"  {fk['src_table']}.{fk['src_col']} -> "
        f"{fk['tgt_schema']}.{fk['tgt_table']}.{fk['tgt_col']}"
        for fk in live_fks
        if fk["tgt_schema"] not in ("public", "auth") and fk["src_table"] in mapped
    )

    model_only = sorted(
        (orm_keys - live_keys) - _ALLOWED_FK_DRIFT
    )  # model declares, live lacks
    live_only = sorted(
        (live_keys - orm_keys) - _ALLOWED_FK_DRIFT
    )  # live has, model lacks

    parts: list[str] = []
    if model_only:
        parts.append(
            f"  model-has / live-lacks ({len(model_only)}) — model FK points at a "
            f"target the live DB has no such relationship for (renamed/dropped "
            f"table?). Fix the model FK target or drop the FK:"
        )
        parts += [f"    {k}" for k in model_only]
    if live_only:
        parts.append(
            f"  live-has / model-lacks ({len(live_only)}) — live DB has an FK the "
            f"model never declared. Add the ForeignKeyConstraint to the model, or "
            f"register it in _ALLOWED_FK_DRIFT if intentionally reference-only:"
        )
        parts += [f"    {k}" for k in live_only]
    if unexpected_targets:
        parts.append(
            f"  live FK targeting a non-public, non-auth schema "
            f"({len(unexpected_targets)}) — not exemptible; investigate:"
        )
        parts += unexpected_targets

    assert not parts, "Foreign-key drift detected:\n" + "\n".join(parts)


# ── Ratchet enforcement ─────────────────────────────────────────────────


def test_allowlists_only_shrink() -> None:
    """The three allowlists may never grow past their recorded ceilings.

    Lower a ceiling when drift is fixed — that locks the gain in. Raising one
    means knowingly registering NEW drift, which must be a visible, reviewed
    edit rather than something that slides in behind a green check.

    Pure static check: no DB needed, so it guards the allowlists even in runs
    where the integration DSN is absent.
    """
    n_cols = sum(len(cols) for cols in _ALLOWED_MISSING_COLUMNS.values())

    assert len(_ALLOWED_MISSING_TABLES) <= _MAX_ALLOWED_MISSING_TABLES, (
        f"_ALLOWED_MISSING_TABLES grew to {len(_ALLOWED_MISSING_TABLES)} "
        f"(ceiling {_MAX_ALLOWED_MISSING_TABLES}). Delete the dead model instead "
        f"of exempting a new one."
    )
    assert n_cols <= _MAX_ALLOWED_MISSING_COLUMNS, (
        f"_ALLOWED_MISSING_COLUMNS grew to {n_cols} columns (ceiling "
        f"{_MAX_ALLOWED_MISSING_COLUMNS}). Add the column to the model instead."
    )
    assert len(_ALLOWED_UNMAPPED_TABLES) <= _MAX_ALLOWED_UNMAPPED_TABLES, (
        f"_ALLOWED_UNMAPPED_TABLES grew to {len(_ALLOWED_UNMAPPED_TABLES)} "
        f"(ceiling {_MAX_ALLOWED_UNMAPPED_TABLES}). Add the model instead."
    )
    assert len(_ALLOWED_FK_DRIFT) <= _MAX_ALLOWED_FK_DRIFT, (
        f"_ALLOWED_FK_DRIFT grew to {len(_ALLOWED_FK_DRIFT)} (ceiling "
        f"{_MAX_ALLOWED_FK_DRIFT}). Fix the model FK target (or declare the "
        f"missing FK) instead of exempting a new mismatch."
    )


async def test_allowlists_are_still_accurate(
    live_schema: dict[str, dict[str, dict[str, str]]],
    live_fks: list[dict[str, str]],
) -> None:
    """Every allowlist entry must still describe REAL drift.

    An entry whose drift has been fixed is a stale exemption: it silently
    re-permits the same drift if it ever comes back. Failing here forces the
    entry to be removed, which is what makes the ratchet tighten.
    """
    orm = _orm_tables()
    mapped = frozenset(orm.keys())
    live_base_tables = frozenset(live_schema.keys())
    stale: list[str] = []

    for tname in sorted(_ALLOWED_MISSING_TABLES):
        if tname not in orm:
            stale.append(
                f"_ALLOWED_MISSING_TABLES: {tname} — model is gone; remove this entry"
            )
        elif tname in live_schema:
            stale.append(
                f"_ALLOWED_MISSING_TABLES: {tname} — table exists live again; "
                f"remove this entry"
            )

    for tname, cols in sorted(_ALLOWED_MISSING_COLUMNS.items()):
        if tname not in live_schema:
            stale.append(
                f"_ALLOWED_MISSING_COLUMNS: {tname} — table no longer live; "
                f"remove this entry"
            )
            continue
        if tname not in orm:
            stale.append(
                f"_ALLOWED_MISSING_COLUMNS: {tname} — no model; remove this entry"
            )
            continue
        model_cols = {col.name for col in orm[tname].columns}
        live_cols = set(live_schema[tname].keys())
        for col in sorted(cols):
            if col not in live_cols:
                stale.append(
                    f"_ALLOWED_MISSING_COLUMNS: {tname}.{col} — not live anymore; "
                    f"remove this entry"
                )
            elif col in model_cols:
                stale.append(
                    f"_ALLOWED_MISSING_COLUMNS: {tname}.{col} — model has it now; "
                    f"remove this entry"
                )

    for tname in sorted(_ALLOWED_UNMAPPED_TABLES):
        if tname not in live_base_tables:
            stale.append(
                f"_ALLOWED_UNMAPPED_TABLES: {tname} — table no longer live; "
                f"remove this entry"
            )
        elif tname in mapped:
            stale.append(
                f"_ALLOWED_UNMAPPED_TABLES: {tname} — model exists now; "
                f"remove this entry"
            )

    # gate 6: an FK-drift exemption is stale unless the pair is STILL drifting in
    # one of the two directions (model-only or live-only, pre-exemption).
    orm_keys = _orm_fks()
    live_keys = _live_public_fk_keys(live_fks)
    still_drifting = (orm_keys - live_keys) | (live_keys - orm_keys)
    for key in sorted(_ALLOWED_FK_DRIFT):
        if key not in still_drifting:
            stale.append(
                f"_ALLOWED_FK_DRIFT: {key} — no longer drifting (model and live "
                f"agree now); remove this entry"
            )

    assert not stale, (
        f"{len(stale)} stale schema-drift allowlist entr(ies) — the drift they "
        f"exempt is gone, so the exemption must go too (that is how the ratchet "
        f"tightens):\n  " + "\n  ".join(stale)
    )
