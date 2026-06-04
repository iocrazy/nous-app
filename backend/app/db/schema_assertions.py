"""Startup fail-fast schema-assertion gate for the ORM-active tables.

ORM 2.0 migration plan §3.3 / architecture-decisions §3.

At app boot, verify the live Postgres schema contains the tables + columns
the ORM-active repositories map. If a mapped column is missing (a migration
was not applied but the code was deployed ahead of it), every query that
SELECTs that table 500s at runtime. This gate catches that BEFORE the app
serves a single request — by CRASHING loudly at startup.

Design (deliberate, narrow):
  * **Curated, not exhaustive.** Only the tables behind a ``USE_ORM_*`` flag
    are checked — those are the ones whose absence/drift breaks live ORM
    queries. The offline ``tests/db/test_schema_drift.py`` already diffs all
    107 models (names + nullability + types); that is too brittle for a boot
    gate, so this checks column NAMES only on the critical subset.
  * **Single source of truth.** Expected column names are derived from the
    MODELS via mapper inspection (``_name_to_attr`` from ``_orm_helpers``),
    so no hand-maintained list can drift. The ``metadata_`` → DB ``metadata``
    rename is handled because ``_name_to_attr`` keys on the DB column name.
  * **ONE batched query.** A single ``information_schema.columns`` SELECT with
    ``table_name IN (...)`` covers every critical table — startup stays cheap.

Fatal vs transient (the crux):
  * **Fatal** — connected, but a critical table/column is missing →
    ``SchemaDriftError`` propagates and crashes boot.
  * **Transient** — engine not configured, or the query raises a connection
    error → log a WARNING and RETURN. We must NOT hard-fail every boot on a
    DB hiccup, nor crash a DB-less local/CI boot (the app would fail on the
    first real query anyway; a boot gate is not the place to be flaky).

Emergency escape hatch: ``SCHEMA_ASSERT_ON_BOOT=false`` (default true) skips
the boot wiring without a redeploy (mirror the ``/app/.env`` ops pattern —
set it on the host bind-mount + restart).
"""

from __future__ import annotations

import os

from loguru import logger

from app.db import engine as db_engine
from app.models.agents import AgentRuns
from app.models.media import (
    Folders,
    ParsedMedia,
    ResourceItems,
    ResourceVersions,
    Resources,
)
from app.models.users import UserSettings
from app.repositories._orm_helpers import _name_to_attr

__all__ = [
    "CRITICAL_MODELS",
    "SchemaDriftError",
    "assert_critical_schema",
    "assert_critical_schema_on_boot",
]


class SchemaDriftError(RuntimeError):
    """A critical ORM-active table or column is missing from the live DB.

    Raised at boot when a confirmed mismatch is detected. Crashing here is
    intentional: it means the code was deployed ahead of its migration, and
    serving traffic would 500 the moment a query touches the table.
    """


# ── Curated ORM-active model set ─────────────────────────────────────────
# These are the tables behind the USE_ORM_* flags — the only tables whose
# absence/drift would break a live ORM query. Add a model here AS its repo
# migrates onto the ORM (i.e. when a new USE_ORM_<X> flag ships).
CRITICAL_MODELS: tuple[type, ...] = (
    ParsedMedia,  # USE_ORM_MEDIA
    Resources,  # USE_ORM_RESOURCES
    ResourceItems,  # USE_ORM_RESOURCES (folder cascade)
    ResourceVersions,  # USE_ORM_RESOURCES
    Folders,  # USE_ORM_RESOURCES
    AgentRuns,  # USE_ORM_AGENT_RUNS
    UserSettings,  # USE_ORM_USER_SETTINGS
)


def _expected_columns_by_table() -> dict[str, frozenset[str]]:
    """Map each critical table → the set of DB column NAMES its model maps.

    Derived from the mapper (single source of truth) via ``_name_to_attr``,
    whose keys ARE the DB column names — so the ``metadata_`` → ``metadata``
    rename resolves to the live DB name, not the Python attribute.
    """
    return {
        model.__tablename__: frozenset(_name_to_attr(model).keys())
        for model in CRITICAL_MODELS
    }


async def assert_critical_schema() -> None:
    """Verify the live DB has every column the ORM-active models map.

    Fatal mismatch (missing table / column) → raise ``SchemaDriftError``.
    Transient (not configured / connection error) → WARN + return.
    """
    if not db_engine.is_configured():
        # DB-less local / CI boot, or the legacy supabase-py path is in use.
        # Nothing to assert; the app would fail on first real query if a flag
        # were actually on without a DSN. Never crash here.
        logger.warning(
            "[schema-assert] SUPAVISOR_DATABASE_URL not configured — skipping "
            "boot schema assertion (transient path; no crash)"
        )
        return

    expected = _expected_columns_by_table()
    table_names = sorted(expected.keys())

    # ONE batched query for ALL critical tables (not one query per table).
    # Named-list expansion: build :t0,:t1,... so each table is a bound param.
    placeholders = ", ".join(f":t{i}" for i in range(len(table_names)))
    params = {f"t{i}": name for i, name in enumerate(table_names)}
    sql = (
        "SELECT table_name, column_name "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        f"AND table_name IN ({placeholders})"
    )

    try:
        rows = await db_engine.fetch_all(sql, params)
    except SchemaDriftError:
        # Never let our own fatal signal be swallowed by the transient guard.
        raise
    except Exception as exc:  # noqa: BLE001 — connection blips are transient
        logger.warning(
            "[schema-assert] live schema query failed ({}); skipping boot "
            "assertion (transient path; no crash)",
            repr(exc),
        )
        return

    # live[table] = set of column names present in the live DB.
    live: dict[str, set[str]] = {}
    for row in rows:
        live.setdefault(row["table_name"], set()).add(row["column_name"])

    problems: list[str] = []
    for table in table_names:
        if table not in live:
            problems.append(f"  {table}: TABLE MISSING (no columns in public schema)")
            continue
        missing = sorted(expected[table] - live[table])
        if missing:
            problems.append(f"  {table}: missing column(s) {missing}")

    if problems:
        raise SchemaDriftError(
            "Live DB schema is missing ORM-active tables/columns — the code is "
            "deployed AHEAD of its migration. Apply the pending migration "
            "before serving traffic. Confirmed mismatches:\n" + "\n".join(problems)
        )

    logger.info(
        "[schema-assert] OK — all {} ORM-active tables present with mapped columns",
        len(table_names),
    )


def _boot_enabled() -> bool:
    """Emergency escape hatch: SCHEMA_ASSERT_ON_BOOT=false disables the gate
    (default ON). Mirror the /app/.env ops pattern so it can be flipped
    without a redeploy."""
    return os.environ.get("SCHEMA_ASSERT_ON_BOOT", "true").strip().lower() not in (
        "false",
        "0",
        "no",
        "off",
    )


async def assert_critical_schema_on_boot() -> None:
    """Startup wrapper: gate the assertion behind ``SCHEMA_ASSERT_ON_BOOT``.

    The lifespan hook calls this (not ``assert_critical_schema`` directly) so
    the env flag is honoured. A fatal ``SchemaDriftError`` is left to
    propagate and crash startup; transient cases were already swallowed
    inside ``assert_critical_schema``.
    """
    if not _boot_enabled():
        logger.warning(
            "[schema-assert] SCHEMA_ASSERT_ON_BOOT disabled — skipping boot "
            "schema assertion (emergency escape hatch)"
        )
        return
    await assert_critical_schema()
