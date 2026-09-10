"""One way to start ``execute_issue`` for an existing issue.

Moved out of ``issues_router`` (phase 2b-1 Task 3) so the fork service and
the two endpoints (``/dispatch``, ``/resume``) share it. The DBOS dispatcher
and the workflow-id persist helper still live on the router module and are
resolved at call time — their tests monkeypatch them THERE.

Phase 2b-2 §4.1: the dispatch WINDOW. ``_dispatch_execute_issue`` returns as
soon as DBOS accepts the enqueue, but the lock (``execution_locked_at``) and
the ``agent_runs`` row are both written INSIDE the workflow, by
``atomic_checkout``. For the seconds in between every pre-existing busy check
— fork's ``running_root_run_id``, resume's ``execution_locked_at``, the
comment→inbox decision — reads idle. A fork landing there swings
``ai_session_id`` and the workflow's own checkout then silently returns
``{"skipped": True}``. ``execution_state.dispatching`` is the marker that
closes the window; ``atomic_checkout`` removes it in the same UPDATE that
opens the lock.

WHERE the marker is written matters. Five call sites reach ``execute_issue``
and only three of them go through ``start_execute_issue``; the other four —
autopilot (``node_start``), pipeline relay, routine schedule, stranded
recovery — call ``issues_router._dispatch_execute_issue`` directly. Those are
the UNATTENDED dispatches, the ones most likely to collide with a human's
fork. So the write lives on that one shared DBOS seam (which calls
``mark_dispatching`` below) rather than at four separate patch points that a
sixth caller would then also have to remember. This module owns the marker's
SHAPE and its readers; the router owns the moment.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta, timezone

from loguru import logger

from app.services.issues.execution_state import merge_execution_state

# The marker's shelf life. execute_issue's first act is atomic_checkout, so the
# real window is milliseconds; 60s is the "the workflow never came up" line,
# same order as the reaper's. An expired marker reads as absent — a failed
# dispatch must never pin an issue busy forever.
DISPATCH_MARKER_TTL_S = 60


class DispatchFailed(RuntimeError):
    """DBOS refused the dispatch; the router maps it to a 500."""


def _utc(value: datetime) -> datetime:
    """A naive timestamp is UTC. Both sides go through here: ``at`` is whatever
    string was written into jsonb, ``now`` is a public kwarg other modules
    were told to use, and subtracting an aware from a naive is a TypeError —
    an advisory guard must never raise on the dispatch path."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def is_dispatching(
    issue: dict, *, now: datetime | None = None, ttl_s: int = DISPATCH_MARKER_TTL_S
) -> bool:
    """True while a dispatch is in flight but the workflow has not checked out.

    ``_dispatch_execute_issue`` returns as soon as DBOS accepts the enqueue, but
    ``execution_locked_at`` is written by ``atomic_checkout`` INSIDE the
    workflow. In between there is no run row and no lock — every pre-existing
    busy check reads idle.

    ``merge_execution_state`` merges with ``||``, so clearing the marker on a
    failed dispatch writes JSON ``null`` rather than dropping the key; ``null``
    (and a marker carrying no ``at``, and a wholly NULL ``execution_state``
    column, which is what most rows have) therefore has to read as NOT busy.
    The real key removal happens in ``atomic_checkout``.
    """
    marker = (issue.get("execution_state") or {}).get("dispatching") or {}
    at = marker.get("at") if isinstance(marker, dict) else None
    if not at:
        return False
    try:
        stamped = _utc(datetime.fromisoformat(str(at)))
    except ValueError:
        return False  # unparseable = not a guard we can trust; never wedge on it
    return _utc(now or datetime.now(timezone.utc)) - stamped < timedelta(seconds=ttl_s)


def looks_like_duplicate_dispatch(exc: BaseException) -> bool:
    """A duplicate workflow_id is a soft success — DBOS already holds it, and
    that workflow's own ``atomic_checkout`` will clear the marker. Shared by
    the seam (decides whether to clear) and ``start_execute_issue`` (decides
    whether to raise) so the two can never disagree about what a duplicate is.
    """
    low = repr(exc).lower()
    return "already exists" in low or "duplicate" in low


async def mark_dispatching(issue_id: int, workflow_id: str) -> None:
    """Open the dispatch window. Best-effort ON PURPOSE.

    The marker is advisory, never a lock: ``atomic_checkout``'s CAS is what
    actually prevents a double run. If this write fails the correct outcome is
    "this dispatch runs unguarded" — the behaviour that existed before the
    marker — not "this issue cannot run at all". Raising here would turn a
    hiccup on a high-traffic table into a refused dispatch, and it would
    surface as an untyped 500 because only ``DispatchFailed`` is mapped.
    """
    try:
        await merge_execution_state(
            issue_id,
            {
                "dispatching": {
                    "workflow_id": workflow_id,
                    "at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
    except Exception as e:  # noqa: BLE001 — degrade to unguarded, never block
        logger.warning(
            f"[issues] could not mark issue {issue_id} dispatching "
            f"(wf={workflow_id}): {e!r}; this dispatch runs unguarded"
        )


async def clear_dispatching(issue_id: int) -> None:
    """Close the window from the failure side, so a dispatch that never
    happened does not read busy for the whole TTL. Best-effort for the same
    reason as ``mark_dispatching`` — and here the TTL is the fallback."""
    try:
        await merge_execution_state(issue_id, {"dispatching": None})
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[issues] could not clear the dispatching marker on issue "
            f"{issue_id}: {e!r}; it expires in {DISPATCH_MARKER_TTL_S}s"
        )


async def start_execute_issue(issue_id: int) -> str:
    """Dispatch ``execute_issue`` under a fresh workflow_id and persist it.

    Unique per dispatch so an issue can be re-dispatched after a prior run
    finished or errored — a fixed ``issue-{id}`` id would dedup in DBOS and
    the re-dispatch would become a silent no-op. The atomic_checkout CAS lock
    (``execution_locked_at``) still prevents concurrent double-runs. A
    duplicate workflow_id is a soft success — DBOS already has it."""
    # importlib, not ``from app.api import issues_router``: the package
    # re-exports the APIRouter under that very name and would shadow the module.
    router_mod = importlib.import_module("app.api.issues_router")

    workflow_id = f"issue-{issue_id}-{uuid.uuid4().hex[:12]}"
    # The dispatching marker is NOT written here — it is written inside
    # _dispatch_execute_issue, so the four callers that bypass this function
    # get it too. See the module docstring.
    try:
        await router_mod._dispatch_execute_issue(issue_id, workflow_id)
    except Exception as e:
        if not looks_like_duplicate_dispatch(e):
            logger.warning(f"[issues] dispatch {issue_id} failed: {e}")
            raise DispatchFailed(f"DBOS dispatch failed: {e}") from e
    await router_mod._persist_workflow_id(issue_id, workflow_id)
    return workflow_id


__all__ = [
    "DISPATCH_MARKER_TTL_S",
    "DispatchFailed",
    "clear_dispatching",
    "is_dispatching",
    "looks_like_duplicate_dispatch",
    "mark_dispatching",
    "start_execute_issue",
]
