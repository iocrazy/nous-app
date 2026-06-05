"""Application-layer tenant-isolation choke point for the ORM 2.0 layer.

This is the *fail-closed* authorization mechanism described in
docs/decisions/2026-06-04-orm-2-architecture-decisions.md §4 / §7.1.

Going direct to Postgres means queries run as ``service_role``
(``BYPASSRLS``); per-user authz therefore lives in app code. A query that
forgets to scope = data leak. This module makes "no-scope user-facing query"
*impossible to issue* rather than relying on per-query vigilance:

  Layer 1 — two typed entry points (``user_session`` / ``system_session``).
            ``user_session`` REQUIRES a ``Scope`` arg, so you cannot open a
            user session without saying who you are.
  Layer 2 — a ``do_orm_execute`` event injects the tenant filter on every
            SELECT that touches a scoped model. Unset scope + scoped model =
            ``UnscopedQueryError`` (fail-closed; blows up in dev/test, never
            silently full-scans). The SAME event FORBIDS bulk/Core DML
            (UPDATE / DELETE / INSERT) on a scoped model under a real ``Scope``:
            these statements cannot be safely WHERE-injected / owner-stamped, so
            we fail-closed and force the sanctioned load-then-modify path (or an
            explicit ``system_session``). See ``_enforce_scope``.
  Layer 3 — a ``before_insert`` mapper event stamps the owner column from the
            active scope on the ORM unit-of-work flush of a mapped *instance*
            (``session.add(obj)``) — asserts equality if already set (can't
            insert for another user). Core ``insert()`` does NOT flow through
            this event; it is caught by the Layer-2 write-path forbid instead.

SELECT full-statement DENY-BY-DEFAULT (closes the C2 / join-shaped leaks): the
columns-clause injection (``all_mappers``) misses scoped tables reached via a
JOIN, a WHERE/IN/scalar subquery, a writable CTE, a raw ``from_statement``, or an
aggregate like ``count(*)`` — all of which used to slip through with NO tenant
predicate. Rather than enumerate the known-leaky shapes (inherently
false-negative-prone — we were bitten repeatedly by shapes not on the list),
``_enforce_scope`` is DENY-BY-DEFAULT: a scoped table referenced ANYWHERE must be
PROVABLY filtered, else it fails closed.

  1. Walk the whole statement tree (``visitors.iterate``) and collect every
     referenced scoped table (FROM list, JOINs, scalar/IN/EXISTS subqueries, CTE
     elements incl. writable-CTE-nested DML).
  2. Build ``with_loader_criteria(cls, criteria, include_aliases=True)`` for every
     referenced scoped class and ask SQLAlchemy itself whether the filter
     attaches: compile the option-bearing statement (with a unique sentinel
     tenant value per table) and check whether the sentinel survived into the
     compiled BOUND-PARAMETER values (NOT a substring match of the SQL text —
     that can be defeated by a user literal containing the sentinel digit-run, a
     cross-tenant leak; and ``literal_binds`` would CompileError on JSONB/ARRAY
     filters, a false fail-closed). This is the POSITIVE, authoritative signal —
     no re-derivation of the loader-criteria attachment rules. A scoped table
     whose sentinel is absent from the params (``count(*)`` subquery, ``count(*)``
     over a Core join, a correlated ``exists().where(...)``, a raw
     ``from_statement``) is NOT injectable.
  3. A scoped table is POSITIVELY-INJECTABLE iff its sentinel survived into the
     params AND it is not in a position that binds-but-does-not-filter — i.e. an
     OUTER-JOIN target (predicate lands in the row-preserving ON clause →
     unmatched foreign row survives) or a writable-CTE-nested DML (binds, but is a
     write).
  4. INJECT (real criteria) for the positively-injectable set; RAISE
     ``UnscopedQueryError`` for EVERY other referenced scoped table. When in
     doubt → RAISE. Restructure to an injectable shape (INNER JOIN / IN-subquery,
     project a scoped column) or use ``system_session(reason=...)``.

  ``None`` scope + any referenced scoped table → raise (unchanged). ``SYSTEM`` →
  never inject / never raise (unchanged). Cost: one extra ORM compile per scoped
  SELECT (deny-by-default's price for being shape-agnostic), plus a single
  ``visitors.iterate`` walk that scales with statement node count. It is a no-op
  when no model is scoped (the scoped-table-name set is empty — the prod state
  today), so prod pays nothing.

OUT OF REACH (known gap): raw ``text()`` SQL that does not flow through a mapped
entity bypasses the ORM events entirely (escape hatch — see decisions doc §4
"raw SQL is a reviewed exception"; governed by the forthcoming ``scoped_sql``
helper). Everything that touches a *mapped* scoped table — via JOIN / subquery /
aggregate / writable CTE — is now either injected or fail-closed.

Scope binding is per-asyncio-task: ``ContextVar`` copies on task creation and
propagates across ``await``, so the scope binds to the *task*, not the pooled
connection. That makes it Supavisor-pooling-safe — a checked-out connection
carries no scope state; the task does.

INERTNESS: the events are registered against the ``Session`` class on import,
but they are no-ops until a *production* model inherits one of the marker
mixins in app/db/orm_base.py (``UserScoped`` / ``TeamScoped`` /
``ProjectScoped``). No prod model is mixed in yet, so registering the events
changes no existing behaviour (the existing unscoped repos keep working).
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Union

from loguru import logger
from sqlalchemy import event, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapper, Session, mapperlib, with_loader_criteria
from sqlalchemy.sql import visitors
from sqlalchemy.sql.dml import UpdateBase
from sqlalchemy.sql.schema import Table

from app.db.orm_base import ProjectScoped, TeamScoped, UserScoped
from app.db.session import get_sessionmaker

# Marker mixins whose presence makes a mapped class "scoped" (one per axis).
_SCOPE_MIXINS: tuple[type, ...] = (UserScoped, TeamScoped, ProjectScoped)

# ── Scope value object + sentinels ──────────────────────────────────────


@dataclass(frozen=True)
class Scope:
    """Who the current task acts as, across the three tenancy axes.

    Populated at an entry boundary — HTTP middleware (from the JWT) or a DBOS
    workflow/task entry (from the task payload). Frozen so it can't be mutated
    mid-request; build a new one to change identity.
    """

    user_id: int
    team_ids: frozenset[int] = field(default_factory=frozenset)
    project_ids: frozenset[int] = field(default_factory=frozenset)


class _SystemSentinel:
    """Singleton type for the SYSTEM scope marker. A distinct type (not a bare
    ``object()``) so it's greppable and reprs cleanly in logs/telemetry."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "SYSTEM"


# Module-level sentinel meaning "explicit cross-user / system access — do NOT
# inject any tenant filter". Set only via system_session().
SYSTEM: _SystemSentinel = _SystemSentinel()

# What _scope may hold.
ScopeValue = Union[Scope, _SystemSentinel]


class UnscopedQueryError(Exception):
    """Raised when a scoped model is touched without a valid scope, or when a
    bulk/Core write that cannot be safely governed is attempted under a user
    scope.

    Covers three fail-closed cases: a SELECT or write on a scoped model with no
    scope set; an instance INSERT for a user other than the active scope; and a
    bulk/Core UPDATE/DELETE/INSERT on a scoped model under a real ``Scope``
    (forbidden because it can't be tenant-filtered / owner-stamped).

    Fail-closed by construction: forgetting to open a user_session/
    system_session around tenant access surfaces immediately instead of
    silently returning every user's rows.
    """


# ── Per-task scope context variable ─────────────────────────────────────
#
# Holds Scope | SYSTEM | None (default None). ContextVar copies per asyncio
# task and propagates across await — scope binds to the task, not the pooled
# connection (Supavisor transaction-pooling-safe).
_scope: ContextVar[ScopeValue | None] = ContextVar("db_scope", default=None)


def current_scope() -> ScopeValue | None:
    """Return the scope bound to the current task (Scope, SYSTEM, or None).

    Mostly for the events below and for tests; business code should rely on
    the entry context managers rather than reading this directly.
    """
    return _scope.get()


# ── Entry context managers (Layer 1) ────────────────────────────────────


@asynccontextmanager
async def user_session(scope: Scope) -> AsyncIterator[AsyncSession]:
    """Open a committing write session bound to ``scope``.

    ``scope`` is REQUIRED — you cannot open a user session without declaring
    identity. Inside the block, SELECTs on scoped models get the tenant filter
    injected and inserts on ``UserScoped`` models get their owner column
    stamped.

    Mirrors write_scope()'s ambient-unit_of_work join: if a unit_of_work() is
    already open on this task, this reuses that session/transaction (the UoW
    owns the single commit); otherwise it opens + commits its own transaction.
    The scope contextvar is set for the block and reset in ``finally`` so it
    never leaks to sibling tasks.
    """
    from app.db.session import _request_session  # local: avoid import cycle

    token = _scope.set(scope)
    try:
        existing = _request_session.get()
        if existing is not None:
            # Join the ambient unit_of_work — do not begin()/commit() here.
            yield existing
            return
        async with get_sessionmaker()() as session:
            async with session.begin():
                yield session
    finally:
        _scope.reset(token)


def _audit_caller() -> str:
    """``module:function:line`` of the code that opened a ``system_session``.

    Walks up from this frame past the contextmanager machinery (this helper, the
    ``system_session`` generator body, and contextlib's ``__aenter__`` wrapper) to
    the first frame OUTSIDE both this module and ``contextlib`` — the real call
    site of the cross-user access. Falls back to ``"<unknown>"`` if the stack is
    shorter than expected (never raises; the audit line must always render).
    """
    _skip = (__name__, "contextlib")
    frame = sys._getframe(1) if hasattr(sys, "_getframe") else None
    while frame is not None:
        if frame.f_globals.get("__name__") not in _skip:
            code = frame.f_code
            module = frame.f_globals.get("__name__", code.co_filename)
            return f"{module}:{code.co_name}:{frame.f_lineno}"
        frame = frame.f_back
    return "<unknown>"


@asynccontextmanager
async def system_session(reason: str) -> AsyncIterator[AsyncSession]:
    """Open a committing write session with NO tenant injection (cross-user /
    system access).

    ``reason`` is mandatory for audit + greppability ("why is this query
    allowed to see every user's rows?"). It is logged at INFO — every loguru
    sink in this project (stderr, file, the ``application_logs`` DB sink) is
    registered at ``level="INFO"``, so a DEBUG line would be silently dropped and
    leave cross-user access with no runtime trail. The line carries the immediate
    caller (``module:function:line``) so the audit trail identifies WHERE the
    access originated. Use for sweepers, admin analytics, migrations, and other
    deliberately cross-user work.
    """
    from app.db.session import _request_session  # local: avoid import cycle

    logger.info(
        "[scope] system_session opened: {} (caller={})", reason, _audit_caller()
    )
    token = _scope.set(SYSTEM)
    try:
        existing = _request_session.get()
        if existing is not None:
            yield existing
            return
        async with get_sessionmaker()() as session:
            async with session.begin():
                yield session
    finally:
        _scope.reset(token)


@asynccontextmanager
async def user_read_session(scope: Scope) -> AsyncIterator[AsyncSession]:
    """Read-only variant of ``user_session`` — no own commit.

    Same scope/injection semantics as ``user_session`` but does not begin a
    write transaction of its own (joins an ambient unit_of_work if present, as
    read_scope() does). Use for pure reads where opening a write transaction is
    needless overhead.
    """
    from app.db.session import _request_session  # local: avoid import cycle

    token = _scope.set(scope)
    try:
        existing = _request_session.get()
        if existing is not None:
            yield existing
            return
        async with get_sessionmaker()() as session:
            yield session
    finally:
        _scope.reset(token)


# ── Scoped-model introspection helpers ──────────────────────────────────


def _scoped_mappers(state: Any) -> list[Mapper]:
    """Mappers in the statement whose class inherits a scope marker mixin.

    ``state.all_mappers`` only contains entities in the *columns clause* — these
    are the mappers ``with_loader_criteria`` can actually inject into (the
    "injectable set"). Scoped tables reached only via a JOIN / subquery / CTE are
    NOT here; those are caught by the full-statement traversal instead.
    """
    return [m for m in state.all_mappers if issubclass(m.class_, _SCOPE_MIXINS)]


# ── Scoped-table registry (table name → mapped class, across ALL registries) ──
#
# The traversal intersects the tables a statement references against the set of
# *scoped* table names; injection then needs the mapped CLASS for a scoped table
# found only in a JOIN/subquery (to build its per-axis criteria). A scoped table
# = the mapped table of any class inheriting a scope mixin, in ANY registry —
# prod ``Base`` AND the test ``_TestBase`` the choke-point tests map onto. We
# enumerate the process-wide mapper registries (mapperlib._mapper_registries)
# rather than a single Base so newly-declared test models are seen. The map is
# cached and invalidated whenever a new mapper is configured (a test model maps
# in), so we pay the scan once per model-set, not per query.

_scoped_table_map_cache: dict[str, type] | None = None


def _invalidate_scoped_table_cache(*_args: Any, **_kwargs: Any) -> None:
    """``mapper_configured`` listener: drop the cache when a new model maps in
    (e.g. a test model on ``_TestBase``), so the next query recomputes it."""
    global _scoped_table_map_cache
    _scoped_table_map_cache = None


def _scoped_table_map() -> dict[str, type]:
    """Map of ``table name → mapped class`` for every scope-mixin class, across
    all registries.

    Generic by construction: returns ``{}`` when no model is scoped (the prod
    state today → the traversal becomes a no-op and the choke point stays inert).
    Cached; invalidated on ``mapper_configured``.

    Collision note: a table name maps to exactly one scoped class. If two scoped
    classes shared a ``local_table`` name (e.g. polymorphic/single-table mappings,
    or a test reusing a name across registries) this is last-write-wins — the
    classes would carry the SAME tenant columns by construction (same table), so
    the per-axis criteria is identical and the choice is immaterial for the
    filter. Cross-registry name reuse is otherwise avoided (tests suffix the PID).
    """
    global _scoped_table_map_cache
    if _scoped_table_map_cache is not None:
        return _scoped_table_map_cache
    mapping: dict[str, type] = {}
    for registry in list(mapperlib._mapper_registries):
        for mapper in registry.mappers:
            if issubclass(mapper.class_, _SCOPE_MIXINS):
                mapping[mapper.local_table.name] = mapper.class_
    _scoped_table_map_cache = mapping
    return _scoped_table_map_cache


def _scoped_table_names() -> frozenset[str]:
    """Names of every table mapped by a scope-mixin class (keys of the map)."""
    return frozenset(_scoped_table_map().keys())


def _axis_criteria(cls: type, scope: Scope):
    """Build the OR-combined per-axis filter for one scoped model class.

    A row is visible if the user owns it (UserScoped) OR it is shared to a team
    they belong to (TeamScoped) OR to a project they belong to (ProjectScoped).
    Empty team_ids/project_ids → that axis matches nothing (a no-op clause), as
    intended: a user in no teams sees no team-shared-only rows.
    """
    clauses = []
    if issubclass(cls, UserScoped):
        col = getattr(cls, cls.__tenant_user_col__)
        clauses.append(col == scope.user_id)
    if issubclass(cls, TeamScoped):
        col = getattr(cls, cls.__tenant_team_col__)
        # in_() of an empty set renders to a guaranteed-false predicate.
        clauses.append(col.in_(scope.team_ids))
    if issubclass(cls, ProjectScoped):
        col = getattr(cls, cls.__tenant_project_col__)
        clauses.append(col.in_(scope.project_ids))
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return or_(*clauses)


# ── Layer 2 — SELECT-time tenant injection + write-path forbid ───────────


def _forbid_scoped_bulk_dml(orm_execute_state: Any) -> bool:
    """Fail-closed guard for bulk/Core DML on scoped models.

    Statement-level UPDATE / DELETE / INSERT (``session.execute(update(...))``,
    ``delete(...)``, ``insert(...)``, including the bulk and
    ``synchronize_session='fetch'`` forms) flow through ``do_orm_execute`` but
    cannot be safely governed the way SELECTs are: rewriting an arbitrary
    WHERE-tree to inject a tenant predicate is fragile, and a Core ``insert()``
    never reaches the ``before_insert`` owner-stamping event. So under a real
    ``Scope`` we FORBID them outright and force the sanctioned safe path
    (load-then-modify via ``session.add()`` / dirty-instance update /
    ``session.delete(instance)``, whose flush IS governed) or a deliberate
    ``system_session(reason=...)``.

    NB: the instance-flush write path does NOT come through ``do_orm_execute``
    (it uses the persistence API directly), so this never fires on it.

    Returns ``True`` if the statement was a write that this guard handled
    (so the caller can stop), ``False`` if it was not a write (fall through to
    the SELECT logic). Raises ``UnscopedQueryError`` on a forbidden write.
    """
    if not (
        orm_execute_state.is_update
        or orm_execute_state.is_delete
        or orm_execute_state.is_insert
    ):
        return False  # not a write statement — let SELECT handling decide

    scoped = _scoped_mappers(orm_execute_state)
    if not scoped:
        return True  # write, but no scoped model touched — allow, nothing to do

    scope = _scope.get()
    if scope is SYSTEM:
        return True  # system code is trusted to write its own WHERE / owner

    names = [m.class_.__name__ for m in scoped]
    if scope is None:
        raise UnscopedQueryError(
            f"Bulk/Core DML touches scoped model(s) {names} but no scope is "
            "set. Open a user_session(scope) or system_session(reason) first."
        )

    # A real Scope: forbid-by-default. Do not try to inject a WHERE into an
    # arbitrary UPDATE/DELETE tree or owner-stamp a Core INSERT — point the
    # caller at the governed safe path instead.
    raise UnscopedQueryError(
        f"Bulk/Core DML on scoped model(s) {names} is forbidden under a user "
        "scope: it cannot be safely tenant-filtered / owner-stamped. Load the "
        "instance(s) then mutate via session.add() / attribute update / "
        "session.delete(instance) (the unit-of-work flush is governed), or use "
        "system_session(reason=...) for a deliberate cross-user write."
    )


def _label(table_name: str) -> str:
    """``ClassName(table)`` for a scoped table, falling back to the bare name.
    Keeps error messages greppable by both the developer-facing class name and
    the physical table the traversal flagged."""
    cls = _scoped_table_map().get(table_name)
    return f"{cls.__name__}({table_name})" if cls is not None else table_name


def _walk_scoped_refs(
    statement: Any, scoped_names: frozenset[str]
) -> tuple[set[str], set[str]]:
    """Single ``visitors.iterate`` walk over ``statement`` returning
    ``(referenced, non_filtering)``:

      * ``referenced`` — every scoped table NAME reachable anywhere (FROM list,
        JOIN targets, scalar/IN/EXISTS subqueries, CTE elements incl. a writable
        CTE's nested ``UPDATE``/``DELETE``/``INSERT`` target).
      * ``non_filtering`` — scoped tables in a position that RENDERS the tenant
        predicate but does NOT filter the foreign row, so the positive
        compile-check would be fooled: an OUTER-JOIN target (predicate in the
        row-preserving ON clause) or a writable-CTE-nested DML (renders, but is a
        write). These are forced to residue regardless of the compile-check.
    """
    referenced: set[str] = set()
    non_filtering: set[str] = set()
    for el in visitors.iterate(statement):
        if isinstance(el, Table) and el.name in scoped_names:
            referenced.add(el.name)
            continue
        setup_joins = getattr(el, "_setup_joins", None)
        if setup_joins:
            for join in setup_joins:
                flags = join[-1] if isinstance(join[-1], dict) else {}
                if flags.get("isouter"):
                    non_filtering |= {
                        t.name
                        for t in visitors.iterate(join[0])
                        if isinstance(t, Table) and t.name in scoped_names
                    }
        elif isinstance(el, UpdateBase):
            non_filtering |= {
                t.name
                for t in visitors.iterate(el)
                if isinstance(t, Table) and t.name in scoped_names
            }
    return referenced, non_filtering


# A sentinel tenant value, used only to probe — by compiling the option-bearing
# statement — whether ``with_loader_criteria`` actually bound a predicate value for
# a given scoped table. Distinct per table via an index suffix, and the SAME value
# is used across every axis (user/team/project) of that table so the probe is
# axis-agnostic (a team-only or project-only scoped model has no user column).
#
# NEGATIVE on purpose: detection matches the sentinel against the compiled bound
# parameter VALUES, so a caller binding a value exactly equal to the sentinel would
# be misclassified injectable. Every legitimate caller-bound tenant-ish value in
# this codebase is non-negative (snowflake IDs, counts, offsets, limits, epoch
# timestamps), so a negative sentinel makes that collision domain provably empty.
_SENTINEL_BASE = -987654321_000000000


def _compile_filtered_tables(
    statement: Any, dialect: Any, table_map: dict[str, type], candidates: set[str]
) -> set[str]:
    """The subset of ``candidates`` for which ``with_loader_criteria`` PROVABLY
    rendered a tenant predicate.

    The authoritative positive signal — we ask SQLAlchemy itself instead of
    re-deriving the loader-criteria attachment rules (which keep having gaps:
    inner-join vs ``count(*)``-over-a-join differ only by whether a scoped column
    reaches a filtering position). For each candidate we attach a
    ``with_loader_criteria`` built from a per-table sentinel scope (the sentinel
    populates ALL of that table's axes, so it works for user/team/project-scoped
    models alike), compile the statement, and check whether the sentinel survived
    into the compiled BOUND-PARAMETER values. A scoped table reached only via
    ``count(*)`` / a Core ``count`` join / a correlated ``exists().where(...)`` /
    a raw ``from_statement`` yields NO loader-criteria predicate → its sentinel is
    absent from the params → it is NOT in the returned set → it fails closed
    upstream.

    Detection is on the BOUND PARAMETER VALUES, NOT the SQL text:

      * Correctness must not depend on the sentinel being collision-free. A text
        substring match against ``literal_binds`` SQL can be defeated by a
        user-influenced literal that happens to contain the sentinel digit-run
        (e.g. ``.where(payload == "...<sentinel>...")`` on a count(*) subquery),
        which would wrongly classify a no-op shape as injectable → cross-tenant
        leak. Loader-criteria bind values are ours, not the caller's.
      * Compiling WITHOUT ``literal_binds`` also lets JSONB/ARRAY filters
        (``meta == {...}`` / ``meta.contains({...})`` / array equality) compile —
        ``literal_binds`` cannot render dict/list values and would raise
        ``CompileError``, wrongly failing-closed on a legitimate injectable read.

    The sentinel lands as a scalar (user axis ``user_id == sentinel``) or inside a
    list (team/project axis ``... IN (sentinel,)``), so we check both. The
    ``except Exception → set()`` is a true last-resort (e.g. a genuinely
    un-compilable statement), NOT the path JSONB/ARRAY filters routinely hit.
    """
    options = []
    sentinels: dict[str, int] = {}
    for idx, name in enumerate(sorted(candidates)):
        cls = table_map.get(name)
        if cls is None:
            continue
        sentinel = _SENTINEL_BASE + idx
        # One sentinel scope drives whichever axes the class declares.
        probe_scope = Scope(
            user_id=sentinel,
            team_ids=frozenset({sentinel}),
            project_ids=frozenset({sentinel}),
        )
        criteria = _axis_criteria(cls, probe_scope)
        if criteria is None:
            continue  # no axis to probe with; cannot positively confirm → raise
        sentinels[name] = sentinel
        options.append(with_loader_criteria(cls, criteria, include_aliases=True))
    if not options:
        return set()
    try:
        compiled = statement.options(*options).compile(dialect=dialect)
    except Exception:  # noqa: BLE001 - true last-resort: fail closed → raise
        return set()
    param_values = list(compiled.params.values())

    def _sentinel_in_params(sentinel: int) -> bool:
        # The loader-criteria value lands as a scalar (== axis) or inside a list
        # (IN axis). Matched against OUR bind values, never the caller's literals.
        return any(
            sentinel == value
            or (isinstance(value, (list, tuple)) and sentinel in value)
            for value in param_values
        )

    return {
        name for name, sentinel in sentinels.items() if _sentinel_in_params(sentinel)
    }


def _enforce_scope(orm_execute_state: Any) -> None:
    """``do_orm_execute`` handler: forbid bulk/Core DML on scoped models under a
    user scope, then DENY-BY-DEFAULT for SELECTs — INJECT the tenant filter for
    every scoped table we can PROVE is filtered (compile-verified), and fail-closed
    RAISE for every other referenced scoped table. Inert until a model inherits a
    scope mixin."""
    if _forbid_scoped_bulk_dml(orm_execute_state):
        return  # handled (or allowed) as a write statement

    # A read flows through here as either a real SELECT or a raw ``from_statement``
    # (which reports is_select=False but is_from_statement=True and is a READ — it
    # must be governed, not skipped). Anything else (e.g. a session.get() primary
    # load, which is neither) has no scoped-table traversal concern here.
    if not (orm_execute_state.is_select or orm_execute_state.is_from_statement):
        return

    scoped_names = _scoped_table_names()
    if not scoped_names:
        return  # no model is scoped (prod today) — inert short-circuit, no walk

    statement = orm_execute_state.statement
    # Single walk: every referenced scoped table + the renders-but-doesn't-filter
    # set (OUTER-join / writable-CTE DML). UNION the columns-clause mappers,
    # because a raw ``from_statement`` references the scoped table only as a mapper
    # (the SQL text is opaque to the tree walk).
    referenced, non_filtering = _walk_scoped_refs(statement, scoped_names)
    referenced |= {m.local_table.name for m in _scoped_mappers(orm_execute_state)}
    if not referenced:
        return  # no scoped table touched anywhere — nothing to enforce

    scope = _scope.get()
    if scope is SYSTEM:
        return  # explicit cross-user / system access — no injection, no raise

    if scope is None:
        # Fail-closed: a scoped table touched (anywhere) with no scope set.
        raise UnscopedQueryError(
            f"SELECT references scoped table(s) {sorted(_label(t) for t in referenced)} "
            "but no scope is set. Open a user_session(scope) or "
            "system_session(reason) first."
        )

    table_map = _scoped_table_map()

    # DENY-BY-DEFAULT. A scoped table is POSITIVELY-INJECTABLE iff:
    #   (a) it is NOT in a renders-but-doesn't-filter position (OUTER join /
    #       writable-CTE DML), AND
    #   (b) a raw from_statement is NOT in play (loader criteria no-ops against
    #       opaque text — nothing is provably filtered), AND
    #   (c) the compile-probe confirms with_loader_criteria rendered its tenant
    #       predicate for that table.
    # Everything else referenced → RAISE. When in doubt, RAISE.
    if orm_execute_state.is_from_statement:
        injectable: set[str] = set()
    else:
        candidates = referenced - non_filtering
        dialect = orm_execute_state.session.bind.dialect
        injectable = _compile_filtered_tables(statement, dialect, table_map, candidates)

    not_injectable = referenced - injectable
    if not_injectable:
        bad = sorted(_label(t) for t in not_injectable)
        raise UnscopedQueryError(
            f"SELECT references scoped table(s) {bad} that the tenant filter could "
            "not be PROVEN to exclude the other tenant's rows (e.g. an OUTER JOIN's "
            "row-preserving ON clause, a count(*)/aggregate or EXISTS correlation "
            "with no scoped column projected, a raw from_statement, or a writable "
            "CTE). Restructure to an injectable shape — query the scoped entity or "
            "project a scoped column via an INNER JOIN / IN-subquery — use "
            "system_session(reason=...) for deliberate cross-user access, or (for "
            f"raw SQL) the scoped_sql helper. Offending table(s): {bad}."
        )

    # Inject per-class OR-combined criteria for the positively-injectable set.
    # One with_loader_criteria per scoped class (each may declare different axes).
    #
    # The criteria is passed as a PRE-BUILT expression (not a lambda): a lambda
    # closing over `scope` trips with_loader_criteria's lambda-cache analysis
    # ("closure variable not a cacheable SQL element"). A concrete expression
    # carries its bound values directly and caches fine — and we *want* a fresh
    # expression per scope anyway (the filter values differ per user).
    options = []
    for table_name in injectable:
        cls = table_map.get(table_name)
        if cls is None:
            continue
        criteria = _axis_criteria(cls, scope)
        if criteria is None:
            continue
        options.append(with_loader_criteria(cls, criteria, include_aliases=True))

    if options:
        orm_execute_state.statement = orm_execute_state.statement.options(*options)


# ── Layer 3 — INSERT-time owner stamping ─────────────────────────────────


def _stamp_user_on_insert(mapper: Any, connection: Any, target: Any) -> None:
    """``before_insert`` handler for ``UserScoped`` instances.

    With a real Scope: stamp the owner column from scope.user_id if unset;
    assert it equals scope.user_id if already set (can't insert for another
    user). Under SYSTEM scope: leave the value as-is (system code sets owners
    explicitly). Under no scope (None): raise — inserting a tenant row with no
    identity is the write-side equivalent of the fail-closed SELECT, and
    silently NULL-stamping would corrupt ownership.
    """
    if not isinstance(target, UserScoped):
        return

    scope = _scope.get()
    if scope is SYSTEM:
        return  # system code owns explicit ownership assignment
    if scope is None:
        raise UnscopedQueryError(
            f"INSERT of scoped model {type(target).__name__} with no scope "
            "set. Open a user_session(scope) or system_session(reason) first."
        )

    col = type(target).__tenant_user_col__
    current = getattr(target, col, None)
    if current is None:
        setattr(target, col, scope.user_id)
    elif current != scope.user_id:
        raise UnscopedQueryError(
            f"INSERT of {type(target).__name__} sets {col}={current!r} but the "
            f"active scope is user {scope.user_id!r}; cannot insert for another "
            "user."
        )


# ── Idempotent event registration ───────────────────────────────────────

_events_registered = False


def register_scope_events() -> None:
    """Register the choke-point events at the class level so they cover every
    session/model the sessionmaker produces.

    Two different event targets:
      * ``do_orm_execute`` is a *Session* event → listen on the ``Session``
        class (the sync class ``AsyncSession`` wraps; the async greenlet
        dispatches through it). Class-level registration covers every session
        ``get_sessionmaker()`` builds.
      * ``before_insert`` is a *Mapper* event → listen on the ``Mapper`` base
        class itself, which registers a global mapper-level listener firing for
        every mapped class (existing and future). (``propagate=True`` is for
        listening on a *mapped class* to also cover its subclasses; on the
        ``Mapper`` base it would try to iterate descendants and fail.)
      * ``mapper_configured`` is a *Mapper* event → drop the scoped-table-name
        cache whenever a new model maps in (a test model on ``_TestBase``), so
        the traversal always sees the current scoped-table set.

    Idempotent — a flag plus ``event.contains`` guards against
    double-registration on re-import / repeated startup.
    """
    global _events_registered
    if _events_registered:
        return
    if not event.contains(Session, "do_orm_execute", _enforce_scope):
        event.listen(Session, "do_orm_execute", _enforce_scope)
    if not event.contains(Mapper, "before_insert", _stamp_user_on_insert):
        event.listen(Mapper, "before_insert", _stamp_user_on_insert)
    if not event.contains(Mapper, "mapper_configured", _invalidate_scoped_table_cache):
        event.listen(Mapper, "mapper_configured", _invalidate_scoped_table_cache)
    _events_registered = True


# Register on import so any code path that uses the ORM is protected. Inert
# until a production model opts into a scope mixin.
register_scope_events()
