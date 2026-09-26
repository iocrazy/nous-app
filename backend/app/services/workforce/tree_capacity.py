"""Per-tree cap on live sub-agents (fh4 E3).

Every existing width limit is per CALL or per PROCESS — ``MAX_FANOUT`` (one
Task call), ``max_parallel_delegates`` (one call's semaphore), the per-agent
rate limit (in-process), ``WORKFORCE_QUEUE_CONCURRENCY`` (one worker). None
bounds how many children one root run keeps alive at once. This does.

What counts
===========
``active`` for a root = the tree's RUNNING child runs
(``agent_runs.root_run_id = root AND parent_run_id IS NOT NULL AND
status = 'running'``) + its RESERVED background tasks (``task_tracking`` agent
tasks still ``queued`` / ``assigned`` whose payload names this root, and that
no ``agent_runs`` row has claimed yet — once the run row exists it is counted
as the run, not twice). A child that has ended frees its slot, so on every
path the order is dispose (row terminal) → release → notify.

How it is serialised
====================
``pg_advisory_xact_lock(hashtext('tree:' || root))`` taken in the SAME
transaction as the count and the write that occupies the slot — the child's
``agent_runs`` INSERT (sync: ``RunRecorder.capacity_guard``) or its
``task_tracking`` INSERT (background: :func:`run_within_tree_capacity`). Two
spawns racing for the last slot therefore see each other.

⚠️ **Soft cap, by ruling (fh4 ruling 4).** Three windows remain and are
accepted rather than closed with a slot table + migration:

* a fan-out is pre-checked as a whole (:func:`check_tree_capacity`), then its
  children insert one by one — a sibling tree write in between can make a
  later child of that fan-out refuse individually;
* a Delegate is checked at call time, but its slot only exists once its run
  starts (its task row carries no ``root_run_id``);
* ``hashtext`` is 32-bit, so two roots can share a lock (they only wait on
  each other, never over-admit).

Production peak over 60 days was 3 live children per tree against a cap of 8,
so the residual windows are millisecond-scale and never observed.

Nothing here touches ``user_slot`` (``ai/chat/agent_concurrency.py``): that
bounds top-level turns per user; this bounds children per tree.
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Optional, TypeVar

from loguru import logger
from sqlalchemy import exists, func, literal, select

from app.models import AgentRuns, TaskTracking
from app.services.ai.runner.run_recorder import RunAdmissionRefused

MAX_ACTIVE_SUBAGENTS_PER_TREE_DEFAULT = 8
_ENV = "MAX_ACTIVE_SUBAGENTS_PER_TREE"
_TASK_KIND_AGENT = "agent_task"
_RESERVED_PHASES = ("queued", "assigned")

T = TypeVar("T")


class TreeCapacityExceeded(RunAdmissionRefused):
    """The tree already holds ``active`` live children and ``requested`` more
    would cross ``limit``. A refusal, not a failure: nothing was written."""

    def __init__(self, *, limit: int, active: int, requested: int) -> None:
        super().__init__(
            f"tree_capacity_exceeded: {active} active + {requested} requested "
            f"> limit {limit}"
        )
        self.limit = limit
        self.active = active
        self.requested = requested

    def as_fields(self) -> dict[str, Any]:
        """The typed refusal fields every spawn path returns to the model."""
        return {
            "error": "tree_capacity_exceeded",
            "limit": self.limit,
            "active": self.active,
            "requested": self.requested,
        }


def max_active_subagents_per_tree() -> int:
    """The cap, read per call so a temporary env override (the acceptance
    probe sets 2) needs only a container restart. Anything but a positive
    integer falls back to the default, loudly."""
    raw = os.environ.get(_ENV)
    if raw is None:
        return MAX_ACTIVE_SUBAGENTS_PER_TREE_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        logger.warning(
            f"[tree-capacity] {_ENV}={raw!r} is not a positive integer; "
            f"using {MAX_ACTIVE_SUBAGENTS_PER_TREE_DEFAULT}"
        )
        return MAX_ACTIVE_SUBAGENTS_PER_TREE_DEFAULT
    return value


def tree_lock_stmt(root: int):
    """Transaction-scoped advisory lock for one tree (released on COMMIT)."""
    key = func.hashtext(literal("tree:") + literal(str(int(root))))
    return select(func.pg_advisory_xact_lock(key))


def active_children_stmt(root: int):
    return (
        select(func.count())
        .select_from(AgentRuns)
        .where(AgentRuns.root_run_id == int(root))
        .where(AgentRuns.parent_run_id.is_not(None))
        .where(AgentRuns.status == "running")
    )


def reserved_tasks_stmt(root: int):
    has_run = exists().where(AgentRuns.task_id == TaskTracking.dbos_workflow_id)
    return (
        select(func.count())
        .select_from(TaskTracking)
        .where(TaskTracking.task_kind == _TASK_KIND_AGENT)
        .where(TaskTracking.phase.in_(_RESERVED_PHASES))
        .where(
            TaskTracking.metadata_["agent_payload"]["root_run_id"].astext
            == str(int(root))
        )
        .where(~has_run)
    )


async def _count_active(session: Any, root: int) -> int:
    runs = (await session.execute(active_children_stmt(root))).scalar_one()
    tasks = (await session.execute(reserved_tasks_stmt(root))).scalar_one()
    return int(runs or 0) + int(tasks or 0)


async def reserve_in_session(session: Any, *, root: int, requested: int) -> int:
    """Lock the tree, count, and refuse if ``requested`` more would cross the
    cap. Runs INSIDE the caller's transaction; the caller's write that
    occupies the slot must follow in the same one. Returns ``active``."""
    await session.execute(tree_lock_stmt(root))
    active = await _count_active(session, root)
    limit = max_active_subagents_per_tree()
    if active + requested > limit:
        logger.warning(
            f"[tree-capacity] tree {root}: refusing {requested} more "
            f"({active} active, limit {limit})"
        )
        raise TreeCapacityExceeded(limit=limit, active=active, requested=requested)
    return active


def _engine_ready() -> bool:
    from app.db.engine import is_configured

    return is_configured()


async def check_tree_capacity(root: Optional[int], requested: int) -> int:
    """A standalone check in its own short transaction (fan-out pre-check,
    Delegate). Reserves nothing — see the soft-cap note above. Returns
    ``active``; 0 when there is no tree or no engine to ask."""
    if root is None or not _engine_ready():
        return 0
    from app.db.session import write_scope

    async with write_scope() as session:
        return await reserve_in_session(session, root=root, requested=requested)


async def run_within_tree_capacity(
    root: Optional[int], requested: int, write: Callable[[], Awaitable[T]]
) -> T:
    """Lock, count, then run ``write`` in ONE transaction: every repository
    ``write_scope()`` inside ``write`` joins it, so the row that occupies the
    slot commits together with the check. Raises ``TreeCapacityExceeded``
    before ``write`` runs when the tree is full. No tree, or no engine (unit
    tests), runs ``write`` unguarded."""
    if root is None or not _engine_ready():
        return await write()
    from app.db.session import unit_of_work

    async with unit_of_work() as session:
        await reserve_in_session(session, root=root, requested=requested)
        return await write()


async def resolve_tree_root(parent_run_id: Optional[str]) -> Optional[int]:
    """The root of the tree a child of ``parent_run_id`` joins: the parent's
    own ``root_run_id``, or the parent itself when it is a root.

    None when there is no parent. A failed read falls back to the parent id
    and says so — the old post-insert attach had the same fallback, and a
    wrong-but-bounded root (the parent's subtree) is better than none."""
    if not parent_run_id:
        return None
    try:
        parent = int(parent_run_id)
    except (TypeError, ValueError):
        logger.error(f"[tree-capacity] unusable parent run id {parent_run_id!r}")
        return None
    if not _engine_ready():
        return parent
    from app.db.session import read_scope

    try:
        async with read_scope() as session:
            row = (
                await session.execute(
                    select(AgentRuns.root_run_id).where(AgentRuns.id == parent)
                )
            ).first()
    except Exception as err:  # noqa: BLE001 — see docstring
        logger.error(
            f"[tree-capacity] could not read the root of run {parent} ({err}); "
            "using the parent as the root"
        )
        return parent
    return int(row[0]) if row is not None and row[0] is not None else parent


__all__ = [
    "MAX_ACTIVE_SUBAGENTS_PER_TREE_DEFAULT",
    "TreeCapacityExceeded",
    "active_children_stmt",
    "check_tree_capacity",
    "max_active_subagents_per_tree",
    "reserve_in_session",
    "reserved_tasks_stmt",
    "resolve_tree_root",
    "run_within_tree_capacity",
    "tree_lock_stmt",
]
