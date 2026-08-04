"""Migration-period guardrail for raw ``text()`` SQL against ``db_engine``.

See docs/decisions/2026-08-04-raw-sql-to-orm-full-migration.md — Phase A of
that plan ports the ~7 user-request-path files off ``db_engine.fetch_*`` /
``execute`` onto the ORM session layer (``app/db/session.py`` +
``app/db/scope.py``). Phase B/C (46 system-table files + 13 workflow files)
are NOT ported in this batch. ``scoped_sql`` is the guardrail those still-raw
call sites move onto in the meantime: it is the SOLE new entry point for
``db_engine`` raw SQL from here on — new call sites should not import
``app.db.engine`` directly, and existing ones migrate here incrementally.

Design goal: every raw-SQL call site declares WHO it is acting as before
touching the database, instead of silently defaulting to "whoever is
running" the way a bare ``db_engine.fetch_all(sql, params)`` call does today.
Callers must pick exactly one:

  * ``scope=Scope(user_id=...)`` — a tenant-bound read/write. The helper does
    NOT build the tenant predicate for you (that is
    ``app.db.scope.scoped_sql`` — the ``(predicate_sql, params)`` builder).
    It checks two NECESSARY-BUT-NOT-SUFFICIENT signals: (a) ``params``
    carries the bind (``params[SCOPE_USER_PARAM] == scope.user_id``) and
    (b) the SQL text mentions the bind token at all. Neither proves the
    token sits in a filtering WHERE position — a caller can still write
    ``SELECT :scope_user_id, *`` (references the token, filters nothing) or
    put it in an ``OR 1=1``-shaped tautology; this guardrail catches the
    "declared a scope, never touched it" class of mistake (unbound/unused
    param), NOT "wrote a scope-shaped predicate that doesn't actually
    filter" (that positive guarantee is what the ORM choke point's
    compile-verified ``with_loader_criteria`` gives — see app/db/scope.py —
    and is out of reach for opaque ``text()`` SQL by construction).
  * ``system=True, reason="..."`` — a deliberate cross-tenant / system
    operation (sweepers, admin analytics, migrations, dead-table probes).
    ``reason`` is mandatory and logged at INFO for the same audit trail
    ``app.db.scope.system_session`` keeps.

Declaring neither (or both) raises ``UnscopedRawSQLError`` — fail-closed, the
raw-SQL analogue of the ORM choke point's "no scope set" raise.

This module does NOT replace ``app.db.scope.scoped_sql`` (the tenant
predicate builder used by the already-reviewed A3 reads in
``resources_repository.py``) — it wraps the EXECUTION step with the same
declare-before-you-query discipline, for callers that haven't been ported to
that helper (or to the ORM outright) yet.
"""

from __future__ import annotations

from typing import Any, Literal

from loguru import logger

from app.db import engine as db_engine
from app.db.scope import SCOPE_USER_PARAM, Scope

__all__ = [
    "UnscopedRawSQLError",
    "scoped_sql",
    "scoped_fetch_all",
    "scoped_fetch_one",
    "scoped_execute",
]

_Mode = Literal["fetch_all", "fetch_one", "execute"]


class UnscopedRawSQLError(Exception):
    """Raised when ``scoped_sql`` (or a mode-specific wrapper) is called
    without declaring exactly one of ``scope=`` / ``system=True``, when a
    ``system=True`` call has no ``reason``, or when a ``scope=`` call's
    ``params`` do not carry the bound tenant value the declared scope
    promises. Fail-closed by construction — mirrors
    ``app.db.scope.UnscopedQueryError`` for the ORM choke point."""


def _validate_declaration(scope: Scope | None, system: bool, reason: str) -> None:
    if scope is not None and system:
        raise UnscopedRawSQLError(
            "scoped_sql(): pass exactly ONE of scope=Scope(...) or "
            "system=True — not both. A call site is either acting as a "
            "specific tenant or deliberately cross-tenant, never both."
        )
    if scope is None and not system:
        raise UnscopedRawSQLError(
            "scoped_sql(): raw text() SQL requires declaring who this query "
            "acts as — pass scope=Scope(user_id=...) for a tenant-bound "
            'read/write, or system=True, reason="..." for a deliberate '
            "cross-tenant/system operation. See docs/decisions/"
            "2026-08-04-raw-sql-to-orm-full-migration.md."
        )
    if system and not (reason and reason.strip()):
        raise UnscopedRawSQLError(
            "scoped_sql(system=True) requires a non-empty reason= for the "
            "audit trail (mirrors app.db.scope.system_session's mandatory "
            "reason)."
        )


def _assert_scope_bound(scope: Scope, params: dict[str, Any], sql: str) -> None:
    """Fail closed on two NECESSARY-BUT-NOT-SUFFICIENT signals that a
    ``scope=`` declaration is actually wired up — see the module docstring
    for exactly what this does and does not prove:

      1. ``params`` carries the bind the declared ``scope`` promises — a
         ``scope=`` declaration with no matching bound value would silently
         run unfiltered while LOOKING governed.
      2. the SQL text mentions the bind token at all (``:scope_user_id`` or
         the bare param name) — catches the "declared a scope, built the
         bind, forgot to reference it in the query string" slip. A plain
         substring check, NOT a parse: it does not (cannot, for opaque
         text()) verify the token sits in a filtering WHERE position rather
         than a SELECT list or a tautology.
    """
    if SCOPE_USER_PARAM not in params:
        raise UnscopedRawSQLError(
            f"scoped_sql(scope=...) requires params['{SCOPE_USER_PARAM}'] to "
            "carry the tenant bind — build the predicate + params with "
            "app.db.scope.scoped_sql(creator_col, params) (or bind "
            f"'{SCOPE_USER_PARAM}' yourself) and pass the returned params "
            f"through. Got params keys: {sorted(params.keys())}."
        )
    bound = params[SCOPE_USER_PARAM]
    if bound != scope.user_id:
        raise UnscopedRawSQLError(
            f"scoped_sql(scope=...): params['{SCOPE_USER_PARAM}']={bound!r} "
            f"does not match the declared scope.user_id={scope.user_id!r} — "
            "the bound tenant value must match the declared identity."
        )
    if SCOPE_USER_PARAM not in sql:
        raise UnscopedRawSQLError(
            f"scoped_sql(scope=...): params carry '{SCOPE_USER_PARAM}' but "
            "the SQL text never references it (no ':scope_user_id' / "
            f"'{SCOPE_USER_PARAM}' substring found) — an unused bind means "
            "the query is NOT actually filtered by the declared scope. "
            "This is a weak, substring-only check (see docstring) — it "
            "cannot verify the token is in a filtering position, only that "
            "it is referenced at all."
        )


async def scoped_sql(
    sql: str,
    params: dict[str, Any] | None = None,
    *,
    scope: Scope | None = None,
    system: bool = False,
    reason: str = "",
    mode: _Mode = "fetch_all",
) -> Any:
    """The sole new entry point for raw ``text()`` SQL during the migration
    (for Phase B/C files not yet ported to the ORM). Forwards to the
    matching ``app.db.engine`` helper after asserting a scope/system
    declaration. See module docstring for the declare-before-you-query
    contract.

    ``mode`` selects the ``db_engine`` helper: ``"fetch_all"`` → list of
    dicts, ``"fetch_one"`` → single dict or ``None``, ``"execute"`` →
    affected row count (INSERT/UPDATE/DELETE). Prefer the mode-specific
    wrappers below (``scoped_fetch_all`` / ``scoped_fetch_one`` /
    ``scoped_execute``) at call sites — same behaviour, a clearer call shape.
    """
    _validate_declaration(scope, system, reason)
    call_params = dict(params or {})
    if scope is not None:
        _assert_scope_bound(scope, call_params, sql)
    else:
        logger.info(
            "[scoped_sql] system access: {} (mode={}, sql={!r})",
            reason,
            mode,
            sql[:160],
        )

    if mode == "fetch_all":
        return await db_engine.fetch_all(sql, call_params)
    if mode == "fetch_one":
        return await db_engine.fetch_one(sql, call_params)
    if mode == "execute":
        return await db_engine.execute(sql, call_params)
    raise ValueError(f"scoped_sql(): unknown mode {mode!r}")  # pragma: no cover


async def scoped_fetch_all(
    sql: str,
    params: dict[str, Any] | None = None,
    *,
    scope: Scope | None = None,
    system: bool = False,
    reason: str = "",
) -> list[dict]:
    """``scoped_sql(..., mode="fetch_all")`` — SELECT → list of dicts."""
    return await scoped_sql(
        sql, params, scope=scope, system=system, reason=reason, mode="fetch_all"
    )


async def scoped_fetch_one(
    sql: str,
    params: dict[str, Any] | None = None,
    *,
    scope: Scope | None = None,
    system: bool = False,
    reason: str = "",
) -> dict | None:
    """``scoped_sql(..., mode="fetch_one")`` — SELECT → first row dict or None."""
    return await scoped_sql(
        sql, params, scope=scope, system=system, reason=reason, mode="fetch_one"
    )


async def scoped_execute(
    sql: str,
    params: dict[str, Any] | None = None,
    *,
    scope: Scope | None = None,
    system: bool = False,
    reason: str = "",
) -> int:
    """``scoped_sql(..., mode="execute")`` — INSERT/UPDATE/DELETE → row count."""
    return await scoped_sql(
        sql, params, scope=scope, system=system, reason=reason, mode="execute"
    )
