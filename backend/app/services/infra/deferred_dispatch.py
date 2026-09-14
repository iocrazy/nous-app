"""Defer workflow dispatch out of a ``@DBOS.step`` and into the workflow body.

DBOS refuses ``DBOS.start_workflow`` (and queue enqueue) from inside a step —
``AssertionError: assert cur_ctx.is_workflow()``. That is not a lint, it is the
engine's durability contract: a step is replayable, and a dispatch performed
inside one would fan out again on every replay.

An issue turn runs inside a step (``run_issue_reply_step`` /
``run_issue_agent_step``), and anything the turn's own tools or hooks decide to
dispatch — ``GenerateShotImage``'s ``script_shot_generate``, the
``memory_harvester`` hook's ``write_memory`` — reaches
``start_workflow_routed`` from there. Before this module every one of those
raised: the tool answered ``dispatch_failed`` and rolled the shot back to
``empty`` while its ``task_tracking`` row sat ``queued`` forever (real-stack
evidence: run 349427295369155, dbos_workflow_id 857490f7-…), and every turn
logged ``[hook:memory_harvester] side_effect dispatch failed``.

Route C, already ruled for the workforce lane (``deliver_or_dispatch`` consumed
by ``agent_workforce_workflow``'s body, and ``agent_worker.py``'s docstring for
the same defect): **the step reports, the body dispatches.** This module
generalises it to "any dispatch a turn originates".

Shape of the seam
-----------------

1. The step body runs under ``collect_deferred_dispatches()``. While that is
   active ``deferral_active()`` is True, and ``start_workflow_routed`` — after
   its routing / enabled / bounds gates still pass, so a dispatch that would
   have been refused is still refused at the same place — records the dispatch
   instead of starting it and answers ``{"deferred": True, …}`` with the
   workflow id the caller (and its ``task_tracking`` row) already holds.
2. The step returns those records in ``pending_dispatches``. They cross the
   step boundary, so every field is JSON-serialisable and the workflow callable
   travels as the string ``"<module>:<name>"``, never a live object.
3. The workflow body calls ``drain_deferred_dispatches(...)`` and performs the
   dispatches for real.

Resolving a string back to a callable is an authorization decision, not a
convenience: the string arrives from a step's return value. It is resolved only
to an object that ``app.workflows._dispatch_bundle`` exports — the existing,
reviewed list of "workflows dispatch is allowed to hand out". Anything else is
refused with an ERROR and its task row failed, never started.

ContextVar, not a parameter, because the originators are three frames apart
from the step (tool handler, hook closure) and neither of them can be handed a
collector without widening every signature in between. The collected value is a
*mutable list* held by the var rather than a var that gets re-``set()``: the
memory-harvester closure runs its dispatch on a worker thread under
``contextvars.copy_context()`` (``app.tasks.utils.run_async``), and a copied
context propagates the list object but never a re-assignment.

Replay
------

A step's result is checkpointed, so a replayed step hands the body the SAME
records — including the workflow id, which is fixed at record time (either the
originator's own pre-generated id, as ``GenerateShotImage`` does to match its
``task_tracking`` row, or a uuid4 minted here). Re-dispatching that id is
idempotent: DBOS treats an explicit workflow id as the idempotency key and
returns the existing handle instead of starting a second run. A record that
carried NO id would fan out one extra workflow per replay, which is why one is
always minted rather than left to the server.

Known Limitations and Deferred Work
-----------------------------------

A step that RAISES loses the dispatches recorded before the raise — the records
travel on the step's return value, and there isn't one. That matches the
originators' own failure handling today (``GenerateShotImage`` rolls the shot
back and fails in-band rather than raising), but it is a real gap for any future
originator that lets an exception through after recording.

Records appended AFTER the step snapshots the list (a fire-and-forget task that
outlives the turn and records on its way out) are dropped silently — nothing
reads the list again, and nothing logs the ones left behind.
"""

from __future__ import annotations

import contextvars
import importlib
import json
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Optional

from loguru import logger

#: The active collector, or ``None`` when dispatch runs immediately (the chat
#: path, every REST path, and the workflow bodies themselves).
_collector: contextvars.ContextVar[Optional[list[dict[str, Any]]]] = (
    contextvars.ContextVar("deferred_dispatches", default=None)
)


class DeferredDispatchError(RuntimeError):
    """A dispatch could not be recorded for later. Raised at the record seam
    (never at drain) so the originator — which still holds its shot status and
    its task row — can roll back and report a typed failure, exactly as it
    would for any other dispatch refusal."""


@dataclass(frozen=True)
class DeferredDispatch:
    """One dispatch a step decided on and the workflow body will perform.

    Every field is JSON-serialisable on purpose: this record is returned out of
    a ``@DBOS.step``, so it is checkpointed by the engine and replayed verbatim.
    ``workflow`` is ``"<module>:<name>"``, resolved at drain time against the
    dispatch bundle (see ``_resolve_workflow``).

    ``task_id`` is the ``task_tracking`` row the originator already created (a
    plain DB write, which a step may do). It is carried so a drain that cannot
    dispatch can FAIL that row instead of leaving it queued forever — the exact
    orphan this module exists to close. It is never passed to the workflow.
    """

    task_type: str
    workflow: str
    kwargs: dict[str, Any]
    workflow_id: str
    task_id: Optional[str] = None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, raw: dict[str, Any]) -> "DeferredDispatch":
        return cls(
            task_type=str(raw.get("task_type") or ""),
            workflow=str(raw.get("workflow") or ""),
            kwargs=dict(raw.get("kwargs") or {}),
            workflow_id=str(raw.get("workflow_id") or ""),
            task_id=raw.get("task_id") or None,
        )


def workflow_ref(fn: Callable[..., Any]) -> str:
    """``"<module>:<name>"`` for a dispatchable workflow callable.

    ``@DBOS.workflow()`` preserves ``__module__``/``__name__`` (functools.wraps),
    so this round-trips through ``_resolve_workflow`` to the same object.
    """
    return f"{getattr(fn, '__module__', '')}:{getattr(fn, '__name__', '')}"


def deferral_active() -> bool:
    """True while a collector is installed on this context."""
    return _collector.get() is not None


@asynccontextmanager
async def collect_deferred_dispatches():
    """Collect dispatches originated inside the block instead of starting them.

    Yields the list the records land in. The list is handed out (rather than
    only readable afterwards) so the caller can snapshot it at return time
    while still inside the block — which is how the issue steps attach
    ``pending_dispatches`` to their own result.

    Nested use restores the outer collector on exit, so an inner block never
    strands the outer one.
    """
    bucket: list[dict[str, Any]] = []
    token = _collector.set(bucket)
    try:
        yield bucket
    finally:
        _collector.reset(token)


def record_deferred_dispatch(
    *,
    task_type: str,
    dbos_workflow_callable: Callable[..., Any],
    dbos_workflow_kwargs: Optional[dict[str, Any]] = None,
    workflow_id: str,
    task_id: Optional[str] = None,
) -> dict[str, Any]:
    """Append one dispatch onto the active collector; return the record.

    Raises ``DeferredDispatchError`` when there is no collector (a caller bug —
    ``deferral_active()`` gates this), when the callable is not one the dispatch
    bundle exports, or when the record would not survive the step boundary.

    Both refusals happen HERE, not at drain, and that is the point: a dispatch
    that can never be performed must fail while the originator is still holding
    the pieces it would need to roll back (``GenerateShotImage``'s shot claim
    and its ``task_tracking`` row). The drain's own allowlist check stays as the
    second net — it guards a *different* threat, a record that reached the body
    without passing through here.
    """
    bucket = _collector.get()
    if bucket is None:
        raise DeferredDispatchError(
            "record_deferred_dispatch called with no active collector"
        )
    if not _is_dispatchable(dbos_workflow_callable):
        raise DeferredDispatchError(
            f"cannot defer {workflow_ref(dbos_workflow_callable)!r}: only "
            "workflows exported by app.workflows._dispatch_bundle can be "
            "dispatched from a workflow body"
        )
    record = DeferredDispatch(
        task_type=task_type,
        workflow=workflow_ref(dbos_workflow_callable),
        kwargs=dict(dbos_workflow_kwargs or {}),
        workflow_id=workflow_id,
        task_id=task_id,
    ).to_record()
    try:
        json.dumps(record)
    except (TypeError, ValueError) as exc:
        raise DeferredDispatchError(
            f"deferred dispatch for task_type={task_type!r} is not "
            f"JSON-serialisable and cannot cross the step boundary: {exc}"
        ) from exc
    bucket.append(record)
    return record


def _is_dispatchable(target: Any) -> bool:
    """Allowlist: ``target`` must be a WORKFLOW the dispatch bundle exports.

    Two conditions, both required:

    * it is defined in ``app.workflows`` — the bundle's namespace also holds
      what the bundle itself imported to build it (``dbos.Queue``, the queue
      instances). Those are in ``vars()`` without being anything a dispatcher
      may hand out, and ``"dbos:Queue"`` resolves to one of them;
    * it IS one of the bundle's objects, by identity — not by name. The bundle
      is the reviewed list (see its module docstring), and a step's return
      value is untrusted input to the body; without this the string in it would
      name any importable callable in the process.
    """
    if not callable(target):
        return False
    module = getattr(target, "__module__", "") or ""
    if module != "app.workflows" and not module.startswith("app.workflows."):
        return False
    try:
        from app.workflows import _dispatch_bundle
    except ImportError:  # pragma: no cover — the bundle is always importable
        logger.error("[deferred_dispatch] dispatch bundle unavailable")
        return False
    return any(value is target for value in vars(_dispatch_bundle).values())


def _resolve_workflow(ref: str) -> Optional[Callable[..., Any]]:
    """``"<module>:<name>"`` → the callable, or ``None`` if it is not one the
    dispatch bundle exports.

    Only ``ImportError`` is swallowed (a module that is not there is simply not
    in the bundle). An import that RUNS and then blows up propagates to
    ``drain_deferred_dispatches``' per-record containment, where it is reported
    with its real cause rather than flattened into "not in the bundle".
    """
    module_name, sep, attr = (ref or "").partition(":")
    if not sep or not module_name or not attr:
        return None
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    target = getattr(module, attr, None)
    if target is None or not _is_dispatchable(target):
        return None
    return target


async def _fail_task(task_id: Optional[str], error_msg: str) -> None:
    """Fail the originator's ``task_tracking`` row, best effort.

    Route C rule 3: ``fail()`` is the manager's own API — nothing here PATCHes
    a phase column. A row that cannot be failed is logged, never raised: the
    remaining records still have to be dispatched.
    """
    if not task_id:
        return
    try:
        from app.services.infra.unified_task_manager import get_task_manager

        await get_task_manager().fail(
            task_id, error_msg, error_code="deferred_dispatch_failed"
        )
    except Exception as exc:  # noqa: BLE001 — reporting the failure, not the op
        logger.error(
            f"[deferred_dispatch] could not fail task {task_id}: {exc!r} "
            f"(original error: {error_msg})"
        )


def _task_id_of(raw: Any) -> Optional[str]:
    """The ``task_tracking`` row a record names, read without trusting its
    shape — this runs on the failure path, where the record's shape is exactly
    what may have gone wrong."""
    if not isinstance(raw, dict):
        return None
    task_id = raw.get("task_id")
    return str(task_id) if task_id else None


async def _dispatch_one(raw: Any, start_workflow_routed: Any) -> dict[str, Any]:
    """Parse, resolve and start ONE record. Raises on every failure mode.

    Deliberately raises rather than returning a status: containment belongs to
    the caller and belongs in ONE place. Parsing a malformed record, an import
    that blows up, a refused callable and a dispatch that fails are four ways
    for one record to go wrong, and a shape where three of them are handled
    inline and one escapes is exactly the defect this structure removes.
    """
    record = DeferredDispatch.from_record(raw)
    target = _resolve_workflow(record.workflow)
    if target is None:
        raise DeferredDispatchError(
            f"refusing deferred dispatch {record.workflow!r}: not a workflow "
            "exported by app.workflows._dispatch_bundle"
        )
    return await start_workflow_routed(
        record.task_type,
        dbos_workflow_callable=target,
        dbos_workflow_kwargs=record.kwargs,
        workflow_id=record.workflow_id or None,
    )


async def drain_deferred_dispatches(
    items: Optional[Iterable[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Perform the dispatches a step recorded. **Workflow body only.**

    Returns the ``start_workflow_routed`` result of each record that started,
    in order. Every record is attempted independently and **nothing here
    raises** — not a malformed record, not an import that explodes, not a
    refused callable, not a dispatch that fails. Each is logged at ERROR and
    has its ``task_tracking`` row failed (so it cannot sit queued forever), and
    the drain moves on.

    That guarantee is load-bearing, not defensive habit: the callers in
    ``issue_lifecycle`` drain BEFORE ``route_finish_outcome``, so anything
    escaping here would eat the issue's status routing — the two results of a
    turn are orthogonal and neither may swallow the other.

    The collector is detached for the duration, so a drain can never feed an
    enclosing collector and defer the very records it is draining.
    """
    records = list(items or [])
    if not records:
        return []

    from app.services.infra.dbos_orchestrator import start_workflow_routed

    started: list[dict[str, Any]] = []
    token = _collector.set(None)
    try:
        for raw in records:
            try:
                started.append(await _dispatch_one(raw, start_workflow_routed))
            except Exception as exc:  # noqa: BLE001 — one record, not the body
                msg = f"deferred dispatch record {raw!r} failed: {exc!r}"[:1000]
                logger.error(f"[deferred_dispatch] {msg}")
                await _fail_task(_task_id_of(raw), msg)
    finally:
        _collector.reset(token)
    return started


__all__ = [
    "DeferredDispatch",
    "DeferredDispatchError",
    "collect_deferred_dispatches",
    "deferral_active",
    "drain_deferred_dispatches",
    "record_deferred_dispatch",
    "workflow_ref",
]
