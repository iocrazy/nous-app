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

OUT OF REACH (known gaps): raw ``text()`` DML bypasses the ORM events entirely
(escape hatch — see decisions doc §4 "raw SQL is a reviewed exception"), and
writable-CTE-nested DML (e.g. a SELECT over an ``update(...).cte()``) reports as
a SELECT, slipping past the bulk-DML forbid — that case is closed by the
follow-on SELECT full-statement-traversal task.

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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Union

from loguru import logger
from sqlalchemy import event, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapper, Session, with_loader_criteria

from app.db.orm_base import ProjectScoped, TeamScoped, UserScoped
from app.db.session import get_sessionmaker

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


@asynccontextmanager
async def system_session(reason: str) -> AsyncIterator[AsyncSession]:
    """Open a committing write session with NO tenant injection (cross-user /
    system access).

    ``reason`` is mandatory for audit + greppability ("why is this query
    allowed to see every user's rows?"). It is logged at debug so the call
    site is recoverable from logs/telemetry. Use for sweepers, admin
    analytics, migrations, and other deliberately cross-user work.
    """
    from app.db.session import _request_session  # local: avoid import cycle

    logger.debug("[scope] system_session opened: {}", reason)
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
    """Mappers in the statement whose class inherits a scope marker mixin."""
    return [
        m
        for m in state.all_mappers
        if issubclass(m.class_, (UserScoped, TeamScoped, ProjectScoped))
    ]


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


def _enforce_scope(orm_execute_state: Any) -> None:
    """``do_orm_execute`` handler: forbid bulk/Core DML on scoped models under a
    user scope and inject the tenant filter on SELECTs that touch a scoped
    model. Inert until a model inherits a scope mixin."""
    if _forbid_scoped_bulk_dml(orm_execute_state):
        return  # handled (or allowed) as a write statement

    if not orm_execute_state.is_select:
        return

    scoped = _scoped_mappers(orm_execute_state)
    if not scoped:
        return  # no scoped model in this statement — nothing to enforce

    scope = _scope.get()
    if scope is SYSTEM:
        return  # explicit cross-user / system access — no injection
    if scope is None:
        # Fail-closed: a scoped table touched with no scope established.
        raise UnscopedQueryError(
            "SELECT touches scoped model(s) "
            f"{[m.class_.__name__ for m in scoped]} but no scope is set. "
            "Open a user_session(scope) or system_session(reason) first."
        )

    # scope is a real Scope — inject per-class OR-combined criteria. One
    # with_loader_criteria per scoped class (each may declare different axes).
    #
    # The criteria is passed as a PRE-BUILT expression (not a lambda): a lambda
    # closing over `scope` trips with_loader_criteria's lambda-cache analysis
    # ("closure variable not a cacheable SQL element"). A concrete expression
    # carries its bound values directly and caches fine — and we *want* a fresh
    # expression per scope anyway (the filter values differ per user).
    options = []
    for mapper in scoped:
        cls = mapper.class_
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
    _events_registered = True


# Register on import so any code path that uses the ORM is protected. Inert
# until a production model opts into a scope mixin.
register_scope_events()
